"""Domena ekstrakcji — logika nad surowym wynikiem Tiki (krok 2.3.2 + jakość 2.3.5).

Warstwa DOMENOWA: niezalezna od tego, ze pod spodem stoi akurat Tika (analogia do
podzialu w LLM: transport `OpenAILLMClient` vs. logika). Transport (`TikaClient`)
oddaje surowy tekst + surowe metadane; tutaj robimy z tego wynik nadajacy sie do
streszczenia.

Ten serwis jest CIENKIM ORKIESTRATOREM — wlasne ma tylko: normalizacje whitespace,
liczenie/wyciaganie metadanych (MIME, jezyk, dlugosc, ocr_used) i sklejenie przeplywu.
Dwie wyspecjalizowane odpowiedzialnosci sa wydzielone do osobnych jednostek (kazda
testowalna w izolacji):
  - `PdfPageLimiter` (`app.extraction.pdf`)     — liczenie/ciecie stron PDF; izoluje `pypdf`,
  - `PuaDetector`    (`app.extraction.quality`) — detekcja smieciowej warstwy tekstowej (PUA).

Struktura (jak w `TikaClient`/`OpenAILLMClient`): czyste fragmenty bez I/O (normalizacja,
liczenie znakow/slow, wyciagniecie MIME/jezyka/OCR) sa w osobnych, krotkich metodach;
w `extract` zostaje samo I/O transportu + delegacja do limitera/detektora + zlozenie wyniku.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from pydantic import BaseModel, Field

from app.extraction.client_tika import TikaClient
from app.extraction.pdf import PdfPageLimiter
from app.extraction.quality import PuaDetector

logger = logging.getLogger(__name__)

# Klucze metadanych Tiki z jezykiem ZADEKLAROWANYM w dokumencie (czytany z wlasciwosci pliku
# przy parsowaniu) — NIE z detekcji tresci. Zweryfikowane (2026-06-21): `/rmeta/text` jezyka
# NIE wykrywa; robi to dopiero osobny endpoint `/language/stream` (odlozone, patrz CLAUDE.md
# "jezyk = refinement"). Probujemy generyczny/starszy `language`, potem Dublin Core
# `dc:language` (ten realnie widziany w testach, np. ustawiony w Wordzie). Brak obu -> None.
_LANGUAGE_KEYS = ("language", "dc:language")

# Wewnatrz linii zwijamy ciagi spacji/tabow/twardej spacji (U+00A0) do jednej spacji.
_INLINE_WS = re.compile(r"[ \t ]+")

# Metadana Tiki z liczba stron PDF realnie poddanych OCR. Wiarygodny sygnal "czy poszlo OCR"
# (zweryfikowane 2026-06-21: `X-TIKA:Parsed-By` dla PDF NIE pokazuje TesseractOCRParser, a
# `pdf:ocrPageCount` owszem: 0 bez OCR, >0 po OCR — tez przy wymuszonym `ocr_only`).
_OCR_PAGE_COUNT_KEY = "pdf:ocrPageCount"


# --- Wynik domenowy ekstrakcji ---------------------------------------------------


class ExtractionMetadata(BaseModel):
    """Metadane wyekstrahowanego dokumentu — diagnostyka i wejscie pod pipeline (2.3.3+).

    Pola `ocr_*`/`pages_*` dochodza w warstwie jakosci (2.3.5): mowia, czy poszlo OCR i
    czy z PDF wziely tylko pierwsze strony (limit zasobow) — by osoba dekretujaca wiedziala,
    ze streszczenie powstalo z czesci dokumentu (zasada "limit nie moze byc cichy").
    """

    content_type: str | None = Field(default=None, description="MIME wykryty przez Tike, np. 'application/pdf' (bez parametrow typu charset).")
    language: str | None     = Field(default=None, description="Wykryty jezyk wg metadanych Tiki, np. 'pl'; None gdy nieznany.")
    char_count: int          = Field(description="Liczba znakow tekstu po normalizacji.")
    word_count: int          = Field(description="Liczba slow tekstu po normalizacji (podzial po bialych znakach).")
    ocr_used: bool           = Field(default=False, description="Czy tresc powstala (w calosci lub czesci) przez OCR — wg `pdf:ocrPageCount`>0.")
    pages_total: int | None  = Field(default=None, description="Liczba stron zrodlowego PDF; None dla nie-PDF lub gdy nie udalo sie odczytac.")
    pages_processed: int | None = Field(default=None, description="Ile pierwszych stron PDF realnie wyslano do Tiki (=pages_total, gdy bez ciecia); None dla nie-PDF.")
    ocr_truncated: bool      = Field(default=False, description="Czy PDF ucieto do limitu `MAX_OCR_PAGES` (pominieto strony pages_processed..pages_total).")


class ExtractionResult(BaseModel):
    """Domenowy wynik ekstrakcji: znormalizowany tekst + policzone metadane."""

    text: str                  = Field(description="Wyekstrahowany tekst po normalizacji whitespace.")
    metadata: ExtractionMetadata = Field(description="Metadane: MIME, jezyk, dlugosc.")


# --- Wyjatki domenowe ekstrakcji (niezalezne od transportu/Tiki) -----------------
# Mapowanie na kody HTTP robi endpoint (krok 2.3.3). Bledy TRANSPORTU (Tika nieosiagalna,
# plik nieobslugiwany) to osobna hierarchia `TikaError` — propaguja przez te warstwe.


class ExtractionError(Exception):
    """Bazowy blad domeny ekstrakcji (logika nad surowym wynikiem Tiki)."""


class EmptyExtractionError(ExtractionError):
    """Po normalizacji nie zostal zaden tekst (plik bez tresci tekstowej / pusty skan)."""


# --- Serwis domenowy -------------------------------------------------------------


class ExtractionService:
    """Domena ekstrakcji nad surowym wynikiem transportu — cienki orkiestrator.

    Do czego:
        Zamienia surowy `TikaRawResult` (tekst + metadane) na domenowy `ExtractionResult`.
        Sklada przeplyw, delegujac wyspecjalizowane decyzje: limit/ciecie stron PDF do
        `PdfPageLimiter`, detekcje smieciowej warstwy (PUA) do `PuaDetector`. Nie wie i nie
        ma wiedziec, ze transportem jest akurat Tika ani ze strony tnie akurat `pypdf`.

    Flow jednego `extract(...)` (strategia (B), krok 2.3.5):
        1. `PdfPageLimiter.apply` — dla PDF ew. utnij do `max_ocr_pages` stron PRZED Tika,
        2. transport (`TikaClient.extract`, `auto`) -> surowy tekst + metadane (I/O),
        3. `PuaDetector.is_garbage` -> jesli warstwa PDF smieciowa, wymus `ocr_only` (drugie I/O),
        4. normalizacja + `_build_metadata` -> `ExtractionResult`.

    Bledy transportu (`TikaUnavailableError`/`TikaExtractionError`) NIE sa tu lapane —
    propaguja do endpointu, ktory mapuje je na HTTP (502/422) w 2.3.3.
    """

    def __init__(
        self,
        client: TikaClient,        # transport do Tiki; w testach atrapa z async `extract`
        max_ocr_pages: int = 30,   # limit stron PDF wysylanych do Tiki (straznik OCR, z `Settings`)
    ) -> None:
        """Opis metody:
        Zbuduj serwis nad wstrzyknietym transportem; limiter stron i detektor PUA tworzymy
        wewnatrz z konfiguracji (to mechanika domeny, nie wymienialne silniki — w odroznieniu
        od transportu wstrzykiwanego). Sama konfiguracja, bez I/O.

        Przyklad argumentow:
            client=TikaClient(base_url="http://tika:9998")
            max_ocr_pages=30

        Przyklad wyniku:
            gotowy ExtractionService
        """
        self._client = client
        self._pager = PdfPageLimiter(max_pages=max_ocr_pages)
        self._pua = PuaDetector()

    # --- Czyste helpery (bez I/O) — testowalne jednostkowo bez transportu -----------

    @staticmethod
    def _normalize_whitespace(
        text: str,   # surowy tekst z Tiki, czesto z nadmiarem pustych linii i spacji
    ) -> str:
        """Opis metody:
        Uporzadkuj biale znaki: zwin spacje/taby w linii, przytnij konce linii, zredukuj
        ciagi pustych linii do pojedynczej. Czysta funkcja — zero I/O. Akapity (pojedyncza
        pusta linia) zostaja, bo niosa strukture istotna dla streszczenia.

        Przyklad argumentow:
            text="  Tytul \\n\\n\\n  Tresc\\tdalej  \\n\\n"

        Przyklad wyniku:
            "Tytul\\n\\nTresc dalej"
        """
        # Per linia: zwin ciagi spacji/tabow/U+00A0 do jednej spacji i przytnij konce.
        lines = [_INLINE_WS.sub(" ", line).strip() for line in text.splitlines()]

        # Zredukuj ciagi pustych linii do jednej (i pomin wiodace puste).
        out: list[str] = []
        for line in lines:
            # Pusta linia tylko gdy poprzednia nie byla pusta — inaczej pomijamy (zwijanie).
            if not line and (not out or not out[-1]):
                continue
            out.append(line)

        # Koncowy strip zdejmuje ewentualna pojedyncza pusta linie na koncu.
        return "\n".join(out).strip()

    @staticmethod
    def _count_chars(
        text: str,   # tekst PO normalizacji
    ) -> int:
        """Opis metody:
        Policz znaki tekstu. Trywialne, ale osobno — by metadane liczyc i testowac
        punktowo (a nie tylko przez caly `_build_metadata`). Czysta funkcja.

        Przyklad argumentow:
            text="Tresc pisma"

        Przyklad wyniku:
            11
        """
        return len(text)

    @staticmethod
    def _count_words(
        text: str,   # tekst PO normalizacji
    ) -> int:
        """Opis metody:
        Policz slowa = niepuste tokeny po podziale na bialych znakach. Czysta funkcja.

        Przyklad argumentow:
            text="Tresc pisma do dekretacji"

        Przyklad wyniku:
            4
        """
        # split() bez argumentu dzieli po dowolnych bialych znakach i pomija puste tokeny.
        return len(text.split())

    # --- Helpery metadanych (czytanie surowego wyjscia Tiki) — bez I/O ---------------

    @staticmethod
    def _as_text(
        value: Any,   # wartosc metadanej Tiki: zwykle str, czasem lista (pole wielowartosciowe)
    ) -> str | None:
        """Opis metody:
        Sprowadz wartosc metadanej do pojedynczego stringa. Tika zwraca wartosci jako
        string, ale pola wielowartosciowe jako liste — bierzemy pierwszy niepusty element.
        Czysta funkcja.

        Przyklad argumentow:
            value=["pl", "en"]

        Przyklad wyniku:
            "pl"
        """
        # Lista -> pierwszy niepusty element jako tekst.
        if isinstance(value, list):
            for item in value:
                if item:
                    return str(item)
            return None
        # Pojedyncza wartosc -> tekst, pusta/None -> None.
        return str(value) if value else None

    @classmethod
    def _pick_content_type(
        cls,
        raw_metadata: dict[str, Any],   # surowe metadane Tiki (zrodlo MIME)
    ) -> str | None:
        """Opis metody:
        Wyciagnij MIME z metadanych Tiki, bez parametrow typu (np. "; charset=UTF-8") —
        do diagnostyki liczy sie sam typ, charset to szum. Brak -> None. Czysta funkcja.

        Przyklad argumentow:
            raw_metadata={"Content-Type": "text/plain; charset=UTF-8"}

        Przyklad wyniku:
            "text/plain"
        """
        content_type = cls._as_text(raw_metadata.get("Content-Type"))
        if not content_type:
            return None
        # Odetnij parametry typu po sredniku ("; charset=...") i przytnij.
        return content_type.split(";")[0].strip()

    @classmethod
    def _pick_language(
        cls,
        raw_metadata: dict[str, Any],   # surowe metadane Tiki (zrodlo jezyka)
    ) -> str | None:
        """Opis metody:
        Wyciagnij ZADEKLAROWANY jezyk z metadanych Tiki (defensywnie, best-effort). Bierze
        pierwszy znany klucz z wartoscia; brak -> None (detekcja z tresci to osobny krok —
        `/language/stream` — odlozony jako refinement). Czysta funkcja.

        Przyklad argumentow:
            raw_metadata={"Content-Type": "application/pdf", "dc:language": "pl"}

        Przyklad wyniku:
            "pl"
        """
        for key in _LANGUAGE_KEYS:
            language = cls._as_text(raw_metadata.get(key))
            if language:
                return language
        return None

    @staticmethod
    def _pick_ocr_used(
        raw_metadata: dict[str, Any],   # surowe metadane Tiki (zrodlo pdf:ocrPageCount)
    ) -> bool:
        """Opis metody:
        Czy poszlo OCR — wg `pdf:ocrPageCount`>0 (jedyny wiarygodny sygnal dla PDF, patrz
        stala `_OCR_PAGE_COUNT_KEY`). Wartosc bywa str lub int; brak/nieparsowalne -> False.
        Czysta funkcja.

        Przyklad argumentow:
            raw_metadata={"pdf:ocrPageCount": "1"}

        Przyklad wyniku:
            True
        """
        value = raw_metadata.get(_OCR_PAGE_COUNT_KEY)
        try:
            # Tika zwraca to pole jako string ("1") albo liczbe; oba sprowadzamy do int.
            return int(value) > 0
        except (TypeError, ValueError):
            return False

    @classmethod
    def _build_metadata(
        cls,
        text: str,                       # tekst PO normalizacji (na nim liczymy dlugosc)
        raw_metadata: dict[str, Any],    # surowe metadane Tiki (zrodlo MIME, jezyka, ocrPageCount)
        pages_total: int | None,         # liczba stron zrodlowego PDF (None dla nie-PDF)
        pages_processed: int | None,     # ile pierwszych stron PDF wyslano (None dla nie-PDF)
        ocr_truncated: bool,             # czy ucieto PDF do limitu stron
    ) -> ExtractionMetadata:
        """Opis metody:
        Zloz metadane wyniku — cienkie zlozenie czystych helperow (MIME, jezyk, znaki,
        slowa, ocr_used) i przekazanych pol stron/ciecia. Czysta funkcja.

        Przyklad argumentow:
            text="Tresc pisma"
            raw_metadata={"Content-Type": "text/plain; charset=UTF-8", "language": "pl"}
            pages_total=None, pages_processed=None, ocr_truncated=False

        Przyklad wyniku:
            ExtractionMetadata(content_type="text/plain", language="pl", char_count=11,
                               word_count=2, ocr_used=False, pages_total=None, ...)
        """
        return ExtractionMetadata(
            content_type=cls._pick_content_type(raw_metadata),
            language=cls._pick_language(raw_metadata),
            char_count=cls._count_chars(text),
            word_count=cls._count_words(text),
            ocr_used=cls._pick_ocr_used(raw_metadata),
            pages_total=pages_total,
            pages_processed=pages_processed,
            ocr_truncated=ocr_truncated,
        )

    # --- Wywolanie (I/O przez transport) — orkiestracja ----------------------------

    async def extract(
        self,
        *,
        data: bytes,                      # surowe bajty pliku, np. zawartosc PDF/DOCX/PNG
        content_type: str | None = None,  # MIME jako podpowiedz dla Tiki; None = autodetekcja
        filename: str | None = None,      # nazwa pliku jako podpowiedz typu; None = pomijamy
    ) -> ExtractionResult:
        """Opis metody:
        Wyekstrahuj dokument (strategia (B), krok 2.3.5):
          1. straznik zasobow (`PdfPageLimiter`) — dla PDF utnij do `max_ocr_pages` stron PRZED Tika,
          2. ekstrakcja `auto` (globalny tika-config: tekstowe natywnie, skany OCR-owane),
          3. jakosc (`PuaDetector`) — jesli warstwa PDF jest smieciowa (PUA), wymus `ocr_only`
             na tym samym (juz uciętym) pliku,
          4. normalizacja + metadane (w tym ocr_used / pages_* / ocr_truncated).

        Przyklad argumentow:
            data=b"%PDF-1.7 ..."
            content_type="application/pdf"

        Przyklad wyniku:
            ExtractionResult(text="Tresc dokumentu...",
                             metadata=ExtractionMetadata(content_type="application/pdf",
                                                         language="pl", char_count=42, word_count=6,
                                                         ocr_used=True, pages_total=100,
                                                         pages_processed=30, ocr_truncated=True))

        Raises:
            EmptyExtractionError:  po normalizacji nie zostal zaden tekst.
            TikaUnavailableError:  transport — tika-server nieosiagalny (propaguje z `TikaClient`).
            TikaExtractionError:   transport — Tika odrzucila plik (propaguje z `TikaClient`).
        """
        # 1) Straznik zasobow: dla PDF ew. utnij do limitu stron (pypdf, bez sieci).
        limit = self._pager.apply(data)
        is_pdf = limit.pages_total is not None  # parsowalny PDF (limiter policzyl strony)

        # 2) Ekstrakcja `auto` (globalny tika-config). Jedyne I/O; bledy Tiki propaguja.
        raw = await self._client.extract(data=limit.data, content_type=content_type, filename=filename)
        text = self._normalize_whitespace(raw.text)

        # 3) Jakosc: smieciowa warstwa tekstowa PDF (PUA) — `auto` jej NIE zOCR-uje (warstwa
        #    "jest"), wiec wymuszamy OCR_ONLY na tym samym, juz uciętym pliku. Drugie I/O.
        if is_pdf and self._pua.is_garbage(text):
            logger.info(
                "Wykryto smieciowa warstwe tekstowa PDF (PUA %.0f%% >= prog %.0f%%); wymuszam OCR_ONLY.",
                100 * self._pua.ratio(text), 100 * self._pua.threshold,
            )
            raw = await self._client.extract(
                data=limit.data, content_type=content_type, filename=filename, ocr_strategy="ocr_only"
            )
            text = self._normalize_whitespace(raw.text)

        # Pusto po normalizacji = brak tresci (pusty PDF / nieczytelny skan) -> 422 w endpointcie.
        # Przy `auto` skany sa juz zOCR-owane wczesniej, wiec pusto = naprawde brak tresci.
        if not text:
            raise EmptyExtractionError("Po normalizacji nie zostal zaden tekst (plik bez tresci tekstowej).")

        # 4) Metadane na tekscie znormalizowanym + sygnaly stron/OCR z ostatniej ekstrakcji.
        metadata = self._build_metadata(
            text, raw.metadata, limit.pages_total, limit.pages_processed, limit.truncated
        )
        return ExtractionResult(text=text, metadata=metadata)
