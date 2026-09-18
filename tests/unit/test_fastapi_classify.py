"""Testy jednostkowe routera POST /classify (krok 9) — mapowanie wejścia / wyników / wyjątków na HTTP.

Bez sieci i bez LLM: podstawiamy ATRAPĘ `ClassificationService` przez `dependency_overrides`
(oddaje zadany `ClassificationResult` albo rzuca zadany wyjątek). Testujemy WYŁĄCZNIE warstwę
HTTP routera: każdy z trzech wyników jako 200 z kontraktem, zamianę opcji na domenowe, walidację
(422) i mapowanie wyjątków na 413/500/502/503/504. Osobno (sekcja DI na końcu): czy funkcja DI
przekazuje ustawienia z `Settings` do konstruktora serwisu.
"""

import pytest
from fastapi.testclient import TestClient

from app.classification import ClassificationOption, ClassificationResult, PromptTooLongError
from app.config import Settings
from app.llm import FakeLLMClient, LLMAuthError, LLMConfigError, LLMError, LLMRateLimitError, LLMResponseError, LLMResult, LLMTimeoutError, LLMUsage
from app.main import app
from app.routers.classify import _get_classification_service

# Żądanie jak w README: `id` liczbowe i tekstowe, `examples` podane i null.
_REQUEST = {
    "summaries": ["• Typ pisma: skarga\n• Czego dotyczy: zawyżony rachunek operatora"],
    "options": [
        {"id": 21, "name": "Skargi", "description": "Skargi konsumentów.", "examples": "Skarga na rachunek."},
        {"id": "numeracja-7", "name": "Numeracja", "description": "Przydział numeracji.", "examples": None},
    ],
}

# Wymiana z modelem wspólna dla wyników z atrapy.
_USAGE    = LLMUsage(prompt_tokens=1180, completion_tokens=58, total_tokens=1238)
_EXCHANGE = {"system_prompt": "Wybierz...", "user_prompt": "Streszczenia...", "response": LLMResult(text='{"rationale": "...", "label": "OPT-1"}', model="gpt-4o-mini", usage=_USAGE)}


class _StubService:
    """Atrapa `ClassificationService`: oddaje zadany wynik albo rzuca zadany wyjątek; nagrywa argumenty (duck-typing)."""

    def __init__(self, *, result: ClassificationResult | None = None, error: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self._result = result
        self._error  = error

    async def classify(self, *, summaries, options) -> ClassificationResult:
        self.calls.append({"summaries": summaries, "options": options})
        if self._error is not None:
            raise self._error
        return self._result


def _client(service: _StubService) -> TestClient:
    """Pomocniczo: `TestClient` z podstawionym serwisem przez `dependency_overrides`."""
    app.dependency_overrides[_get_classification_service] = lambda: service
    return TestClient(app)


@pytest.fixture(autouse=True)
def _czysc_overrides():
    """Po każdym teście czyścimy podstawienia DI — `app` jest współdzielony między testami."""
    yield
    app.dependency_overrides.clear()


# --- Trzy wyniki: każdy to 200 z kontraktem --------------------------------------


def test_matched_zwraca_200_z_pelnym_kontraktem():
    """`matched` -> 200: `option_id` w typie z żądania, uzasadnienie, pola audytu i metadane."""
    client = _client(_StubService(result=ClassificationResult.matched(option_id=21, label="OPT-1", rationale="Skarga na operatora.", **_EXCHANGE)))

    resp = client.post("/classify", json=_REQUEST)

    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "outcome":       "matched",
        "option_id":     21,
        "rationale":     "Skarga na operatora.",
        "error":         None,
        "system_prompt": "Wybierz...",
        "user_prompt":   "Streszczenia...",
        "raw_response":  '{"rationale": "...", "label": "OPT-1"}',
        "metadata":      {"model": "gpt-4o-mini", "usage": {"prompt_tokens": 1180, "completion_tokens": 58, "total_tokens": 1238}},
    }


def test_no_match_zwraca_200_z_null_i_uzasadnieniem():
    """`no_match` -> 200: `option_id` null, uzasadnienie jest, `error` null."""
    client = _client(_StubService(result=ClassificationResult.no_match(rationale="Nic nie pasuje.", **_EXCHANGE)))

    body = client.post("/classify", json=_REQUEST).json()

    assert (body["outcome"], body["option_id"], body["rationale"], body["error"]) == ("no_match", None, "Nic nie pasuje.", None)


def test_invalid_response_zwraca_200_z_przyczyna_i_audytem():
    """`invalid_response` -> 200 (nie 5xx — odpowiedź modelu była): przyczyna, prompty i surowa odpowiedź obecne."""
    raw = '{"rationale": "Skarga na'
    result = ClassificationResult.invalid(error="Niepoprawny JSON w odpowiedzi modelu: Unterminated string.", system_prompt="Wybierz...", user_prompt="Streszczenia...", response=LLMResult(text=raw, model="gpt-4o-mini", usage=_USAGE))

    resp = _client(_StubService(result=result)).post("/classify", json=_REQUEST)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["outcome"], body["option_id"], body["rationale"]) == ("invalid_response", None, None)
    assert body["error"].startswith("Niepoprawny JSON")
    assert (body["system_prompt"], body["user_prompt"], body["raw_response"]) == ("Wybierz...", "Streszczenia...", raw)


