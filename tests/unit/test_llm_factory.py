"""Testy jednostkowe fabryki klienta LLM (krok 2.2) — dispatch + walidacja configu.

Bez sieci i bez SDK: sciezki bledne (brak klucza/modelu, nieznany provider) nie
buduja realnego klienta, wiec `openai` nie jest potrzebny. Realne wywolanie OpenAI
sprawdza test integracyjny (tests/integration/test_llm.py).
"""

import pytest

from app.config import Settings
from app.llm import FakeLLMClient, LLMConfigError, build_llm_client


def test_fabryka_fake_bez_klucza():
    """provider 'fake' nie wymaga klucza i daje FakeLLMClient."""
    s = Settings(_env_file=None, llm_provider="fake", llm_api_key=None)
    assert isinstance(build_llm_client(s), FakeLLMClient)


def test_fabryka_openai_bez_klucza_blad():
    """provider 'openai' bez LLM_API_KEY -> czytelny LLMConfigError (nie 401 w runtime)."""
    s = Settings(_env_file=None, llm_provider="openai", llm_api_key=None, llm_model="gpt-4o-mini")
    with pytest.raises(LLMConfigError, match="LLM_API_KEY"):
        build_llm_client(s)


def test_fabryka_openai_bez_modelu_blad():
    """provider 'openai' bez LLM_MODEL -> LLMConfigError."""
    s = Settings(_env_file=None, llm_provider="openai", llm_api_key="sk-test", llm_model=None)
    with pytest.raises(LLMConfigError, match="LLM_MODEL"):
        build_llm_client(s)


def test_fabryka_nieznany_provider_blad():
    """Nieobslugiwany provider (np. 'azure') -> LLMConfigError."""
    s = Settings(_env_file=None, llm_provider="azure", llm_api_key="sk-test", llm_model="x")
    with pytest.raises(LLMConfigError, match="Nieobslugiwany"):
        build_llm_client(s)
