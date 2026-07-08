"""Testy jednostkowe logowania PRZYCZYNY bledu (handlery wyjatkow w `app.main`).

Middleware loguje sam status ("-> 422"); `detail` zyje wylacznie w wyjatku i bez osobnych
handlerow nigdy nie trafia do logu. Konsekwencja z produkcji: 422, ktorego przyczyny nie
dalo sie ustalic bez dostepu do klienta. Te testy pilnuja, ze przyczyna JEST logowana.

Sprawdzamy log, nie odpowiedz HTTP — kontrakt HTTP jest testowany w `test_fastapi_*.py`
i handlery maja go NIE zmieniac (delegacja do domyslnych handlerow FastAPI).
"""

import logging

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _rekordy_dokus(caplog) -> list[logging.LogRecord]:
    """Rekordy z loggera 'dokus' (pomija szum httpx/uvicorn)."""
    return [r for r in caplog.records if r.name == "dokus"]


# --- HTTPException z routera (nasze 4xx/5xx) --------------------------------------


def test_http_exception_loguje_detail(caplog):
    """422 z routera niesie `detail` do logu — inaczej diagnoza wymaga dostepu do klienta."""
    with caplog.at_level(logging.WARNING, logger="dokus"):
        odp = client.post("/extract", json={"content_base64": "!!! to nie jest base64 !!!"})

    assert odp.status_code == 422                      # kontrakt HTTP nietkniety
    komunikaty = [r.getMessage() for r in _rekordy_dokus(caplog)]
    assert any("detail=" in m and "base64" in m for m in komunikaty), komunikaty


def test_4xx_jest_warningiem_a_nie_errorem(caplog):
    """Wina wejscia != awaria uslugi. Filtr po poziomie logu ma je rozdzielac."""
    with caplog.at_level(logging.WARNING, logger="dokus"):
        client.post("/extract", json={"content_base64": "!!!"})

    poziomy = {r.levelno for r in _rekordy_dokus(caplog) if "detail=" in r.getMessage()}
    assert poziomy == {logging.WARNING}


def test_log_niesie_request_id(caplog):
    """Ten sam request-id co naglowek X-Request-ID — inaczej korelacja logu z klientem nie dziala."""
    with caplog.at_level(logging.WARNING, logger="dokus"):
        odp = client.post(
            "/extract",
            json={"content_base64": "!!!"},
            headers={"X-Request-ID": "test-rid-123"},
        )

    assert odp.headers["X-Request-ID"] == "test-rid-123"
    assert any("req=test-rid-123" in r.getMessage() for r in _rekordy_dokus(caplog))


# --- RequestValidationError (422 zanim router ruszy) ------------------------------


def test_blad_walidacji_pydantic_jest_logowany(caplog):
    """Brak wymaganego pola: `RequestValidationError` NIE jest HTTPException — wlasny handler.

    Bez niego najczestszy 422 (zle/brakujace pole w JSON) bylby w logach niemy.
    """
    with caplog.at_level(logging.WARNING, logger="dokus"):
        odp = client.post("/extract", json={"zle_pole": "x"})

    assert odp.status_code == 422
    komunikaty = [r.getMessage() for r in _rekordy_dokus(caplog)]
    assert any("walidacja=" in m and "content_base64" in m for m in komunikaty), komunikaty
