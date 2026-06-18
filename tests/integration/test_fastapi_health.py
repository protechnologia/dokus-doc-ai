"""Testy integracyjne uslugi FastAPI (krok 2.1) — na razie /health.

Gdy usluga niedostepna -> testy SKIP (jak konwencja Tiki), nie fail.
Uruchomienie: `docker compose up -d fastapi` (albo lokalnie `uvicorn app.main:app`).
"""

import pytest

# Parasol `integration` + wezszy `integration_fastapi`.
pytestmark = [pytest.mark.integration, pytest.mark.integration_fastapi]


def test_health_ok(fastapi_client):
    """/health zwraca 200 i poprawny ksztalt odpowiedzi (HealthResponse)."""
    resp = fastapi_client.get("/health")
    assert resp.status_code == 200, resp.text

    body = resp.json()
    assert body["status"] in ("ok", "degraded")
    assert body["service"] == "dokus-doc-ai"
    assert body["version"]  # niepusta wersja
    assert "tika" in body["dependencies"]


def test_health_reports_request_id(fastapi_client):
    """Kazda odpowiedz niesie X-Request-ID (przekrojowe: request-id w logach)."""
    resp = fastapi_client.get("/health")
    assert resp.headers.get("X-Request-ID")


def test_health_tika_dependency_value(fastapi_client):
    """Status zaleznosci 'tika' to jedna z dozwolonych wartosci.

    Nie wymuszamy 'ok' — Tika moze nie byc podniesiona w danym srodowisku.
    Gdy FastAPI i Tika wstaja razem (compose, depends_on: service_healthy),
    spodziewamy sie 'ok'.
    """
    body = fastapi_client.get("/health").json()
    assert body["dependencies"]["tika"] in ("ok", "unreachable")
