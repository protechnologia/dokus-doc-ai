"""Modele HTTP: POST /extract-and-summarize (krok 2.5.2) — plik -> streszczenie + pelny tekst + metadane obu etapow."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.extract import ExtractMetadata
from app.models.summarize import SummarizeMetadata
from app.models.truncation import TruncationParams
from app.pipeline import PipelineResult


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
