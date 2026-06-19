"""Test integracyjny realnego dostawcy LLM (krok 2.2) — uderza w API OpenAI.

Marker: integration_llm. Wymaga LLM_PROVIDER=openai + LLM_API_KEY (z .env/ENV).
Gdy ich brak -> SKIP (nie fail), spojnie z konwencja "Tika niedostepna -> skip".

Koszt swiadomie minimalny: jedno wywolanie, max_tokens=5. To NIE jest test jakosci
streszczen — tylko dowod, ze transport (klucz, model, mapowanie odpowiedzi) dziala.
"""

import asyncio
import pytest
from app.config import get_settings
from app.llm    import LLMResult, build_llm_client

# Konfiguracja z .env/ENV. Test ma sens tylko dla realnego dostawcy z kluczem.
_settings = get_settings()
pytestmark = pytest.mark.integration_llm

_skip_powod = None
if _settings.llm_provider != "openai":
    _skip_powod = f"LLM_PROVIDER={_settings.llm_provider} (test wymaga 'openai')"
elif not _settings.llm_api_key:
    _skip_powod = "brak LLM_API_KEY — ustaw w .env, by odpalic test realnego LLM"


@pytest.mark.skipif(_skip_powod is not None, reason=_skip_powod or "")
def test_openai_realne_wywolanie():
    """Realny OpenAI odpowiada: niepusty tekst + zuzycie tokenow > 0."""
    client = build_llm_client(_settings)
    result = asyncio.run(
        client.complete(
            user="Odpowiedz jednym slowem: dziala?",
            system="Jestes lakonicznym asystentem. Odpowiadaj po polsku.",
            max_tokens=5,
        )
    )

    assert isinstance(result, LLMResult)
    assert result.text.strip() != ""               # model cokolwiek wygenerowal
    assert result.usage.total_tokens > 0           # zuzycie realnie zmapowane
    assert result.model                            # model z odpowiedzi obecny