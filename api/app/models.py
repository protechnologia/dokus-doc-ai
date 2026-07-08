"""Modele wejscia/wyjscia (Pydantic).

Granica HTTP uslugi: /health (krok 2.1), ekstrakcja (krok 2.3), summaryzacja (krok 2.4)
oraz pelny pipeline (krok 2.5). Modele API sa SWIADOMIE odrebne od modeli domenowych
(`ExtractionResult`/`SummarizationResult`/`PipelineResult`): domena moze ewoluowac (np.
doszla info o OCR-fallbacku w 2.3.5) bez zmiany kontraktu HTTP. Mapowanie domena -> API
robi `*.from_result` (cienkie, jawne).
"""

from __future__ import annotations

from typing   import Literal
from pydantic import BaseModel, Field

from app.extraction import ExtractionResult
from app.llm import LLMUsage
from app.pipeline import PipelineResult
from app.summarization import SummarizationResult

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


# --- Summaryzacja: POST /summarize (krok 2.4.2) ----------------------------------


class SummarizeRequest(BaseModel):
    """Wejscie `POST /summarize` — czysta summaryzacja: sam tekst (bez pliku/ekstrakcji)."""

    text: str = Field(description="Tekst dokumentu do streszczenia.")


class SummarizeMetadata(BaseModel):
    """Metadane w odpowiedzi /summarize — odbicie `SummarizationMetadata` z domeny na granicy HTTP."""

    model: str       = Field(description="Identyfikator modelu, ktory odpowiedzial, np. 'gpt-4o-mini'/'fake-echo'.")
    input_chars: int = Field(description="Dlugosc wejscia (po strip), w znakach — PRZED ewentualna truncacja.")
    truncated: bool  = Field(default=False, description="Czy wejscie ucieto do limitu (streszczenie z czesci dokumentu).")
    usage: LLMUsage  = Field(default_factory=LLMUsage, description="Zuzycie tokenow (prompt/completion/total) — diagnostyka kosztu.")


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
                model="gpt-4o-mini", input_chars=812, truncated=False, usage=LLMUsage(...)))

        Przyklad wyniku:
            SummarizeResponse(summary="...", metadata=SummarizeMetadata(
                model="gpt-4o-mini", input_chars=812, truncated=False, usage=LLMUsage(...)))
        """
        meta = result.metadata
        return cls(
            summary  = result.summary,
            metadata = SummarizeMetadata(
                model       = meta.model,
                input_chars = meta.input_chars,
                truncated   = meta.truncated,
                usage       = meta.usage,
            ),
        )


# --- Pelny pipeline: POST /extract-and-summarize (krok 2.5.2) --------------------


class SummarizeDocumentRequest(BaseModel):
    """Wejscie `POST /extract-and-summarize` — plik w base64 (jak /extract; JSON, nie multipart).

    Identyczny ksztalt jak `ExtractRequest` (osobny model, bo to osobny kontrakt endpointu):
    `filename`/`content_type` to OPCJONALNE podpowiedzi typu dla Tiki; brak -> autodetekcja.
    """

    content_base64: str       = Field(description="Zawartosc pliku zakodowana base64.")
    filename: str | None      = Field(default=None, description="Opcjonalna nazwa pliku (podpowiedz typu dla Tiki), np. 'pismo.pdf'.")
    content_type: str | None  = Field(default=None, description="Opcjonalny MIME (podpowiedz dla Tiki), np. 'application/pdf'; brak = autodetekcja.")


class SummarizeDocumentResponse(BaseModel):
    """Wyjscie `POST /extract-and-summarize` — streszczenie + pelny tekst + metadane OBU etapow.

    Metadane ZAGNIEZDZONE (reuse `ExtractMetadata` + `SummarizeMetadata`), by uniknac kolizji
    nazw (`char_count` ekstrakcji vs `input_chars` summaryzacji) i by kazdy etap byl
    diagnozowalny osobno. `text` = PELNY wyekstrahowany tekst (przed truncacja pod LLM) —
    `summarization.truncated` mowi, czy model widzial tylko poczatek.
    """

    summary: str                     = Field(description="Streszczenie dokumentu (wypunktowanie kluczowych pól).")
    text: str                        = Field(description="Pelny wyekstrahowany tekst (przed truncacja pod LLM).")
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
            summarization = SummarizeMetadata(
                model       = su.model,
                input_chars = su.input_chars,
                truncated   = su.truncated,
                usage       = su.usage,
            ),
        )
