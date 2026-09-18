"""Testy jednostkowe parsera odpowiedzi klasyfikacji (`parse_response`) — bez I/O.

Sprawdzamy trzy obietnice jednostki: poprawna odpowiedź -> uzasadnienie + etykieta DOSŁOWNIE
(przynależność do listy to `OptionLabeler.resolve`), każda wada -> `InvalidModelResponseError`
z czytelną przyczyną (trafia do pola `error`), oraz decyzję (a) z 2026-09-17: ściśle, bez tolerancji
zapisu — blok kodu, tekst wokół JSON-a i powtórzony klucz to odpowiedź niepoprawna. Helpery
z własnym zachowaniem (`_json_type`, `_reject_duplicate_keys`) wołane wprost.
"""

import json
import re

import pytest

from app.classification.exception import InvalidModelResponseError
from app.classification.service_parsing import ParsedResponse, _json_type, _reject_duplicate_keys, parse_response


def _raw(**fields):
    """Pomocniczo: surowa odpowiedź jak z modelu — obiekt JSON z podanymi polami, w podanej kolejności."""
    return json.dumps(fields, ensure_ascii=False)


# --- _json_type: nazwy typów JSON w komunikatach ---------------------------------


@pytest.mark.parametrize(
    "value, expected",
    [
        (None, "null"),
        (True, "boolean"),       # bool przed liczbą — w Pythonie True to też int
        (3, "number"),
        (2.5, "number"),
        ("OPT-1", "string"),
        (["OPT-1"], "array"),
        ({"label": "OPT-1"}, "object"),
    ],
)
def test_json_type_nazwy_z_json_nie_z_pythona(value, expected):
    """Wartość po `json.loads` -> nazwa typu JSON (`null`, `number`…), nie pythonowa (`NoneType`, `int`)."""
    assert _json_type(value) == expected


# --- _reject_duplicate_keys: hook json.loads -------------------------------------


def test_reject_duplicate_keys_sklada_obiekt_w_kolejnosci():
    """Unikalne klucze -> zwykły dict z zachowaną kolejnością pól."""
    data = _reject_duplicate_keys([("rationale", "r"), ("label", "OPT-1")])
    assert data == {"rationale": "r", "label": "OPT-1"}
    assert list(data) == ["rationale", "label"]


def test_reject_duplicate_keys_odrzuca_powtorzony_klucz():
    """Powtórzony klucz -> wyjątek z nazwą pola, zamiast po cichu wygranej ostatniej wartości."""
    with pytest.raises(InvalidModelResponseError, match=re.escape("Powtórzone pole 'label'")):
        _reject_duplicate_keys([("label", "OPT-1"), ("label", "OPT-2")])


# --- parse_response: odpowiedzi poprawne -----------------------------------------


def test_poprawna_odpowiedz():
    """Obiekt z uzasadnieniem i etykietą -> `ParsedResponse` z obiema wartościami."""
    parsed = parse_response(_raw(rationale="Skarga konsumenta na operatora.", label="OPT-1"))
    assert parsed == ParsedResponse(rationale="Skarga konsumenta na operatora.", label="OPT-1")


def test_opt00_to_poprawna_odpowiedz():
    """`OPT-00` („brak dopasowania") to zwykła poprawna odpowiedź, nie błąd parsera."""
    parsed = parse_response(_raw(rationale="Żadna opcja nie obejmuje dotacji.", label="OPT-00"))
    assert parsed.label == "OPT-00"
    assert parsed.rationale == "Żadna opcja nie obejmuje dotacji."


def test_biale_znaki_i_pretty_print_dopuszczalne():
    """Wieloliniowy JSON z wcięciami i białymi znakami wokół (jak przykłady w prompcie) -> poprawny."""
    raw = '\n  {\n    "rationale": "Wniosek o zezwolenie.",\n    "label": "OPT-2"\n  }\n'
    assert parse_response(raw) == ParsedResponse(rationale="Wniosek o zezwolenie.", label="OPT-2")


def test_pola_spoza_schematu_ignorowane():
    """Pole nadmiarowe nie zmienia wyboru -> ignorowane (sprawdzamy strukturę, której potrzebujemy)."""
    parsed = parse_response(_raw(rationale="r", label="OPT-1", confidence=0.9))
    assert parsed == ParsedResponse(rationale="r", label="OPT-1")


def test_puste_uzasadnienie_przyjete():
    """Pusty string to poprawny typ — treści uzasadnienia parser nie ocenia."""
    assert parse_response(_raw(rationale="", label="OPT-1")).rationale == ""


@pytest.mark.parametrize(
    "label",
    [
        "opt-3",     # inna wielkość liter — bez normalizacji (decyzja (a))
        " OPT-3 ",   # białe znaki — jw.
        "OPT-01",    # zera wiodące — to nie jest OPT-1
        "OPT-N",     # etykieta z przykładu w prompcie
    ],
)
def test_etykieta_wraca_doslownie(label):
    """Parser nie zna listy i nie normalizuje zapisu: etykieta bez zmian, odrzuci ją dopiero `resolve`."""
    assert parse_response(_raw(rationale="r", label=label)).label == label


