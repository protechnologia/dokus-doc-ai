"""Parser odpowiedzi modelu przy klasyfikacji: surowy tekst -> uzasadnienie + etykieta.

Czysta logika (bez I/O, bez logów). Etykiety NIE rozwiązuje — nie zna listy opcji: `label` wraca
dosłownie, a na `id` zamienia ją serwis przez `OptionLabeler.resolve` (spoza listy ->
`UnknownLabelError`). Oba wyjątki serwis zamienia na wynik `invalid_response`; komunikat
`InvalidModelResponseError` to przyczyna, która trafia do pola `error` odpowiedzi HTTP (audyt klienta).

Decyzje (2026-09-17 — nie „poprawiać" bez powodu):
  - ŚCIŚLE, bez tolerancji zapisu: cała odpowiedź musi być obiektem JSON (białe znaki wokół
    dopuszcza sam `json.loads`). Blok kodu markdown i tekst wokół JSON-a -> niepoprawny JSON;
    `opt-3` / ` OPT-3 ` przechodzą dosłownie i odpadają na `resolve`. OpenAI i Ollama egzekwują
    schemat, więc tolerancja nigdy by się tam nie uruchomiła. Na zapleczu, które schemat gubi (Open
    WebUI — niesprawdzone, krok 11), ścisły parser ujawnia to od pierwszego żądania, zamiast
    przykryć. Tolerancję dokładać dopiero na podstawie zmierzonych surowych odpowiedzi, nie
    zgadywanych,
  - powtórzony klucz -> błąd: `json.loads` po cichu bierze ostatnią wartość, czyli zgadywałby,
    którą etykietę model „miał na myśli" (np. gdy poprawił się w tym samym obiekcie),
  - sprawdzamy strukturę, nie treść (jak model API): pola spoza schematu ignorowane, puste
    uzasadnienie przyjęte — żadne nie zmienia tego, którą pozycję model wybrał.
"""

from __future__ import annotations

import json
from typing import Any, NamedTuple

from app.classification.exception import InvalidModelResponseError
from app.classification.service_schema import LABEL_FIELD, RATIONALE_FIELD

# --- Wynik -----------------------------------------------------------------------


class ParsedResponse(NamedTuple):
    """Sparsowana odpowiedź modelu: uzasadnienie + etykieta dosłownie (bez rozwiązania na `id`)."""

    rationale: str
    label: str


# --- Czyste helpery — testowalne punktowo ----------------------------------------


def _json_type(
    value: Any,   # wartość po `json.loads`, np. 3 / None / ["OPT-1"]
) -> str:
    """Opis metody:
    Nazwa typu JSON wartości — do komunikatów błędu (klient czyta je jako opis odpowiedzi JSON,
    więc `null` / `number`, a nie pythonowe `NoneType` / `int`).

    Przyklad argumentow:
        value=3

    Przyklad wyniku:
        "number"   # None -> "null", True -> "boolean", ["OPT-1"] -> "array"
    """
    # bool PRZED liczbą: w Pythonie `True` jest instancją `int`.
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"   # `json.loads` innych typów nie zwraca


def _reject_duplicate_keys(
    pairs: list[tuple[str, Any]],   # pary klucz-wartość jednego obiektu JSON, np. [("label", "OPT-1"), ("label", "OPT-2")]
) -> dict[str, Any]:
    """Opis metody:
    `object_pairs_hook` dla `json.loads`: złóż obiekt, ale odrzuć powtórzony klucz (domyślnie
    wygrywa po cichu ostatni). Działa na każdym poziomie zagnieżdżenia.

    Przyklad argumentow:
        pairs=[("rationale", "..."), ("label", "OPT-1")]

    Przyklad wyniku:
        {"rationale": "...", "label": "OPT-1"}

    Raises:
        InvalidModelResponseError: klucz występuje w obiekcie więcej niż raz.
    """
    data: dict[str, Any] = {}
    for key, value in pairs:
        # Drugie wystąpienie klucza -> nie wiemy, która wartość jest odpowiedzią; głośno.
        if key in data:
            raise InvalidModelResponseError(f"Powtórzone pole {key!r} w odpowiedzi modelu.")
        data[key] = value
    return data


