"""Testy jednostkowe ClassificationService (krok 7) — bez sieci.

Treść promptów, schemat, etykiety i parser mają własne testy (`test_classification_*`) — tu tylko
to, jak serwis ich używa: co leci do `LLMClient`, jak odpowiedź zamienia się na jeden z trzech
wyników, budżet promptu i log. Orkiestracja na atrapie nagrywającej argumenty (`_RecordingLLM`)
oraz `FakeLLMClient` (determinizm end-to-end). Brak pytest-asyncio -> `asyncio.run`.
"""

import asyncio
import logging

import pytest

from app.classification.exception import PromptTooLongError
from app.classification.prompt_system import ClassificationSystemPrompt
from app.classification.service import ClassificationService
from app.classification.service_labels import ClassificationOption, OptionLabeler
from app.classification.service_schema import build_response_schema
from app.llm import FakeLLMClient, LLMClient, LLMResult, LLMTimeoutError, LLMUsage

# Streszczenie z charakterystycznym zwrotem — test logu sprawdza, że treść pisma do niego nie trafia.
_SUMMARY = "• Typ pisma: skarga\n• Czego dotyczy: naliczenie opłat przez operatora Telkomex"

# Katalog z `id` liczbowym i tekstowym — typ ma wrócić taki, jak przyszedł.
_OPTIONS = [
    ClassificationOption(id=21, name="Skargi konsumenckie", description="Skargi na dostawców usług."),
    ClassificationOption(id="db-7781", name="Kadry", description="Sprawy pracownicze.", examples="Wniosek o urlop."),
    ClassificationOption(id=23, name="Numeracja", description="Przydział zasobów numeracji."),
]


