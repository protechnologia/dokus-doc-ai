"""Testy jednostkowe FakeLLMClient (krok 2.2) — bez sieci i bez SDK `openai`.

Fake to domyslny dostawca dev/test: deterministyczny i offline (nic nie wychodzi na
zewnatrz — "prywatnosc pierwsza"). Sprawdzamy ksztalt LLMResult i echo wejscia.
"""

import asyncio

from app.llm import FakeLLMClient, LLMResult


def test_fake_zwraca_deterministyczny_wynik():
    """Ta sama tresc wejscia -> ten sam wynik; ksztalt = LLMResult z echem wejscia."""
    client = FakeLLMClient()
    r1 = asyncio.run(client.complete(user="Pismo w sprawie podatku od nieruchomosci"))
    r2 = asyncio.run(client.complete(user="Pismo w sprawie podatku od nieruchomosci"))

    assert isinstance(r1, LLMResult)
    assert r1.text == r2.text                      # determinizm
    assert "podatku" in r1.text                    # echo realnego wejscia
    assert r1.model == "fake-echo"
    assert r1.usage.total_tokens > 0


def test_fake_skraca_dlugie_wejscie():
    """Fake bierze tylko poczatek (max ~40 slow) — nie zwraca calego dokumentu."""
    dlugi = " ".join(f"slowo{i}" for i in range(200))
    r = asyncio.run(FakeLLMClient().complete(user=dlugi))

    assert "slowo0" in r.text
    assert "slowo199" not in r.text                # ogon uciety
