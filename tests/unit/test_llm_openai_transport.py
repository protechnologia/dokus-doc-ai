"""Testy OpenAILLMClient na poziomie zadania HTTP (krok 3 /classify) — bez sieci.

Helpery testowane w `test_llm_openai.py` nie mowia, co REALNIE wychodzi z SDK: czy
pominiety parametr znika z ciala zadania, ile prob SDK robi przy bledzie i czy limit czasu
dociera do zadania. Tu przechodzimy przez `complete()` z prawdziwym SDK `openai`, a siec
zastepuje `httpx.MockTransport`, ktory nagrywa zadania i odpowiada ustalonym statusem.

Podmiana transportu: `client._client.with_options(http_client=...)` — kopia klienta SDK
z konfiguracja z konstruktora (`max_retries`, `timeout`), tylko z innym transportem.
Siegamy do prywatnego `_client`, bo konstruktor swiadomie nie przyjmuje `http_client`
(kod produkcyjny nie zmienia sie pod testy).
"""

import asyncio
import json
import httpx
import pytest

from app.llm import LLMRateLimitError, LLMResponseError, LLMTimeoutError
from app.llm.client_openai import OpenAILLMClient

# Minimalna odpowiedz chat.completions, ktora SDK potrafi zdeserializowac.
_OK = {
    "id": "x",
    "object": "chat.completion",
    "created": 0,
    "model": "gpt-4o-mini",
    "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "ok"}}],
}

# Schemat w ksztalcie klasyfikacji: uzasadnienie przed etykieta, etykieta jako enum.
_SCHEMA = {
    "type": "object",
    "properties": {
        "rationale": {"type": "string"},
        "label":     {"type": "string", "enum": ["OPT-1", "OPT-2", "OPT-00"]},
    },
    "required": ["rationale", "label"],
    "additionalProperties": False,
}


def _nagrywarka(status=200, timeout=False):
    """Atrapa serwera dostawcy (handler MockTransport): nagrywa zadania i odpowiada tak, jak kazemy.

    Tresci zadania nie ocenia — kazde zadanie w tych testach jest poprawne. Blad nie wynika wiec
    z zadania, tylko z konfiguracji atrapy, ktora udaje zachowanie prawdziwego serwera:
        status=200     -> poprawna odpowiedz `_OK` (testy ciala zadania i limitu czasu),
        status=429     -> serwer odrzuca zadanie limitem zapytan,
        status=500     -> serwer ma wewnetrzna awarie,
        timeout=True   -> serwer nie odpowiada w czasie (httpx rzuca ReadTimeout, `status` bez znaczenia).
    """
    requests = []

    def handler(request):
        requests.append(request)
        if timeout:
            raise httpx.ReadTimeout("timeout", request=request)          # jak zerwany/przekroczony odczyt
        body = _OK if status == 200 else {"error": {"message": "blad"}}
        return httpx.Response(status, json=body)

    return handler, requests


def _klient(handler, timeout=60.0):
    """OpenAILLMClient z konfiguracja z konstruktora, ale z MockTransport zamiast sieci."""
    client = OpenAILLMClient(api_key="sk-test", model="gpt-4o-mini", timeout=timeout)
    client._client = client._client.with_options(http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    return client


# --- Cialo zadania: response_format ----------------------------------------------


def test_bez_schematu_response_format_nie_trafia_do_zadania():
    """Brak schematu -> w ciele zadania nie ma klucza `response_format` (nawet `null`)."""
    handler, requests = _nagrywarka()                        # serwer odpowiada 200 — liczy sie tylko, co wyslalismy
    asyncio.run(_klient(handler).complete(user="dok", system="Po polsku", max_tokens=10))

    body = json.loads(requests[0].content)
    assert "response_format" not in body                     # zadanie streszczenia jak przed krokiem 2


def test_ze_schematem_response_format_trafia_do_zadania():
    """Schemat -> `response_format` typu json_schema ze schematem bez zmian."""
    handler, requests = _nagrywarka()                        # serwer odpowiada 200 — liczy sie tylko, co wyslalismy
    asyncio.run(_klient(handler).complete(user="dok", json_schema=_SCHEMA))

    rf = json.loads(requests[0].content)["response_format"]
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["schema"] == _SCHEMA


# --- Limit czasu i ponowienia ----------------------------------------------------


def test_limit_czasu_z_konstruktora_trafia_do_zadania():
    """`timeout` z konstruktora (z LLM_TIMEOUT_SECONDS) jest limitem czasu wyslanego zadania."""
    handler, requests = _nagrywarka()                        # serwer odpowiada 200 — liczy sie tylko, co wyslalismy
    asyncio.run(_klient(handler, timeout=42.0).complete(user="dok"))

    # httpx niesie limit w extensions zadania; transport go egzekwuje (tu atrapa, wiec tylko odczyt).
    assert requests[0].extensions["timeout"]["read"] == 42.0


# Dlaczego akurat te trzy przypadki: to realne bledy dostawcy, ktore SDK `openai` przy domyslnym
# `max_retries=2` po cichu ponawia (poza nimi ponawia jeszcze 408, 409 i zerwane polaczenie).
# Bledy nieponawiane (np. 400, 401) SDK zwraca od razu, wiec nie sprawdzilyby niczego nowego.
# Kazdy wiersz: jak zachowuje sie serwer -> wyjatek SDK -> nasz LLMError.
@pytest.mark.parametrize(
    ("status", "timeout", "blad"),
    [
        (None, True,  LLMTimeoutError),     # serwer milczy -> ReadTimeout -> APITimeoutError     -> LLMTimeoutError
        (429,  False, LLMRateLimitError),   # serwer: 429   ->                RateLimitError      -> LLMRateLimitError
        (500,  False, LLMResponseError),    # serwer: 500   ->                InternalServerError -> LLMResponseError
    ],
)
def test_blad_konczy_sie_jedna_proba_bez_ponowien(status, timeout, blad):
    """Timeout / 429 / 5xx -> dokladnie jedna proba i od razu nasz LLMError (ponawia DOKUS, nie SDK)."""
    # Zadanie (`user="dok"`) jest poprawne — blad nie bierze sie z jego tresci. Wymusza go atrapa
    # serwera, ustawiona parametrami przypadku tak, jak zachowalby sie prawdziwy dostawca.
    handler, requests = _nagrywarka(status=status, timeout=timeout)

    with pytest.raises(blad):
        asyncio.run(_klient(handler).complete(user="dok"))

    assert len(requests) == 1                                # przy domyslnym max_retries=2 bylyby 3
