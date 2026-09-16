"""Testy jednostkowe kontraktu wejscia `POST /classify` — walidacja struktury `ClassifyRequest`, bez sieci.

Sprawdzamy bezposrednio na modelach: `ValidationError` z pydantic to w endpoincie 422. Walidacja
swiadomie minimalna (README "POST /classify"): wymagane pola, niepuste listy, `id` jako liczba
calkowita albo string zwracany w typie z zadania. Tresci (puste teksty, powtorzone `id`) NIE
sprawdzamy, a dlugosci limituje serwis (413).
"""

import pytest
from pydantic import ValidationError

from app.models import ClassifyRequest


def _option(**overrides) -> dict:
    """Pomocniczo: poprawna opcja jako dane JSON; `overrides` podmienia pojedyncze pola."""
    return {"id": 21, "name": "Skargi i interwencje konsumenckie", "description": "Skargi konsumentow na dostawcow uslug.", **overrides}


def _payload(**overrides) -> dict:
    """Pomocniczo: poprawne zadanie jako dane JSON; `overrides` podmienia pola najwyzszego poziomu."""
    return {"summaries": ["• Typ pisma: skarga\n• Czego dotyczy: zawyzony rachunek operatora"], "options": [_option()], **overrides}


# --- Poprawne wejscie ---------------------------------------------------------------


def test_przyklad_z_readme_przechodzi():
    """Zadanie jak w README (dwa streszczenia, `id` liczbowe i tekstowe, `examples` tekst / null / brak) -> poprawne."""
    req = ClassifyRequest.model_validate(_payload(
        summaries = ["• Typ pisma: skarga", "• Typ pisma: faktura VAT"],
        options   = [_option(id=21, examples="Skarga na zawyzony rachunek."), _option(id=22, examples=None), _option(id="numeracja-7")],
    ))
    assert [(o.id, o.examples) for o in req.options] == [(21, "Skarga na zawyzony rachunek."), (22, None), ("numeracja-7", None)]


@pytest.mark.parametrize("option_id", [21, "21", "numeracja-7"])
def test_id_zachowuje_typ(option_id):
    """`id` wraca w typie z zadania: int zostaje int, string (takze cyfrowy) zostaje stringiem."""
    req = ClassifyRequest.model_validate(_payload(options=[_option(id=option_id)]))
    assert req.options[0].id == option_id and type(req.options[0].id) is type(option_id)


# --- Odrzucane wejscie (422) ------------------------------------------------------------


@pytest.mark.parametrize("field", ["summaries", "options"])
def test_brak_listy_odrzucony(field):
    """Brak `summaries` albo `options` -> `ValidationError`."""
    payload = _payload()
    del payload[field]
    with pytest.raises(ValidationError):
        ClassifyRequest.model_validate(payload)


@pytest.mark.parametrize("field", ["summaries", "options"])
def test_pusta_lista_odrzucona(field):
    """Pusta lista `summaries` albo `options` -> `ValidationError`."""
    with pytest.raises(ValidationError):
        ClassifyRequest.model_validate(_payload(**{field: []}))


@pytest.mark.parametrize("field", ["id", "name", "description"])
def test_brak_wymaganego_pola_opcji_odrzucony(field):
    """Brak `id`, `name` albo `description` w opcji -> `ValidationError`."""
    option = _option()
    del option[field]
    with pytest.raises(ValidationError):
        ClassifyRequest.model_validate(_payload(options=[option]))


@pytest.mark.parametrize("option_id", [True, 21.0])
def test_id_bez_koercji(option_id):
    """`true` i `21.0` -> `ValidationError`; bez strict pydantic zamienilby je na 1 i 21 (inny klucz niz od klienta)."""
    with pytest.raises(ValidationError):
        ClassifyRequest.model_validate(_payload(options=[_option(id=option_id)]))
