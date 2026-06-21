"""Test integracyjny endpointu POST /summarize (krok 2.4.2) — przez działającą usługę FastAPI.

Marker: integration_fastapi. Uderza w kontener (fixture `fastapi_client`); gdy usługa
niedostępna -> SKIP. To test WIRINGU (router -> DI -> serwis -> serializacja). Asercje są
PROVIDER-AGNOSTYCZNE (sam kontrakt), więc test przechodzi niezależnie od `LLM_PROVIDER`
kontenera: z `fake` jest darmowy i deterministyczny (zalecane do CI), z `openai` robi realne,
minimalne wywołanie. Realny LLM + jakość promptu pokrywa osobno `integration_llm`
(`test_summarization_service.py`), a dokładne mapowanie błędów — jednostkowy
`tests/unit/test_fastapi_summarize.py`.
"""

import pytest

# Parasol `integration` + węższy `integration_fastapi` (uderzamy w usługę FastAPI).
pytestmark = [pytest.mark.integration, pytest.mark.integration_fastapi]


def test_summarize_przez_endpoint(fastapi_client):
    """POST /summarize -> 200 + kontrakt: niepuste summary i pełne metadane (model/input_chars/usage)."""
    text = "Urząd Skarbowy wzywa do zapłaty zaległości podatkowej 1 240 zł w terminie 14 dni."
    resp = fastapi_client.post("/summarize", json={"text": text})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["summary"].strip() != ""                 # cokolwiek streszczone (fake lub realny model)
    meta = body["metadata"]
    assert meta["model"]                                 # model obecny w metadanych
    assert meta["input_chars"] == len(text)              # długość wejścia (bez whitespace do strip)
    assert meta["truncated"] is False                    # krótki tekst, bez truncacji
    assert meta["usage"]["total_tokens"] > 0             # zużycie zmapowane


def test_summarize_puste_wejscie_daje_422(fastapi_client):
    """Puste wejście (sam whitespace) -> 422 (`EmptyInputError` zmapowany w routerze)."""
    resp = fastapi_client.post("/summarize", json={"text": "   \n  "})
    assert resp.status_code == 422, resp.text
