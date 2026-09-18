"""Testy integracyjne promptu klasyfikacji na realnym modelu — przypadek pozytywny i siatka bezpieczeństwa.

Marker: integration_llm (+ parasol integration). Prompty (`ClassificationSystemPrompt`,
`ClassificationUserPrompt`), schemat (`build_response_schema`) i etykiety (`OptionLabeler`) jak
w usłudze; klient z konfiguracji (`build_llm_client`), więc działa na `openai` i `ollama`.
Przy `LLM_PROVIDER=fake` albo niekompletnej konfiguracji -> SKIP (nie fail).

Ollama z hosta: `.env` wskazuje adres z sieci compose (`http://ollama:11434/v1`), więc nadpisz ENV:
    LLM_PROVIDER=ollama LLM_BASE_URL=http://localhost:11434/v1 \
    LLM_MODEL=SpeakLeash/bielik-4.5b-v3.0-instruct:Q8_0 LLM_TIMEOUT_SECONDS=300 pytest -m integration_llm
(CPU: ok. 1 min na wywołanie — domyślne 60 s nie wystarcza.)

Syntetyczny mini-katalog i przypadki oczywiste: to NIE jest pomiar jakości wyboru (krok 11), tylko
dowód, że prompt + schemat prowadzą realny model do właściwej etykiety w obu kierunkach. Gdy
powstanie `ClassificationService` (krok 7), krok 10 przenosi te przypadki na poziom serwisu.
"""

import asyncio
import json

import pytest

from app.classification.prompt_system import ClassificationSystemPrompt
from app.classification.prompt_user import ClassificationUserPrompt
from app.classification.service import DEFAULT_MAX_OUTPUT_TOKENS
from app.classification.service_labels import ClassificationOption, OptionLabeler
from app.classification.service_schema import LABEL_FIELD, RATIONALE_FIELD, build_response_schema
from app.config import get_settings
from app.llm import LLMClient, LLMConfigError, build_llm_client

# Parasol `integration` + węższy `integration_llm` (uderzamy w realnego dostawcę LLM).
pytestmark = [pytest.mark.integration, pytest.mark.integration_llm]

# Syntetyczny katalog (przykład z README „POST /classify"): id liczbowe i tekstowe.
_OPTIONS = [
    ClassificationOption(id=21, name="Skargi i interwencje konsumenckie", description="Skargi konsumentów na dostawców usług telekomunikacyjnych i pocztowych, wnioski o interwencję.", examples="Skarga na zawyżony rachunek; reklamacja nieuwzględniona przez operatora."),
    ClassificationOption(id=22, name="Rynek pocztowy", description="Sprawy operatorów pocztowych: wpisy do rejestru, sprawozdania, kontrole.", examples=None),
    ClassificationOption(id="numeracja-7", name="Numeracja", description="Przydział i rezerwacja zasobów numeracji.", examples=None),
]


@pytest.fixture
def llm_client() -> LLMClient:
    """Realny klient LLM z konfiguracji, NOWY na każdy test; `fake` albo niekompletna konfiguracja -> SKIP.

    Nie `scope="module"`: `AsyncOpenAI` wiąże pulę połączeń z pętlą zdarzeń pierwszego żądania,
    a każdy test woła `asyncio.run` (nowa pętla) — wspólny klient wywala drugi test błędem
    „Event loop is closed" (zmierzone). W usłudze jest jedna pętla, więc tam klient z cache jest OK.
    """
    settings = get_settings()
    if settings.llm_provider == "fake":
        pytest.skip("LLM_PROVIDER=fake — test wymaga realnego dostawcy (openai / ollama)")
    try:
        return build_llm_client(settings)
    except LLMConfigError as exc:
        pytest.skip(f"niekompletna konfiguracja LLM: {exc}")


def _classify(client: LLMClient, summaries: list[str]) -> tuple[int | str | None, dict]:
    """Jedno wywołanie jak w usłudze: prompty + schemat -> (id wybranej opcji albo None, sparsowana odpowiedź)."""
    labeler = OptionLabeler(_OPTIONS)
    result  = asyncio.run(
        client.complete(
            system      = ClassificationSystemPrompt().render(),
            user        = ClassificationUserPrompt().render(summaries=summaries, entries=labeler.entries),
            max_tokens  = DEFAULT_MAX_OUTPUT_TOKENS,   # ten sam limit co w usłudze
            temperature = 0.0,
            json_schema = build_response_schema(labeler.labels),
        )
    )
    data = json.loads(result.text)
    return labeler.resolve(data[LABEL_FIELD]), data


def test_oczywiste_dopasowanie_wybiera_wlasciwa_opcje(llm_client):
    """Skarga konsumenta na operatora telekomunikacyjnego (+ faktura) -> opcja „Skargi i interwencje” (id 21)."""
    summaries = [
        "• Typ pisma: skarga\n• Nadawca: Jan Kowalski\n• Czego dotyczy: naliczenie przez operatora telekomunikacyjnego opłat za niezamówione usługi\n• Oczekiwana akcja: interwencja wobec operatora i zwrot nadpłaty",
        "• Typ pisma: faktura VAT\n• Nadawca: TelKom S.A.\n• Czego dotyczy: rozliczenie usług telekomunikacyjnych za sierpień 2026, kwota 312,40 zł",
    ]

    option_id, data = _classify(llm_client, summaries)

    assert option_id == 21, data                   # surowa odpowiedź w komunikacie — czytać uzasadnienie, nie tylko etykietę
    assert data[RATIONALE_FIELD].strip()


def test_dokument_spoza_opcji_daje_brak_dopasowania(llm_client):
    """Siatka bezpieczeństwa: wniosek o dofinansowanie sieci światłowodowej nie pasuje do żadnej opcji -> OPT-00 (None)."""
    summaries = ["• Typ pisma: wniosek\n• Nadawca: Gmina Zielonka\n• Czego dotyczy: dofinansowanie budowy sieci światłowodowej w ramach programu regionalnego\n• Oczekiwana akcja: rozpatrzenie wniosku o dotację"]

    option_id, data = _classify(llm_client, summaries)

    assert option_id is None, data
    assert data[RATIONALE_FIELD].strip()
