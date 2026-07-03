"""Konfiguracja aplikacji (pydantic-settings) — wylacznie z ENV.

Zasada projektu: zadnych sekretow ani endpointow na sztywno w kodzie.
Dostawce LLM przelaczamy konfiguracja, nie edycja logiki biznesowej
(klient `LLMClient` dochodzi w kroku 2.2 — tu trzymamy juz jego konfiguracje).
"""

from functools import lru_cache

from pydantic          import field_validator
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
    llm_provider: str = "fake"           # 'fake' (offline), 'openai' lub 'ollama' (lokalny Bielik)
    llm_api_key: str | None = None       # wymagany dla 'openai'; dla 'ollama' zbedny (atrapa)
    llm_base_url: str | None = None      # endpoint zgodny z API OpenAI (.../v1); wymagany dla 'ollama'
    llm_model: str | None = None         # wymagany dla 'openai'/'ollama', np. "gpt-4o-mini" / tag Bielika
    llm_timeout_seconds: float = 60.0    # timeout wolania LLM [s]

    # --- Summaryzacja (domena: app.summarization, krok 2.4) ---
    # Strażnik okna kontekstu modelu: górny limit ZNAKÓW tekstu wysyłanego do LLM. Powyżej —
    # bierzemy pierwsze N znaków + log + metadana `truncated` (truncacja POD OKNO MODELU, co
    # innego niż MAX_OCR_PAGES z ekstrakcji). Liczony w znakach (odporny na zmianę modelu/
    # tokenizera). Domyślnie 90 000 — spójnie z MAX_OCR_PAGES=30 (~3000 znaków/stronę); pod
    # mniejszy model (Bielik, ~32k tok.) obniżyć (patrz README → „Spójność limitów pipeline'u").
    llm_max_input_chars: int = 90_000

    @field_validator("llm_api_key", "llm_base_url", "llm_model", mode="before")
    @classmethod
    def _puste_na_none(
        cls,
        v: object,   # surowa wartosc pola opcjonalnego (str z ENV, None, itp.)
    ) -> object:
        """Opis metody:
        Pusty/bialy string z ENV traktuj jak BRAK -> None. Krytyczne dla `docker-compose`,
        ktory dla niezdefiniowanych zmiennych wstawia PUSTY string (`${LLM_BASE_URL:-}` ->
        `LLM_BASE_URL=""`), a nie pomija zmienną. Bez tego np. `llm_base_url=""` trafia do
        `AsyncOpenAI(base_url="")` i wywraca wywolanie (`APIConnectionError`). Dotyczy pol
        opcjonalnych (`str | None`); nie-stringi przepuszczamy bez zmian.

        Przyklad argumentow:
            v=""      (albo "  ")

        Przyklad wyniku:
            None      (dla niepustego stringa zwraca go bez zmian)
        """
        if isinstance(v, str) and v.strip() == "":
            return None
        return v


@lru_cache
def get_settings() -> Settings:
    """Singleton ustawien (cache — ENV/.env czytamy raz na proces)."""
    return Settings()
