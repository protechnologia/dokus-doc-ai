"""Testy jednostkowe `TextTruncator` — trunkacja początek / środek / koniec, bez I/O.

Budżet testowy dobrany tak, by arytmetyka była czytelna: `max_chars = MARKER_RESERVE + 100`,
więc na treść zostaje dokładnie 100 znaków i proporcje w procentach = budżety w znakach
(45/20/35 -> 45/20/35 znaków). Oczekiwany tekst składamy jawnie z wycinków i znaczników,
żeby test pokazywał kształt wejścia modelu, a nie tylko liczby.

Tekst testowy `_doc(n)` to cyfry pozycji (`0123456789012...`) — wycinek jednoznacznie
pokazuje, skąd pochodzi. Czyste helpery (`_middle_percent`, `_part_budgets`, `_middle_start`,
`_part`, `_assemble`) wołane wprost na klasie.
"""

import pytest

from app.summarization.truncation import MARKER_RESERVE, OMISSION_MARKER, TextPart, TextParts, TextTruncator

# Budżet na treść = 100 znaków (reszta to stała rezerwa na dwa znaczniki).
_CONTENT = 100
_MAX = MARKER_RESERVE + _CONTENT

# Znacznik jako osobny akapit — tak, jak stoi w tekście dla modelu.
_SEP = "\n\n"


def _doc(n: int) -> str:
    """Pomocniczo: tekst długości `n` z cyfr pozycji (wycinek pokazuje, skąd pochodzi)."""
    return "".join(str(i % 10) for i in range(n))


def _join(*pieces: str) -> str:
    """Pomocniczo: sklej kawałki separatorem akapitowym (jak `_assemble`)."""
    return _SEP.join(pieces)


# --- Tekst mieszczący się w budżecie ---------------------------------------------


def test_tekst_krotszy_niz_budzet_bez_zmian():
    """Tekst krótszy niż budżet -> całość bez zmian, `truncated` False, `parts` None."""
    text = _doc(50)
    result = TextTruncator(_MAX).apply(text)
    assert result.text == text
    assert result.truncated is False
    assert result.parts is None


def test_tekst_rowny_budzetowi_bez_zmian():
    """Granica: długość == budżet NIE jest cięta (tniemy dopiero > budżet)."""
    text = _doc(_MAX)
    result = TextTruncator(_MAX).apply(text)
    assert result.text == text
    assert result.parts is None


# --- Warianty proporcji (tekst 1000 znaków, budżet treści 100) -------------------


def test_domyslne_45_35_trzy_czesci_i_dwa_znaczniki():
    """Domyślnie 45/20/35: początek [0,45], środek wokół n/2 [490,510], koniec [965,1000], dwa znaczniki."""
    text = _doc(1000)
    result = TextTruncator(_MAX).apply(text)

    assert result.truncated is True
    assert result.parts == TextParts(
        head   = TextPart(percent=45, start=0, end=45),
        middle = TextPart(percent=20, start=490, end=510),
        tail   = TextPart(percent=35, start=965, end=1000),
    )
    assert result.text == _join(text[0:45], OMISSION_MARKER, text[490:510], OMISSION_MARKER, text[965:1000])


def test_45_55_bez_srodka_jeden_znacznik():
    """45/55 -> środek wyłączony (proporcja 0, zakres None), między początkiem a końcem jeden znacznik."""
    text = _doc(1000)
    result = TextTruncator(_MAX).apply(text, head_percent=45, tail_percent=55)

    assert result.parts.middle == TextPart(percent=0, start=None, end=None)
    assert result.parts.head == TextPart(percent=45, start=0, end=45)
    assert result.parts.tail == TextPart(percent=55, start=945, end=1000)
    assert result.text == _join(text[0:45], OMISSION_MARKER, text[945:1000])


