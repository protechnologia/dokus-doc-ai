"""Testy jednostkowe czystych helperow ExtractionService (krok 2.3.2) — bez transportu.

Helpery sa statyczne/klasowe i czyste (zero I/O), wiec wolamy je wprost na klasie.
Kazdy ma swoj test, bo w 2.3.2 swiadomie wydzielilismy nawet krotkie operacje
(liczenie znakow/slow, wyciagniecie MIME) do osobnych metod — testujemy je punktowo,
nie tylko przez zlozenie `_build_metadata`.

Orkiestracja `extract` (async + atrapa transportu, pusty -> wyjatek) ma osobny plik:
`test_extraction_service_extract.py`.
"""

from app.extraction.service import ExtractionMetadata, ExtractionService


# --- _normalize_whitespace: porzadkowanie bialych znakow -------------------------


def test_normalize_zwija_inline_spacje_i_taby():
    # Scenariusz: w linii ciagi spacji i tabow (typowe dla tekstu z PDF/OCR).
    # Oczekujemy: zwiniete do pojedynczej spacji, konce przyciete.
    assert ExtractionService._normalize_whitespace("Tresc \t  dalej  ") == "Tresc dalej"


def test_normalize_zwija_ciagi_pustych_linii_do_jednej():
    # Scenariusz: nadmiar pustych linii miedzy akapitami.
    # Oczekujemy: pojedyncza pusta linia (akapit zostaje), wiodace/koncowe puste znikaja.
    assert ExtractionService._normalize_whitespace("\n\nTytul\n\n\n\nTresc\n\n") == "Tytul\n\nTresc"


def test_normalize_sam_whitespace_daje_pusty_string():
    # Scenariusz: wejscie to wylacznie biale znaki (kandydat na EmptyExtractionError).
    # Oczekujemy: "" — czysty sygnal "brak tresci" dla warstwy wyzej.
    assert ExtractionService._normalize_whitespace("  \n\t  \n ") == ""


# --- _count_chars / _count_words: dlugosc ----------------------------------------


def test_count_chars_liczy_znaki():
    # Scenariusz: tekst po normalizacji.
    # Oczekujemy: dlugosc w znakach.
    assert ExtractionService._count_chars("Tresc pisma") == 11


def test_count_words_liczy_slowa_po_bialych_znakach():
    # Scenariusz: slowa rozdzielone roznymi bialymi znakami.
    # Oczekujemy: liczba niepustych tokenow.
    assert ExtractionService._count_words("Tresc pisma do dekretacji") == 4


def test_count_words_pusty_tekst_to_zero():
    # Scenariusz: pusty tekst.
    # Oczekujemy: 0 (split() nie daje pustych tokenow).
    assert ExtractionService._count_words("") == 0


# --- _as_text: sprowadzenie wartosci metadanej do stringa ------------------------


def test_as_text_lista_bierze_pierwszy_niepusty():
    # Scenariusz: pole wielowartosciowe Tiki (lista).
    # Oczekujemy: pierwszy niepusty element jako tekst.
    assert ExtractionService._as_text(["", "pl", "en"]) == "pl"


def test_as_text_pusta_wartosc_daje_none():
    # Scenariusz: brak wartosci / pusty string.
    # Oczekujemy: None.
    assert ExtractionService._as_text("") is None
    assert ExtractionService._as_text(None) is None


# --- _pick_content_type: MIME bez parametrow -------------------------------------


def test_pick_content_type_odcina_charset():
    # Scenariusz: Tika podaje typ z parametrem charset (typowe dla text/plain).
    # Oczekujemy: sam typ, bez "; charset=...".
    assert ExtractionService._pick_content_type({"Content-Type": "text/plain; charset=UTF-8"}) == "text/plain"


def test_pick_content_type_brak_daje_none():
    # Scenariusz: metadane bez Content-Type.
    # Oczekujemy: None.
    assert ExtractionService._pick_content_type({}) is None


# --- _pick_language: jezyk defensywnie -------------------------------------------


def test_pick_language_z_klucza_language():
    # Scenariusz: w metadanych jest generyczny klucz `language` (zadeklarowany, nie z detekcji).
    # Oczekujemy: ta wartosc.
    assert ExtractionService._pick_language({"language": "pl"}) == "pl"


def test_pick_language_fallback_na_dc_language():
    # Scenariusz: brak `language`, ale jest deklaracja z wlasciwosci pliku (`dc:language`).
    # Oczekujemy: wartosc z fallbacku.
    assert ExtractionService._pick_language({"dc:language": "en"}) == "en"


def test_pick_language_brak_daje_none():
    # Scenariusz: zaden znany klucz jezyka (porzadna detekcja to pozniejszy refinement).
    # Oczekujemy: None.
    assert ExtractionService._pick_language({"Content-Type": "application/pdf"}) is None


# --- _build_metadata: zlozenie helperow ------------------------------------------


def test_build_metadata_sklada_wszystkie_pola():
    # Scenariusz: typowe metadane + tekst po normalizacji.
    # Oczekujemy: MIME bez charset, jezyk, oraz dlugosc liczona na podanym tekscie.
    meta = ExtractionService._build_metadata(
        "Tresc pisma",
        {"Content-Type": "text/plain; charset=UTF-8", "language": "pl"},
    )
    assert meta == ExtractionMetadata(content_type="text/plain", language="pl", char_count=11, word_count=2)
