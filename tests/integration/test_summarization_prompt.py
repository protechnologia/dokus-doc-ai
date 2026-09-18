"""Testy integracyjne promptu streszczeń na realnym modelu — czy pola wypełniane są treścią z pisma.

Marker: integration_llm (+ parasol integration). Prompty (`SummarySystemPrompt`, `SummaryUserPrompt`)
jak w usłudze; klient z konfiguracji (`build_llm_client`), więc działa na `openai` i `ollama`.
Przy `LLM_PROVIDER=fake` albo niekompletnej konfiguracji -> SKIP (nie fail). Ollama z hosta —
nadpisanie ENV jak w `test_classification_prompt.py`.

Symetria z `test_classification_prompt.py`: tam wybór etykiety, tu treść pól. Sprawdzamy tylko pola
jednoznaczne w piśmie — Typ pisma (rdzeń „wnios") i Nadawcę (nazwisko). „Termin / data" i „Oczekiwana
akcja" świadomie pominięte: to znane słabości (CLAUDE.md, TODO pkt 9), test czerwieniłby się na nich
bez regresji. Warunek „nazwa pola i wartość w jednej linii", nie dokładny format „• " — kosmetyka
formatu mniejszego modelu (np. `**Typ pisma:**`) nie ma czerwienić testu; format pilnują testy
jednostkowe promptu.
"""

import asyncio

import pytest

from app.config import get_settings
from app.llm import LLMClient, LLMConfigError, build_llm_client
from app.summarization.prompt_system import SummarySystemPrompt
from app.summarization.prompt_user import SummaryUserPrompt

# Parasol `integration` + węższy `integration_llm` (uderzamy w realnego dostawcę LLM).
pytestmark = [pytest.mark.integration, pytest.mark.integration_llm]

# Jak w usłudze (`LLM_MAX_OUTPUT_TOKENS_SUMMARY`) — streszczenie nie może się urwać przed sprawdzanymi polami.
_MAX_TOKENS = get_settings().llm_max_output_tokens_summary

# Jednoznaczny wniosek: typ w nagłówku i w pierwszym zdaniu, nadawca z imienia i nazwiska.
_WNIOSEK = (
    "Jan Nowak\n"
    "ul. Lipowa 5\n"
    "00-001 Warszawa\n"
    "\n"
    "Urząd Skarbowy Warszawa-Śródmieście\n"
    "\n"
    "WNIOSEK\n"
    "o wydanie zaświadczenia o niezaleganiu w podatkach\n"
    "\n"
    "Wnoszę o wydanie zaświadczenia o niezaleganiu w podatkach, potrzebnego do złożenia oferty "
    "w przetargu. Zaświadczenie proszę przesłać na adres korespondencyjny.\n"
    "\n"
    "Z poważaniem\n"
    "Jan Nowak"
)


@pytest.fixture
def llm_client() -> LLMClient:
    """Realny klient LLM z konfiguracji, NOWY na każdy test (pętla zdarzeń — patrz `test_classification_prompt.py`); `fake` -> SKIP."""
    settings = get_settings()
    if settings.llm_provider == "fake":
        pytest.skip("LLM_PROVIDER=fake — test wymaga realnego dostawcy (openai / ollama)")
    try:
        return build_llm_client(settings)
    except LLMConfigError as exc:
        pytest.skip(f"niekompletna konfiguracja LLM: {exc}")


def _summarize(client: LLMClient, text: str) -> str:
    """Jedno wywołanie jak w usłudze (bez truncacji — krótkie pismo): prompty -> surowe streszczenie."""
    result = asyncio.run(
        client.complete(
            system      = SummarySystemPrompt().render(),
            user        = SummaryUserPrompt().render(text=text),
            max_tokens  = _MAX_TOKENS,
            temperature = 0.0,
        )
    )
    return result.text


def _has_field_line(summary: str, field: str, stem: str) -> bool:
    """Czy któraś linia zawiera naraz nazwę pola i rdzeń wartości (bez rozróżniania wielkości liter)."""
    return any(field in line.lower() and stem in line.lower() for line in summary.splitlines())


def test_typ_pisma_i_nadawca_z_tresci_pisma(llm_client):
    """Jednoznaczny wniosek -> linia „Typ pisma” z rdzeniem „wnios” i linia „Nadawca” z nazwiskiem nadawcy."""
    summary = _summarize(llm_client, _WNIOSEK)

    assert _has_field_line(summary, "typ pisma", "wnios"), summary   # rdzeń: „wniosek” / „wniosku” / „wnioskiem”
    assert _has_field_line(summary, "nadawca", "nowak"), summary     # nazwisko model przepisuje dosłownie
