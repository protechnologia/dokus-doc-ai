"""Testy jednostkowe czystych helperow TikaClient (krok 2.3.1) — bez sieci.

Testujemy trzy statyczne, czyste metody:
  - `_build_headers`  — sklada naglowki zadania do /rmeta/text,
  - `_pick_text`      — wyjmuje tresc dokumentu-kontenera z odpowiedzi rmeta,
  - `_pick_metadata`  — wyjmuje metadane (bez samej tresci).
Statyczne -> wolamy wprost na klasie, bez instancji i bez I/O.

Mapowanie wyjatkow httpx -> TikaError ma osobny plik: `test_tika_client_errors.py`.
"""

from app.extraction.client_tika import TikaClient


# --- _build_headers: skladanie naglowkow ----------------------------------------


def test_build_headers_tylko_accept_gdy_brak_podpowiedzi():
    # Scenariusz: nie znamy typu ani nazwy pliku, bez wymuszania OCR (wszystko None).
    # Oczekujemy: sam Accept JSON — wykrywanie typu i strategie OCR zostawiamy Tice.
    h = TikaClient._build_headers(None, None, None)
    assert h == {"Accept": "application/json"}


def test_build_headers_dodaje_content_type():
    # Scenariusz: znamy MIME pliku.
    # Oczekujemy: trafia jako podpowiedz Content-Type.
    h = TikaClient._build_headers("application/pdf", None, None)
    assert h["Content-Type"] == "application/pdf"


def test_build_headers_dodaje_nazwe_pliku():
    # Scenariusz: znamy nazwe pliku (dodatkowa podpowiedz typu po rozszerzeniu).
    # Oczekujemy: nazwa laduje w Content-Disposition.
    h = TikaClient._build_headers(None, "pismo.pdf", None)
    assert h["Content-Disposition"] == 'attachment; filename="pismo.pdf"'


def test_build_headers_dodaje_ocr_strategy():
    # Scenariusz: domena wymusza OCR dla smieciowej warstwy PDF (krok 2.3.5).
    # Oczekujemy: per-request naglowek X-Tika-PDFOcrStrategy (nadpisuje globalny auto).
    h = TikaClient._build_headers("application/pdf", None, "ocr_only")
    assert h["X-Tika-PDFOcrStrategy"] == "ocr_only"


def test_build_headers_bez_ocr_strategy_nie_dodaje_naglowka():
    # Scenariusz: brak wymuszenia (None) — najczestszy przypadek (dziala globalny auto).
    # Oczekujemy: naglowka OCR nie ma w ogole (nie pusty string, nie domyslny).
    h = TikaClient._build_headers("application/pdf", None, None)
    assert "X-Tika-PDFOcrStrategy" not in h


# --- _pick_text: wyjecie tresci kontenera ---------------------------------------


def test_pick_text_bierze_tresc_kontenera():
    # Scenariusz: typowa odpowiedz rmeta z jednym zasobem.
    # Oczekujemy: tresc spod klucza X-TIKA:content.
    payload = [{"X-TIKA:content": "Tresc pisma", "Content-Type": "application/pdf"}]
    assert TikaClient._pick_text(payload) == "Tresc pisma"


def test_pick_text_pusta_lista_daje_pusty_string():
    # Scenariusz: Tika zwrocila pusta liste zasobow (brak tresci).
    # Oczekujemy: "", a nie wyjatek (IndexError).
    assert TikaClient._pick_text([]) == ""


def test_pick_text_brak_klucza_tresci_daje_pusty_string():
    # Scenariusz: zasob bez wyekstrahowanej tresci (np. obraz bez tekstu) -> brak klucza.
    # Oczekujemy: brak klucza / None normalizowane do "".
    assert TikaClient._pick_text([{"Content-Type": "image/png"}]) == ""


def test_pick_text_ignoruje_zasoby_embedded():
    # Scenariusz: dokument-kontener [0] + zasob zagniezdzony [1].
    # Oczekujemy: bierzemy tylko tresc kontenera (children swiadomie pomijane w 2.3.1).
    payload = [
        {"X-TIKA:content": "Glowny tekst"},
        {"X-TIKA:content": "Tekst zalacznika"},
    ]
    assert TikaClient._pick_text(payload) == "Glowny tekst"


# --- _pick_metadata: wyjecie metadanych (bez tresci) ----------------------------


def test_pick_metadata_pomija_tresc():
    # Scenariusz: odpowiedz rmeta z trescia + metadanymi.
    # Oczekujemy: zwracamy metadane, ale BEZ klucza X-TIKA:content (to robi _pick_text).
    payload = [{"X-TIKA:content": "tresc", "Content-Type": "application/pdf", "language": "pl"}]
    meta = TikaClient._pick_metadata(payload)
    assert meta == {"Content-Type": "application/pdf", "language": "pl"}
    assert "X-TIKA:content" not in meta


def test_pick_metadata_pusta_lista_daje_pusty_slownik():
    # Scenariusz: pusta odpowiedz rmeta.
    # Oczekujemy: {} bez wyjatku.
    assert TikaClient._pick_metadata([]) == {}
