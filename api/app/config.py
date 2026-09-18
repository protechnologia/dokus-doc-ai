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

    # --- Okno modelu (summaryzacja i klasyfikacja) ---
    # Okno modelu w ZNAKACH (niezależne od tokenizera w kodzie). Summaryzacja (`TextTruncator`):
    # dłuższy TEKST dokumentu jest cięty — początek / środek / koniec + log + metadane
    # `truncated`/`sent_chars`/`parts` (truncacja pod okno modelu, co innego niż MAX_OCR_PAGES).
    # Klasyfikacja (`ClassificationService`): dłuższy CAŁY prompt -> 413, nigdy cięcie (ucięcie opcji
    # zmienia zbiór wyboru). Rezerwy na odpowiedź kod nie odejmuje — dobór: (okno − prompt systemowy −
    # `llm_max_output_tokens_summary`) × znaki/token modelu (Bielik ~1,65, OpenAI ~2,7; README „Okno
    # modelu ≠ okno…"). Domyślne 90 000 spójne z MAX_OCR_PAGES=30, ale przy `num_ctx` Ollamy 4096 NIE
    # chroni klasyfikacji (Ollama po cichu utnie prompt razem z instrukcją o OPT-00).
    llm_max_input_chars: int = 90_000
    # Górny limit długości streszczenia (tokeny, `max_tokens` wywołania). Typowe streszczenie
    # pięciu pól to ~300 tokenów. Ucięcie limitem jest dziś CICHE (tekst, nie JSON — brak flagi);
    # jedyny ślad: `usage.completion_tokens` równe limitowi. Wlicza się do okna modelu obok
    # wejścia — przy zmianie przeliczyć `LLM_MAX_INPUT_CHARS` (README → „Okno modelu").
    llm_max_output_tokens_summary: int = 600

    # --- Klasyfikacja (domena: app.classification) ---
    # Górny limit długości odpowiedzi przy /classify (tokeny). W ENV, bo liczba tokenów tego
    # samego tekstu zależy od tokenizera modelu, czyli od wdrożenia. Pomiar 2026-09-18
    # (`usage.completion_tokens`, gpt-4o-mini i Bielik 4.5B, dwa przebiegi, katalog 8 opcji):
    # wybór opcji 44–62, ale przy `OPT-00` model wylicza w uzasadnieniu odrzucone opcje (~5 tokenów
    # na nazwę), więc długość rośnie z katalogiem — gpt-4o-mini: 8 opcji -> 84, 14 -> 118, 20 -> 62
    # („itp."); Bielik 4.5B do 112. 400 ≈ 3,4 × maksimum: urwany JSON przy `temperature=0` urwie się
    # tak samo przy każdym ponowieniu. Za ciasny limit widać w logu: WARNING `completion_tokens 400/400`.
    llm_max_output_tokens_classify: int = 400

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
