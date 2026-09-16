"""Testy jednostkowe FakeLLMClient (krok 2.2) — bez sieci i bez SDK `openai`.

Fake to domyslny dostawca dev/test: deterministyczny i offline (nic nie wychodzi na
zewnatrz — "prywatnosc pierwsza"). Sprawdzamy ksztalt LLMResult i echo wejscia, a przy
`json_schema` — JSON zgodny ze schematem (`complete`) i budowe instancji (`_minimal_instance`).
"""

import asyncio
import json
import pytest

from app.llm import FakeLLMClient, LLMResult

# Schemat w ksztalcie klasyfikacji: uzasadnienie przed etykieta, etykieta jako enum.
_SCHEMA = {
    "type": "object",
    "properties": {
        "rationale": {"type": "string"},
        "label":     {"type": "string", "enum": ["OPT-1", "OPT-2", "OPT-00"]},
    },
    "required": ["rationale", "label"],
    "additionalProperties": False,
}


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


# --- complete z json_schema: JSON zamiast echa -----------------------------------


def test_fake_ze_schematem_zwraca_json_zgodny_ze_schematem():
    """Schemat -> parsowalny JSON: pola w kolejnosci schematu, enum = pierwsza wartosc, tekst = echo."""
    r = asyncio.run(FakeLLMClient().complete(user="Pismo w sprawie podatku", json_schema=_SCHEMA))
    data = json.loads(r.text)

    assert list(data) == ["rationale", "label"]              # kolejnosc `properties`, jak u realnego modelu
    assert data["label"] == "OPT-1"                          # pierwsza wartosc enum
    assert "podatku" in data["rationale"]                    # echo wejscia — dev widzi, ze prompt dolecial


def test_fake_ze_schematem_zachowuje_polskie_znaki():
    """JSON bez eskejpowania `\\uXXXX` — polskie znaki jak w odpowiedzi modelu."""
    r = asyncio.run(FakeLLMClient().complete(user="Wniosek o zażalenie", json_schema=_SCHEMA))
    assert "zażalenie" in r.text


# --- _minimal_instance: budowa instancji ze schematu -----------------------------


def test_minimal_instance_typy_proste_i_const():
    """Kazdy obslugiwany typ -> najprostsza poprawna wartosc; const wygrywa z typem."""
    schema = {
        "type": "object",
        "properties": {
            "s": {"type": "string"},
            "i": {"type": "integer"},
            "n": {"type": "number"},
            "b": {"type": "boolean"},
            "z": {"type": "null"},
            "a": {"type": "array", "items": {"type": "string"}},
            "c": {"type": "string", "const": "stala"},
            "o": {"type": ["string", "null"]},                  # lista typow -> pierwszy
        },
        "required": ["s", "i", "n", "b", "z", "a", "c", "o"],
    }
    wynik = FakeLLMClient._minimal_instance(schema, "echo")
    assert wynik == {"s": "echo", "i": 0, "n": 0, "b": False, "z": None, "a": [], "c": "stala", "o": "echo"}


def test_minimal_instance_pomija_pola_niewymagane():
    """Pole spoza `required` nie trafia do instancji (minimalna = tylko wymagane)."""
    schema = {"type": "object", "properties": {"opcjonalne": {"type": "string"}, "wymagane": {"type": "integer"}}, "required": ["wymagane"]}
    assert FakeLLMClient._minimal_instance(schema, "echo") == {"wymagane": 0}


def test_minimal_instance_zagniezdzony_obiekt():
    """Obiekt w obiekcie -> rekurencja po `properties`."""
    schema = {"type": "object", "properties": {"wew": {"type": "object", "properties": {"x": {"type": "boolean"}}, "required": ["x"]}}, "required": ["wew"]}
    assert FakeLLMClient._minimal_instance(schema, "echo") == {"wew": {"x": False}}


def test_minimal_instance_nieobslugiwany_schemat_rzuca():
    """Konstrukcja spoza podzbioru (np. `anyOf`) -> ValueError, nie cichy `null` niezgodny ze schematem."""
    with pytest.raises(ValueError, match="nieobslugiwany"):
        FakeLLMClient._minimal_instance({"anyOf": [{"type": "string"}, {"type": "integer"}]}, "echo")
