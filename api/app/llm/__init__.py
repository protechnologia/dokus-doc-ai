"""Warstwa LLM (krok 2.2): interfejs LLMClient + implementacje + fabryka.

Publiczne API pakietu. Logika biznesowa importuje stad — np.:
    from app.llm import get_llm_client, LLMError
"""

from app.llm.base import LLMAuthError, LLMClient, LLMError, LLMRateLimitError, LLMResponseError, LLMResult, LLMTimeoutError, LLMUsage
from app.llm.client_fake import FakeLLMClient
from app.llm.client_openai import OpenAILLMClient
from app.llm.factory import LLMConfigError, build_llm_client, get_llm_client

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
