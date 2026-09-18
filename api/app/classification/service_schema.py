"""Schemat odpowiedzi modelu przy klasyfikacji: uzasadnienie + etykieta z listy.

JSON Schema w trybie strict (każde pole w `required`, `additionalProperties: false`), przekazywany
do `LLMClient.complete(json_schema=...)`. Wymusza strukturę na OpenAI i Ollamie (krok 2). Czysta
logika — bez I/O. Nazwy pól są wewnętrzne (kontrakt HTTP ich nie widzi), ale prompt systemowy
wymienia je wprost — zgodność pilnują testy promptu.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

# --- Stałe -----------------------------------------------------------------------

# Pole z uzasadnieniem wyboru (1–2 zdania po polsku) — trafia do `rationale` odpowiedzi HTTP.
RATIONALE_FIELD = "rationale"

# Pole z etykietą wybranej pozycji (`OPT-n` albo `OPT-00`) — parser rozwiązuje ją na `id` opcji.
LABEL_FIELD = "label"


# --- Budowa schematu -------------------------------------------------------------


def build_response_schema(
    labels: Sequence[str],   # etykiety w kolejności `OptionLabeler.labels`, np. ["OPT-1", "OPT-2", "OPT-00"]
) -> dict[str, Any]:
    """Opis metody:
    Zbuduj JSON Schema odpowiedzi: najpierw uzasadnienie (string), potem etykieta (`enum` etykiet).
    Czysta funkcja.

    Przyklad argumentow:
        labels=["OPT-1", "OPT-2", "OPT-00"]

    Przyklad wyniku:
        {"type": "object",
         "properties": {"rationale": {"type": "string"},
                        "label": {"type": "string", "enum": ["OPT-1", "OPT-2", "OPT-00"]}},
         "required": ["rationale", "label"], "additionalProperties": False}
    """
    return {
        "type": "object",
        # KOLEJNOŚĆ PÓL = kolejność generowania (zmierzone w kroku 2 na Bieliku 4.5B i gpt-4o-mini).
        # Model pisze token po tokenie, więc etykieta wynika wtedy z uzasadnienia — wierny ślad do
        # audytu. Odwrotnie uzasadnienie tylko broniłoby przesądzonego wyboru. Kontrakt HTTP tej
        # kolejności nie widzi, więc ewentualne odwrócenie (krok 11) nie wymaga DOKUS-a.
        "properties": {
            RATIONALE_FIELD: {"type": "string"},
            LABEL_FIELD:     {"type": "string", "enum": list(labels)},   # enum: etykieta spoza listy niemożliwa na zapleczach egzekwujących schemat
        },
        "required":             [RATIONALE_FIELD, LABEL_FIELD],   # strict: każde pole wymagane
        "additionalProperties": False,                            # strict: bez pól spoza schematu
    }
