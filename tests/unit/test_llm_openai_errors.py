"""Testy mapowania wyjatkow SDK -> LLMError w OpenAILLMClient (krok 2.2).

To domyka dawne `[DO UZUPELNIENIA]`: klasyfikator `_map_sdk_error` zamienia wyjatki
SDK `openai` na nasze domenowe `LLMError`. Sprawdzamy kazda galaz BEZ sieci i BEZ
mocka klienta — wystarczy zbudowac instancje wyjatkow SDK i podac je wprost.

W odroznieniu od `test_llm_openai.py` ten plik POTRZEBUJE biblioteki `openai`
(tworzymy jej typy wyjatkow). Gdy nie jest zainstalowana -> SKIP, nie fail.
"""

import httpx
import pytest

# Klasyfikator uzywa typow wyjatkow z SDK -> bez biblioteki nie ma czego testowac.
pytest.importorskip("openai", reason="openai nie zainstalowany — pip install -r api/requirements.txt")

from openai import APIError, APITimeoutError, AuthenticationError, PermissionDeniedError, RateLimitError  # noqa: E402

from app.llm import LLMAuthError, LLMRateLimitError, LLMResponseError, LLMTimeoutError  # noqa: E402
from app.llm.client_openai import OpenAILLMClient  # noqa: E402

# Wspolne, sztuczne zadanie/odpowiedz HTTP — wyjatki SDK wymagaja ich w konstruktorze.
_REQ = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")


def _status_error(cls, code):
    """Zbuduj wyjatek statusowy SDK (np. 401/403/429) z odpowiedzia o danym kodzie."""
    return cls("blad", response=httpx.Response(code, request=_REQ), body=None)


def test_timeout_mapuje_na_llm_timeout():
    # APITimeoutError (zerwany/przekroczony czas) -> LLMTimeoutError.
    assert isinstance(OpenAILLMClient._map_sdk_error(APITimeoutError(_REQ)), LLMTimeoutError)


def test_auth_401_mapuje_na_llm_auth():
    # AuthenticationError (401, zly/brak klucza) -> LLMAuthError.
    assert isinstance(OpenAILLMClient._map_sdk_error(_status_error(AuthenticationError, 401)), LLMAuthError)


def test_permission_403_mapuje_na_llm_auth():
    # PermissionDeniedError (403, brak uprawnien do modelu) -> tez LLMAuthError.
    assert isinstance(OpenAILLMClient._map_sdk_error(_status_error(PermissionDeniedError, 403)), LLMAuthError)


def test_rate_limit_429_mapuje_na_llm_rate_limit():
    # RateLimitError (429, limit/kwota) -> LLMRateLimitError.
    assert isinstance(OpenAILLMClient._map_sdk_error(_status_error(RateLimitError, 429)), LLMRateLimitError)


def test_pozostale_api_error_mapuje_na_llm_response():
    # Bazowy APIError (reszta: 5xx, blad polaczenia, zla odpowiedz) -> LLMResponseError.
    assert isinstance(OpenAILLMClient._map_sdk_error(APIError("blad", _REQ, body=None)), LLMResponseError)
