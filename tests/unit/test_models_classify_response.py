"""Testy jednostkowe kontraktu wyjscia `POST /classify` — ksztalt `ClassifyResponse`, bez sieci.

Straznik zamrozonego kontraktu (README "POST /classify"): nazwy pol, typ `option_id` w JSON
i dozwolone wartosci `outcome`. Spojnosc pol z `outcome` zapewnia serwis przy skladaniu wyniku —
tu jej nie walidujemy. Osobno (sekcja `from_result`): mapowanie trzech wynikow domenowych na
kontrakt — tabela "ktore pola sa wypelnione" z README, wprost z konstruktorow `ClassificationResult`.
"""

import pytest
from pydantic import ValidationError

from app.classification import ClassificationResult
from app.llm import LLMResult, LLMUsage
from app.models import ClassifyMetadata, ClassifyResponse


def _response(**overrides) -> ClassifyResponse:
    """Pomocniczo: odpowiedz `matched`; `overrides` podmienia pojedyncze pola."""
    fields = {
        "outcome":       "matched",
        "option_id":     21,
        "rationale":     "Skarga konsumenta na operatora odpowiada opisowi opcji.",
        "error":         None,
        "system_prompt": "Jestes asystentem...",
        "user_prompt":   "Streszczenia dokumentu: ...",
        "raw_response":  '{"rationale": "...", "option": "OPT-1"}',
        "metadata":      ClassifyMetadata(model="gpt-4o-mini", usage=LLMUsage(prompt_tokens=1180, completion_tokens=58, total_tokens=1238)),
        **overrides,
    }
    return ClassifyResponse(**fields)


def test_pola_odpowiedzi_zgodne_z_kontraktem():
    """Nazwy pol odpowiedzi i metadanych = zamrozony kontrakt (straznik przed przypadkowa zmiana nazwy)."""
    dumped = _response().model_dump()
    assert set(dumped) == {"outcome", "option_id", "rationale", "error", "system_prompt", "user_prompt", "raw_response", "metadata"}
    assert set(dumped["metadata"]) == {"model", "usage"}
    assert set(dumped["metadata"]["usage"]) == {"prompt_tokens", "completion_tokens", "total_tokens"}


def test_option_id_string_zostaje_stringiem_w_json():
    """`option_id` tekstowy (takze cyfrowy) serializuje sie jako string, nie liczba."""
    assert '"option_id":"21"' in _response(option_id="21").model_dump_json()


def test_nieznany_outcome_odrzucony():
    """`outcome` spoza `matched` / `no_match` / `invalid_response` -> `ValidationError`."""
    with pytest.raises(ValidationError):
        _response(outcome="error")


# --- from_result: wynik domenowy -> kontrakt ---------------------------------------

_USAGE    = LLMUsage(prompt_tokens=1180, completion_tokens=58, total_tokens=1238)
_EXCHANGE = {"system_prompt": "Wybierz...", "user_prompt": "Streszczenia...", "response": LLMResult(text='{"rationale": "...", "label": "OPT-1"}', model="gpt-4o-mini", usage=_USAGE)}


@pytest.mark.parametrize(
    "result, expected",
    [
        # outcome            option_id   rationale              error
        (ClassificationResult.matched(option_id=21, label="OPT-1", rationale="Skarga.", **_EXCHANGE), ("matched", 21, "Skarga.", None)),
        (ClassificationResult.no_match(rationale="Nic nie pasuje.", **_EXCHANGE),                      ("no_match", None, "Nic nie pasuje.", None)),
        (ClassificationResult.invalid(error="Pusta odpowiedź modelu.", **_EXCHANGE),                  ("invalid_response", None, None, "Pusta odpowiedź modelu.")),
    ],
    ids=["matched", "no_match", "invalid_response"],
)
def test_from_result_pola_wyniku_jak_w_tabeli_readme(result, expected):
    """Trzy wyniki domenowe -> `outcome` / `option_id` / `rationale` / `error` jak w tabeli README."""
    response = ClassifyResponse.from_result(result)

    assert (response.outcome, response.option_id, response.rationale, response.error) == expected


def test_from_result_przepisuje_audyt_i_metadane():
    """Oba prompty i surowa odpowiedź dosłownie, `model` i `usage` do `metadata`."""
    response = ClassifyResponse.from_result(ClassificationResult.matched(option_id="db-7781", label="OPT-1", rationale="Skarga.", **_EXCHANGE))

    assert (response.system_prompt, response.user_prompt, response.raw_response) == ("Wybierz...", "Streszczenia...", '{"rationale": "...", "label": "OPT-1"}')
    assert response.metadata == ClassifyMetadata(model="gpt-4o-mini", usage=_USAGE)
    assert response.option_id == "db-7781"   # typ id z wejścia przechodzi przez mapowanie
