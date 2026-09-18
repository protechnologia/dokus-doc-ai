"""Test integracyjny endpointu POST /classify (krok 10) — przez działającą usługę FastAPI.

Marker: integration_fastapi. Uderza w kontener (fixture `fastapi_client`); gdy usługa niedostępna
-> SKIP. To test WIRINGU i KONTRAKTU (router -> DI -> serwis -> serializacja), nie jakości wyboru
(decyzja 3 kroku 10): asercje są PROVIDER-AGNOSTYCZNE, więc przechodzi na każdym `LLM_PROVIDER`
kontenera — z `fake` darmowo i deterministycznie (CI), z `openai` / `ollama` realnym wywołaniem.
Trafność wyboru: `test_classification_service.py` (golden set) i `test_classification_prompt.py`.
Dokładne mapowanie błędów: jednostkowy `tests/unit/test_fastapi_classify.py`.
"""

import pytest

# Parasol `integration` + węższy `integration_fastapi` (uderzamy w usługę FastAPI).
pytestmark = [pytest.mark.integration, pytest.mark.integration_fastapi]

# Przykład z README „POST /classify" (id liczbowe i tekstowe).
_REQUEST = {
    "summaries": ["• Typ pisma: skarga\n• Czego dotyczy: naliczenie przez operatora telekomunikacyjnego opłat za niezamówione usługi"],
    "options": [
        {"id": 21, "name": "Skargi i interwencje konsumenckie", "description": "Skargi konsumentów na dostawców usług telekomunikacyjnych i pocztowych, wnioski o interwencję.", "examples": "Skarga na zawyżony rachunek."},
        {"id": 22, "name": "Rynek pocztowy", "description": "Sprawy operatorów pocztowych: wpisy do rejestru, sprawozdania, kontrole.", "examples": None},
        {"id": "numeracja-7", "name": "Numeracja", "description": "Przydział i rezerwacja zasobów numeracji.", "examples": None},
    ],
}


def test_classify_przez_endpoint_zgodnie_z_kontraktem(fastapi_client):
    """POST /classify -> 200 + kontrakt: pola jak w README, spójne z `outcome`, audyt i metadane wypełnione."""
    resp = fastapi_client.post("/classify", json=_REQUEST, timeout=300)   # Bielik na CPU: minuty

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"outcome", "option_id", "rationale", "error", "system_prompt", "user_prompt", "raw_response", "metadata"}
    assert body["outcome"] in ("matched", "no_match", "invalid_response")

    # Spójność pól z `outcome` (tabela w README) — niezależnie od tego, co wybrał model.
    if body["outcome"] == "matched":
        assert body["option_id"] in (21, 22, "numeracja-7") and body["rationale"] is not None and body["error"] is None
    elif body["outcome"] == "no_match":
        assert body["option_id"] is None and body["rationale"] is not None and body["error"] is None
    else:
        assert body["option_id"] is None and body["rationale"] is None and body["error"]

    # Audyt i metadane: zawsze.
    assert body["system_prompt"] and body["user_prompt"] and body["raw_response"]
    assert body["metadata"]["model"]
    assert body["metadata"]["usage"]["total_tokens"] > 0


def test_classify_za_dlugie_wejscie_daje_413(fastapi_client):
    """Prompt ponad LLM_MAX_INPUT_CHARS kontenera -> 413 z `detail` tekstem; model niewołany (odpowiedź natychmiast)."""
    resp = fastapi_client.post("/classify", json={**_REQUEST, "summaries": ["x" * 500_000]})

    assert resp.status_code == 413, resp.text
    assert isinstance(resp.json()["detail"], str)


def test_classify_pusta_lista_opcji_daje_422(fastapi_client):
    """Pusta lista opcji -> 422 z walidacji (wymóg kontraktu)."""
    resp = fastapi_client.post("/classify", json={**_REQUEST, "options": []})

    assert resp.status_code == 422, resp.text
