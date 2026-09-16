"""Atrapa klienta LLM — dev/test bez sieci i bez kosztow (krok 2.2).

Domyslny provider ('fake'): pipeline dziala end-to-end, ale NIC nie wychodzi na
zewnatrz — spojne z zasada "prywatnosc pierwsza". Odpowiedz jest deterministyczna
(zalezna od wejscia), zeby testy mogly cokolwiek asertowac, a dev widzial, ze
prompt naprawde dolecial.

Z `json_schema` atrapa zwraca JSON z minimalna poprawna instancja schematu — inaczej
na 'fake' kazda odpowiedz strukturalna (np. klasyfikacja) bylaby niesparsowalna.
"""

from __future__ import annotations

import json
from typing import Any

from app.llm.base import LLMClient, LLMResult, LLMUsage

FAKE_MODEL = "fake-echo"
_PREFIX = "[FAKE-LLM]"


class FakeLLMClient(LLMClient):
    """Atrapa `LLMClient`: echo wejscia albo JSON zgodny ze schematem. Bez I/O, deterministyczna.

    Do czego:
        Domyslny dostawca dev/test — pipeline dziala end-to-end bez sieci i kosztow,
        a wynik zalezy wylacznie od wejscia (testy moga go asertowac).

    Flow jednego `complete(...)`:
        1. echo: prefiks + pierwsze ~40 slow `user`,
        2. z `json_schema` -> `_minimal_instance` (pola tekstowe dostaja echo) -> `json.dumps`,
        3. zuzycie tokenow liczone ze slow wejscia i wyniku.
    """

    def __init__(
        self,
        *,
        model: str = FAKE_MODEL,   # etykieta modelu w wyniku, np. "fake-echo"
    ) -> None:
        """Opis metody:
        Zbuduj atrape (bez sieci, bez kosztow).

        Przyklad argumentow:
            model="fake-echo"   (domyslny)

        Przyklad wyniku:
            atrapa zwracajaca echo wejscia jako "streszczenie"
        """
        self._model = model

    # --- Czysty helper (bez I/O) — testowalny jednostkowo --------------------------

    @staticmethod
    def _minimal_instance(
        schema: dict[str, Any],   # (pod)schemat JSON Schema, np. {"type": "string", "enum": ["OPT-1", "OPT-00"]}
        text: str,                # wartosc dla pol tekstowych, np. "[FAKE-LLM] Pismo w sprawie..."
    ) -> Any:
        """Opis metody:
        Zbuduj minimalna instancje zgodna ze schematem. Czysta funkcja, rekurencyjna po
        obiektach. Obslugiwany podzbior: `enum`, `const`, typy `object` / `array` / `string` /
        `integer` / `number` / `boolean` / `null` (takze lista typow). Ograniczenia typu
        `minLength` / `minItems` / `pattern` pomijane — atrapa ich nie potrzebuje.

        Przyklad argumentow:
            schema={"type": "object",
                    "properties": {"rationale": {"type": "string"}, "label": {"type": "string", "enum": ["OPT-1", "OPT-00"]}},
                    "required": ["rationale", "label"]}
            text="[FAKE-LLM] Pismo"

        Przyklad wyniku:
            {"rationale": "[FAKE-LLM] Pismo", "label": "OPT-1"}

        Raises:
            ValueError: fragment schematu spoza obslugiwanego podzbioru (np. `anyOf`, `$ref`).
        """
        # --- Zawezenia wartosci przed typem ---------------------------------------------
        if "enum" in schema:
            return schema["enum"][0]                 # pierwsza dopuszczalna wartosc (np. pierwsza etykieta)
        if "const" in schema:
            return schema["const"]                   # jedyna dopuszczalna wartosc

        # --- Typ; lista typow (np. ["string", "null"]) -> pierwszy wystarczy -----------
        kind = schema.get("type")
        if isinstance(kind, list):
            kind = kind[0]

        # Obiekt: tylko pola wymagane, w kolejnosci `properties` (dict zachowuje kolejnosc,
        # json.dumps tez) — jak u realnego modelu, ktory pisze klucze w kolejnosci schematu.
        if kind == "object":
            required = set(schema.get("required", []))
            return {name: FakeLLMClient._minimal_instance(sub, text) for name, sub in schema.get("properties", {}).items() if name in required}

        # Pozostale typy: najprostsza poprawna wartosc.
        if kind == "array":
            return []                                # pusta lista (bez `minItems`)
        if kind == "string":
            return text                              # echo zamiast "" — dev widzi, ze prompt dolecial
        if kind in ("integer", "number"):
            return 0
        if kind == "boolean":
            return False
        if kind == "null":
            return None

        # Brak typu albo konstrukcja spoza podzbioru: glosno, nie cichy `null` niezgodny ze schematem.
        raise ValueError(f"FakeLLMClient: nieobslugiwany fragment schematu: {schema!r}")

    # --- Wywolanie (bez I/O) --------------------------------------------------------

    async def complete(
        self,
        *,
        user: str,                                  # tresc usera, np. "Pismo w sprawie podatku..."
        system: str | None = None,                  # ignorowany w atrapie (jest dla zgodnosci interfejsu)
        max_tokens: int | None = None,              # ignorowany w atrapie
        temperature: float = 0.0,                   # ignorowany w atrapie
        json_schema: dict[str, Any] | None = None,  # schemat odpowiedzi, np. {"type": "object", ...}; None = echo tekstem
    ) -> LLMResult:
        """Opis metody:
        Zwroc deterministyczne "streszczenie": prefiks + pierwsze ~40 slow wejscia.
        Z `json_schema` — JSON z minimalna instancja schematu (pola tekstowe = to samo echo).

        Przyklad argumentow:
            user="Pismo w sprawie podatku od nieruchomosci"
            json_schema=None

        Przyklad wyniku:
            LLMResult(text="[FAKE-LLM] Pismo w sprawie podatku od nieruchomosci",
                      model="fake-echo", usage=LLMUsage(...))

        Raises:
            ValueError: `json_schema` spoza podzbioru obslugiwanego przez `_minimal_instance`.
        """
        # "Streszczenie": pierwsze ~40 slow wejscia, znormalizowany whitespace.
        snippet = " ".join(user.split()[:40])
        text = f"{_PREFIX} {snippet}".strip()

        # Odpowiedz strukturalna: JSON zgodny ze schematem (ensure_ascii=False — polskie znaki jak u modelu).
        if json_schema is not None:
            text = json.dumps(self._minimal_instance(json_schema, text), ensure_ascii=False)

        usage = LLMUsage(
            prompt_tokens=len(user.split()),
            completion_tokens=len(text.split()),
            total_tokens=len(user.split()) + len(text.split()),
        )
        return LLMResult(text=text, model=self._model, usage=usage)