class _RecordingLLM(LLMClient):
    """Atrapa LLM: oddaje zadany surowy tekst (albo rzuca zadany wyjątek) i nagrywa argumenty `complete`."""

    def __init__(self, *, text: str = '{"rationale": "Skarga na operatora.", "label": "OPT-1"}', error: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self._text  = text
        self._error = error

    async def complete(self, *, user, system=None, max_tokens=None, temperature=0.0, json_schema=None) -> LLMResult:
        self.calls.append({"user": user, "system": system, "max_tokens": max_tokens, "temperature": temperature, "json_schema": json_schema})
        if self._error is not None:
            raise self._error
        return LLMResult(text=self._text, model="rec-model", usage=LLMUsage(prompt_tokens=900, completion_tokens=40, total_tokens=940))


def _classify(llm: LLMClient, **service_kwargs):
    """Pomocniczo: jedno `classify` na stałym streszczeniu i katalogu."""
    return asyncio.run(ClassificationService(llm, **service_kwargs).classify(summaries=[_SUMMARY], options=_OPTIONS))


def _prompt_chars() -> int:
    """Pomocniczo: długość całego promptu dla stałego wejścia — zmierzona na nagranym wywołaniu."""
    llm = _RecordingLLM()
    _classify(llm)
    return len(llm.calls[0]["system"]) + len(llm.calls[0]["user"])


# --- classify: co leci do LLM ----------------------------------------------------


def test_classify_przekazuje_parametry_wywolania():
    """Do modelu: prompt systemowy, streszczenie w userze, `temperature=0`, `max_tokens` z konstruktora, schemat z enum etykiet w kolejności promptu."""
    llm = _RecordingLLM()

    _classify(llm, max_output_tokens=321)

    assert len(llm.calls) == 1                                    # jedno żądanie = jedno wywołanie modelu
    call = llm.calls[0]
    assert call["system"] == ClassificationSystemPrompt().render()
    assert _SUMMARY in call["user"]
    assert call["temperature"] == 0.0
    assert call["max_tokens"] == 321
    assert call["json_schema"] == build_response_schema(["OPT-1", "OPT-2", "OPT-3", "OPT-00"])


def test_classify_audyt_to_dokladnie_to_co_wyslano_i_odebrano():
    """Prompty i surowa odpowiedź w wyniku = dokładnie te wysłane do modelu i od niego odebrane (audyt klienta)."""
    raw = '{"rationale": "Skarga na operatora.", "label": "OPT-1"}'
    llm = _RecordingLLM(text=raw)

    result = _classify(llm)

    assert result.system_prompt == llm.calls[0]["system"]
    assert result.user_prompt == llm.calls[0]["user"]
    assert result.raw_response == raw
    assert (result.model, result.usage.completion_tokens) == ("rec-model", 40)


# --- classify: odpowiedź -> wynik ------------------------------------------------


@pytest.mark.parametrize(
    "label, option_id",
    [
        ("OPT-1", 21),          # id liczbowe zostaje int
        ("OPT-2", "db-7781"),   # id tekstowe zostaje str
        ("OPT-3", 23),
    ],
)
def test_classify_etykieta_opcji_daje_matched_z_id_tej_opcji(label, option_id):
    """`OPT-n` -> `matched` z `id` n-tej opcji w typie z wejścia, etykietą i uzasadnieniem."""
    result = _classify(_RecordingLLM(text=f'{{"rationale": "Uzasadnienie.", "label": "{label}"}}'))

    assert (result.outcome, result.option_id, result.label, result.rationale, result.error) == ("matched", option_id, label, "Uzasadnienie.", None)


def test_classify_opt_00_daje_no_match():
    """`OPT-00` -> `no_match`: bez `id`, z uzasadnieniem, bez błędu."""
    result = _classify(_RecordingLLM(text='{"rationale": "Żadna opcja nie obejmuje dotacji.", "label": "OPT-00"}'))

    assert (result.outcome, result.option_id, result.label, result.rationale, result.error) == ("no_match", None, "OPT-00", "Żadna opcja nie obejmuje dotacji.", None)


@pytest.mark.parametrize(
    "raw, error_start",
    [
        ("To nie jest JSON.", "Niepoprawny JSON w odpowiedzi modelu"),                     # śmieci
        ('{"rationale": "Skarga na operatora, który nalicz', "Niepoprawny JSON w odpowiedzi modelu"),   # urwany limitem max_tokens
        ("", "Pusta odpowiedź modelu"),                                                     # odmowa / filtr treści
        ('{"rationale": "Uzasadnienie."}', "Brak pola 'label'"),                            # brak pola
        ('{"rationale": "Uzasadnienie.", "label": "OPT-7"}', "Etykieta 'OPT-7' spoza nadanych"),   # zaplecze bez enum
    ],
)
def test_classify_bezuzyteczna_odpowiedz_daje_invalid_response(raw, error_start):
    """Każda wada odpowiedzi -> `invalid_response` z przyczyną (nie wyjątek); bez `id` i uzasadnienia; audyt kompletny."""
    result = _classify(_RecordingLLM(text=raw))

    assert (result.outcome, result.option_id, result.label, result.rationale) == ("invalid_response", None, None, None)
    assert result.error.startswith(error_start)
    assert result.raw_response == raw                  # surowa odpowiedź dosłownie
    assert result.system_prompt and result.user_prompt # prompty są także przy porażce


def test_classify_blad_llm_propaguje():
    """Błąd LLM (odpowiedzi nie było) propaguje — to nie `invalid_response`; router mapuje go na 5xx."""
    with pytest.raises(LLMTimeoutError):
        _classify(_RecordingLLM(error=LLMTimeoutError("timeout")))


def test_classify_na_fake_wybiera_pierwsza_opcje():
    """`FakeLLMClient` bierze pierwszą wartość enum (`OPT-1`) -> `matched` z `id` pierwszej opcji (dev bez sieci)."""
    result = _classify(FakeLLMClient())

    assert (result.outcome, result.option_id, result.label) == ("matched", 21, "OPT-1")
    assert result.model == "fake-echo"


# --- budżet promptu --------------------------------------------------------------


def test_classify_prompt_o_znak_za_dlugi_rzuca_bez_wywolania_modelu():
    """Prompt o znak ponad budżet -> `PromptTooLongError` z obiema liczbami; model niewołany."""
    llm = _RecordingLLM()
    chars = _prompt_chars()

    with pytest.raises(PromptTooLongError) as exc_info:
        _classify(llm, max_prompt_chars=chars - 1)

    assert llm.calls == []
    assert (exc_info.value.prompt_chars, exc_info.value.max_chars) == (chars, chars - 1)
    assert str(exc_info.value) == f"Prompt klasyfikacji ma {chars} znaków, limit {chars - 1}."


def test_classify_prompt_rowny_budzetowi_wola_model():
    """Prompt dokładnie równy budżetowi mieści się -> jedno wywołanie modelu."""
    llm = _RecordingLLM()

    _classify(llm, max_prompt_chars=_prompt_chars())

    assert len(llm.calls) == 1


def test_check_budget_liczy_prompt_systemowy_i_uzytkownika():
    """Budżet obejmuje OBA prompty: każdy z osobna się mieści, razem nie -> wyjątek."""
    svc = ClassificationService(_RecordingLLM(), max_prompt_chars=10)

    svc._check_budget("x" * 4, "y" * 6)   # równo budżetowi — bez wyjątku
    with pytest.raises(PromptTooLongError):
        svc._check_budget("x" * 5, "y" * 6)


# --- _interpret: czysta zamiana odpowiedzi na wynik ------------------------------


def test_interpret_bez_llm():
    """`_interpret` działa bez klienta LLM: odpowiedź + etykiety -> wynik (czysta funkcja)."""
    response = LLMResult(text='{"rationale": "Sprawy kadrowe.", "label": "OPT-2"}', model="gpt-4o-mini")

    result = ClassificationService._interpret(OptionLabeler(_OPTIONS), response, "system", "user")

    assert (result.outcome, result.option_id, result.system_prompt, result.user_prompt) == ("matched", "db-7781", "system", "user")


# --- log: wynik i etykieta, bez treści pisma -------------------------------------


def test_log_poprawnego_wyboru_info_bez_tresci_pisma(caplog):
    """Wybór -> INFO z wynikiem i etykietą; ani streszczenie, ani uzasadnienie nie trafiają do logu."""
    with caplog.at_level(logging.INFO, logger="app.classification.service"):
        _classify(_RecordingLLM(text='{"rationale": "Skarga na operatora Telkomex.", "label": "OPT-1"}'))

    messages = [r.getMessage() for r in caplog.records if r.name == "app.classification.service"]
    assert len(messages) == 1
    assert caplog.records[-1].levelno == logging.INFO
    assert "matched" in messages[0] and "OPT-1" in messages[0]
    assert "Telkomex" not in messages[0]


def test_log_invalid_response_warning_z_przyczyna(caplog):
    """`invalid_response` -> WARNING z przyczyną i licznikiem tokenów (urwanie widać po completion_tokens = limit)."""
    with caplog.at_level(logging.INFO, logger="app.classification.service"):
        _classify(_RecordingLLM(text="To nie jest JSON."), max_output_tokens=321)

    records = [r for r in caplog.records if r.name == "app.classification.service"]
    assert [r.levelno for r in records] == [logging.WARNING]
    message = records[0].getMessage()
    assert "Niepoprawny JSON w odpowiedzi modelu" in message
    assert "40/321" in message
