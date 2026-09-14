"""Modele wejscia/wyjscia (Pydantic).

Granica HTTP uslugi: /health (krok 2.1), ekstrakcja (krok 2.3), summaryzacja (krok 2.4)
oraz pelny pipeline (krok 2.5). Modele API sa SWIADOMIE odrebne od modeli domenowych
(`ExtractionResult`/`SummarizationResult`/`PipelineResult`): domena moze ewoluowac (np.
doszla info o OCR-fallbacku w 2.3.5) bez zmiany kontraktu HTTP. Mapowanie domena -> API
robi `*.from_result` (cienkie, jawne).
"""

from __future__ import annotations

from typing   import Literal
from pydantic import BaseModel, Field, model_validator

from app.extraction import ExtractionResult
from app.llm import LLMUsage
from app.pipeline import PipelineResult
from app.summarization import DEFAULT_HEAD_PERCENT, DEFAULT_TAIL_PERCENT, SummarizationMetadata, SummarizationResult, TextParts

# Status pojedynczej zaleznosci zewnetrznej (np. Tiki).
DependencyStatus = Literal["ok", "unreachable"]


class HealthResponse(BaseModel):
    """Odpowiedz /health — zdrowie samej aplikacji plus stan zaleznosci."""

    status: Literal["ok", "degraded"] = Field(
        description=(
            "ok = aplikacja i zaleznosci zdrowe; "
            "degraded = aplikacja zyje, ale jakas zaleznosc jest niedostepna."
        )
    )
    service: str = Field(description="Nazwa uslugi.")
    version: str = Field(description="Wersja aplikacji.")
    dependencies: dict[str, DependencyStatus] = Field(
        default_factory = dict,
        description     = "Stan zaleznosci, np. {'tika': 'ok'}.",
    )


# --- Ekstrakcja: POST /extract (krok 2.3.3) --------------------------------------


class ExtractRequest(BaseModel):
    """Wejscie `POST /extract` — plik w base64 (JSON, nie multipart; patrz CLAUDE.md "Kontrakty").

    `filename`/`content_type` to OPCJONALNE podpowiedzi typu dla Tiki; brak -> Tika sama
    wykrywa typ (jej mocna strona).
    """

    content_base64: str       = Field(description="Zawartosc pliku zakodowana base64.")
    filename: str | None      = Field(default=None, description="Opcjonalna nazwa pliku (podpowiedz typu dla Tiki), np. 'pismo.pdf'.")
    content_type: str | None  = Field(default=None, description="Opcjonalny MIME (podpowiedz dla Tiki), np. 'application/pdf'; brak = autodetekcja.")


class ExtractMetadata(BaseModel):
    """Metadane w odpowiedzi /extract — odbicie `ExtractionMetadata` z domeny na granicy HTTP.

    Pola `ocr_*`/`pages_*` (krok 2.3.5) informuja, czy poszlo OCR i czy z PDF wziely tylko
    pierwsze strony (limit zasobow) — by konsument (DOKUS / osoba dekretujaca) wiedzial, ze
    streszczenie powstalo z czesci dokumentu.
    """

    content_type: str | None    = Field(default=None, description="MIME wykryty przez Tike, np. 'application/pdf'.")
    language: str | None        = Field(default=None, description="Wykryty jezyk wg metadanych Tiki, np. 'pl'; None gdy nieznany.")
    char_count: int             = Field(description="Liczba znakow tekstu po normalizacji.")
    word_count: int             = Field(description="Liczba slow tekstu po normalizacji.")
    ocr_used: bool              = Field(default=False, description="Czy tresc powstala (w calosci lub czesci) przez OCR.")
    pages_total: int | None     = Field(default=None, description="Liczba stron zrodlowego PDF; None dla nie-PDF.")
    pages_processed: int | None = Field(default=None, description="Ile pierwszych stron PDF realnie przetworzono; None dla nie-PDF.")
    ocr_truncated: bool         = Field(default=False, description="Czy PDF ucieto do limitu stron (pominieto dalsze strony).")


