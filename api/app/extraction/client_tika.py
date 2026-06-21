"""Klient transportowy do tika-server (krok 2.3.1).

To JEDYNE miejsce, ktore rozmawia po HTTP z Tika (analogia do `OpenAILLMClient`,
ktory izoluje SDK dostawcy). Czysty TRANSPORT: `bytes (+ podpowiedz typu) -> surowy
tekst + surowe metadane`. ZADNYCH decyzji o tresci (normalizacja, detekcja smieciowej
warstwy PUA, OCR-fallback, liczenie metadanych) — to nalezy do `ExtractionService`
(krok 2.3.2). Tu tylko naglowki, wywolanie, parsowanie odpowiedzi i mapowanie bledow.

Swiadomie KONKRETNA klasa, nie abstrakcyjny interfejs (patrz CLAUDE.md "Architektura
warstwy ekstrakcji"): Tika zostaje, nie ma mapy drogowej podmiany silnika. Interfejs
(ABC) + fabryke formalizujemy dopiero, gdyby pojawil sie drugi silnik (np. odlozony
OCRmyPDF) albo potrzeba uruchamiania API bez Dockera.

Realizacja przez `PUT /rmeta/text`: jedno wywolanie zwraca JSON (lista zasobow); tresc
dokumentu-kontenera siedzi pod kluczem `X-TIKA:content`, reszta pol to metadane (m.in.
wykryty `Content-Type`). Dzieki temu nie wolamy osobno `/tika` i `/meta`.

Struktura (jak w `OpenAILLMClient`): czyste fragmenty bez I/O (`_build_headers`,
`_pick_text`, `_pick_metadata`) oraz klasyfikator bledow (`_map_http_error`) sa
wydzielone i testowalne jednostkowo bez sieci; w `extract` zostaje samo I/O + try/except.
"""

from __future__ import annotations

from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

# Klucz, pod ktorym Tika w odpowiedzi /rmeta/text zwraca wyekstrahowana tresc.
_CONTENT_KEY = "X-TIKA:content"

# Dozwolone wartosci strategii OCR dla PDF (per-request naglowek X-Tika-PDFOcrStrategy).
# Zamkniety zbior = enum Tiki OCR_STRATEGY (AUTO/NO_OCR/OCR_ONLY/OCR_AND_TEXT_EXTRACTION),
# w formie malymi literami jak w naglowku. Domena uzywa "ocr_only"; reszta dla kompletnosci
# kontraktu transportu (a `Literal` zamiast `str` lapie literowki i dokumentuje opcje).
OcrStrategy = Literal["auto", "no_ocr", "ocr_only", "ocr_and_text_extraction"]


# --- Surowy wynik transportu -----------------------------------------------------


class TikaRawResult(BaseModel):
    """Surowy wynik z Tiki: tekst + metadane, BEZ decyzji domenowych (krok 2.3.1)."""

    text: str                = Field(description="Wyekstrahowany surowy tekst (jeszcze bez normalizacji).")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Surowe metadane Tiki, np. {'Content-Type': 'application/pdf'}.")


# --- Wyjatki domenowe transportu (niezalezne od httpx) ---------------------------
# Logika mapuje je na kody HTTP dopiero w endpointcie (krok 2.3.3) — tu klasyfikujemy.


class TikaError(Exception):
    """Bazowy blad warstwy transportu Tiki."""


class TikaUnavailableError(TikaError):
    """tika-server nieosiagalny: brak polaczenia albo przekroczony timeout."""


class TikaExtractionError(TikaError):
    """tika-server odpowiedzial bledem HTTP na konkretny plik (nieobslugiwany/uszkodzony)."""


# --- Klient transportowy ---------------------------------------------------------


