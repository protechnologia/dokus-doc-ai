"""Testy jednostkowe SummarizationService (krok 2.4.1) — bez sieci.

Czyste helpery (`_truncate`/`_build_user_message`/`_build_metadata`) wołane wprost. Orkiestracja
`summarize` (async) na DWÓCH atrapach: nagrywającej (sprawdza, CO leci do `LLMClient` — system
prompt, max_tokens, user z tekstem) oraz `FakeLLMClient` (determinizm end-to-end). Brak
pytest-asyncio -> `asyncio.run` (jak w pozostałych testach projektu).
"""

import asyncio
import re

from app.llm import FakeLLMClient, LLMClient, LLMResult, LLMUsage
from app.summarization.service import _SYSTEM_PROMPT, EmptyInputError, SummarizationService


class _RecordingLLM(LLMClient):
    """Atrapa LLM: oddaje zadany `LLMResult` i nagrywa argumenty każdego `complete`."""

    def __init__(self, *, text: str = "Streszczenie.", model: str = "rec-model") -> None:
        self.calls: list[dict] = []
        self._text = text
        self._model = model

    async def complete(self, *, user, system=None, max_tokens=None, temperature=0.0) -> LLMResult:
        self.calls.append({"user": user, "system": system, "max_tokens": max_tokens, "temperature": temperature})
        return LLMResult(text=self._text, model=self._model, usage=LLMUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15))


# --- _truncate: przycięcie wejścia pod okno modelu -------------------------------


def test_truncate_ponizej_limitu_bez_zmian():
    """Tekst krótszy niż limit -> zwracany bez zmian, flaga truncated False."""
    svc = SummarizationService(None, max_input_chars=10)
    assert svc._truncate("krótki") == ("krótki", False)


def test_truncate_dokladnie_na_limicie_bez_zmian():
    """Granica: długość == limit NIE jest cięta (tniemy dopiero > limit)."""
    svc = SummarizationService(None, max_input_chars=5)
    assert svc._truncate("12345") == ("12345", False)


def test_truncate_powyzej_limitu_tnie_poczatek():
    """Tekst dłuższy niż limit -> brany jest POCZĄTEK (nie chunking), flaga truncated True."""
    svc = SummarizationService(None, max_input_chars=5)
    body, truncated = svc._truncate("1234567890")
    assert body == "12345"
    assert truncated is True


# --- _build_user_message: ramka + dokument ---------------------------------------


def test_build_user_message_wstawia_tekst_w_szablon():
    """Wiadomość usera = ramka „Streść poniższy dokument:” + treść dokumentu."""
    msg = SummarizationService._build_user_message("Pismo w sprawie podatku")
    assert "Pismo w sprawie podatku" in msg
    assert msg.startswith("Streść poniższy dokument:")


# --- _build_metadata: złożenie z LLMResult ---------------------------------------


def test_build_metadata_przepisuje_model_usage_i_flagi():
    """Metadane biorą model i usage z `LLMResult` oraz przekazaną długość wejścia i flagę truncacji."""
    result = LLMResult(text="...", model="gpt-4o-mini", usage=LLMUsage(prompt_tokens=100, completion_tokens=20, total_tokens=120))
    meta = SummarizationService._build_metadata(812, True, result)
    assert meta.model == "gpt-4o-mini"
    assert meta.input_chars == 812
    assert meta.truncated is True
    assert meta.usage.total_tokens == 120


# --- summarize: orkiestracja (co leci do LLM) ------------------------------------


def test_summarize_przekazuje_system_prompt_i_max_tokens():
    """Domena dokłada system prompt (rola + format wypunktowania) i `max_tokens`, a dokument ląduje w userze."""
    llm = _RecordingLLM()
    svc = SummarizationService(llm, max_output_tokens=321)

    asyncio.run(svc.summarize(text="Pismo z Urzędu Skarbowego o zaległości"))

    call = llm.calls[0]
    assert call["system"] is not None
    assert "dekretując" in call["system"]          # rola pod dekretację
    assert "• Typ pisma" in call["system"]         # narzucony format: wypunktowanie pól
    assert call["max_tokens"] == 321
    assert call["temperature"] == 0.0
    assert "Pismo z Urzędu Skarbowego" in call["user"]   # dokument w wiadomości usera