class ExtractResponse(BaseModel):
    """Wyjscie `POST /extract` — wyekstrahowany tekst + metadane (nie samo streszczenie).

    Metadane przydaja sie diagnostycznie (jaki MIME wykryto, czy poszlo OCR) i pod pelny
    pipeline w 2.5.
    """

    text: str                 = Field(description="Wyekstrahowany tekst po normalizacji whitespace.")
    metadata: ExtractMetadata = Field(description="Metadane: MIME, jezyk, dlugosc.")

    @classmethod
    def from_result(
        cls,
        result: ExtractionResult,   # domenowy wynik z ExtractionService.extract
    ) -> ExtractResponse:
        """Opis metody:
        Zmapuj domenowy `ExtractionResult` na model odpowiedzi HTTP. Cienkie, jawne
        przepisanie pol — granica miedzy domena a kontraktem API (domena moze sie zmienic
        bez ruszania schematu HTTP).

        Przyklad argumentow:
            result=ExtractionResult(text="Tresc...", metadata=ExtractionMetadata(
                content_type="application/pdf", language="pl", char_count=42, word_count=6))

        Przyklad wyniku:
            ExtractResponse(text="Tresc...", metadata=ExtractMetadata(
                content_type="application/pdf", language="pl", char_count=42, word_count=6))
        """
        meta = result.metadata
        return cls(
            text     = result.text,
            metadata = ExtractMetadata(
                content_type    = meta.content_type,
                language        = meta.language,
                char_count      = meta.char_count,
                word_count      = meta.word_count,
                ocr_used        = meta.ocr_used,
                pages_total     = meta.pages_total,
                pages_processed = meta.pages_processed,
                ocr_truncated   = meta.ocr_truncated,
            ),
        )


# --- Trunkacja wejscia modelu: parametry wspolne /summarize i /extract-and-summarize ---


class TruncationParams(BaseModel):
    """Wspolne, OPCJONALNE parametry trunkacji wejscia modelu (poczatek / srodek / koniec).

    Dziedzicza je `SummarizeRequest` i `SummarizeDocumentRequest` — jedna definicja i jeden
    walidator sumy dla obu endpointow. Brak klucza = wartosc domyslna; `null`, wartosc spoza
    0–100 albo suma powyzej 100 -> 422 z walidacji pydantic (loguje `log_validation_error`).
    Dzialaja tylko, gdy tekst przekracza `LLM_MAX_INPUT_CHARS` — krotszy idzie do modelu w calosci.
    """

    head_percent: int = Field(default=DEFAULT_HEAD_PERCENT, ge=0, le=100, description="Proporcja budzetu LLM_MAX_INPUT_CHARS na POCZATEK dokumentu (0–100). Srodek dostaje reszte: 100 - head_percent - tail_percent.")
    tail_percent: int = Field(default=DEFAULT_TAIL_PERCENT, ge=0, le=100, description="Proporcja budzetu LLM_MAX_INPUT_CHARS na KONIEC dokumentu (0–100). 0 wylacza dana czesc.")

    @model_validator(mode="after")
    def _suma_proporcji_max_100(self) -> TruncationParams:
        """Opis metody:
        Sprawdz, ze poczatek + koniec nie przekraczaja 100 (srodek nie moze byc ujemny). Zakres
        pojedynczej wartosci pilnuja `ge`/`le` na polach.

        Przyklad argumentow:
            self=TruncationParams(head_percent=60, tail_percent=50)

        Przyklad wyniku:
            ValueError -> 422 (dla 45/55 zwraca self bez zmian)

        Raises:
            ValueError: suma `head_percent + tail_percent` powyzej 100.
        """
        # Suma > 100 -> brak miejsca na srodek; pydantic zamienia ValueError na 422.
        if self.head_percent + self.tail_percent > 100:
            raise ValueError(f"head_percent + tail_percent nie moze przekraczac 100 (jest {self.head_percent + self.tail_percent}).")
        return self


# --- Summaryzacja: POST /summarize (krok 2.4.2) ----------------------------------


class SummarizeRequest(TruncationParams):
    """Wejscie `POST /summarize` — czysta summaryzacja: sam tekst (bez pliku/ekstrakcji) + opcjonalne proporcje trunkacji."""

    text: str = Field(description="Tekst dokumentu do streszczenia.")


class SummarizePart(BaseModel):
    """Jedna czesc wejscia modelu po trunkacji — odbicie `TextPart` z domeny na granicy HTTP."""

    percent: int       = Field(description="Zastosowana proporcja budzetu tej czesci (0–100).")
    start: int | None  = Field(default=None, description="Offset poczatku zachowanego fragmentu (wlacznie), w znakach Unicode wzgledem tekstu klienta; null gdy proporcja 0.")
    end: int | None    = Field(default=None, description="Offset konca zachowanego fragmentu (wylacznie), w znakach Unicode wzgledem tekstu klienta; null gdy proporcja 0.")


