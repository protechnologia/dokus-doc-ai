"""Testy jednostkowe kontraktu wyjscia `POST /classify` — ksztalt `ClassifyResponse`, bez sieci.

Straznik zamrozonego kontraktu (README "POST /classify"): nazwy pol, typ `option_id` w JSON
i dozwolone wartosci `outcome`. Spojnosc pol z `outcome` zapewnia serwis przy skladaniu wyniku —
tu jej nie walidujemy.
"""

import pytest
from pydantic import ValidationError

from app.llm import LLMUsage
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
