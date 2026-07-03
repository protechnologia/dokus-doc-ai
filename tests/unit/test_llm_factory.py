"""Testy jednostkowe fabryki klienta LLM (krok 2.2) — dispatch + walidacja configu.

Bez sieci i bez SDK: sciezki bledne (brak klucza/modelu, nieznany provider) nie
buduja realnego klienta, wiec `openai` nie jest potrzebny. Realne wywolanie OpenAI
sprawdza test integracyjny (tests/integration/test_llm.py).
"""

import pytest

from app.config import Settings
from app.llm import FakeLLMClient, LLMConfigError, build_llm_client
from app.llm.client_openai import OpenAILLMClient


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


def test_fabryka_ollama_buduje_openai_client_bez_klucza():
    """provider 'ollama' z modelem i base_url buduje OpenAILLMClient BEZ wymogu klucza.

    Ollama ignoruje klucz (fabryka podstawia atrape), wiec brak LLM_API_KEY jest OK —
    inaczej niz 'openai'. Konstrukcja klienta nie robi I/O (AsyncOpenAI tylko sie konfiguruje).
    """
    s = Settings(
        _env_file=None,
        llm_provider="ollama",
        llm_api_key=None,
        llm_model="SpeakLeash/bielik-11b-v3.0-instruct:Q4_K_M",
        llm_base_url="http://ollama:11434/v1",
    )
    assert isinstance(build_llm_client(s), OpenAILLMClient)


def test_fabryka_ollama_bez_modelu_blad():
    """provider 'ollama' bez LLM_MODEL -> LLMConfigError."""
    s = Settings(_env_file=None, llm_provider="ollama", llm_model=None, llm_base_url="http://ollama:11434/v1")
    with pytest.raises(LLMConfigError, match="LLM_MODEL"):
        build_llm_client(s)


def test_fabryka_ollama_bez_base_url_blad():
    """provider 'ollama' bez LLM_BASE_URL -> LLMConfigError (inaczej trafilibysmy na publiczny OpenAI)."""
    s = Settings(_env_file=None, llm_provider="ollama", llm_model="bielik", llm_base_url=None)
    with pytest.raises(LLMConfigError, match="LLM_BASE_URL"):
        build_llm_client(s)