class SummarizeParts(BaseModel):
    """Trzy czesci wejscia modelu (poczatek / srodek / koniec) — odbicie `TextParts` z domeny."""

    head: SummarizePart    = Field(description="Poczatek dokumentu.")
    middle: SummarizePart  = Field(description="Ciagly fragment z geometrycznego srodka dokumentu.")
    tail: SummarizePart    = Field(description="Koniec dokumentu.")

    @classmethod
    def from_parts(
        cls,
        parts: TextParts,   # domenowe zakresy czesci z `SummarizationMetadata.parts`
    ) -> SummarizeParts:
        """Opis metody:
        Zmapuj domenowe `TextParts` na model HTTP. Cienkie, jawne przepisanie pol.

        Przyklad argumentow:
            parts=TextParts(head=TextPart(percent=45, start=0, end=40210), middle=..., tail=...)

        Przyklad wyniku:
            SummarizeParts(head=SummarizePart(percent=45, start=0, end=40210), middle=..., tail=...)
        """
        return cls(
            head   = SummarizePart(percent=parts.head.percent, start=parts.head.start, end=parts.head.end),
            middle = SummarizePart(percent=parts.middle.percent, start=parts.middle.start, end=parts.middle.end),
            tail   = SummarizePart(percent=parts.tail.percent, start=parts.tail.start, end=parts.tail.end),
        )


class SummarizeMetadata(BaseModel):
    """Metadane w odpowiedzi /summarize — odbicie `SummarizationMetadata` z domeny na granicy HTTP.

    `sent_chars`/`parts` (trunkacja poczatek/srodek/koniec) DOSZLY do kontraktu wstecznie zgodnie:
    stare pola bez zmian, nowe tylko dochodza. `parts` = null dokladnie wtedy, gdy `truncated` = false.
    """

    model: str                    = Field(description="Identyfikator modelu, ktory odpowiedzial, np. 'gpt-4o-mini'/'fake-echo'.")
    input_chars: int              = Field(description="Dlugosc wejscia (po strip), w znakach — PRZED ewentualna truncacja.")
    truncated: bool               = Field(default=False, description="Czy wejscie ucieto do limitu (streszczenie z czesci dokumentu).")
    usage: LLMUsage               = Field(default_factory=LLMUsage, description="Zuzycie tokenow (prompt/completion/total) — diagnostyka kosztu.")
    sent_chars: int               = Field(description="Dlugosc tekstu wyslanego do modelu (po truncacji, ze znacznikami pominiecia), w znakach; zawsze <= LLM_MAX_INPUT_CHARS.")
    parts: SummarizeParts | None  = Field(default=None, description="Proporcje i zakresy zachowanych fragmentow (poczatek/srodek/koniec); null, gdy tekst zmiescil sie w budzecie.")

    @classmethod
    def from_metadata(
        cls,
        meta: SummarizationMetadata,   # domenowe metadane z `SummarizationResult.metadata`
    ) -> SummarizeMetadata:
        """Opis metody:
        Zmapuj domenowe `SummarizationMetadata` na model HTTP. Wspolne dla /summarize i
        /extract-and-summarize (oba zwracaja te same metadane summaryzacji).

        Przyklad argumentow:
            meta=SummarizationMetadata(model="gpt-4o-mini", input_chars=812, truncated=False,
                                       usage=LLMUsage(...), sent_chars=812, parts=None)

        Przyklad wyniku:
            SummarizeMetadata(model="gpt-4o-mini", input_chars=812, truncated=False,
                              usage=LLMUsage(...), sent_chars=812, parts=None)
        """
        return cls(
            model       = meta.model,
            input_chars = meta.input_chars,
            truncated   = meta.truncated,
            usage       = meta.usage,
            sent_chars  = meta.sent_chars,
            parts       = SummarizeParts.from_parts(meta.parts) if meta.parts is not None else None,
        )


class SummarizeResponse(BaseModel):
    """Wyjscie `POST /summarize` — streszczenie (wypunktowanie kluczowych pól) + metadane."""

    summary: str               = Field(description="Streszczenie dokumentu (jeden tekst).")
    metadata: SummarizeMetadata = Field(description="Metadane: model, dlugosc wejscia, truncacja, zuzycie.")

    @classmethod
    def from_result(
        cls,
        result: SummarizationResult,   # domenowy wynik z SummarizationService.summarize
    ) -> SummarizeResponse:
        """Opis metody:
        Zmapuj domenowy `SummarizationResult` na model odpowiedzi HTTP. Cienkie, jawne
        przepisanie pol — granica miedzy domena a kontraktem API.

        Przyklad argumentow:
            result=SummarizationResult(summary="...", metadata=SummarizationMetadata(
                model="gpt-4o-mini", input_chars=812, truncated=False, usage=LLMUsage(...),
                sent_chars=812, parts=None))

        Przyklad wyniku:
            SummarizeResponse(summary="...", metadata=SummarizeMetadata(
                model="gpt-4o-mini", input_chars=812, truncated=False, usage=LLMUsage(...),
                sent_chars=812, parts=None))
        """
        return cls(
            summary  = result.summary,
            metadata = SummarizeMetadata.from_metadata(result.metadata),
        )


