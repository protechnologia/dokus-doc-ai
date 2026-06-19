"""Warstwa LLM (krok 2.2): interfejs LLMClient + implementacje + fabryka.

Publiczne API pakietu. Logika biznesowa importuje stad — np.:
    from app.llm import get_llm_client, LLMError

Import tego pakietu NIE wymaga zainstalowanego SDK `openai` (jest leniwy w
OpenAILLMClient) — dzieki temu Fake i fabryka dzialaja w testach bez SDK.
"""

from app.llm.base import (
    LLMAuthError,
    LLMClient,
    LLMError,
    LLMRateLimitError,
    LLMResponseError,
    LLMResult,
    LLMTimeoutError,
    LLMUsage,
)
from app.llm.factory import LLMConfigError, build_llm_client, get_llm_client
from app.llm.fake import FakeLLMClient
from app.llm.openai_client import OpenAILLMClient

__all__ = [
    # interfejs + modele wyniku
    "LLMClient",
    "LLMResult",
    "LLMUsage",
    # wyjatki domenowe
    "LLMError",
    "LLMAuthError",
    "LLMRateLimitError",
    "LLMTimeoutError",
    "LLMResponseError",
    # implementacje
    "FakeLLMClient",
    "OpenAILLMClient",
    # fabryka
    "build_llm_client",
    "get_llm_client",
    "LLMConfigError",
]