# --- parse_response: odpowiedzi niepoprawne --------------------------------------


def test_smieci_to_niepoprawny_json():
    """Wolny tekst zamiast JSON-a -> „Niepoprawny JSON" z miejscem błędu."""
    with pytest.raises(InvalidModelResponseError, match=r"^Niepoprawny JSON w odpowiedzi modelu: Expecting value"):
        parse_response("Wybieram opcję OPT-2, bo dotyczy skargi.")


def test_json_urwany_w_srodku_uzasadnienia():
    """Odpowiedź urwana przez `max_tokens` -> komunikat dokładnie jak w przykładzie `invalid_response` z README."""
    raw = '{"rationale": "Pismo dotyczy skargi konsumenta na operatora, który naliczył'
    with pytest.raises(InvalidModelResponseError) as exc_info:
        parse_response(raw)
    assert str(exc_info.value) == "Niepoprawny JSON w odpowiedzi modelu: Unterminated string starting at: line 1 column 15 (char 14)."


@pytest.mark.parametrize("raw", ["", "  \n\t "])
def test_pusta_odpowiedz_osobna_przyczyna(raw):
    """Brak treści (odmowa / filtr u dostawcy -> "") -> „Pusta odpowiedź", nie mylące „Expecting value"."""
    with pytest.raises(InvalidModelResponseError, match=r"^Pusta odpowiedź modelu\.$"):
        parse_response(raw)


@pytest.mark.parametrize(
    "raw",
    [
        '```json\n{"rationale": "r", "label": "OPT-1"}\n```',   # blok kodu markdown
        'Oto odpowiedź: {"rationale": "r", "label": "OPT-1"}',   # tekst przed JSON-em
        '{"rationale": "r", "label": "OPT-1"} Mam nadzieję, że pomogłem.',   # tekst za JSON-em
        '{"rationale": "r", "label": "OPT-1"}\n{"rationale": "r", "label": "OPT-2"}',   # dwa obiekty
    ],
)
def test_bez_tolerancji_zapisu_wokol_json(raw):
    """Decyzja (a): JSON opakowany w blok kodu albo tekst -> niepoprawny, bez wyłuskiwania „na oko"."""
    with pytest.raises(InvalidModelResponseError, match=r"^Niepoprawny JSON w odpowiedzi modelu: "):
        parse_response(raw)


@pytest.mark.parametrize(
    "raw, json_type",
    [
        ('["r", "OPT-1"]', "array"),
        ('"OPT-1"', "string"),
        ("null", "null"),
    ],
)
def test_korzen_inny_niz_obiekt(raw, json_type):
    """Poprawny JSON, ale nie obiekt (lista, sama etykieta, null) -> przyczyna z typem korzenia."""
    with pytest.raises(InvalidModelResponseError, match=re.escape(f"nie jest obiektem JSON (typ: {json_type})")):
        parse_response(raw)


def test_brak_etykiety():
    """Obiekt bez `label` -> „Brak pola 'label'"."""
    with pytest.raises(InvalidModelResponseError, match=re.escape("Brak pola 'label'")):
        parse_response(_raw(rationale="Skarga na operatora."))


def test_brak_uzasadnienia():
    """Obiekt bez `rationale` -> „Brak pola 'rationale'" (mimo poprawnej etykiety)."""
    with pytest.raises(InvalidModelResponseError, match=re.escape("Brak pola 'rationale'")):
        parse_response(_raw(label="OPT-1"))


def test_brak_obu_pol_wskazuje_pierwsze_ze_schematu():
    """Kilka wad naraz -> przyczyna to pierwsza w kolejności schematu (uzasadnienie)."""
    with pytest.raises(InvalidModelResponseError, match=re.escape("Brak pola 'rationale'")):
        parse_response("{}")


@pytest.mark.parametrize(
    "fields, name, json_type",
    [
        ({"rationale": "r", "label": 3}, "label", "number"),             # numer zamiast etykiety — bez koercji na OPT-3
        ({"rationale": "r", "label": None}, "label", "null"),
        ({"rationale": "r", "label": ["OPT-1"]}, "label", "array"),
        ({"rationale": None, "label": "OPT-1"}, "rationale", "null"),
        ({"rationale": True, "label": "OPT-1"}, "rationale", "boolean"),
    ],
)
def test_zly_typ_pola(fields, name, json_type):
    """Pole innego typu niż string (także null) -> przyczyna z nazwą pola i typem JSON."""
    with pytest.raises(InvalidModelResponseError, match=re.escape(f"Pole {name!r} w odpowiedzi modelu ma typ {json_type}, oczekiwano string.")):
        parse_response(json.dumps(fields))


def test_powtorzony_klucz_etykiety():
    """Model „poprawił się" w tym samym obiekcie -> niepoprawna, zamiast po cichu wziąć ostatnią etykietę."""
    raw = '{"rationale": "r", "label": "OPT-1", "label": "OPT-2"}'
    with pytest.raises(InvalidModelResponseError, match=re.escape("Powtórzone pole 'label'")):
        parse_response(raw)
