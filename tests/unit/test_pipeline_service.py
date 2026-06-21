"""Testy jednostkowe PipelineService (krok 2.5.1) — bez sieci.

Orkiestrator komponuje DWA serwisy, więc testujemy na ATRAPACH obu (nagrywających), sprawdzając:
kolejność wywołań (extract przed summarize), przekazanie PEŁNEGO tekstu z ekstrakcji jako wejścia
summaryzacji, złożenie wyniku (streszczenie + pełny tekst + metadane obu etapów) i propagację
wyjątków obu warstw (mapuje endpoint w 2.5.2). Brak pytest-asyncio -> `asyncio.run` (jak w
pozostałych testach projektu).
"""

import asyncio

from app.extraction import EmptyExtractionError, ExtractionMetadata, ExtractionResult, TikaUnavailableError
from app.llm import LLMUsage
from app.pipeline.service import PipelineService
from app.summarization import EmptyInputError, SummarizationMetadata, SummarizationResult


# --- Atrapy obu serwisow ---------------------------------------------------------


class _RecordingExtraction:
    """Atrapa ekstrakcji: oddaje zadany `ExtractionResult` (lub rzuca) i nagrywa argumenty `extract`."""

    def __init__(self, *, result: ExtractionResult | None = None, error: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self._result = result
        self._error = error

    async def extract(self, *, data, content_type=None, filename=None) -> ExtractionResult:
        self.calls.append({"data": data, "content_type": content_type, "filename": filename})
        if self._error is not None:
            raise self._error
        return self._result


class _RecordingSummarization:
    """Atrapa summaryzacji: oddaje zadany `SummarizationResult` (lub rzuca) i nagrywa argumenty `summarize`."""

    def __init__(self, *, result: SummarizationResult | None = None, error: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self._result = result
        self._error = error

    async def summarize(self, *, text) -> SummarizationResult:
        self.calls.append({"text": text})
        if self._error is not None:
            raise self._error
        return self._result


def _extraction_result(text: str = "Pełna treść pisma do dekretacji.") -> ExtractionResult:
    """Gotowy `ExtractionResult` z realistycznymi metadanymi (do testów orkiestracji)."""
    meta = ExtractionMetadata(
        content_type="application/pdf", language="pl", char_count=len(text), word_count=len(text.split()),
        ocr_used=True, pages_total=3, pages_processed=3, ocr_truncated=False,
    )
    return ExtractionResult(text=text, metadata=meta)


def _summarization_result(summary: str = "Urząd wzywa do zapłaty.") -> SummarizationResult:
    """Gotowy `SummarizationResult` z realistycznymi metadanymi (do testów orkiestracji)."""
    meta = SummarizationMetadata(
        model="rec-model", input_chars=120, truncated=False,
        usage=LLMUsage(prompt_tokens=80, completion_tokens=20, total_tokens=100),
    )
    return SummarizationResult(summary=summary, metadata=meta)


# --- process: kolejnosc i przekazanie tekstu -------------------------------------


def test_process_woła_ekstrakcję_potem_summaryzację_z_jej_tekstem():
    """extract dostaje surowe argumenty, a PEŁNY `extraction.text` ląduje jako wejście summarize."""
    extraction = _RecordingExtraction(result=_extraction_result("Treść z ekstrakcji"))
    summarization = _RecordingSummarization(result=_summarization_result())
    svc = PipelineService(extraction, summarization)

    asyncio.run(svc.process(data=b"%PDF-1.7", content_type="application/pdf", filename="pismo.pdf"))

    # Ekstrakcja dostala surowe wejscie...
    assert extraction.calls[0] == {"data": b"%PDF-1.7", "content_type": "application/pdf", "filename": "pismo.pdf"}
    # ...a summaryzacja dostala dokladnie tekst z ekstrakcji (pelny, niezmieniony).
    assert summarization.calls[0] == {"text": "Treść z ekstrakcji"}


# --- process: zlozenie wyniku ----------------------------------------------------


def test_process_składa_wynik_obu_etapów():
    """`PipelineResult` = streszczenie z summaryzacji + PEŁNY tekst z ekstrakcji + metadane obu etapów."""
    extraction = _RecordingExtraction(result=_extraction_result("Pełna treść pisma"))
    summarization = _RecordingSummarization(result=_summarization_result("Krótkie streszczenie"))
    svc = PipelineService(extraction, summarization)

    result = asyncio.run(svc.process(data=b"x"))

    assert result.summary == "Krótkie streszczenie"
    assert result.text == "Pełna treść pisma"                 # pelny tekst, nie streszczenie
    assert result.extraction.content_type == "application/pdf"
    assert result.extraction.ocr_used is True
    assert result.summarization.model == "rec-model"
    assert result.summarization.usage.total_tokens == 100


# --- process: propagacja wyjatkow obu warstw -------------------------------------


def test_process_propaguje_błąd_ekstrakcji_i_nie_woła_summaryzacji():
    """Błąd etapu ekstrakcji propaguje (endpoint mapuje na HTTP); summaryzacja NIE jest wołana."""
    extraction = _RecordingExtraction(error=TikaUnavailableError("Tika down"))
    summarization = _RecordingSummarization(result=_summarization_result())
    svc = PipelineService(extraction, summarization)

    try:
        asyncio.run(svc.process(data=b"x"))
        assert False, "oczekiwano TikaUnavailableError"
    except TikaUnavailableError:
        pass
    # Skoro ekstrakcja padla, do summaryzacji w ogole nie doszlo.
    assert summarization.calls == []


def test_process_propaguje_empty_extraction():
    """Pusty wynik ekstrakcji (`EmptyExtractionError`) propaguje przez pipeline."""
    extraction = _RecordingExtraction(error=EmptyExtractionError("brak treści"))
    summarization = _RecordingSummarization(result=_summarization_result())
    svc = PipelineService(extraction, summarization)

    try:
        asyncio.run(svc.process(data=b"x"))
        assert False, "oczekiwano EmptyExtractionError"
    except EmptyExtractionError:
        pass


def test_process_propaguje_błąd_summaryzacji():
    """Błąd etapu summaryzacji (np. `EmptyInputError`) propaguje po udanej ekstrakcji."""
    extraction = _RecordingExtraction(result=_extraction_result())
    summarization = _RecordingSummarization(error=EmptyInputError("puste wejście"))
    svc = PipelineService(extraction, summarization)

    try:
        asyncio.run(svc.process(data=b"x"))
        assert False, "oczekiwano EmptyInputError"
    except EmptyInputError:
        pass
    # Ekstrakcja zdazyla sie wykonac (blad jest dalej).
    assert len(extraction.calls) == 1
