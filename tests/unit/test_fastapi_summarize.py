"""Testy jednostkowe routera POST /summarize (krok 2.4.2) — mapowanie wejscia/wyjatkow na HTTP.

Bez sieci i bez LLM: podstawiamy ATRAPE `SummarizationService` przez `dependency_overrides`
(serwis oddaje zadany `SummarizationResult` albo rzuca zadany wyjatek). Dzieki temu testujemy
WYLACZNIE warstwe HTTP routera — ksztalt odpowiedzi i mapowanie wyjatkow domenowych/LLM na
kody (422/500/502/503/504) — bez kosztu i bez wysylania danych na zewnatrz. Realny przeplyw
przez kontener jest w `tests/integration/test_fastapi_summarize.py`.
"""

import pytest
from fastapi.testclient import TestClient

from app.llm import LLMAuthError, LLMRateLimitError, LLMResponseError, LLMTimeoutError, LLMUsage
from app.main import app
from app.routers.summarize import _get_summarization_service
from app.summarization import EmptyInputError, SummarizationMetadata, SummarizationResult


class _StubService:
    """Atrapa `SummarizationService`: oddaje zadany wynik albo rzuca zadany wyjatek (duck-typing)."""

    def __init__(self, *, result: SummarizationResult | None = None, error: Exception | None = None) -> None:
        self._result = result
        self._error = error

    async def summarize(self, *, text: str) -> SummarizationResult:
        if self._error is not None:
            raise self._error
        return self._result


def _result(summary: str = "Streszczenie.") -> SummarizationResult:
    """Pomocniczo: gotowy `SummarizationResult` do atrapy happy-path."""
    return SummarizationResult(
        summary  = summary,
        metadata = SummarizationMetadata(model="fake-echo", input_chars=42, truncated=False, usage=LLMUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15)),
    )


def _client(service: _StubService) -> TestClient:
    """Pomocniczo: `TestClient` z podstawionym serwisem przez `dependency_overrides`."""
    app.dependency_overrides[_get_summarization_service] = lambda: service
    return TestClient(app)


@pytest.fixture(autouse=True)
def _czysc_overrides():
    """Po kazdym tescie czyscimy podstawienia DI — `app` jest wspoldzielony miedzy testami."""
    yield
    app.dependency_overrides.clear()


# --- Happy path ------------------------------------------------------------------


def test_summarize_zwraca_streszczenie_i_metadane():
    """Serwis oddaje wynik -> 200 + kontrakt SummarizeResponse (summary + zagniezdzone metadane)."""
    client = _client(_StubService(result=_result("Urzad wzywa do zaplaty.")))

    resp = client.post("/summarize", json={"text": "Pismo w sprawie podatku"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["summary"] == "Urzad wzywa do zaplaty."
    assert body["metadata"] == {
        "model": "fake-echo",
        "input_chars": 42,
        "truncated": False,
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


# --- Walidacja wejscia -----------------------------------------------------------


def test_brak_pola_text_daje_422():
    """Brak wymaganego pola `text` -> 422 z walidacji pydantic (serwis nie wolany)."""
    client = _client(_StubService(result=_result()))
    resp = client.post("/summarize", json={})
    assert resp.status_code == 422, resp.text


def test_puste_wejscie_daje_422():
    """`EmptyInputError` z domeny (sam whitespace) -> 422."""
    client = _client(_StubService(error=EmptyInputError("puste")))
    resp = client.post("/summarize", json={"text": "   \n  "})
    assert resp.status_code == 422, resp.text


# --- Mapowanie wyjatkow LLM -> kody HTTP -----------------------------------------


def test_timeout_llm_daje_504():
    """`LLMTimeoutError` -> 504 (dostawca nie odpowiedzial w czasie)."""
    client = _client(_StubService(error=LLMTimeoutError("timeout")))
    resp = client.post("/summarize", json={"text": "abc"})
    assert resp.status_code == 504, resp.text


def test_rate_limit_llm_daje_503():
    """`LLMRateLimitError` -> 503 (dostawca dlawi/limit/kwota)."""
    client = _client(_StubService(error=LLMRateLimitError("429")))
    resp = client.post("/summarize", json={"text": "abc"})
    assert resp.status_code == 503, resp.text


def test_auth_llm_daje_500():
    """`LLMAuthError` -> 500 (zly/brakujacy klucz to NASZ config, nie wejscie klienta)."""
    client = _client(_StubService(error=LLMAuthError("401")))
    resp = client.post("/summarize", json={"text": "abc"})
    assert resp.status_code == 500, resp.text


def test_response_blad_llm_daje_502():
    """`LLMResponseError` (oraz bazowy `LLMError`) -> 502 (blad po stronie dostawcy)."""
    client = _client(_StubService(error=LLMResponseError("5xx")))
    resp = client.post("/summarize", json={"text": "abc"})
    assert resp.status_code == 502, resp.text


def test_zla_konfiguracja_dostawcy_daje_500(monkeypatch):
    """Bledna konfiguracja dostawcy (`LLMConfigError` z fabryki) -> 500; serwis w ogole nie powstaje."""
    from app.llm import LLMConfigError
    import app.routers.summarize as summarize_router

    # Fabryka rzuca przy budowie klienta -> DI helper mapuje to na 500 (NIE override serwisu).
    def _boom() -> None:
        raise LLMConfigError("brak LLM_API_KEY")

    monkeypatch.setattr(summarize_router, "get_llm_client", _boom)
    resp = TestClient(app).post("/summarize", json={"text": "abc"})
    assert resp.status_code == 500, resp.text
