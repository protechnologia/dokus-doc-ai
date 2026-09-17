"""Testy jednostkowe schematu odpowiedzi klasyfikacji (`build_response_schema`) — bez sieci.

Etykiety z realnego `OptionLabeler`, więc test pilnuje też, że `enum` przejmuje kolejność
pozycji (z `OPT-00` na końcu), zamiast składać własną.
"""

from app.classification.labels import ClassificationOption, OptionLabeler
from app.classification.schema import LABEL_FIELD, RATIONALE_FIELD, build_response_schema

_OPTIONS = [
    ClassificationOption(id=21, name="Skargi", description="Skargi konsumentów."),
    ClassificationOption(id="db-7781", name="Kadry", description="Sprawy pracownicze."),
]


def test_enum_to_dokladnie_etykiety_pozycji_w_kolejnosci():
    """`enum` etykiety = etykiety opcji + `OPT-00`, w kolejności `OptionLabeler.labels`."""
    schema = build_response_schema(OptionLabeler(_OPTIONS).labels)
    assert schema["properties"][LABEL_FIELD]["enum"] == ["OPT-1", "OPT-2", "OPT-00"]


def test_uzasadnienie_przed_etykieta():
    """Kolejność pól = kolejność generowania: uzasadnienie przed etykietą, w `properties` i w `required`."""
    schema = build_response_schema(["OPT-1", "OPT-00"])
    assert list(schema["properties"]) == [RATIONALE_FIELD, LABEL_FIELD]
    assert schema["required"] == [RATIONALE_FIELD, LABEL_FIELD]


def test_schemat_spelnia_wymogi_strict():
    """Tryb strict: każde pole wymagane, bez pól dodatkowych, uzasadnienie jako tekst."""
    schema = build_response_schema(["OPT-1", "OPT-00"])
    assert schema["type"] == "object"
    assert set(schema["required"]) == set(schema["properties"])
    assert schema["additionalProperties"] is False
    assert schema["properties"][RATIONALE_FIELD] == {"type": "string"}


def test_enum_to_kopia_wejscia():
    """Schemat nie trzyma referencji do listy wołającego — późniejsza zmiana listy go nie psuje."""
    labels = ["OPT-1", "OPT-00"]
    schema = build_response_schema(labels)
    labels.append("OPT-7")
    assert schema["properties"][LABEL_FIELD]["enum"] == ["OPT-1", "OPT-00"]
