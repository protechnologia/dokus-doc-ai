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


def test_pick_content_type_sciaga_znacznik_ocr_tiki():
    # Scenariusz: obraz poddany OCR — Tika znakuje MIME wlasnym prefiksem "image/ocr-".
    # Oczekujemy: prawdziwy MIME (znacznik parsera nie moze wyciec do kontraktu HTTP).
    assert ExtractionService._pick_content_type({"Content-Type": "image/ocr-png"}) == "image/png"
    assert ExtractionService._pick_content_type({"Content-Type": "image/ocr-jpeg"}) == "image/jpeg"
    # Zwykly obraz (bez OCR) przechodzi bez zmian.
    assert ExtractionService._pick_content_type({"Content-Type": "image/png"}) == "image/png"


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


# --- _pick_ocr_used: sygnal "czy poszlo OCR" z metadanych ------------------------

_TESSERACT = "org.apache.tika.parser.ocr.TesseractOCRParser"
_IMAGE_PARSER = "org.apache.tika.parser.image.ImageParser"


def test_ocr_from_page_count_sygnal_pdf():
    # Tika zwraca pdf:ocrPageCount jako string albo int; >0 = OCR poszedl.
    assert ExtractionService._ocr_from_page_count({"pdf:ocrPageCount": "1"}) is True
    assert ExtractionService._ocr_from_page_count({"pdf:ocrPageCount": 3}) is True
    assert ExtractionService._ocr_from_page_count({"pdf:ocrPageCount": 0}) is False
    assert ExtractionService._ocr_from_page_count({}) is False                         # brak klucza
    assert ExtractionService._ocr_from_page_count({"pdf:ocrPageCount": "x"}) is False  # nieparsowalne -> False


def test_ocr_from_parsed_by_sygnal_obrazu():
    # Dla obrazu Tika NIE zwraca pdf:ocrPageCount — jedynym sladem jest lista parserow.
    assert ExtractionService._ocr_from_parsed_by({"X-TIKA:Parsed-By": [_IMAGE_PARSER, _TESSERACT]}) is True
    assert ExtractionService._ocr_from_parsed_by({"X-TIKA:Parsed-By": [_IMAGE_PARSER]}) is False
    assert ExtractionService._ocr_from_parsed_by({}) is False                              # brak klucza
    assert ExtractionService._ocr_from_parsed_by({"X-TIKA:Parsed-By": _TESSERACT}) is False  # nie-lista -> brak sygnalu
    assert ExtractionService._ocr_from_parsed_by({"X-TIKA:Parsed-By": []}) is False


def test_pick_ocr_used_macierz_wejsc():
    # Macierz zmierzona na naszej Tice (2026-07-08) — patrz komentarz przy stalych w service.py.
    # Natywny PDF: warstwa tekstowa, OCR nie poszedl.
    assert ExtractionService._pick_ocr_used(
        {"Content-Type": "application/pdf", "pdf:ocrPageCount": "0", "X-TIKA:Parsed-By": ["...PDFParser"]}
    ) is False
    # Skan PDF: OCR poszedl, ale Tesseracta NIE MA na liscie parserow — ratuje licznik stron.
    assert ExtractionService._pick_ocr_used(
        {"Content-Type": "application/pdf", "pdf:ocrPageCount": "1", "X-TIKA:Parsed-By": ["...PDFParser"]}
    ) is True
    # Obraz: OCR poszedl, ale NIE MA licznika stron — ratuje lista parserow (regresja: bylo False).
    assert ExtractionService._pick_ocr_used(
        {"Content-Type": "image/ocr-png", "X-TIKA:Parsed-By": [_IMAGE_PARSER, _TESSERACT]}
    ) is True
    # Zaden sygnal (np. czysty tekst).
    assert ExtractionService._pick_ocr_used({"Content-Type": "text/plain"}) is False


# --- _build_metadata: zlozenie helperow ------------------------------------------


def test_build_metadata_sklada_wszystkie_pola():
    # Scenariusz: typowe metadane + tekst po normalizacji; bez OCR, bez stron (nie-PDF).
    # Oczekujemy: MIME bez charset, jezyk, dlugosc, oraz domyslne pola jakosci (ocr/strony).
    meta = ExtractionService._build_metadata(
        "Tresc pisma",
        {"Content-Type": "text/plain; charset=UTF-8", "language": "pl"},
        None,    # pages_total (nie-PDF)
        None,    # pages_processed
        False,   # ocr_truncated
    )
    assert meta == ExtractionMetadata(
        content_type="text/plain", language="pl", char_count=11, word_count=2,
        ocr_used=False, pages_total=None, pages_processed=None, ocr_truncated=False,
    )


def test_build_metadata_przepisuje_pola_jakosci():
    # Scenariusz: PDF po OCR z limitem stron (ocrPageCount>0 + przekazane pages_*).
    # Oczekujemy: ocr_used wyliczone z metadanych, pola stron przepisane, truncated zachowany.
    meta = ExtractionService._build_metadata(
        "Tresc po OCR",
        {"Content-Type": "application/pdf", "pdf:ocrPageCount": "2"},
        10,      # pages_total
        2,       # pages_processed
        True,    # ocr_truncated
    )
    assert meta.ocr_used is True
    assert meta.pages_total == 10 and meta.pages_processed == 2
    assert meta.ocr_truncated is True
