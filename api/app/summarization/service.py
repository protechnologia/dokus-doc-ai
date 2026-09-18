"""Domena summaryzacji — streszczenie tekstu pod dekretację (krok 2.4.1).

Warstwa DOMENOWA: niezalezna od tego, ktory dostawca LLM stoi pod spodem (analogia do
`ExtractionService` nad `TikaClient`). Transport/generacje (`LLMClient`, krok 2.2) dostaje
wstrzyknieta — rozmawia z nia tylko przez `complete`. Tu: truncacja wejscia pod okno modelu
+ wywolanie; prompty skladaja `SummarySystemPrompt` / `SummaryUserPrompt` (`prompt_system.py` /
`prompt_user.py`) z tekstow w `app/prompt/`.

Format streszczenia (decyzja produktowa, 2.4; zrewidowana 2026-07-08): WYPUNKTOWANIE
kluczowych pol (typ pisma, nadawca, czego dotyczy, termin, akcja) jako JEDEN string
`summary` (bez JSON/parsowania). Prompt systemowy narzuca ten format. Akapit
otwierajacy byl w pierwotnym zamysle, ale model go nie oddaje bez protez — patrz macierz
pomiarow w `SummarySystemPrompt`.

Truncacja (truncacja POD OKNO MODELU — co innego niz limit stron ekstrakcji z 2.3.5):
w znakach (`max_input_chars`), POCZATEK / SRODEK / KONIEC w proporcjach z zadania, ze
znacznikami pominiecia (`TextTruncator`, osobny czysty modul `truncation.py`) + log + metadane
`truncated`/`sent_chars`/`parts`. Nie chunking. Prog konfigurowalny (z
`Settings.llm_max_input_chars`), spojny z `MAX_OCR_PAGES`/`MAX_UPLOAD_BYTES` (patrz README ->
"Spojnosc limitow pipeline'u").

Struktura (jak w `ExtractionService`): czyste fragmenty bez I/O (truncacja w `TextTruncator`,
prompty w klasach promptow, metadane) osobno; w async `summarize` zostaje samo wywolanie `LLMClient` +
zlozenie wyniku. Wyjatki LLM (`LLMError`...) NIE sa tu lapane — propaguja do endpointu, ktory
mapuje je na HTTP (krok 2.4.2).
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from app.llm import LLMClient, LLMResult, LLMUsage
from app.summarization.prompt_system import SummarySystemPrompt
from app.summarization.prompt_user import SummaryUserPrompt
from app.summarization.truncation import DEFAULT_HEAD_PERCENT, DEFAULT_TAIL_PERCENT, TextPart, TextParts, TextTruncator, TruncationResult

logger = logging.getLogger(__name__)

# --- Prompty (teksty w app/prompt/*.md; wczytane raz, przy imporcie) ---------------
# Brak pliku albo rozjazd placeholderów wywala import serwisu, czyli start aplikacji —
# nie pierwsze żądanie. Uzasadnienie treści (pomiary formatu): `SummarySystemPrompt`.
_SYSTEM_PROMPT = SummarySystemPrompt()
_USER_PROMPT   = SummaryUserPrompt()


# --- Wynik domenowy summaryzacji -------------------------------------------------


class SummarizationMetadata(BaseModel):
    """Metadane streszczenia — diagnostyka (jaki model, koszt) i opis tego, co realnie trafiło do modelu."""

    model: str               = Field(description="Identyfikator modelu, który faktycznie odpowiedział, np. 'gpt-4o-mini' lub 'fake-echo'.")
    input_chars: int         = Field(description="Długość tekstu wejściowego (po strip), w znakach — PRZED ewentualną truncacją.")
    truncated: bool          = Field(default=False, description="Czy wejście ucięto do `max_input_chars` (streszczenie z części dokumentu).")
    usage: LLMUsage          = Field(default_factory=LLMUsage, description="Zużycie tokenów (prompt/completion/total) — diagnostyka kosztu.")
    sent_chars: int          = Field(description="Długość tekstu wysłanego do modelu (po truncacji, ze znacznikami pominięcia), w znakach.")
    parts: TextParts | None  = Field(default=None, description="Proporcje i zakresy części (początek/środek/koniec) względem tekstu wejściowego; None, gdy nie cięto.")


class SummarizationResult(BaseModel):
    """Domenowy wynik: streszczenie + metadane."""

    summary: str                    = Field(description="Streszczenie (wypunktowanie kluczowych pól), jeden tekst.")
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
        składa prompty (`SummarySystemPrompt` + `SummaryUserPrompt`), pilnuje truncacji wejścia pod okno modelu
        (początek / środek / koniec przez `TextTruncator`), woła `LLMClient.complete`. Nie wie
        i nie ma wiedzieć, który dostawca odpowiada — dostaje go wstrzykniętego z fabryki
        (`get_llm_client()`), bo LLM jest wymienialny.

    Flow jednego `summarize(...)`:
        1. strip + pusto -> `EmptyInputError`,
        2. `TextTruncator.apply` -> tekst pod limit + zakresy części (log, gdy cięto),
        3. `LLMClient.complete` (system + user z klas promptów) -> `LLMResult` (jedyne I/O; błędy LLM propagują),
        4. `_leading_whitespace_len` + `_build_metadata` -> `SummarizationResult` (offsety
           przesunięte na tekst klienta przez `_shift_parts`).
    """

    def __init__(
        self,
        client: LLMClient,             # transport/generacja LLM (z fabryki); w testach `FakeLLMClient`
        *,
        max_input_chars: int = 90_000,   # limit znaków wejścia pod okno modelu (z `Settings.llm_max_input_chars`)
        max_output_tokens: int = 600,    # górny limit długości streszczenia (z `Settings.llm_max_output_tokens_summary`)
    ) -> None:
        """Opis metody:
        Zbuduj serwis nad wstrzykniętym klientem LLM (sama konfiguracja, bez I/O). Trunkator
        buduje sam z limitu znaków (analogia do `ExtractionService`, który buduje `PdfPageLimiter`).

        Przyklad argumentow:
            client=FakeLLMClient()
            max_input_chars=90000

        Przyklad wyniku:
            gotowy SummarizationService

        Raises:
            ValueError: `max_input_chars` za mały na rezerwę znaczników (z `TextTruncator`).
        """
        self._client            = client
        self._truncator         = TextTruncator(max_chars=max_input_chars)
        self._max_output_tokens = max_output_tokens

    # --- Czyste helpery (bez I/O) — testowalne jednostkowo bez LLM -------------------

    @staticmethod
    def _leading_whitespace_len(
        text: str,   # surowy tekst od klienta (PRZED strip), np. "  \nPismo..."
    ) -> int:
        """Opis metody:
        Policz wiodące białe znaki zdjęte przez `strip` — o tyle trzeba przesunąć offsety części,
        by wskazywały pozycje w tekście, który klient ma w ręku (a nie w tekście po strip).
        Czysta funkcja.

        Przyklad argumentow:
            text="  \\nPismo w sprawie podatku"

        Przyklad wyniku:
            3
        """
        return len(text) - len(text.lstrip())

    @staticmethod
    def _shift_parts(
        parts: TextParts | None,   # zakresy części względem tekstu po strip; None = nie cięto
        offset: int,               # przesunięcie (wiodące białe znaki), np. 3
    ) -> TextParts | None:
        """Opis metody:
        Przesuń zakresy części o `offset` (proporcje bez zmian; części wyłączone zostają bez
        zakresu). Brak cięcia (None) przechodzi bez zmian. Czysta funkcja.

        Przyklad argumentow:
            parts=TextParts(head=TextPart(percent=45, start=0, end=40), ...), offset=3

        Przyklad wyniku:
            TextParts(head=TextPart(percent=45, start=3, end=43), ...)
        """
        # Nie cięto -> nie ma czego przesuwać.
        if parts is None:
            return None

        # Część wyłączona (bez zakresu) zostaje jak jest; włączona -> oba końce + offset.
        head, middle, tail = (
            p if p.start is None else TextPart(percent=p.percent, start=p.start + offset, end=p.end + offset)
            for p in (parts.head, parts.middle, parts.tail)
        )
        return TextParts(head=head, middle=middle, tail=tail)

    @classmethod
    def _build_metadata(
        cls,
        input_chars: int,        # długość wejścia (po strip) PRZED truncacją, np. 120000
        cut: TruncationResult,   # wynik `TextTruncator.apply` (tekst dla modelu + zakresy części)
        offset: int,             # wiodące białe znaki zdjęte przez strip (przesunięcie zakresów), np. 0
        result: LLMResult,       # wynik z `complete` (źródło model + usage)
    ) -> SummarizationMetadata:
        """Opis metody:
        Złóż metadane wyniku z długości wejścia, wyniku truncacji (flaga, długość wysłana, zakresy
        przesunięte na tekst klienta) i danych z `LLMResult`. Czysta funkcja.

        Przyklad argumentow:
            input_chars=1280, cut=TruncationResult(text="...", truncated=False, parts=None), offset=0
            result=LLMResult(text="...", model="gpt-4o-mini", usage=LLMUsage(total_tokens=420))

        Przyklad wyniku:
            SummarizationMetadata(model="gpt-4o-mini", input_chars=1280, truncated=False, usage=...,
                                  sent_chars=1280, parts=None)
        """
        return SummarizationMetadata(
            model       = result.model,
            input_chars = input_chars,
            truncated   = cut.truncated,
            usage       = result.usage,
            sent_chars  = len(cut.text),
            parts       = cls._shift_parts(cut.parts, offset),
        )

    # --- Wywolanie (I/O przez LLMClient) — orkiestracja ----------------------------

    async def summarize(
        self,
        *,
        text: str,                                 # surowy tekst dokumentu do streszczenia
        head_percent: int = DEFAULT_HEAD_PERCENT,  # proporcja budżetu na początek 0–100, np. 45
        tail_percent: int = DEFAULT_TAIL_PERCENT,  # proporcja budżetu na koniec 0–100, np. 35
    ) -> SummarizationResult:
        """Opis metody:
        Streść tekst: walidacja pustego, truncacja pod okno modelu (początek / środek / koniec),
        wołanie LLM, złożenie wyniku.

        Przyklad argumentow:
            text="Pismo z Urzędu Skarbowego w sprawie zaległości..."
            head_percent=45, tail_percent=35

        Przyklad wyniku:
            SummarizationResult(summary="• Typ pisma: wezwanie...",
                                metadata=SummarizationMetadata(model="gpt-4o-mini", input_chars=812,
                                                               truncated=False, usage=...,
                                                               sent_chars=812, parts=None))

        Raises:
            EmptyInputError:    wejście puste (sam whitespace).
            ValueError:         niespójne proporcje (spoza 0–100 albo suma > 100) — API waliduje wcześniej.
            LLMError:           dowolny błąd warstwy LLM (auth/limit/timeout/odpowiedź) — propaguje.
        """
        # Pusto po strip = nie ma czego streszczać -> błąd domenowy (endpoint -> 422).
        normalized = text.strip()
        if not normalized:
            raise EmptyInputError("Puste wejście — brak tekstu do streszczenia.")

        # Truncacja pod okno modelu (czysty `TextTruncator`, testowany osobno).
        cut = self._truncator.apply(normalized, head_percent=head_percent, tail_percent=tail_percent)
        if cut.truncated:
            # Nie cicho: log, by było wiadomo, że streszczenie powstało z części dokumentu.
            logger.warning(
                "Tekst %d znaków > limit %d; tnę początek/środek/koniec %d/%d/%d%% -> %d znaków do modelu (streszczenie z części dokumentu).",
                len(normalized), self._truncator.max_chars,
                cut.parts.head.percent, cut.parts.middle.percent, cut.parts.tail.percent, len(cut.text),
            )

        # Jedyne I/O: generacja przez wstrzyknięty klient. Błędy LLM propagują do endpointu.
        result = await self._client.complete(
            user        = _USER_PROMPT.render(text=cut.text),
            system      = _SYSTEM_PROMPT.render(),
            max_tokens  = self._max_output_tokens,
            temperature = 0.0,   # streszczenia stabilne/powtarzalne
        )

        # Metadane: długość ORYGINAŁU (po strip); zakresy przesunięte na tekst, który ma klient.
        metadata = self._build_metadata(len(normalized), cut, self._leading_whitespace_len(text), result)
        return SummarizationResult(summary=result.text.strip(), metadata=metadata)