def _load_object(
    raw: str,   # surowa odpowiedź modelu, np. '{"rationale": "...", "label": "OPT-2"}'
) -> dict[str, Any]:
    """Opis metody:
    Wczytaj surową odpowiedź jako obiekt JSON — ściśle: cały tekst to jeden dokument JSON
    (białe znaki wokół dopuszczalne), a jego korzeń to obiekt.

    Przyklad argumentow:
        raw='{"rationale": "Skarga na operatora.", "label": "OPT-1"}'

    Przyklad wyniku:
        {"rationale": "Skarga na operatora.", "label": "OPT-1"}

    Raises:
        InvalidModelResponseError: pusta odpowiedź, niepoprawny (także urwany) JSON, powtórzony klucz
            albo korzeń inny niż obiekt.
    """
    # --- Pusta odpowiedź: osobna przyczyna ---------------------------------------------
    # `OpenAILLMClient` zamienia brak treści (odmowa przy schemacie, filtr treści) na "". Komunikat
    # `json.loads` („Expecting value: line 1 column 1") sugerowałby zepsuty JSON, a JSON-a nie było.
    if not raw.strip():
        raise InvalidModelResponseError("Pusta odpowiedź modelu.")

    # --- Dokument JSON -----------------------------------------------------------------
    # Treść `JSONDecodeError` zostaje w komunikacie — wskazuje miejsce (np. „Unterminated string"
    # przy odpowiedzi urwanej przez `max_tokens`). Powtórzony klucz rzuca już nasz wyjątek z hooka.
    try:
        data = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise InvalidModelResponseError(f"Niepoprawny JSON w odpowiedzi modelu: {exc}.") from exc

    # --- Korzeń musi być obiektem (np. nie lista ani sam string z etykietą) -------------
    if not isinstance(data, dict):
        raise InvalidModelResponseError(f"Odpowiedź modelu nie jest obiektem JSON (typ: {_json_type(data)}).")
    return data


def _string_field(
    data: dict[str, Any],   # obiekt odpowiedzi z `_load_object`, np. {"rationale": "...", "label": "OPT-1"}
    name: str,              # nazwa pola ze schematu, np. "label"
) -> str:
    """Opis metody:
    Wyciągnij wymagane pole tekstowe. Treści nie ocenia: pusty string to poprawny typ.

    Przyklad argumentow:
        data={"rationale": "...", "label": "OPT-1"}
        name="label"

    Przyklad wyniku:
        "OPT-1"

    Raises:
        InvalidModelResponseError: brak pola albo wartość innego typu niż string (także null).
    """
    # Brak pola -> odpowiedź niekompletna.
    if name not in data:
        raise InvalidModelResponseError(f"Brak pola {name!r} w odpowiedzi modelu.")

    # Zły typ -> bez koercji (`3` to nie etykieta `OPT-3`, `null` to nie uzasadnienie).
    value = data[name]
    if not isinstance(value, str):
        raise InvalidModelResponseError(f"Pole {name!r} w odpowiedzi modelu ma typ {_json_type(value)}, oczekiwano string.")
    return value


# --- Parser — złożenie helperów --------------------------------------------------


def parse_response(
    raw: str,   # surowa odpowiedź modelu (`LLMResult.text`), np. '{"rationale": "...", "label": "OPT-2"}'
) -> ParsedResponse:
    """Opis metody:
    Sparsuj surową odpowiedź modelu na uzasadnienie i etykietę. Etykieta wraca dosłownie — jej
    przynależność do listy sprawdza `OptionLabeler.resolve`. Czysta funkcja.

    Przyklad argumentow:
        raw='{"rationale": "Skarga konsumenta na operatora.", "label": "OPT-1"}'

    Przyklad wyniku:
        ParsedResponse(rationale="Skarga konsumenta na operatora.", label="OPT-1")

    Raises:
        InvalidModelResponseError: pusta odpowiedź, niepoprawny lub urwany JSON, powtórzony klucz,
            korzeń inny niż obiekt, brak pola albo pole innego typu niż string.
    """
    data = _load_object(raw)
    # Pola w kolejności schematu: przy kilku wadach komunikat wskazuje pierwszą (np. brak obu -> brak uzasadnienia).
    return ParsedResponse(rationale=_string_field(data, RATIONALE_FIELD), label=_string_field(data, LABEL_FIELD))
