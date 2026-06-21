"""Domena summaryzacji — streszczenie tekstu pod dekretację (krok 2.4.1).

Warstwa DOMENOWA: niezalezna od tego, ktory dostawca LLM stoi pod spodem (analogia do
`ExtractionService` nad `TikaClient`). Transport/generacje (`LLMClient`, krok 2.2) dostaje
wstrzyknieta — rozmawia z nia tylko przez `complete`. To TU mieszka wiedza o promptach:
system prompt po polsku + szablon usera + truncacja wejscia pod okno modelu.

Format streszczenia (decyzja produktowa, 2.4): HYBRYDA — krotki akapit + wypunktowanie
kluczowych elementow (typ pisma, nadawca, czego dotyczy, termin, akcja), wszystko jako
JEDEN string `summary` (bez JSON/parsowania). System prompt nizej narzuca ten format.

Truncacja (truncacja POD OKNO MODELU — co innego niz limit stron ekstrakcji z 2.3.5):
prosta, w znakach (`max_input_chars`), bierzemy POCZATEK tekstu (nie chunking) + log +
metadana `truncated`. Prog konfigurowalny (docelowo z `Settings.llm_max_input_chars`),
spojny z `MAX_OCR_PAGES`/`MAX_UPLOAD_BYTES` (patrz README -> "Spojnosc limitow pipeline'u").

Struktura (jak w `ExtractionService`): czyste fragmenty bez I/O (truncacja, budowa
wiadomosci, metadane) w osobnych helperach; w async `summarize` zostaje samo wywolanie
`LLMClient` + zlozenie wyniku. Wyjatki LLM (`LLMError`...) NIE sa tu lapane — propaguja do
endpointu, ktory mapuje je na HTTP (krok 2.4.2).
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from app.llm import LLMClient, LLMResult, LLMUsage

logger = logging.getLogger(__name__)

# --- Prompt (LOGIKA, nie sekret -> w kodzie, nie w ENV; nie zmienia sie przy zmianie dostawcy) ---

# System prompt po polsku: rola pod dekretacje + narzucony format hybrydowy (akapit + punkty).
_SYSTEM_PROMPT = (
    "Jesteś asystentem przygotowującym zwięzłe streszczenia pism dla osoby dekretującej "
    "dokumenty w urzędzie. Streść dokument tak, by osoba dekretująca od razu wiedziała, "
    "czego pismo dotyczy i co należy z nim zrobić.\n\n"
    "Odpowiadaj WYŁĄCZNIE po polsku, w formacie:\n"
    "1. Jedno- lub dwuzdaniowe streszczenie naturalnym językiem.\n"
    "2. Pusta linia, a pod nią wypunktowanie kluczowych elementów — każdy w osobnej linii "
    "zaczynającej się od „• ”, TYLKO te, które faktycznie występują w dokumencie:\n"
    "   • Typ pisma\n"
    "   • Nadawca\n"
    "   • Czego dotyczy\n"
    "   • Termin / data\n"
    "   • Oczekiwana akcja\n\n"
    "Pomijaj punkty, których w dokumencie nie ma — niczego nie zmyślaj. Bądź rzeczowy i krótki."
)

# Szablon wiadomosci usera: ramka + tresc dokumentu (system trzyma instrukcje formatu).
_USER_TEMPLATE = "Streść poniższy dokument:\n\n{text}"


# --- Wynik domenowy summaryzacji -------------------------------------------------


class SummarizationMetadata(BaseModel):
    """Metadane streszczenia — diagnostyka (jaki model, koszt) i sygnał truncacji."""

    model: str        = Field(description="Identyfikator modelu, który faktycznie odpowiedział, np. 'gpt-4o-mini' lub 'fake-echo'.")
    input_chars: int  = Field(description="Długość tekstu wejściowego (po strip), w znakach — PRZED ewentualną truncacją.")
    truncated: bool   = Field(default=False, description="Czy wejście ucięto do `max_input_chars` (streszczenie z części dokumentu).")
    usage: LLMUsage   = Field(default_factory=LLMUsage, description="Zużycie tokenów (prompt/completion/total) — diagnostyka kosztu.")


class SummarizationResult(BaseModel):
    """Domenowy wynik: streszczenie + metadane."""

    summary: str                    = Field(description="Streszczenie (hybryda: akapit + punkty), jeden tekst.")
    metadata: SummarizationMetadata = Field(description="Metadane: model, długość wejścia, truncacja, zużycie.")


# --- Wyjatki domenowe summaryzacji (niezalezne od dostawcy LLM) ------------------
# Mapowanie na kody HTTP robi endpoint (2.4.2). Bledy LLM (`LLMError`...) to osobna
# hierarchia z `app.llm` — propaguja przez te warstwe.


class SummarizationError(Exception):
    """Bazowy błąd domeny summaryzacji."""


class EmptyInputError(SummarizationError):
    """Wejście jest puste (sam whitespace) — nie ma czego streszczać."""


# --- Serwis domenowy -------------------------------------------------------------


class SummarizationService:
    """Domena summaryzacji nad wstrzykniętym `LLMClient`.

    Do czego:
        Zamienia surowy tekst dokumentu na `SummarizationResult` (streszczenie + metadane):
        składa prompt (system + szablon usera), pilnuje truncacji wejścia pod okno modelu,
        woła `LLMClient.complete`. Nie wie i nie ma wiedzieć, który dostawca odpowiada —
        dostaje go wstrzykniętego z fabryki (`get_llm_client()`), bo LLM jest wymienialny.

    Flow jednego `summarize(...)`:
        1. strip + pusto -> `EmptyInputError`,
        2. `_truncate` -> tekst pod limit + flaga `truncated`,
        3. `LLMClient.complete` (system + user) -> `LLMResult` (jedyne I/O; błędy LLM propagują),
        4. `_build_metadata` -> `SummarizationResult`.
    """

    def __init__(
        self,
        client: LLMClient,             # transport/generacja LLM (z fabryki); w testach `FakeLLMClient`
        *,
        max_input_chars: int = 90_000,   # limit znaków wejścia pod okno modelu (docelowo z `Settings`)
        max_output_tokens: int = 600,    # górny limit długości streszczenia (zwięzłe -> krótkie)
    ) -> None:
        """Opis metody:
        Zbuduj serwis nad wstrzykniętym klientem LLM (sama konfiguracja, bez I/O).

        Przyklad argumentow:
            client=FakeLLMClient()
            max_input_chars=90000

        Przyklad wyniku:
            gotowy SummarizationService
        """
        self._client            = client
        self._max_input_chars   = max_input_chars
        self._max_output_tokens = max_output_tokens

    # --- Czyste helpery (bez I/O) — testowalne jednostkowo bez LLM -------------------

    def _truncate(
        self,
        text: str,   # tekst wejściowy PO strip
    ) -> tuple[str, bool]:
        """Opis metody:
        Przytnij tekst do `max_input_chars` znaków (bierzemy POCZĄTEK — nie chunking). Zwraca
        (tekst, czy_ucięto). Truncacja NIE jest cicha: gdy tniemy, logujemy ostrzeżenie.

        Przyklad argumentow:
            text="...120 000 znaków..."   # przy max_input_chars=90000

        Przyklad wyniku:
            ("...pierwsze 90 000 znaków...", True)
        """
        # Mieści się w limicie -> bez zmian.
        if len(text) <= self._max_input_chars:
            return text, False
        # Za długie -> tniemy POCZĄTEK; log (nie cicho), by było wiadomo, że streszczenie z części.
        logger.warning(
            "Tekst %d znaków > limit %d; tnę do pierwszych %d (streszczenie z części dokumentu).",
            len(text), self._max_input_chars, self._max_input_chars,
        )
        return text[: self._max_input_chars], True

    @staticmethod
    def _build_user_message(
        text: str,   # (już przycięta) treść dokumentu
    ) -> str:
        """Opis metody:
        Zbuduj wiadomość usera z szablonu (ramka + dokument). System prompt trzyma format.
        Czysta funkcja.

        Przyklad argumentow:
            text="Pismo w sprawie podatku..."

        Przyklad wyniku:
            "Streść poniższy dokument:\\n\\nPismo w sprawie podatku..."
        """
        return _USER_TEMPLATE.format(text=text)

    @staticmethod
    def _build_metadata(
        input_chars: int,    # długość wejścia (po strip) PRZED truncacją
        truncated: bool,     # czy wejście ucięto
        result: LLMResult,   # wynik z `complete` (źródło model + usage)
    ) -> SummarizationMetadata:
        """Opis metody:
        Złóż metadane wyniku z długości wejścia, flagi truncacji i danych z `LLMResult`.
        Czysta funkcja.

        Przyklad argumentow:
            input_chars=1280, truncated=False
            result=LLMResult(text="...", model="gpt-4o-mini", usage=LLMUsage(total_tokens=420))

        Przyklad wyniku:
            SummarizationMetadata(model="gpt-4o-mini", input_chars=1280, truncated=False, usage=...)
        """
        return SummarizationMetadata(
            model       = result.model,
            input_chars = input_chars,
            truncated   = truncated,
            usage       = result.usage,
        )

    # --- Wywolanie (I/O przez LLMClient) — orkiestracja ----------------------------

    async def summarize(
        self,
        *,
        text: str,   # surowy tekst dokumentu do streszczenia
    ) -> SummarizationResult:
        """Opis metody:
        Streść tekst: walidacja pustego, truncacja pod okno modelu, wołanie LLM, złożenie wyniku.

        Przyklad argumentow:
            text="Pismo z Urzędu Skarbowego w sprawie zaległości..."

        Przyklad wyniku:
            SummarizationResult(summary="Urząd Skarbowy wzywa do zapłaty...\\n\\n• Typ: ...",
                                metadata=SummarizationMetadata(model="gpt-4o-mini", input_chars=812,
                                                               truncated=False, usage=...))

        Raises:
            EmptyInputError:    wejście puste (sam whitespace).
            LLMError:           dowolny błąd warstwy LLM (auth/limit/timeout/odpowiedź) — propaguje.
        """
        # Pusto po strip = nie ma czego streszczać -> błąd domenowy (endpoint -> 422).
        normalized = text.strip()
        if not normalized:
            raise EmptyInputError("Puste wejście — brak tekstu do streszczenia.")

        # Truncacja pod okno modelu (testowana osobno); flaga ląduje w metadanych.
        body, truncated = self._truncate(normalized)

        # Jedyne I/O: generacja przez wstrzyknięty klient. Błędy LLM propagują do endpointu.
        result = await self._client.complete(
            user        = self._build_user_message(body),
            system      = _SYSTEM_PROMPT,
            max_tokens  = self._max_output_tokens,
            temperature = 0.0,   # streszczenia stabilne/powtarzalne
        )

        # Metadane liczone na DŁUGOŚCI ORYGINAŁU (po strip), nie po przycięciu.
        metadata = self._build_metadata(len(normalized), truncated, result)
        return SummarizationResult(summary=result.text.strip(), metadata=metadata)
