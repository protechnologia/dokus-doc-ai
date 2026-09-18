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

from app.config import get_settings
from app.llm import LLMClient, LLMConfigError, build_llm_client

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


@pytest.fixture
def llm_client() -> LLMClient:
    """Realny klient LLM z konfiguracji, NOWY na kazdy test; `fake` albo niekompletna konfiguracja -> SKIP.

    Nie `scope="module"`: `AsyncOpenAI` wiaze pule polaczen z petla zdarzen pierwszego zadania,
    a kazdy test wola `asyncio.run` (nowa petla) — wspolny klient wywala drugi test bledem
    „Event loop is closed" (zmierzone). Z tego samego powodu test z wieloma wywolaniami robi je
    w JEDNYM `asyncio.run`. W usludze jest jedna petla, wiec tam klient z cache jest OK.
    """
    settings = get_settings()
    if settings.llm_provider == "fake":
        pytest.skip("LLM_PROVIDER=fake — test wymaga realnego dostawcy (openai / ollama)")
    try:
        return build_llm_client(settings)
    except LLMConfigError as exc:
        pytest.skip(f"niekompletna konfiguracja LLM: {exc}")
