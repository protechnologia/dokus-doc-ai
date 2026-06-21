"""Domena ekstrakcji — logika nad surowym wynikiem Tiki (krok 2.3.2, happy path).

Warstwa DOMENOWA: niezalezna od tego, ze pod spodem stoi akurat Tika (analogia do
podzialu w LLM: transport `OpenAILLMClient` vs. logika). Transport (`TikaClient`)
oddaje surowy tekst + surowe metadane; tutaj robimy z tego wynik nadajacy sie do
streszczenia: normalizujemy whitespace i liczymy metadane (MIME, jezyk, dlugosc).

Zakres 2.3.2 jest SWIADOMIE WASKI (happy path). Tu jest TYLKO:
  - normalizacja whitespace,
  - metadane: MIME (z metadanych Tiki), dlugosc (znaki/slowa), jezyk (defensywnie z
    metadanych Tiki, brak -> None; porzadna detekcja to pozniejszy refinement),
  - pusty wynik po normalizacji -> `EmptyExtractionError`.

SWIADOMIE POZA ZAKRESEM (wchodzi w 2.3.5 / patrz CLAUDE.md "Architektura warstwy
ekstrakcji" i "2.3.5"): detekcja smieciowej warstwy tekstowej (PUA), OCR-fallback
(`X-Tika-PDFOcrStrategy`), limit zakresu OCR (`MAX_OCR_PAGES`), rekurencyjna obsluga
zasobow zagniezdzonych (embedded). Nowe wyjatki domenowe z 2.3.5 dolaczaja pod
`ExtractionError`.

Struktura (jak w `TikaClient`/`OpenAILLMClient`): czyste fragmenty bez I/O (normalizacja,
liczenie znakow/slow, wyciagniecie MIME/jezyka) sa wydzielone do osobnych, krotkich metod
— kazda testowalna jednostkowo bez sieci/transportu; w `extract` zostaje samo wywolanie
transportu + zlozenie wyniku.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from app.extraction.client_tika import TikaClient

# Klucze metadanych Tiki z jezykiem ZADEKLAROWANYM w dokumencie (czytany z wlasciwosci pliku
# przy parsowaniu) — NIE z detekcji tresci. Zweryfikowane (2026-06-21): `/rmeta/text` jezyka
# NIE wykrywa; robi to dopiero osobny endpoint `/language/stream` (odlozone, patrz CLAUDE.md
# "jezyk = refinement"). Probujemy generyczny/starszy `language`, potem Dublin Core
# `dc:language` (ten realnie widziany w testach, np. ustawiony w Wordzie). Brak obu -> None.
_LANGUAGE_KEYS = ("language", "dc:language")

# Wewnatrz linii zwijamy ciagi spacji/tabow/twardej spacji (U+00A0) do jednej spacji.
_INLINE_WS = re.compile(r"[ \t ]+")


# --- Wynik domenowy ekstrakcji ---------------------------------------------------


class ExtractionMetadata(BaseModel):
    """Metadane wyekstrahowanego dokumentu — diagnostyka i wejscie pod pipeline (2.3.3+)."""

    content_type: str | None = Field(default=None, description="MIME wykryty przez Tike, np. 'application/pdf' (bez parametrow typu charset).")
    language: str | None     = Field(default=None, description="Wykryty jezyk wg metadanych Tiki, np. 'pl'; None gdy nieznany.")
    char_count: int          = Field(description="Liczba znakow tekstu po normalizacji.")
    word_count: int          = Field(description="Liczba slow tekstu po normalizacji (podzial po bialych znakach).")


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
    """Domena ekstrakcji nad surowym wynikiem transportu.

    Do czego:
        Zamienia surowy `TikaRawResult` (tekst + metadane) na domenowy
        `ExtractionResult`: czysci whitespace i liczy metadane (MIME, jezyk, dlugosc).
        Nie wie i nie ma wiedziec, ze transportem jest akurat Tika — dostaje go
        wstrzyknietego, rozmawia tylko przez `TikaClient.extract`.

    Flow jednego `extract(...)`:
        1. transport (`TikaClient.extract`) -> surowy tekst + surowe metadane (jedyne I/O),
        2. `_normalize_whitespace` -> czysty tekst,
        3. pusty po normalizacji -> `EmptyExtractionError`,
        4. `_build_metadata` -> MIME/jezyk/dlugosc -> `ExtractionResult`.

    Bledy transportu (`TikaUnavailableError`/`TikaExtractionError`) NIE sa tu lapane —
    propaguja do endpointu, ktory mapuje je na HTTP (502/422) w 2.3.3.
    """

    def __init__(
        self,
        client: TikaClient,   # transport do Tiki; w testach atrapa z async `extract`
    ) -> None:
        """Opis metody:
        Zbuduj serwis nad wstrzyknietym transportem (sama konfiguracja, bez I/O).

        Przyklad argumentow:
            client=TikaClient(base_url="http://tika:9998")

        Przyklad wyniku:
            gotowy ExtractionService
        """
        self._client = client

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

    @classmethod
    def _build_metadata(
        cls,
        text: str,                  # tekst PO normalizacji (na nim liczymy dlugosc)
        raw_metadata: dict[str, Any],   # surowe metadane Tiki (zrodlo MIME i jezyka)
    ) -> ExtractionMetadata:
        """Opis metody:
        Zloz metadane wyniku — cienkie zlozenie czterech czystych helperow (MIME, jezyk,
        znaki, slowa), kazdy testowany osobno. Czysta funkcja.

        Przyklad argumentow:
            text="Tresc pisma"
            raw_metadata={"Content-Type": "text/plain; charset=UTF-8", "language": "pl"}

        Przyklad wyniku:
            ExtractionMetadata(content_type="text/plain", language="pl", char_count=11, word_count=2)
        """
        return ExtractionMetadata(
            content_type=cls._pick_content_type(raw_metadata),
            language=cls._pick_language(raw_metadata),
            char_count=cls._count_chars(text),
            word_count=cls._count_words(text),
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
        Wyekstrahuj dokument: wolaj transport, znormalizuj tekst, policz metadane.

        Przyklad argumentow:
            data=b"%PDF-1.7 ..."
            content_type="application/pdf"

        Przyklad wyniku:
            ExtractionResult(text="Tresc dokumentu...",
                             metadata=ExtractionMetadata(content_type="application/pdf",
                                                         language="pl", char_count=42, word_count=6))

        Raises:
            EmptyExtractionError:  po normalizacji nie zostal zaden tekst.
            TikaUnavailableError:  transport — tika-server nieosiagalny (propaguje z `TikaClient`).
            TikaExtractionError:   transport — Tika odrzucila plik (propaguje z `TikaClient`).
        """
        # Jedyne I/O: transport oddaje surowy tekst + metadane (bledy Tiki propaguja).
        raw = await self._client.extract(data=data, content_type=content_type, filename=filename)

        # Czysta normalizacja (testowana osobno).
        text = self._normalize_whitespace(raw.text)

        # Pusto po normalizacji = brak tresci do streszczenia -> blad domenowy (endpoint -> 422).
        # Uwaga (2.3.5): smieciowa warstwa PUA daje tekst NIEpusty (smiec) — tego warunek
        # nie wykryje; detekcja PUA + OCR-fallback dochodzi osobno.
        if not text:
            raise EmptyExtractionError("Po normalizacji nie zostal zaden tekst (plik bez tresci tekstowej).")

        # Metadane liczymy na tekscie znormalizowanym (testowane osobno).
        metadata = self._build_metadata(text, raw.metadata)
        return ExtractionResult(text=text, metadata=metadata)