# --- Wejście: zamiana na domenę --------------------------------------------------


def test_opcje_trafiaja_do_serwisu_jako_domenowe_w_kolejnosci_i_typie():
    """Serwis dostaje streszczenia i `ClassificationOption` w kolejności żądania, z typem `id` z żądania."""
    service = _StubService(result=ClassificationResult.no_match(rationale="...", **_EXCHANGE))

    _client(service).post("/classify", json=_REQUEST)

    assert service.calls[0]["summaries"] == _REQUEST["summaries"]
    assert service.calls[0]["options"] == [
        ClassificationOption(id=21, name="Skargi", description="Skargi konsumentów.", examples="Skarga na rachunek."),
        ClassificationOption(id="numeracja-7", name="Numeracja", description="Przydział numeracji.", examples=None),
    ]


@pytest.mark.parametrize(
    "request_json",
    [
        {**_REQUEST, "options": []},                                                               # pusta lista opcji
        {**_REQUEST, "summaries": []},                                                             # pusta lista streszczeń
        {"options": _REQUEST["options"]},                                                          # brak streszczeń
        {**_REQUEST, "options": [{"id": True, "name": "X", "description": "Y"}]},                  # id bool (strict)
    ],
    ids=["puste-opcje", "puste-streszczenia", "brak-streszczen", "id-bool"],
)
def test_bledna_struktura_daje_422_bez_wolania_serwisu(request_json):
    """Błąd struktury żądania -> 422 z walidacji pydantic; serwis niewołany."""
    service = _StubService(result=ClassificationResult.no_match(rationale="...", **_EXCHANGE))

    resp = _client(service).post("/classify", json=request_json)

    assert resp.status_code == 422, resp.text
    assert service.calls == []


# --- Mapowanie wyjątków -> kody HTTP ----------------------------------------------


@pytest.mark.parametrize(
    "error, status_code",
    [
        (PromptTooLongError(91234, 90000), 413),   # za długi prompt — odrzucenie, nie ucinanie
        (LLMAuthError("401"), 500),                # zły klucz — nasz config
        (LLMResponseError("5xx"), 502),            # błąd dostawcy
        (LLMError("inny"), 502),                   # catch-all bazowy
        (LLMRateLimitError("429"), 503),           # dławienie
        (LLMTimeoutError("timeout"), 504),         # brak odpowiedzi w czasie
    ],
    ids=["413-prompt", "500-auth", "502-response", "502-llm-error", "503-rate-limit", "504-timeout"],
)
def test_wyjatek_mapowany_na_kod_z_detail_tekstem(error, status_code):
    """Wyjątek serwisu -> kod HTTP z kontraktu, `detail` tekstem (bez promptów w ciele)."""
    resp = _client(_StubService(error=error)).post("/classify", json=_REQUEST)

    assert resp.status_code == status_code, resp.text
    body = resp.json()
    assert set(body) == {"detail"}
    assert isinstance(body["detail"], str) and str(error) in body["detail"]


def test_zla_konfiguracja_dostawcy_daje_500(monkeypatch):
    """`LLMConfigError` z fabryki -> 500; serwis w ogóle nie powstaje."""
    def _boom() -> None:
        raise LLMConfigError("brak LLM_API_KEY")

    monkeypatch.setattr("app.routers.classify.get_llm_client", _boom)
    resp = TestClient(app).post("/classify", json=_REQUEST)

    assert resp.status_code == 500, resp.text
    assert "brak LLM_API_KEY" in resp.json()["detail"]


def test_x_request_id_wraca_w_naglowku():
    """Podany `X-Request-ID` wraca w odpowiedzi /classify (korelacja z dziennikiem klienta)."""
    client = _client(_StubService(result=ClassificationResult.no_match(rationale="...", **_EXCHANGE)))

    resp = client.post("/classify", json=_REQUEST, headers={"X-Request-ID": "dokus-rid-7"})

    assert resp.headers["X-Request-ID"] == "dokus-rid-7"


# --- DI: ustawienia docierają do serwisu -------------------------------------------
# Testy wyżej podstawiają cały serwis, więc nie widzą, CO funkcja DI przekazuje do konstruktora.
# Pokrętło z `.env`, którego router nie przekaże, cicho działałoby na defaulcie z kodu (TODO pkt 6).


def test_di_przekazuje_limity_llm_z_settings(monkeypatch):
    """`LLM_MAX_INPUT_CHARS` (budżet promptu) i `LLM_MAX_OUTPUT_TOKENS_CLASSIFY` z `Settings` trafiają do konstruktora serwisu."""
    calls: list[dict] = []
    monkeypatch.setattr("app.routers.classify.get_llm_client", FakeLLMClient)
    monkeypatch.setattr("app.routers.classify.ClassificationService", lambda client, **kwargs: calls.append(kwargs))

    # Wartości różne od defaultów — test nie przejdzie przypadkiem na wartościach domyślnych.
    _get_classification_service(settings=Settings(_env_file=None, llm_max_input_chars=12_345, llm_max_output_tokens_classify=321))

    assert calls == [{"max_prompt_chars": 12_345, "max_output_tokens": 321}]
