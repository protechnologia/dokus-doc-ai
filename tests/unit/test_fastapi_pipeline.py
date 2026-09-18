"""Testy jednostkowe routera POST /extract-and-summarize (krok 2.5.2) — mapowanie wejscia/wyjatkow na HTTP.

Bez sieci, bez Tiki i bez LLM: podstawiamy ATRAPE `PipelineService` przez `dependency_overrides`
(oddaje zadany `PipelineResult` albo rzuca zadany wyjatek dowolnej z dwoch warstw). Testujemy
WYLACZNIE warstwe HTTP routera: dekodowanie base64, walidacje rozmiaru, ksztalt odpowiedzi i
UNIE mapowan wyjatkow obu warstw (ekstrakcja + summaryzacja) na kody. Realny przeplyw przez
kontener jest w `tests/integration/test_fastapi_pipeline.py`. Osobno (sekcja DI na koncu): czy
funkcja DI przekazuje ustawienia z `Settings` do konstruktora serwisu streszczen.
"""

import base64

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.extraction import EmptyExtractionError, ExtractionMetadata, TikaExtractionError, TikaUnavailableError
from app.llm import FakeLLMClient, LLMAuthError, LLMRateLimitError, LLMResponseError, LLMTimeoutError, LLMUsage
from app.main import app
from app.pipeline import PipelineResult
from app.routers.pipeline import _get_pipeline_service
from app.summarization import EmptyInputError, SummarizationMetadata, TextPart, TextParts


class _StubService:
    """Atrapa `PipelineService`: oddaje zadany wynik albo rzuca zadany wyjatek; nagrywa proporcje (duck-typing)."""

    def __init__(self, *, result: PipelineResult | None = None, error: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self._result = result
        self._error = error

    async def process(self, *, data, content_type=None, filename=None, head_percent, tail_percent) -> PipelineResult:
        self.calls.append({"head_percent": head_percent, "tail_percent": tail_percent})
        if self._error is not None:
            raise self._error
        return self._result


def _result(summary: str = "Streszczenie.", text: str = "Pelna tresc.") -> PipelineResult:
    """Pomocniczo: gotowy `PipelineResult` (metadane obu etapow) do atrapy happy-path."""
    return PipelineResult(
        summary       = summary,
        text          = text,
        extraction    = ExtractionMetadata(content_type="application/pdf", language="pl", char_count=len(text), word_count=2, ocr_used=True, pages_total=3, pages_processed=3, ocr_truncated=False),
        summarization = SummarizationMetadata(model="fake-echo", input_chars=len(text), truncated=False, usage=LLMUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15), sent_chars=len(text)),
    )


def _b64(text: str = "dowolna tresc") -> str:
    """Pomocniczo: poprawny base64 (zawartosc nieistotna — serwis jest atrapa)."""
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _client(service: _StubService, *, max_upload_bytes: int = 20 * 1024 * 1024) -> TestClient:
    """Pomocniczo: `TestClient` z podstawiona atrapa serwisu i (opcjonalnie) malym limitem rozmiaru."""
    app.dependency_overrides[_get_pipeline_service] = lambda: service
    app.dependency_overrides[get_settings] = lambda: Settings(max_upload_bytes=max_upload_bytes)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _czysc_overrides():
    """Po kazdym tescie czyscimy podstawienia DI — `app` jest wspoldzielony miedzy testami."""
    yield
    app.dependency_overrides.clear()


# --- Happy path ------------------------------------------------------------------


