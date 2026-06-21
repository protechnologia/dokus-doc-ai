"""Test integracyjny SummarizationService (krok 2.4.1) — uderza w realnego dostawcę LLM.

Marker: integration_llm. Wymaga LLM_PROVIDER=openai + LLM_API_KEY (z .env/ENV); inaczej SKIP
(spójnie z konwencją „usługa niedostępna -> skip, nie fail”).

Cel, którego atrapa (`FakeLLMClient`) NIE dowiedzie: czy nasz PROMPT HYBRYDOWY (system po
polsku + szablon usera) faktycznie daje sensowne, polskie streszczenie z prawdziwego modelu,
i czy metadane (model/usage) wracają realnie wypełnione. To NIE jest test jakości treści —
asercje są MIĘKKIE (niepuste, polskie diakrytyki, model/usage obecne). Koszt świadomie mały:
jedno krótkie pismo + ograniczony `max_output_tokens`.
"""

import asyncio

import pytest

from app.config import get_settings
from app.llm import build_llm_client
from app.summarization import SummarizationResult, SummarizationService

# Konfiguracja z .env/ENV — test ma sens tylko dla realnego dostawcy z kluczem.
_settings = get_settings()
pytestmark = pytest.mark.integration_llm

_skip_powod = None
if _settings.llm_provider != "openai":
    _skip_powod = f"LLM_PROVIDER={_settings.llm_provider} (test wymaga 'openai')"
elif not _settings.llm_api_key:
    _skip_powod = "brak LLM_API_KEY — ustaw w .env, by odpalic test realnego LLM"

# Krótkie, realistyczne pismo urzędowe — wejście do streszczenia.
_PISMO = (
    "Urząd Skarbowy w Krakowie wzywa Pana Jana Kowalskiego do zapłaty zaległości w podatku "
    "od nieruchomości za rok 2025 w kwocie 1 240 zł w terminie 14 dni od doręczenia pisma, "
    "pod rygorem wszczęcia postępowania egzekucyjnego."
)


@pytest.mark.skipif(_skip_powod is not None, reason=_skip_powod or "")
def test_summarize_realny_llm_daje_polskie_streszczenie():
    """Realny LLM + nasz prompt -> niepuste polskie streszczenie; metadane (model/usage) wypełnione, bez truncacji."""
    # Mały limit wyjścia -> koszt minimalny; klient realny budowany z .env.
    service = SummarizationService(build_llm_client(_settings), max_output_tokens=200)
    result = asyncio.run(service.summarize(text=_PISMO))

    assert isinstance(result, SummarizationResult)
    assert result.summary.strip() != ""                     # cokolwiek streszczone
    assert any(ch in result.summary for ch in "ąćęłńóśżź")   # po polsku (diakrytyki)
    assert result.metadata.model                             # realny model w metadanych
    assert result.metadata.usage.total_tokens > 0            # zużycie realnie zmapowane
    assert result.metadata.truncated is False                # krótkie pismo, bez truncacji