# --- Pelny pipeline: POST /extract-and-summarize (krok 2.5.2) --------------------


class SummarizeDocumentRequest(TruncationParams):
    """Wejscie `POST /extract-and-summarize` — plik w base64 (jak /extract; JSON, nie multipart).

    Ksztalt jak `ExtractRequest` (osobny model, bo to osobny kontrakt endpointu) plus opcjonalne
    proporcje trunkacji z `TruncationParams`: `filename`/`content_type` to OPCJONALNE podpowiedzi
    typu dla Tiki; brak -> autodetekcja. Proporcje dotycza TYLKO wejscia modelu — `text` w
    odpowiedzi zostaje pelny.
    """

    content_base64: str       = Field(description="Zawartosc pliku zakodowana base64.")
    filename: str | None      = Field(default=None, description="Opcjonalna nazwa pliku (podpowiedz typu dla Tiki), np. 'pismo.pdf'.")
    content_type: str | None  = Field(default=None, description="Opcjonalny MIME (podpowiedz dla Tiki), np. 'application/pdf'; brak = autodetekcja.")


class SummarizeDocumentResponse(BaseModel):
    """Wyjscie `POST /extract-and-summarize` — streszczenie + pelny tekst + metadane OBU etapow.

    Metadane ZAGNIEZDZONE (reuse `ExtractMetadata` + `SummarizeMetadata`), by uniknac kolizji
    nazw (`char_count` ekstrakcji vs `input_chars` summaryzacji) i by kazdy etap byl
    diagnozowalny osobno. `text` = PELNY wyekstrahowany tekst (przed truncacja pod LLM; DOKUS
    zapisuje go do wyszukiwarki pelnotekstowej) — `summarization.truncated`/`parts` mowia, czy
    i ktore fragmenty `text` widzial model.
    """

    summary: str                     = Field(description="Streszczenie dokumentu (wypunktowanie kluczowych pól).")
    text: str                        = Field(description="Pelny wyekstrahowany tekst (przed truncacja pod LLM); offsety `summarization.parts` wskazuja pozycje w nim.")
    extraction: ExtractMetadata      = Field(description="Metadane etapu ekstrakcji (MIME, jezyk, dlugosc, OCR, strony).")
    summarization: SummarizeMetadata = Field(description="Metadane etapu summaryzacji (model, dlugosc wejscia, truncacja, zuzycie).")

    @classmethod
    def from_result(
        cls,
        result: PipelineResult,   # domenowy wynik z PipelineService.process
    ) -> SummarizeDocumentResponse:
        """Opis metody:
        Zmapuj domenowy `PipelineResult` na model odpowiedzi HTTP. Cienkie, jawne przepisanie
        pol obu etapow na zagniezdzone metadane API — granica miedzy domena a kontraktem HTTP.

        Przyklad argumentow:
            result=PipelineResult(summary="Urzad wzywa...", text="Pelna tresc...",
                extraction=ExtractionMetadata(content_type="application/pdf", ...),
                summarization=SummarizationMetadata(model="gpt-4o-mini", ...))

        Przyklad wyniku:
            SummarizeDocumentResponse(summary="Urzad wzywa...", text="Pelna tresc...",
                extraction=ExtractMetadata(content_type="application/pdf", ...),
                summarization=SummarizeMetadata(model="gpt-4o-mini", ...))
        """
        ex = result.extraction
        su = result.summarization
        return cls(
            summary    = result.summary,
            text       = result.text,
            extraction = ExtractMetadata(
                content_type    = ex.content_type,
                language        = ex.language,
                char_count      = ex.char_count,
                word_count      = ex.word_count,
                ocr_used        = ex.ocr_used,
                pages_total     = ex.pages_total,
                pages_processed = ex.pages_processed,
                ocr_truncated   = ex.ocr_truncated,
            ),
            summarization = SummarizeMetadata.from_metadata(su),
        )