def test_zwraca_streszczenie_tekst_i_metadane_obu_etapow():
    """Serwis oddaje wynik -> 200 + kontrakt: summary + pelny text + zagniezdzone metadane obu etapow."""
    client = _client(_StubService(result=_result("Urzad wzywa.", "Pelna tresc pisma.")))

    resp = client.post("/extract-and-summarize", json={"content_base64": _b64(), "content_type": "application/pdf"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["summary"] == "Urzad wzywa."
    assert body["text"] == "Pelna tresc pisma."          # pelny tekst, nie streszczenie
    assert body["extraction"]["content_type"] == "application/pdf"
    assert body["extraction"]["ocr_used"] is True
    assert body["summarization"]["model"] == "fake-echo"
    assert body["summarization"]["usage"]["total_tokens"] == 15
    assert body["summarization"]["sent_chars"] == len("Pelna tresc pisma.")
    assert body["summarization"]["parts"] is None                 # tekst w budzecie -> bez ciecia


def test_trunkacja_text_pelny_i_parts_w_summarization():
    """Wejscie modelu ciete -> `text` nadal pelny, a `summarization.parts` niesie zakresy wzgledem `text`."""
    full_text = "Pelna tresc pisma. " * 10
    parts = TextParts(head=TextPart(percent=45, start=0, end=20), middle=TextPart(percent=20, start=90, end=100), tail=TextPart(percent=35, start=175, end=190))
    result = _result("Urzad wzywa.", full_text)
    result.summarization = SummarizationMetadata(model="fake-echo", input_chars=len(full_text), truncated=True, usage=LLMUsage(), sent_chars=102, parts=parts)
    client = _client(_StubService(result=result))

    resp = client.post("/extract-and-summarize", json={"content_base64": _b64()})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["text"] == full_text                               # pelny tekst pod wyszukiwarke
    assert body["summarization"]["truncated"] is True
    assert body["summarization"]["parts"]["middle"] == {"percent": 20, "start": 90, "end": 100}


# --- Proporcje trunkacji: przekazanie do serwisu ---------------------------------


def test_bez_proporcji_przekazuje_domyslne_45_35():
    """Brak `head_percent`/`tail_percent` w zadaniu -> pipeline dostaje domyslne 45/35."""
    service = _StubService(result=_result())
    _client(service).post("/extract-and-summarize", json={"content_base64": _b64()})
    assert service.calls[0] == {"head_percent": 45, "tail_percent": 35}


def test_wlasne_proporcje_trafiaja_do_serwisu():
    """Podane proporcje (45/55) trafiaja do pipeline'u bez zmian."""
    service = _StubService(result=_result())
    resp = _client(service).post("/extract-and-summarize", json={"content_base64": _b64(), "head_percent": 45, "tail_percent": 55})
    assert resp.status_code == 200, resp.text
    assert service.calls[0] == {"head_percent": 45, "tail_percent": 55}


def test_suma_proporcji_ponad_100_daje_422():
    """`head_percent + tail_percent > 100` -> 422 z walidacji pydantic; serwis nie jest wolany."""
    service = _StubService(result=_result())
    resp = _client(service).post("/extract-and-summarize", json={"content_base64": _b64(), "head_percent": 80, "tail_percent": 30})
    assert resp.status_code == 422, resp.text
    assert service.calls == []


# --- Walidacja wejscia (wspolna z /extract) --------------------------------------


def test_brak_pola_content_base64_daje_422():
    """Brak wymaganego pola `content_base64` -> 422 z walidacji pydantic (serwis nie wolany)."""
    client = _client(_StubService(result=_result()))
    resp = client.post("/extract-and-summarize", json={})
    assert resp.status_code == 422, resp.text


def test_zly_base64_daje_422():
    """Nie-base64 -> 422 (walidacja wejscia w routerze, bez kontaktu z serwisem)."""
    client = _client(_StubService(result=_result()))
    resp = client.post("/extract-and-summarize", json={"content_base64": "to nie jest base64!!!"})
    assert resp.status_code == 422, resp.text


def test_pusty_plik_daje_422():
    """Pusty base64 (zero bajtow po dekodowaniu) -> 422."""
    client = _client(_StubService(result=_result()))
    resp = client.post("/extract-and-summarize", json={"content_base64": ""})
    assert resp.status_code == 422, resp.text


def test_za_duzy_plik_daje_413():
    """Plik powyzej MAX_UPLOAD_BYTES -> 413 (straznik rozmiaru przed serwisem)."""
    client = _client(_StubService(result=_result()), max_upload_bytes=10)
    resp = client.post("/extract-and-summarize", json={"content_base64": base64.b64encode(b"x" * 50).decode("ascii")})
    assert resp.status_code == 413, resp.text


# --- Mapowanie wyjatkow warstwy EKSTRAKCJI -> kody HTTP ---------------------------


def test_tika_niedostepna_daje_502():
    """`TikaUnavailableError` -> 502 (tika-server nieosiagalny)."""
    client = _client(_StubService(error=TikaUnavailableError("down")))
    resp = client.post("/extract-and-summarize", json={"content_base64": _b64()})
    assert resp.status_code == 502, resp.text


def test_tika_odrzucila_plik_daje_422():
    """`TikaExtractionError` (plik nieobslugiwany/uszkodzony) -> 422."""
    client = _client(_StubService(error=TikaExtractionError("bad file")))
    resp = client.post("/extract-and-summarize", json={"content_base64": _b64()})
    assert resp.status_code == 422, resp.text


def test_pusta_ekstrakcja_daje_422():
    """`EmptyExtractionError` (po normalizacji brak tresci) -> 422."""
    client = _client(_StubService(error=EmptyExtractionError("brak tresci")))
    resp = client.post("/extract-and-summarize", json={"content_base64": _b64()})
    assert resp.status_code == 422, resp.text


# --- Mapowanie wyjatkow warstwy SUMMARYZACJI -> kody HTTP -------------------------


def test_puste_wejscie_llm_daje_422():
    """`EmptyInputError` (po ekstrakcji brak tekstu do streszczenia) -> 422."""
    client = _client(_StubService(error=EmptyInputError("puste")))
    resp = client.post("/extract-and-summarize", json={"content_base64": _b64()})
    assert resp.status_code == 422, resp.text


def test_timeout_llm_daje_504():
    """`LLMTimeoutError` -> 504 (dostawca nie odpowiedzial w czasie)."""
    client = _client(_StubService(error=LLMTimeoutError("timeout")))
    resp = client.post("/extract-and-summarize", json={"content_base64": _b64()})
    assert resp.status_code == 504, resp.text


def test_rate_limit_llm_daje_503():
    """`LLMRateLimitError` -> 503 (dostawca dlawi/limit/kwota)."""
    client = _client(_StubService(error=LLMRateLimitError("429")))
    resp = client.post("/extract-and-summarize", json={"content_base64": _b64()})
    assert resp.status_code == 503, resp.text


def test_auth_llm_daje_500():
    """`LLMAuthError` -> 500 (zly/brakujacy klucz to NASZ config, nie wejscie klienta)."""
    client = _client(_StubService(error=LLMAuthError("401")))
    resp = client.post("/extract-and-summarize", json={"content_base64": _b64()})
    assert resp.status_code == 500, resp.text


def test_response_blad_llm_daje_502():
    """`LLMResponseError` (oraz bazowy `LLMError`) -> 502 (blad po stronie dostawcy)."""
    client = _client(_StubService(error=LLMResponseError("5xx")))
    resp = client.post("/extract-and-summarize", json={"content_base64": _b64()})
    assert resp.status_code == 502, resp.text


# --- DI: ustawienia docieraja do serwisu -------------------------------------------
# Testy wyzej podstawiaja caly serwis, wiec nie widza, CO funkcja DI przekazuje do konstruktorow.
# Na razie tylko czesc streszczen; ustawienia ekstrakcji (TIKA_URL, TIKA_TIMEOUT_SECONDS,
# MAX_OCR_PAGES) bez takiego testu — otwarta czesc TODO pkt 6.


def test_di_przekazuje_limity_llm_z_settings(monkeypatch):
    """`LLM_MAX_INPUT_CHARS` i `LLM_MAX_OUTPUT_TOKENS_SUMMARY` z `Settings` trafiaja do serwisu streszczen w pipeline."""
    calls: list[dict] = []
    monkeypatch.setattr("app.routers.pipeline.get_llm_client", FakeLLMClient)
    monkeypatch.setattr("app.routers.pipeline.SummarizationService", lambda client, **kwargs: calls.append(kwargs))

    # Wartosci rozne od defaultow — test nie przejdzie przypadkiem na wartosciach domyslnych.
    _get_pipeline_service(settings=Settings(_env_file=None, llm_max_input_chars=12_345, llm_max_output_tokens_summary=321))

    assert calls == [{"max_input_chars": 12_345, "max_output_tokens": 321}]
