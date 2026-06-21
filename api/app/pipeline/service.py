"""Domena pełnego pipeline'u — plik → tekst → streszczenie (krok 2.5.1).

Warstwa ORKIESTRUJĄCA: spina dwie gotowe domeny (`ExtractionService` z 2.3 i
`SummarizationService` z 2.4) w JEDNO wywołanie. **Nie wprowadza nowej logiki domenowej
ani własnego I/O** — całe I/O (Tika, LLM) siedzi w serwisach składowych; tutaj jest tylko
sekwencja (extract -> summarize) + złożenie wyniku obu etapów. Symetria do
`ExtractionService`, który komponuje `PdfPageLimiter` + `PuaDetector`.

Oba serwisy dostaje WSTRZYKNIĘTE (router buduje je nad odpowiednim transportem: Tika inline,
LLM z fabryki). Pipeline nie wie i nie ma wiedzieć, że pod spodem stoi akurat Tika czy OpenAI.

Wyjątki OBU warstw (`ExtractionError`/`TikaError` z ekstrakcji, `SummarizationError`/`LLMError`
z summaryzacji) NIE są tu łapane — propagują do endpointu, który mapuje je na HTTP (unia kodów
z `/extract` i `/summarize`, krok 2.5.2).
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from app.extraction import ExtractionMetadata, ExtractionResult, ExtractionService
from app.summarization import SummarizationMetadata, SummarizationResult, SummarizationService

logger = logging.getLogger(__name__)


# --- Wynik domenowy pipeline'u ---------------------------------------------------


class PipelineResult(BaseModel):
    """Domenowy wynik pełnego przebiegu: streszczenie + pełny tekst + metadane obu etapów.

    `text` to PEŁNY wyekstrahowany tekst (przed truncacją pod okno modelu) — gdy był dłuższy
    niż limit LLM, model widział tylko początek, o czym mówi `summarization.truncated`.
    Metadane obu etapów ZAGNIEŻDŻONE, by uniknąć kolizji nazw (`char_count` ekstrakcji vs
    `input_chars` summaryzacji) i by każdy etap był diagnozowalny osobno.
    """

    summary: str                        = Field(description="Streszczenie dokumentu (hybryda: akapit + punkty).")
    text: str                           = Field(description="Pełny wyekstrahowany tekst (przed truncacją pod LLM).")
    extraction: ExtractionMetadata      = Field(description="Metadane etapu ekstrakcji (MIME, język, długość, OCR, strony).")
    summarization: SummarizationMetadata = Field(description="Metadane etapu summaryzacji (model, długość wejścia, truncacja, zużycie).")


# --- Serwis orkiestrujący --------------------------------------------------------


class PipelineService:
    """Orkiestrator pełnego pipeline'u nad wstrzykniętymi `ExtractionService` + `SummarizationService`.

    Do czego:
        Z surowych bajtów pliku robi `PipelineResult` (streszczenie + pełny tekst + metadane
        obu etapów). Sam nie rozmawia z Tiką ani z LLM — tylko składa przepływ: ekstrakcja,
        przekazanie wyekstrahowanego tekstu do summaryzacji, złożenie wyniku. Cienki — całe
        I/O i decyzje domenowe są w serwisach składowych.

    Flow jednego `process(...)`:
        1. `ExtractionService.extract` -> `ExtractionResult` (plik -> tekst + metadane; I/O Tika),
        2. weź `extraction.text` (pełny) i podaj jako wejście summaryzacji,
        3. `SummarizationService.summarize` -> `SummarizationResult` (tekst -> streszczenie; I/O LLM),
        4. `_build_result` -> `PipelineResult` (streszczenie + pełny tekst + metadane obu etapów).

    Wyjątki obu warstw propagują (endpoint mapuje na HTTP w 2.5.2).
    """

    def __init__(
        self,
        extraction: ExtractionService,        # domena ekstrakcji (TikaClient inline) — wstrzyknięta
        summarization: SummarizationService,  # domena summaryzacji (LLMClient z fabryki) — wstrzyknięta
    ) -> None:
        """Opis metody:
        Zbuduj orkiestrator nad oboma serwisami (sama kompozycja, bez I/O).

        Przyklad argumentow:
            extraction=ExtractionService(TikaClient(...))
            summarization=SummarizationService(get_llm_client())

        Przyklad wyniku:
            gotowy PipelineService
        """
        self._extraction    = extraction
        self._summarization = summarization

    # --- Czysty helper (bez I/O) — testowalny jednostkowo --------------------------

    @staticmethod
    def _build_result(
        extraction: ExtractionResult,        # wynik etapu ekstrakcji (źródło pełnego tekstu + metadanych)
        summarization: SummarizationResult,  # wynik etapu summaryzacji (źródło streszczenia + metadanych)
    ) -> PipelineResult:
        """Opis metody:
        Złóż wynik pipeline'u z wyników obu etapów. `text` = PEŁNY tekst z ekstrakcji (nie ten
        ewentualnie przycięty przed LLM). Czysta funkcja.

        Przyklad argumentow:
            extraction=ExtractionResult(text="Treść pisma...", metadata=ExtractionMetadata(...))
            summarization=SummarizationResult(summary="Urząd wzywa...", metadata=SummarizationMetadata(...))

        Przyklad wyniku:
            PipelineResult(summary="Urząd wzywa...", text="Treść pisma...",
                           extraction=ExtractionMetadata(...), summarization=SummarizationMetadata(...))
        """
        return PipelineResult(
            summary       = summarization.summary,
            text          = extraction.text,
            extraction    = extraction.metadata,
            summarization = summarization.metadata,
        )

    # --- Wywolanie (I/O w serwisach skladowych) — orkiestracja ----------------------

    async def process(
        self,
        *,
        data: bytes,                      # surowe bajty pliku (PDF/DOCX/PNG/...)
        content_type: str | None = None,  # MIME jako podpowiedź dla Tiki; None = autodetekcja
        filename: str | None = None,      # nazwa pliku jako podpowiedź typu; None = pomijamy
    ) -> PipelineResult:
        """Opis metody:
        Przepuść plik przez cały pipeline: ekstrakcja -> streszczenie -> złożenie wyniku.

        Przyklad argumentow:
            data=b"%PDF-1.7 ..."
            content_type="application/pdf"
            filename="pismo.pdf"

        Przyklad wyniku:
            PipelineResult(summary="Urząd Skarbowy wzywa do zapłaty...", text="Pełna treść pisma...",
                           extraction=ExtractionMetadata(content_type="application/pdf", ...),
                           summarization=SummarizationMetadata(model="gpt-4o-mini", ...))

        Raises:
            ExtractionError / TikaError:        błędy etapu ekstrakcji (propagują z `ExtractionService`).
            SummarizationError / LLMError:      błędy etapu summaryzacji (propagują z `SummarizationService`).
        """
        # 1) Ekstrakcja: plik -> tekst + metadane (I/O Tika; błędy propagują).
        extraction = await self._extraction.extract(
            data=data, content_type=content_type, filename=filename
        )

        # 2-3) Summaryzacja PEŁNEGO wyekstrahowanego tekstu (truncację pod okno modelu robi
        #      już `SummarizationService`; I/O LLM; błędy propagują).
        logger.info(
            "Pipeline: ekstrakcja %d znaków (typ=%s) -> summaryzacja.",
            extraction.metadata.char_count, extraction.metadata.content_type,
        )
        summarization = await self._summarization.summarize(text=extraction.text)

        # 4) Złożenie wyniku obu etapów (czysty helper).
        return self._build_result(extraction, summarization)
