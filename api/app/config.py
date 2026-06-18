"""Konfiguracja aplikacji (pydantic-settings) — wylacznie z ENV.

Zasada projektu: zadnych sekretow ani endpointow na sztywno w kodzie.
Dostawce LLM przelaczamy konfiguracja, nie edycja logiki biznesowej
(klient `LLMClient` dochodzi w kroku 2.2 — tu trzymamy juz jego konfiguracje).
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Ustawienia czytane z ENV (i opcjonalnie z .env). Nazwy pol = ENV bez prefiksu,
    np. pole `tika_url` -> zmienna `TIKA_URL` (wielkosc liter bez znaczenia)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        # ENV spoza modelu (np. TIKA_PORT, FASTAPI_PORT) nie maja wywalac aplikacji.
        extra="ignore",
    )

    # --- Tika (krok 1) ---
    # Adres tika-server widziany przez FastAPI. W docker-compose: http://tika:9998.
    tika_url: str = "http://localhost:9998"
    # OCR bywa wolny — timeout ekstrakcji liczymy hojnie (sekundy).
    tika_timeout_seconds: float = 120.0
    # Krotki timeout na samo sprawdzenie dostepnosci Tiki w /health.
    health_check_timeout_seconds: float = 3.0

    # --- Dostawca LLM (klient dochodzi w kroku 2.2) ---
    # Faza 1 = komercyjne API. Na dev/test domyslnie 'fake' — nic nie wychodzi na
    # zewnatrz (spojne z zasada "prywatnosc pierwsza").
    llm_provider: str = "fake"
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_version: str | None = None  # tylko Azure OpenAI
    llm_timeout_seconds: float = 60.0


@lru_cache
def get_settings() -> Settings:
    """Singleton ustawien (cache — ENV/.env czytamy raz na proces)."""
    return Settings()
