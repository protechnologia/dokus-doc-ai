"""Testy jednostkowe konfiguracji (pydantic-settings). Bez uruchamiania uslug.

Wymagaja zainstalowanego runtime aplikacji (`pip install -r requirements-dev.txt`, ktory
ciagnie `api/requirements.txt`). Zadeklarowanych zaleznosci NIE guardujemy `importorskip` —
ich brak to blad instalacji (glosny), nie powod do cichego skip.
"""

from app.config import Settings

# ENV, ktore moga byc ustawione w srodowisku/.env i zaklocic testy domyslnych wartosci.
_RELEVANT_ENV = (
    "TIKA_URL",
    "TIKA_TIMEOUT_SECONDS",
    "LLM_PROVIDER",
    "LLM_MODEL",
    "LLM_API_KEY",
)


def _clear_env(monkeypatch):
    for var in _RELEVANT_ENV:
        monkeypatch.delenv(var, raising=False)


def test_defaults(monkeypatch):
    """Bez ENV i bez .env padaja sensowne domyslne (dev/test): localhost + 'fake'."""
    _clear_env(monkeypatch)
    s = Settings(_env_file=None)

    assert s.tika_url == "http://localhost:9998"
    assert s.tika_timeout_seconds > 0
    # Domyslnie nic nie wychodzi na zewnatrz — zgodne z "prywatnosc pierwsza".
    assert s.llm_provider == "fake"
    assert s.llm_api_key is None


def test_env_override(monkeypatch):
    """Wartosci z ENV nadpisuja domyslne (config wylacznie przez ENV)."""
    _clear_env(monkeypatch)
    monkeypatch.setenv("TIKA_URL", "http://tika:9998")
    monkeypatch.setenv("LLM_PROVIDER", "azure")
    monkeypatch.setenv("LLM_MODEL", "gpt-4o")

    s = Settings(_env_file=None)
    assert s.tika_url == "http://tika:9998"
    assert s.llm_provider == "azure"
    assert s.llm_model == "gpt-4o"


def test_unknown_env_ignored(monkeypatch):
    """ENV spoza modelu (np. TIKA_PORT, FASTAPI_PORT) nie wywala konfiguracji."""
    _clear_env(monkeypatch)
    monkeypatch.setenv("TIKA_PORT", "9998")
    monkeypatch.setenv("FASTAPI_PORT", "8000")

    s = Settings(_env_file=None)  # nie powinno rzucic
    assert s.tika_url == "http://localhost:9998"
