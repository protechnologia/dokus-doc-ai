"""Testy jednostkowe `PdfPageLimiter` (krok 2.3.5) — liczenie/ciecie stron PDF, bez sieci.

Helpery na `pypdf` i `apply` dostaja PRAWDZIWE bajty PDF generowane w locie pustymi stronami
(`PdfWriter.add_blank_page`) — wystarcza, bo liczymy/tniemy strony, a nie ekstrahujemy tresc.
"""

from io import BytesIO

from pypdf import PdfReader, PdfWriter

from app.extraction.pdf import PageLimit, PdfPageLimiter


def _make_pdf(n_pages: int) -> bytes:
    """Zbuduj prawdziwy PDF z `n_pages` pustymi stronami (do testow liczenia/ciecia)."""
    writer = PdfWriter()
    for _ in range(n_pages):
        writer.add_blank_page(width=200, height=200)
    buf = BytesIO()
    writer.write(buf)
    return buf.getvalue()


# --- is_pdf: rozpoznanie PDF po magic bytes --------------------------------------


def test_is_pdf_po_sygnaturze():
    assert PdfPageLimiter.is_pdf(b"%PDF-1.7\nreszta") is True
    assert PdfPageLimiter.is_pdf(b"PK\x03\x04") is False        # ZIP/DOCX
    assert PdfPageLimiter.is_pdf(b"") is False


def test_is_pdf_sygnatura_z_prefiksem():
    # `%PDF-` bywa poprzedzone smieciem przed naglowkiem — szukamy w prefiksie, nie tylko [0:5].
    assert PdfPageLimiter.is_pdf(b"junk\x00\x01%PDF-1.4 ...") is True


# --- _page_count / _take_first_pages: mechanika pypdf ----------------------------


def test_page_count_liczy_strony():
    assert PdfPageLimiter._page_count(_make_pdf(3)) == 3


def test_page_count_uszkodzony_pdf_to_none():
    # pypdf nie sparsuje -> None (degradacja do "bez limitu", nie wyjatek).
    assert PdfPageLimiter._page_count(b"%PDF-1.7 polamany") is None


def test_take_first_pages_zostawia_pierwsze_n():
    trimmed = PdfPageLimiter._take_first_pages(_make_pdf(5), 2)
    assert trimmed is not None
    assert len(PdfReader(BytesIO(trimmed)).pages) == 2


def test_take_first_pages_uszkodzony_to_none():
    assert PdfPageLimiter._take_first_pages(b"%PDF-1.7 polamany", 2) is None


# --- apply: straznik zasobow (limit PRZED auto) ----------------------------------


def test_apply_nie_pdf_bez_zmian():
    # DOCX/obraz: stron nie liczymy, bajty bez zmian, pages_* = None.
    data = b"PK\x03\x04 to nie pdf"
    limit = PdfPageLimiter().apply(data)
    assert isinstance(limit, PageLimit)
    assert limit.data is data
    assert limit.pages_total is None and limit.pages_processed is None
    assert limit.truncated is False


def test_apply_pdf_w_limicie_bez_ciecia():
    # PDF mieszczacy sie w limicie: processed = total, bez ciecia.
    data = _make_pdf(3)
    limit = PdfPageLimiter(max_pages=30).apply(data)
    assert limit.data is data                    # te same bajty (nie ruszamy)
    assert limit.pages_total == 3 and limit.pages_processed == 3
    assert limit.truncated is False


def test_apply_pdf_za_duzy_tnie_do_n():
    # PDF powyzej limitu: tniemy do N, znacznik truncated, processed = N, total zachowany.
    data = _make_pdf(5)
    limit = PdfPageLimiter(max_pages=2).apply(data)
    assert limit.truncated is True
    assert limit.pages_total == 5 and limit.pages_processed == 2
    assert len(PdfReader(BytesIO(limit.data)).pages) == 2   # realnie pociety strumien


def test_apply_uszkodzony_pdf_przetwarza_calosc():
    # pypdf nie policzy -> bez limitu (bajty bez zmian, pages_* None), nie wyjatek.
    data = b"%PDF-1.7 polamany naglowek"
    limit = PdfPageLimiter(max_pages=2).apply(data)
    assert limit.data is data
    assert limit.pages_total is None and limit.truncated is False
