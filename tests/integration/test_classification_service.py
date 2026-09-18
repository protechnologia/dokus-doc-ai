"""Test integracyjny ClassificationService na golden secie — trafność wyboru na realnym modelu (krok 10).

Marker: integration_llm (+ parasol integration). Dane w `samples/classification/`: `katalog.json`
(grupy ze stanowiskami), `golden.json` (oczekiwana grupa i stanowisko dla 20 pism),
`summaries.json` (zamrożone streszczenia — wejście stałe, decyzja 1a). Klient z konfiguracji
(fixture `llm_client`), więc `openai` albo `ollama`; `fake` -> SKIP.

Dwa szczeble jak u klienta (grupa, potem stanowisko), każdy z progiem 80% (decyzja 1b). Stanowisko
pytane w OCZEKIWANEJ grupie z golden, nie w wybranej przez model: pomyłka na grupie nie liczy się
podwójnie i widać, który szczebel zawodzi. Pisma spoza katalogu (oczekiwane `null`) liczą się
w progu jak każde inne — osobny, bezwzględny test siatki bezpieczeństwa jest w
`test_classification_prompt.py`. `invalid_response` to zawsze pomyłka.

Niedeterminizm: Bielik potrafi dać inny wynik w kolejnym przebiegu mimo `temperature=0`, więc wynik
tuż przy progu może się przełączać. Wszystkie wywołania testu w JEDNYM `asyncio.run` (klient
`AsyncOpenAI` wiąże pulę połączeń z pętlą zdarzeń). Koszt: 20 + 17 wywołań na przebieg.
"""

import asyncio
import json
from pathlib import Path
from typing import NamedTuple

import pytest

from app.classification import ClassificationOption, ClassificationResult, ClassificationService
from app.config import get_settings
from app.llm import LLMClient

# Parasol `integration` + węższy `integration_llm` (uderzamy w realnego dostawcę LLM).
pytestmark = [pytest.mark.integration, pytest.mark.integration_llm]

# Minimalna trafność na każdym szczeblu (decyzja 1b): łapie regres, toleruje pojedyncze pomyłki.
_THRESHOLD = 0.8

# --- Dane golden setu ------------------------------------------------------------

_DATA      = Path(__file__).resolve().parents[2] / "samples" / "classification"
_CATALOG   = json.loads((_DATA / "katalog.json").read_text(encoding="utf-8"))["grupy"]
_GOLDEN    = json.loads((_DATA / "golden.json").read_text(encoding="utf-8"))["pisma"]
_SUMMARIES = json.loads((_DATA / "summaries.json").read_text(encoding="utf-8"))["streszczenia"]


def _options(entries: list[dict]) -> list[ClassificationOption]:
    """Pomocniczo: wpisy katalogu (pola kontraktu `ClassifyOption`) -> opcje domenowe, w kolejności pliku."""
    return [ClassificationOption(id=e["id"], name=e["name"], description=e["description"], examples=e["examples"]) for e in entries]


_GROUPS    = _options(_CATALOG)
_POSITIONS = {group["id"]: _options(group["stanowiska"]) for group in _CATALOG}


class _Case(NamedTuple):
    """Jedno wywołanie: pismo, lista opcji i oczekiwane `id` (None = brak dopasowania)."""

    file: str
    options: list[ClassificationOption]
    expected: int | str | None


# --- Wspólne wykonanie i ocena ------------------------------------------------------


def _run(client: LLMClient, cases: list[_Case]) -> list[ClassificationResult]:
    """Pomocniczo: sklasyfikuj wszystkie przypadki jednym serwisem, w jednej pętli zdarzeń, po kolei."""
    settings = get_settings()
    service  = ClassificationService(client, max_prompt_chars=settings.llm_max_input_chars, max_output_tokens=settings.llm_max_output_tokens_classify)

    async def _all() -> list[ClassificationResult]:
        return [await service.classify(summaries=[_SUMMARIES[case.file]], options=case.options) for case in cases]

    return asyncio.run(_all())


def _hit(case: _Case, result: ClassificationResult) -> bool:
    """Pomocniczo: trafienie = poprawna odpowiedź modelu z oczekiwanym `id` (`no_match` przy oczekiwanym None)."""
    return result.outcome != "invalid_response" and result.option_id == case.expected


def _assert_accuracy(level: str, cases: list[_Case], results: list[ClassificationResult]) -> None:
    """Pomocniczo: trafność >= progu; raport (także przy sukcesie, widoczny z `-s`) wymienia pomyłki z uzasadnieniem modelu."""
    misses = [
        f"  - {case.file}: oczekiwano {case.expected!r}, jest {result.option_id!r} ({result.outcome}) — {result.rationale or result.error}"
        for case, result in zip(cases, results)
        if not _hit(case, result)
    ]
    accuracy = (len(cases) - len(misses)) / len(cases)
    report   = f"{level}: {len(cases) - len(misses)}/{len(cases)} = {accuracy:.0%} (próg {_THRESHOLD:.0%}, model {results[0].model})\n" + "\n".join(misses)
    print(report)
    assert accuracy >= _THRESHOLD, report


# --- Testy -----------------------------------------------------------------------


def test_trafnosc_grup(llm_client):
    """Szczebel 1: każde pismo wobec listy grup -> trafność >= 80% (pisma spoza katalogu: oczekiwane `null`)."""
    cases = [_Case(doc["plik"], _GROUPS, doc["grupa"]) for doc in _GOLDEN]

    _assert_accuracy("grupy", cases, _run(llm_client, cases))


def test_trafnosc_stanowisk(llm_client):
    """Szczebel 2: pismo z oczekiwaną grupą wobec stanowisk TEJ grupy -> trafność >= 80% (`null` = żadne stanowisko)."""
    cases = [_Case(doc["plik"], _POSITIONS[doc["grupa"]], doc["stanowisko"]) for doc in _GOLDEN if doc["grupa"] is not None]

    _assert_accuracy("stanowiska", cases, _run(llm_client, cases))