def test_system_prompt_zada_wypunktowania_wszystkich_pol():
    """Kontrakt formatu: pięć pól, każde jako punkt „• ”. Bez tego streszczenie traci strukturę."""
    for pole in ("Typ pisma", "Nadawca", "Czego dotyczy", "Termin / data", "Oczekiwana akcja"):
        assert f"• {pole}" in _SYSTEM_PROMPT


def test_system_prompt_nie_numeruje_wlasnego_opisu_formatu():
    """Strażnik przed nawrotem defektu: numeracja W OPISIE formatu przecieka do WYJŚCIA.

    Historyczny błąd (2026-07-08): opis brzmiał „1. streszczenie… 2. wypunktowanie…”, a Bielik
    brał tę numerację za wzór odpowiedzi i zwracał listę `1.`–`9.` zamiast punktów. Model
    naśladuje najbardziej konkretny wzorzec w prompcie — a numerowana lista nim jest.
    """
    linie = _SYSTEM_PROMPT.splitlines()
    numerowane = [l for l in linie if re.match(r"^\s*\d+[.)]\s", l)]
    assert not numerowane, f"opis formatu znów zawiera numerację: {numerowane}"


def test_system_prompt_nie_obiecuje_akapitu():
    """Akapit otwierający świadomie usunięty (patrz macierz pomiarów w service.py).

    Model go nie oddaje bez przykładu jako tury `assistant`; obietnica w prompcie bez pokrycia
    w wyjściu to gorzej niż jej brak — dokumentacja i kontrakt zaczynają kłamać.
    """
    assert "akapit" not in _SYSTEM_PROMPT.lower()
    assert "streszczenie naturalnym językiem" not in _SYSTEM_PROMPT


def test_summarize_metadane_z_oryginalnej_dlugosci_i_truncacja():
    """input_chars liczone na ORYGINALE (po strip), nie po przycięciu; przy nadmiarze truncated True."""
    llm = _RecordingLLM()
    svc = SummarizationService(llm, max_input_chars=5)

    result = asyncio.run(svc.summarize(text="  1234567890  "))   # po strip: 10 znaków

    assert result.metadata.input_chars == 10
    assert result.metadata.truncated is True
    # Do LLM poszło tylko 5 znaków treści (ramka + 5), nie całe 10.
    assert len(llm.calls[0]["user"]) < len("Streść poniższy dokument:\n\n") + 10


def test_summarize_pusty_input_rzuca_empty():
    """Wejście z samych białych znaków -> `EmptyInputError` (mapowane na 422 w endpointcie)."""
    svc = SummarizationService(_RecordingLLM())
    try:
        asyncio.run(svc.summarize(text="   \n\t "))
        assert False, "oczekiwano EmptyInputError"
    except EmptyInputError:
        pass


# --- summarize: end-to-end na FakeLLMClient --------------------------------------


def test_summarize_na_fake_llm_deterministycznie():
    """FakeLLMClient echo-uje usera -> streszczenie ma prefiks Fake + tekst dokumentu; metadane spójne."""
    svc = SummarizationService(FakeLLMClient())
    result = asyncio.run(svc.summarize(text="Pismo do dekretacji w sprawie podatku"))

    assert "[FAKE-LLM]" in result.summary
    assert "dekretacji" in result.summary
    assert result.metadata.model == "fake-echo"
    assert result.metadata.truncated is False
    assert result.metadata.input_chars == len("Pismo do dekretacji w sprawie podatku")
    assert result.metadata.usage.total_tokens > 0
