"""Fixtures dla testow integracyjnych — uderzaja w dzialajace uslugi (Tika, FastAPI).

Usluga musi byc uruchomiona (docker compose up -d <usluga>). Jesli jest niedostepna,
jej testy sa POMIJANE (skip), a nie wywalane — dzieki temu `pytest` na maszynie bez
kontenerow nie czerwieni sie bezsensownie.
"""

import os
import socket
from urllib.parse import urlparse

import httpx
import pytest

TIKA_URL = os.environ.get("TIKA_URL", "http://localhost:9998")
FASTAPI_URL = os.environ.get("FASTAPI_URL", "http://localhost:8000")


def _reachable(url: str, default_port: int) -> bool:
    parsed = urlparse(url)
    host = parsed.hostname or "localhost"
    port = parsed.port or default_port
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


@pytest.fixture(scope="session")
def tika_url() -> str:
    if not _reachable(TIKA_URL, 9998):
        pytest.skip(
            f"Tika niedostepna pod {TIKA_URL} — uruchom: docker compose up -d tika"
        )
    return TIKA_URL


@pytest.fixture(scope="session")
def tika_client(tika_url: str):
    """Klient HTTP do Tiki z ustawionym base_url i dluzszym timeoutem (OCR bywa wolny)."""
    with httpx.Client(base_url=tika_url, timeout=120) as c:
        yield c


@pytest.fixture(scope="session")
def fastapi_url() -> str:
    if not _reachable(FASTAPI_URL, 8000):
        pytest.skip(
            f"FastAPI niedostepna pod {FASTAPI_URL} — uruchom: docker compose up -d fastapi"
        )
    return FASTAPI_URL


@pytest.fixture(scope="session")
def fastapi_client(fastapi_url: str):
    """Klient HTTP do uslugi FastAPI z ustawionym base_url."""
    with httpx.Client(base_url=fastapi_url, timeout=30) as c:
        yield c
