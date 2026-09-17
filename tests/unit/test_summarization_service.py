"""Testy jednostkowe SummarizationService (krok 2.4.1) — bez sieci.

Czyste helpery (`_leading_whitespace_len`/`_shift_parts`/`_build_metadata`) wołane wprost.
Treść promptów testowana osobno w `test_summarization_prompt_system.py` / `_prompt_user.py`, a sam algorytm cięcia
(początek / środek / koniec) w `test_summarization_truncation.py` — tu tylko to, jak serwis ich używa. Orkiestracja
`summarize` (async) na DWÓCH atrapach: nagrywającej (sprawdza, CO leci do `LLMClient` — system
prompt, max_tokens, user z tekstem) oraz `FakeLLMClient` (determinizm end-to-end). Brak
pytest-asyncio -> `asyncio.run` (jak w pozostałych testach projektu).
"""

import asyncio

from app.llm import FakeLLMClient, LLMClient, LLMResult, LLMUsage
from app.summarization.service import EmptyInputError, SummarizationService
from app.summarization.truncation import MARKER_RESERVE, OMISSION_MARKER, TextPart, TextParts, TruncationResult

# Budżet testowy: na treść zostaje 100 znaków (proporcje w % = budżety w znakach).
_MAX = MARKER_RESERVE + 100


class _RecordingLLM(LLMClient):
    """Atrapa LLM: oddaje zadany `LLMResult` i nagrywa argumenty każdego `complete`."""

    def __init__(self, *, text: str = "Streszczenie.", model: str = "rec-model") -> None:
        self.calls: list[dict] = []
        self._text = text
        self._model = model

    async def complete(self, *, user, system=None, max_tokens=None, temperature=0.0) -> LLMResult:
        self.calls.append({"user": user, "system": system, "max_tokens": max_tokens, "temperature": temperature})
        return LLMResult(text=self._text, model=self._model, usage=LLMUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15))


# --- _leading_whitespace_len / _shift_parts: offsety względem tekstu klienta ------


def test_leading_whitespace_len_liczy_tylko_wiodace():
    """Liczone są wyłącznie wiodące białe znaki (to one przesuwają offsety); końcowe nie."""
    assert SummarizationService._leading_whitespace_len("  \nPismo  ") == 3
    assert SummarizationService._leading_whitespace_len("Pismo") == 0


def test_shift_parts_przesuwa_zakresy_i_pomija_wylaczone():
    """Zakresy włączonych części przesunięte o offset; wyłączona (bez zakresu) i None bez zmian."""
    parts = TextParts(head=TextPart(percent=45, start=0, end=45), middle=TextPart(percent=0), tail=TextPart(percent=55, start=945, end=1000))

    shifted = SummarizationService._shift_parts(parts, 3)

    assert shifted.head == TextPart(percent=45, start=3, end=48)
    assert shifted.middle == TextPart(percent=0)
    assert shifted.tail == TextPart(percent=55, start=948, end=1003)
    assert SummarizationService._shift_parts(None, 3) is None


# --- _build_metadata: złożenie z LLMResult ---------------------------------------


def test_build_metadata_przepisuje_model_usage_i_trunkacje():
    """Metadane: model/usage z `LLMResult`, długość wejścia, flaga i długość wysłana z trunkacji, zakresy przesunięte."""
    result = LLMResult(text="...", model="gpt-4o-mini", usage=LLMUsage(prompt_tokens=100, completion_tokens=20, total_tokens=120))
    parts = TextParts(head=TextPart(percent=45, start=0, end=45), middle=TextPart(percent=0), tail=TextPart(percent=55, start=945, end=1000))
    cut = TruncationResult(text="x" * 150, truncated=True, parts=parts)

    meta = SummarizationService._build_metadata(1000, cut, 2, result)

    assert meta.model == "gpt-4o-mini"
    assert meta.input_chars == 1000
    assert meta.truncated is True
    assert meta.usage.total_tokens == 120
    assert meta.sent_chars == 150                                 # długość tekstu dla modelu, nie oryginału
    assert meta.parts.head == TextPart(percent=45, start=2, end=47)   # przesunięte o offset


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


def test_summarize_metadane_z_oryginalnej_dlugosci_i_truncacja():
    """input_chars na ORYGINALE (po strip); do LLM idzie tekst przycięty ze znacznikami; sent_chars <= limit."""
    llm = _RecordingLLM()
    svc = SummarizationService(llm, max_input_chars=_MAX)
    text = "".join(str(i % 10) for i in range(1000))

    result = asyncio.run(svc.summarize(text=text))

    meta = result.metadata
    assert meta.input_chars == 1000
    assert meta.truncated is True
    assert meta.sent_chars <= _MAX
    assert meta.parts.middle == TextPart(percent=20, start=490, end=510)   # domyślne 45/35 -> środek 20
    # Do LLM poszedł tekst przycięty (ze znacznikiem), a nie całe 1000 znaków.
    assert OMISSION_MARKER in llm.calls[0]["user"]
    assert len(llm.calls[0]["user"]) == len("Streść poniższy dokument:\n\n") + meta.sent_chars


def test_summarize_bez_trunkacji_parts_none():
    """Tekst w budżecie -> truncated False, `parts` None, `sent_chars` == `input_chars`."""
    svc = SummarizationService(_RecordingLLM(), max_input_chars=_MAX)

    result = asyncio.run(svc.summarize(text="Krótkie pismo"))

    assert result.metadata.truncated is False
    assert result.metadata.parts is None
    assert result.metadata.sent_chars == result.metadata.input_chars == len("Krótkie pismo")


def test_summarize_przekazuje_proporcje_do_trunkacji():
    """Własne proporcje (45/55) trafiają do trunkatora: środek wyłączony, koniec 55%."""
    svc = SummarizationService(_RecordingLLM(), max_input_chars=_MAX)

    result = asyncio.run(svc.summarize(text="x" * 1000, head_percent=45, tail_percent=55))

    assert result.metadata.parts.middle == TextPart(percent=0)
    assert result.metadata.parts.tail == TextPart(percent=55, start=945, end=1000)


def test_summarize_offsety_wzgledem_tekstu_klienta():
    """Wiodące białe znaki zdjęte przez strip przesuwają offsety: wycinek z tekstu KLIENTA = zachowany fragment."""
    llm = _RecordingLLM()
    svc = SummarizationService(llm, max_input_chars=_MAX)
    body = "".join(str(i % 10) for i in range(1000))
    text = "  \n" + body   # 3 wiodące białe znaki

    result = asyncio.run(svc.summarize(text=text))

    head = result.metadata.parts.head
    assert (head.start, head.end) == (3, 48)
    assert text[head.start:head.end] == body[0:45]   # offsety wskazują w tekście, który ma klient
    assert body[0:45] in llm.calls[0]["user"]


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
    assert result.metadata.sent_chars == result.metadata.input_chars
    assert result.metadata.usage.total_tokens > 0
