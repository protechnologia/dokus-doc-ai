"""Testy jednostkowe `OptionLabeler` — etykiety opcji klasyfikacji, bez I/O.

Sprawdzamy trzy obietnice jednostki: kolejność pozycji (opcje w kolejności wejścia, `OPT-00`
zawsze dokładnie raz i na końcu), rozwiązanie etykiety po pozycji na `id` w oryginalnym typie
oraz głośny błąd dla etykiety spoza listy (nie `None`, bo `None` znaczy „brak dopasowania").
Helper `_label` wołany wprost na klasie.
"""

import pytest

from app.classification.exception import UnknownLabelError
from app.classification.service_labels import NO_MATCH_LABEL, ClassificationOption, OptionLabeler


def _opcja(id, name="Opcja"):
    """Pomocniczo: opcja z podanym `id`; opis nieistotny dla etykiet."""
    return ClassificationOption(id=id, name=name, description="opis")


# --- _label: budowa etykiety z pozycji -------------------------------------------


def test_label_bez_dopelniania_zerami():
    """Pozycja -> `OPT-<n>` bez zer wiodących, także od 10 w górę."""
    assert OptionLabeler._label(1) == "OPT-1"
    assert OptionLabeler._label(10) == "OPT-10"


# --- Kolejność pozycji -----------------------------------------------------------


def test_etykiety_w_kolejnosci_wejscia_opt00_na_koncu():
    """Opcje dostają OPT-1…OPT-n w kolejności wejścia, `OPT-00` stoi ostatnia."""
    opcje = [_opcja("a"), _opcja("b"), _opcja("c")]
    labeler = OptionLabeler(opcje)

    assert labeler.labels == ["OPT-1", "OPT-2", "OPT-3", "OPT-00"]
    assert [entry.option for entry in labeler.entries] == [*opcje, None]      # OPT-00 bez opcji


def test_opt00_dokladnie_raz_przy_jednej_opcji():
    """Jedna opcja -> dwie pozycje; `OPT-00` doklejona dokładnie raz."""
    labels = OptionLabeler([_opcja(1)]).labels

    assert labels == ["OPT-1", NO_MATCH_LABEL]
    assert labels.count(NO_MATCH_LABEL) == 1


# --- resolve: etykieta -> id -----------------------------------------------------


def test_resolve_zwraca_id_w_oryginalnym_typie():
    """`21` wraca jako int, `"21"` jako string — klient dostaje ten klucz, który przysłał."""
    labeler = OptionLabeler([_opcja(21), _opcja("21")])

    assert type(labeler.resolve("OPT-1")) is int and labeler.resolve("OPT-1") == 21
    assert type(labeler.resolve("OPT-2")) is str and labeler.resolve("OPT-2") == "21"


def test_resolve_opt00_zwraca_none():
    """`OPT-00` -> None (brak dopasowania), bez wyjątku."""
    assert OptionLabeler([_opcja(1)]).resolve(NO_MATCH_LABEL) is None


def test_resolve_po_pozycji_mimo_powtorzonych_id():
    """Powtórzone `id` nie psują mapowania: każda etykieta wskazuje swoją pozycję."""
    labeler = OptionLabeler([_opcja(7, "A"), _opcja(8, "B"), _opcja(7, "C")])

    assert labeler.labels == ["OPT-1", "OPT-2", "OPT-3", "OPT-00"]           # etykiety unikalne mimo duplikatu
    assert [labeler.resolve(label) for label in ["OPT-1", "OPT-2", "OPT-3"]] == [7, 8, 7]
    assert labeler.entries[2].option.name == "C"                            # OPT-3 to trzecia pozycja, nie pierwsza z id 7


@pytest.mark.parametrize(
    "label",
    [
        "OPT-3",    # poza zakresem przy dwóch opcjach
        "OPT-0",    # nie ma pozycji zerowej; brak dopasowania to OPT-00
        "OPT-01",   # zera wiodące — to nie jest OPT-1
        "opt-1",    # inna wielkość liter — tolerancję zapisu rozstrzyga parser (krok 6)
        " OPT-1",   # białe znaki — jw.
        "",
    ],
)
def test_resolve_nieznana_etykieta_rzuca(label):
    """Etykieta spoza nadanych -> `UnknownLabelError` (nie None, które znaczyłoby „brak dopasowania")."""
    with pytest.raises(UnknownLabelError):
        OptionLabeler([_opcja(1), _opcja(2)]).resolve(label)
