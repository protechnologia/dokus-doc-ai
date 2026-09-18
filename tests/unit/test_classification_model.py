"""Testy jednostkowe wyniku domenowego klasyfikacji (`ClassificationResult`) — bez I/O.

Obietnica jednostki: spójność pól z `outcome` wynika z konstruktorów wariantów (tabela z README
„POST /classify"), a pola wymiany z modelem są przepisane dosłownie przy każdym wariancie.
"""

from app.classification.model import ClassificationResult
from app.classification.service_labels import NO_MATCH_LABEL
from app.llm import LLMResult, LLMUsage

_SYSTEM = "Wybierz dla dokumentu dokładnie jedną pozycję z listy."
_USER   = "Streszczenia plików dokumentu (kolejność bez znaczenia): ..."
_USAGE  = LLMUsage(prompt_tokens=1100, completion_tokens=48, total_tokens=1148)


def _response(text: str) -> LLMResult:
    """Pomocniczo: odpowiedź modelu z zadanym surowym tekstem."""
    return LLMResult(text=text, model="gpt-4o-mini", usage=_USAGE)


def _assert_exchange(result: ClassificationResult, raw: str) -> None:
    """Pomocniczo: pola wymiany z modelem przepisane dosłownie."""
    assert result.model == "gpt-4o-mini"
    assert result.usage == _USAGE
    assert result.system_prompt == _SYSTEM
    assert result.user_prompt == _USER
    assert result.raw_response == raw


def test_matched_wypelnia_wybor_i_uzasadnienie_bez_bledu():
    """`matched` -> `option_id`, etykieta i uzasadnienie; `error` None; wymiana z modelem przepisana."""
    raw = '{"rationale": "Skarga na operatora.", "label": "OPT-1"}'

    result = ClassificationResult.matched(option_id=21, label="OPT-1", rationale="Skarga na operatora.", system_prompt=_SYSTEM, user_prompt=_USER, response=_response(raw))

    assert (result.outcome, result.option_id, result.label, result.rationale, result.error) == ("matched", 21, "OPT-1", "Skarga na operatora.", None)
    _assert_exchange(result, raw)


def test_matched_zachowuje_typ_id():
    """`id` tekstowe wyglądające jak liczba zostaje stringiem (kontrakt: zwrot w typie z wejścia)."""
    result = ClassificationResult.matched(option_id="21", label="OPT-1", rationale="...", system_prompt=_SYSTEM, user_prompt=_USER, response=_response("{}"))

    assert result.option_id == "21"


def test_no_match_wpisuje_opt_00_i_zostawia_uzasadnienie():
    """`no_match` -> etykieta `OPT-00` wpisana przez konstruktor, uzasadnienie jest, `option_id` i `error` None."""
    raw = '{"rationale": "Żadna opcja nie obejmuje dotacji.", "label": "OPT-00"}'

    result = ClassificationResult.no_match(rationale="Żadna opcja nie obejmuje dotacji.", system_prompt=_SYSTEM, user_prompt=_USER, response=_response(raw))

    assert (result.outcome, result.option_id, result.label, result.rationale, result.error) == ("no_match", None, NO_MATCH_LABEL, "Żadna opcja nie obejmuje dotacji.", None)
    _assert_exchange(result, raw)


def test_invalid_niesie_tylko_przyczyne_i_surowa_odpowiedz():
    """`invalid` -> sama przyczyna; `option_id`, etykieta i uzasadnienie None; surowa odpowiedź dosłownie (bez strip)."""
    raw = '  {"rationale": "Skarga na'   # urwana, z wiodącymi spacjami — audyt dostaje ją dokładnie taką

    result = ClassificationResult.invalid(error="Niepoprawny JSON w odpowiedzi modelu: Unterminated string.", system_prompt=_SYSTEM, user_prompt=_USER, response=_response(raw))

    assert (result.outcome, result.option_id, result.label, result.rationale) == ("invalid_response", None, None, None)
    assert result.error == "Niepoprawny JSON w odpowiedzi modelu: Unterminated string."
    _assert_exchange(result, raw)
