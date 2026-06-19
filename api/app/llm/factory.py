"""Fabryka klienta LLM — wybor implementacji po konfiguracji (krok 2.2).

Zasada naczelna nr 2: dostawce zmieniamy KONFIGURACJA (`LLM_PROVIDER` + ENV), nie
edycja logiki. Tu zamieniamy `Settings` na konkretny `LLMClient`:
  - 'fake'   -> FakeLLMClient (dev/test, nic nie wychodzi na zewnatrz),
  - 'openai' -> OpenAILLMClient (komercyjne API fazy 1).
Walidujemy spojnosc configu wczesnie (LLMConfigError), zeby brak klucza wywalil sie
czytelnie tu, a nie dopiero golym 401 z dostawcy w trakcie obslugi zadania.
"""

from __future__ import annotations

from functools import lru_cache

from app.config import Settings, get_settings
from app.llm.base import LLMClient
from app.llm.client_fake import FakeLLMClient
from app.llm.client_openai import OpenAILLMClient


class LLMConfigError(Exception):
    """Niespojna konfiguracja dostawcy LLM (np. brak klucza dla 'openai')."""


def build_llm_client(
    settings: Settings,    # ustawienia z ENV; istotne: llm_provider/llm_api_key/llm_model
) -> LLMClient:
    """Opis metody:
    Zbuduj klienta wg `settings.llm_provider`. Czysta funkcja (bez cache).

    Przyklad argumentow:
        settings=Settings(llm_provider="openai", llm_api_key="sk-...", llm_model="gpt-4o-mini")

    Przyklad wyniku:
        OpenAILLMClient(...)   # 'fake' -> FakeLLMClient(); 'openai' bez klucza -> LLMConfigError

    Raises:
        LLMConfigError: nieznany provider albo brak wymaganego klucza/modelu dla 'openai'.
    """
    provider = (settings.llm_provider or "fake").lower()

    # --- 'fake': bez sieci, bez klucza — domyslny tryb dev/test ------------------
    if provider == "fake":
        return FakeLLMClient()

    # --- 'openai': wymaga klucza i modelu; reszta opcjonalna --------------------
    if provider == "openai":
        if not settings.llm_api_key:
            raise LLMConfigError("LLM_PROVIDER=openai wymaga LLM_API_KEY")
        if not settings.llm_model:
            raise LLMConfigError("LLM_PROVIDER=openai wymaga LLM_MODEL")
        return OpenAILLMClient(
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            base_url=settings.llm_base_url,        # zwykle None -> domyslny endpoint OpenAI
            timeout=settings.llm_timeout_seconds,
        )

    # --- kazdy inny provider (np. 'azure') jest nieobslugiwany -------------------
    raise LLMConfigError(f"Nieobslugiwany LLM_PROVIDER: {settings.llm_provider!r}")


@lru_cache
def get_llm_client() -> LLMClient:
    """Opis metody:
    Singleton klienta (analogicznie do get_settings) — budowany raz na proces.

    Przyklad argumentow:
        (brak — czyta konfiguracje przez get_settings())

    Przyklad wyniku:
        ten sam OpenAILLMClient/FakeLLMClient przy kazdym wywolaniu
    """
    return build_llm_client(get_settings())
