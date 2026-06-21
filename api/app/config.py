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

    # --- Ekstrakcja (krok 2.3) ---
    # Gorny limit rozmiaru ZDEKODOWANEGO pliku (bajty). Straznik zasobow: za duzy upload
    # odrzucamy wczesnie (HTTP 413) zamiast obciazac Tike/OCR. Domyslnie 20 MiB.
    max_upload_bytes: int = 20 * 1024 * 1024

    # Straznik zasobow OCR (krok 2.3.5). Limit liczby stron PDF, ktore wysylamy do Tiki:
    # gdy PDF ma wiecej stron, tniemy go (pypdf) do pierwszych N PRZED ekstrakcja —
    # inaczej skan obrazowy OCR-owalby sie w calosci i moglby zatkac usluge/przekroczyc
    # timeout. UWAGA: w przyjetej strategii (limit PRZED `auto`) limit dotyczy KAZDEGO
    # duzego PDF, nie tylko sciezki OCR — czysty dlugi PDF tekstowy tez jest ucinany
    # (de facto MAX_PDF_PAGES). Ciecie NIE jest ciche: log + metadane "przetworzono N z M
    # stron" w odpowiedzi. Default 30 — z zapasem ponizej `tika_timeout_seconds` przy OCR.
    max_ocr_pages: int = 30

    # --- Dostawca LLM (klient: app.llm, krok 2.2) ---
    # Faza 1 = komercyjne API. Na dev/test domyslnie 'fake' — nic nie wychodzi na
    # zewnatrz (spojne z zasada "prywatnosc pierwsza").
    llm_provider: str = "fake"           # 'fake' (offline) lub 'openai'
    llm_api_key: str | None = None       # wymagany dla 'openai', np. "sk-proj-..."
    llm_base_url: str | None = None      # opcjonalny wlasny endpoint zgodny z API OpenAI (.../v1)
    llm_model: str | None = None         # wymagany dla 'openai', np. "gpt-4o-mini"
    llm_timeout_seconds: float = 60.0    # timeout wolania LLM [s]


@lru_cache
def get_settings() -> Settings:
    """Singleton ustawien (cache — ENV/.env czytamy raz na proces)."""
    return Settings()