def test_poczatek_0_znacznik_na_poczatku():
    """0/35 -> początek wyłączony: tekst zaczyna się znacznikiem, potem środek (65) i koniec (35)."""
    text = _doc(1000)
    result = TextTruncator(_MAX).apply(text, head_percent=0, tail_percent=35)

    assert result.parts.head == TextPart(percent=0)
    assert result.parts.middle == TextPart(percent=65, start=467, end=532)
    assert result.text == _join(OMISSION_MARKER, text[467:532], OMISSION_MARKER, text[965:1000])


def test_koniec_0_znacznik_na_koncu():
    """60/0 -> koniec wyłączony: początek, znacznik, środek (40) i znacznik zamykający."""
    text = _doc(1000)
    result = TextTruncator(_MAX).apply(text, head_percent=60, tail_percent=0)

    assert result.parts.tail == TextPart(percent=0)
    assert result.text == _join(text[0:60], OMISSION_MARKER, text[480:520], OMISSION_MARKER)


def test_100_0_sam_poczatek():
    """100/0 -> sam początek (cały budżet treści) i jeden znacznik na końcu."""
    text = _doc(1000)
    result = TextTruncator(_MAX).apply(text, head_percent=100, tail_percent=0)

    assert result.parts.middle == TextPart(percent=0)
    assert result.text == _join(text[0:100], OMISSION_MARKER)


def test_0_100_sam_koniec():
    """0/100 -> sam koniec i jeden znacznik na początku."""
    text = _doc(1000)
    result = TextTruncator(_MAX).apply(text, head_percent=0, tail_percent=100)

    assert result.parts.head == TextPart(percent=0)
    assert result.text == _join(OMISSION_MARKER, text[900:1000])


def test_0_0_sam_srodek_wokol_geometrycznego_srodka():
    """0/0 -> sam środek (100%) wyśrodkowany na n/2, ze znacznikami po obu stronach."""
    text = _doc(1000)
    result = TextTruncator(_MAX).apply(text, head_percent=0, tail_percent=0)

    middle = result.parts.middle
    assert (middle.start, middle.end) == (450, 550)
    assert (middle.start + middle.end) / 2 == 500   # geometryczny środek tekstu
    assert result.text == _join(OMISSION_MARKER, text[450:550], OMISSION_MARKER)


# --- Przypadki brzegowe ----------------------------------------------------------


def test_tekst_tuz_nad_budzetem():
    """Tekst o 1 znak dłuższy niż budżet -> cięty; zakresy rosnące, rozłączne, wynik w budżecie."""
    text = _doc(_MAX + 1)
    result = TextTruncator(_MAX).apply(text)

    parts = result.parts
    assert result.truncated is True
    assert parts.head.end <= parts.middle.start < parts.middle.end <= parts.tail.start
    assert len(result.text) <= _MAX


def test_stykajace_sie_czesci_sklejone_bez_znacznika():
    """90/0 tuż nad budżetem: środek dosunięty do końca początku -> sklejone wprost, bez znacznika i `\\n\\n`."""
    text = _doc(_MAX + 1)
    result = TextTruncator(_MAX).apply(text, head_percent=90, tail_percent=0)

    assert result.parts.head == TextPart(percent=90, start=0, end=90)
    assert result.parts.middle == TextPart(percent=10, start=90, end=100)   # dosunięty, nie wokół n/2
    assert result.text == _join(text[0:100], OMISSION_MARKER)


def test_jeden_dlugi_wyraz_tniety_jak_kazdy_tekst():
    """Tekst bez białych znaków (jeden wyraz) -> cięcie na offsetach, bez wyjątku, wynik w budżecie."""
    text = "a" * 5000
    result = TextTruncator(_MAX).apply(text)

    assert result.truncated is True
    assert result.parts.head == TextPart(percent=45, start=0, end=45)
    assert len(result.text) <= _MAX


