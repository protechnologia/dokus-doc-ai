"""Modele wejscia/wyjscia HTTP (Pydantic) — modul na endpoint, jak w `app/routers/`.

Granica HTTP uslugi: /health (krok 2.1), ekstrakcja (krok 2.3), summaryzacja (krok 2.4),
pelny pipeline (krok 2.5) oraz klasyfikacja (POST /classify — kontrakt zamrozony, README).
Modele API sa SWIADOMIE odrebne od modeli domenowych
(`ExtractionResult`/`SummarizationResult`/`PipelineResult`/`ClassificationResult`): domena moze
ewoluowac (np. doszla info o OCR-fallbacku w 2.3.5) bez zmiany kontraktu HTTP. Mapowanie
domena -> API robi `*.from_result` (cienkie, jawne).

Publiczne API pakietu. Routery i testy importuja stad — np.:
    from app.models import SummarizeRequest, SummarizeResponse
"""

from app.models.classify import ClassifyMetadata, ClassifyOption, ClassifyOutcome, ClassifyRequest, ClassifyResponse, OptionId
from app.models.extract import ExtractMetadata, ExtractRequest, ExtractResponse
from app.models.health import DependencyStatus, HealthResponse
from app.models.pipeline import SummarizeDocumentRequest, SummarizeDocumentResponse
from app.models.summarize import SummarizeMetadata, SummarizePart, SummarizeParts, SummarizeRequest, SummarizeResponse
from app.models.truncation import TruncationParams

__all__ = [
    # GET /health
    "HealthResponse",
    "DependencyStatus",
    # POST /extract
    "ExtractRequest",
    "ExtractResponse",
    "ExtractMetadata",
    # trunkacja wejscia modelu (baza zadan /summarize i /extract-and-summarize)
    "TruncationParams",
    # POST /summarize
    "SummarizeRequest",
    "SummarizeResponse",
    "SummarizeMetadata",
    "SummarizeParts",
    "SummarizePart",
    # POST /extract-and-summarize
    "SummarizeDocumentRequest",
    "SummarizeDocumentResponse",
    # POST /classify
    "ClassifyRequest",
    "ClassifyResponse",
    "ClassifyOption",
    "ClassifyMetadata",
    "ClassifyOutcome",
    "OptionId",
]