class TikaClient:
    """Klient HTTP do tika-server — transport ekstrakcji.

    Do czego:
        Zamienia bajty pliku na surowy tekst + surowe metadane, rozmawiajac z
        tika-server po REST. Izoluje cala "rozmowe z Tika" (naglowki, timeouty, bledy
        HTTP) od logiki domenowej, ktora widzi wylacznie `TikaRawResult` / `TikaError`.

    Flow jednego `extract(...)`:
        1. `_build_headers` — Accept JSON + opcjonalne podpowiedzi typu/nazwy pliku,
        2. `PUT /rmeta/text` — wlasciwe wywolanie Tiki (jedyne I/O),
        3. blad transportu/HTTP -> `_map_http_error` -> `TikaUnavailableError`/`TikaExtractionError`,
        4. sukces -> `_pick_text` + `_pick_metadata` -> `TikaRawResult`.

    Konfiguracja (base_url / timeout) wstrzykiwana z ENV przez wywolujacego; sam klient
    niczego nie czyta z konfiguracji globalnej.
    """

    def __init__(
        self,
        *,
        base_url: str,            # adres tika-server, np. "http://tika:9998"
        timeout: float = 120.0,   # limit czasu wywolania [s]; OCR bywa wolny -> hojnie
    ) -> None:
        """Opis metody:
        Zbuduj klienta (sama konfiguracja, bez polaczenia do Tiki).

        Przyklad argumentow:
            base_url="http://tika:9998"
            timeout=120.0

        Przyklad wyniku:
            gotowy, skonfigurowany TikaClient
        """
        # rstrip("/"), by bezpiecznie doklejac sciezki (".../rmeta/text") bez podwojnego "/".
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    # --- Czyste helpery (bez I/O) — testowalne jednostkowo bez sieci ----------------

    @staticmethod
    def _build_headers(
        content_type: str | None,          # MIME pliku jako podpowiedz, np. "application/pdf"; None = autodetekcja Tiki
        filename: str | None,              # nazwa pliku jako dodatkowa podpowiedz, np. "pismo.pdf"; None = pomijamy
        ocr_strategy: OcrStrategy | None,   # wymuszenie strategii OCR dla PDF, np. "ocr_only"; None = domyslna z tika-config (auto)
    ) -> dict[str, str]:
        """Opis metody:
        Zloz naglowki zadania do /rmeta/text. Czysta funkcja — zero I/O.

        `ocr_strategy` mapuje sie na per-request naglowek `X-Tika-PDFOcrStrategy`
        (mechanizm "Configuring Parsers At Parse Time" — prefiks `X-Tika-PDF` + nazwa
        parametru). To NADPISUJE globalny `ocrStrategy=auto` z tika-config.xml tylko dla
        tego jednego zadania; konfig pliku zostaje nietkniety. Uzywane przez domene do
        wymuszenia `ocr_only`, gdy warstwa tekstowa PDF jest smieciowa (PUA, krok 2.3.5).

        Przyklad argumentow:
            content_type="application/pdf"
            filename="pismo.pdf"
            ocr_strategy="ocr_only"

        Przyklad wyniku:
            {"Accept": "application/json",
             "Content-Type": "application/pdf",
             "Content-Disposition": 'attachment; filename="pismo.pdf"',
             "X-Tika-PDFOcrStrategy": "ocr_only"}
        """
        # /rmeta/text zwraca JSON (tresc + metadane w jednym wywolaniu).
        headers = {"Accept": "application/json"}
        # Content-Type = podpowiedz typu; brak -> Tika sama wykryje typ (jej mocna strona).
        if content_type:
            headers["Content-Type"] = content_type
        # Nazwa pliku bywa dodatkowa podpowiedzia przy wykrywaniu typu (po rozszerzeniu).
        if filename:
            headers["Content-Disposition"] = f'attachment; filename="{filename}"'
        # Per-request override strategii OCR dla PDF (None = nie ruszamy, dziala globalny auto).
        if ocr_strategy:
            headers["X-Tika-PDFOcrStrategy"] = ocr_strategy
        return headers

    @staticmethod
    def _pick_text(
        payload: list[dict[str, Any]],   # odpowiedz /rmeta/text: lista zasobow (kontener + ew. embedded)
    ) -> str:
        """Opis metody:
        Wyjmij tekst dokumentu-kontenera z odpowiedzi /rmeta/text. Czysta funkcja.

        Przyklad argumentow:
            payload=[{"X-TIKA:content": "Tresc...", "Content-Type": "application/pdf"}]

        Przyklad wyniku:
            "Tresc..."
        """
        # rmeta zwykle daje >=1 element; pusta lista (brak zasobow) -> brak tresci.
        if not payload:
            return ""
        # Element [0] to dokument-kontener; tresc zasobow zagniezdzonych (children)
        # swiadomie pomijamy — rekurencyjna obsluga embedded to pozniejszy krok.
        # Brak klucza / None (np. zasob bez tekstu) normalizujemy do "".
        return payload[0].get(_CONTENT_KEY) or ""

    @staticmethod
    def _pick_metadata(
        payload: list[dict[str, Any]],   # odpowiedz /rmeta/text (jak w _pick_text)
    ) -> dict[str, Any]:
        """Opis metody:
        Wyjmij metadane dokumentu-kontenera (bez samej tresci). Czysta funkcja.

        Przyklad argumentow:
            payload=[{"X-TIKA:content": "...", "Content-Type": "application/pdf"}]

        Przyklad wyniku:
            {"Content-Type": "application/pdf"}
        """
        if not payload:
            return {}
        # Wszystkie pola kontenera POZA tresc — te zwraca _pick_text osobno.
        return {k: v for k, v in payload[0].items() if k != _CONTENT_KEY}

    # --- Klasyfikator bledow (bez I/O; uzywa typow httpx, nie wymaga sieci) ---------

    @staticmethod
    def _map_http_error(
        exc: httpx.HTTPError,   # wyjatek httpx zlapany w extract (status albo transport)
    ) -> TikaError:
        """Opis metody:
        Zaklasyfikuj wyjatek httpx na domenowy `TikaError`. ZWRACA (nie rzuca) wyjatek —
        wolajacy robi `raise ... from exc`, by zachowac przyczyne. Testowalny bez sieci:
        wystarczy zbudowac instancje wyjatku httpx.

        Przyklad argumentow:
            exc=httpx.ConnectError("Connection refused")

        Przyklad wyniku:
            TikaUnavailableError("...")   # a httpx.HTTPStatusError(422) -> TikaExtractionError
        """
        # Tika ODPOWIEDZIALA kodem bledu na konkretny plik (4xx/5xx) — np. format nieobslugiwany.
        if isinstance(exc, httpx.HTTPStatusError):
            return TikaExtractionError(f"Tika zwrocila HTTP {exc.response.status_code}: {exc.response.text[:200]}")
        # Reszta httpx.HTTPError (brak polaczenia, timeout, blad sieci) = problem dotarcia do Tiki.
        return TikaUnavailableError(str(exc))

    # --- Wywolanie (I/O) — orkiestracja --------------------------------------------

    async def extract(
        self,
        *,
        data: bytes,                      # surowe bajty pliku, np. zawartosc PDF/DOCX/PNG
        content_type: str | None = None,          # MIME jako podpowiedz; None = autodetekcja Tiki
        filename: str | None = None,              # nazwa pliku jako podpowiedz typu; None = pomijamy
        ocr_strategy: OcrStrategy | None = None,   # per-request override OCR dla PDF, np. "ocr_only"; None = globalny auto
    ) -> TikaRawResult:
        """Opis metody:
        Wyslij plik do tika-server i zwroc surowy tekst + metadane.
        Buduje naglowki -> wola /rmeta/text -> parsuje odpowiedz na `TikaRawResult`.

        `ocr_strategy` (gdy podane) nadpisuje strategie OCR PDF tylko dla tego zadania
        (np. domena wymusza "ocr_only" przy smieciowej warstwie PUA — krok 2.3.5).

        Przyklad argumentow:
            data=b"%PDF-1.7 ..."
            content_type="application/pdf"

        Przyklad wyniku:
            TikaRawResult(text="Tresc dokumentu...", metadata={"Content-Type": "application/pdf"})

        Raises:
            TikaUnavailableError: tika-server nieosiagalny (brak polaczenia / timeout).
            TikaExtractionError:  Tika odpowiedziala bledem HTTP na plik (nieobslugiwany/uszkodzony).
        """
        # Czysta budowa naglowkow (testowana osobno).
        headers = self._build_headers(content_type, filename, ocr_strategy)
        url = self._base_url + "/rmeta/text"

        # --- Wywolanie Tiki; bledy httpx -> TikaError przez klasyfikator ------------
        # Klienta tworzymy per-wywolanie (jak /health) — prosto, bez zarzadzania cyklem
        # zycia w lifespan; pooling polaczen to ewentualny pozniejszy refinement.
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.put(url, content=data, headers=headers)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise self._map_http_error(exc) from exc

        # Czyste parsowanie odpowiedzi na TikaRawResult (testowane osobno).
        payload = resp.json()
        return TikaRawResult(text=self._pick_text(payload), metadata=self._pick_metadata(payload))