@pytest.mark.parametrize("max_chars", [_MAX, 1000, 90_000])
def test_znaczniki_mieszcza_sie_w_budzecie_dla_wszystkich_proporcji(max_chars):
    """Dla każdej proporcji (co 5%) i różnych długości: wynik <= budżet, zakresy rosnące i rozłączne."""
    for n in (max_chars + 1, max_chars + 73, 2 * max_chars, 7 * max_chars + 3):
        text = _doc(n)
        truncator = TextTruncator(max_chars)
        for head in range(0, 101, 5):
            for tail in range(0, 101 - head, 5):
                result = truncator.apply(text, head_percent=head, tail_percent=tail)

                # Stała rezerwa gwarantuje budżet niezależnie od liczby faktycznych znaczników.
                assert len(result.text) <= max_chars, (n, head, tail)
                assert result.text.count(OMISSION_MARKER) <= 2, (n, head, tail)

                # Zakresy części włączonych: w granicach tekstu, rosnące, bez nakładania.
                spans = [(p.start, p.end) for p in (result.parts.head, result.parts.middle, result.parts.tail) if p.start is not None]
                assert all(0 <= s <= e <= n for s, e in spans), (n, head, tail)
                assert all(a[1] <= b[0] for a, b in zip(spans, spans[1:])), (n, head, tail)


# --- Konfiguracja i walidacja ----------------------------------------------------


def test_zbyt_maly_budzet_rzuca_value_error():
    """Budżet nie większy niż rezerwa na znaczniki -> `ValueError` przy budowie (błąd konfiguracji)."""
    with pytest.raises(ValueError):
        TextTruncator(MARKER_RESERVE)


@pytest.mark.parametrize("head, tail", [(60, 50), (-1, 35), (45, 101)])
def test_niespojne_proporcje_rzucaja_value_error(head, tail):
    """Suma > 100 albo wartość spoza 0–100 -> `ValueError`, także dla tekstu mieszczącego się w budżecie."""
    truncator = TextTruncator(_MAX)
    with pytest.raises(ValueError):
        truncator.apply(_doc(1000), head_percent=head, tail_percent=tail)
    with pytest.raises(ValueError):
        truncator.apply("krótki", head_percent=head, tail_percent=tail)


# --- Czyste helpery (punktowo) ---------------------------------------------------


def test_middle_percent_to_reszta_do_100():
    """Środek = 100 - początek - koniec (także 0, gdy początek i koniec biorą całość)."""
    assert TextTruncator._middle_percent(45, 35) == 20
    assert TextTruncator._middle_percent(45, 55) == 0


def test_part_budgets_zaokragla_w_dol():
    """Budżety części zaokrąglane w dół — suma nigdy nie przekracza budżetu treści."""
    assert TextTruncator._part_budgets(99, 45, 20, 35) == (44, 19, 34)


def test_middle_start_wysrodkowany_i_dosuniety():
    """Okno środka wokół n/2; dosunięte do końca początku albo do początku końca, gdy by na nie weszło."""
    assert TextTruncator._middle_start(1000, 45, 20, 35) == 490   # wyśrodkowane
    assert TextTruncator._middle_start(200, 150, 10, 0) == 150    # centrum (95) wewnątrz początku -> dosunięte
    assert TextTruncator._middle_start(200, 0, 10, 150) == 40     # centrum (95) wewnątrz końca -> dosunięte


def test_part_proporcja_0_bez_zakresu():
    """Proporcja 0 -> część wyłączona bez zakresu; włączona zachowuje zakres."""
    assert TextTruncator._part(0, 490, 490) == TextPart(percent=0, start=None, end=None)
    assert TextTruncator._part(20, 490, 510) == TextPart(percent=20, start=490, end=510)


def test_assemble_znacznik_tylko_w_miejscach_pominiecia():
    """Znacznik przed pierwszym fragmentem i po ostatnim tylko, gdy coś tam pominięto."""
    text = "AAAABBBBCCCC"
    parts = TextParts(head=TextPart(percent=0), middle=TextPart(percent=50, start=4, end=8), tail=TextPart(percent=50, start=8, end=12))
    # Środek i koniec się stykają -> jeden fragment; pominięty tylko początek -> znacznik na starcie.
    assert TextTruncator._assemble(text, parts) == _join(OMISSION_MARKER, "BBBBCCCC")
