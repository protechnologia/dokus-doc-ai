"""Test integracyjny endpointu POST /extract (krok 2.3.3) — pelny przeplyw przez usluge.

Uderza w dzialajaca usluge FastAPI, ktora woła realna Tike (DI: ExtractionService nad
TikaClient). Dowodzi, ze granica HTTP spina sie z domena i transportem end-to-end:
  - DOCX natywny -> tekst (POI) + metadane (MIME 'wordprocessing'),
  - OCR PNG       -> tekst ze skanu (Tesseract + pakiet 'pol'); niezalezne od decyzji PUA.

Pliki testowe generowane w locie (python-docx/Pillow), bez binariow w repo. Gdy usluga
niedostepna -> SKIP (fixture `fastapi_client`), nie fail. Test PDF (warstwa PUA) jest
WSTRZYMANY do kroku 2.3.5.
"""

import base64
import io
import os

import pytest

# Parasol `integration` + wezszy `integration_fastapi` (uderzamy w usluge FastAPI).
pytestmark = [pytest.mark.integration, pytest.mark.integration_fastapi]

# Polski pangram — wszystkie znaki diakrytyczne (dowod, ze polskie znaki przeszly).
PL_PANGRAM = "Zażółć gęślą jaźń"

# Typowe lokalizacje fontu z polskimi glifami (jak w test_tika_extraction.py).
_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
]


def _find_font() -> str | None:
    return next((p for p in _FONT_CANDIDATES if os.path.exists(p)), None)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def test_extract_docx_przez_endpoint(fastapi_client):
    """DOCX -> POST /extract: 200, tekst z polskimi znakami, metadane MIME 'wordprocessing'."""
    docx = pytest.importorskip("docx", reason="python-docx nie zainstalowany")

    document = docx.Document()
    document.add_paragraph(PL_PANGRAM)
    buf = io.BytesIO()
    document.save(buf)

    # Bez content_type/filename — autodetekcja typu po stronie Tiki.
    resp = fastapi_client.post("/extract", json={"content_base64": _b64(buf.getvalue())})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "gęślą" in body["text"]                                        # polskie znaki end-to-end
    assert "wordprocessing" in (body["metadata"]["content_type"] or "")   # realny MIME DOCX
    assert body["metadata"]["word_count"] == 3
    assert body["metadata"]["char_count"] > 0


def test_extract_ocr_png_przez_endpoint(fastapi_client):
    """Skan PNG -> POST /extract: OCR (pakiet 'pol') zwraca polskie znaki. Niezalezne od PUA."""
    pytest.importorskip("PIL", reason="Pillow nie zainstalowany")
    from PIL import Image, ImageDraw, ImageFont

    font_path = _find_font()
    if not font_path:
        pytest.skip("Brak fontu TTF z polskimi glifami (np. DejaVuSans) — pomijam OCR.")

    img = Image.new("RGB", (1100, 220), "white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(font_path, 56)
    draw.text((30, 70), PL_PANGRAM, fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, format="PNG")

    resp = fastapi_client.post(
        "/extract",
        json={"content_base64": _b64(buf.getvalue()), "content_type": "image/png"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    text = body["text"].lower()
    # Najwazniejsze: OCR zwrocil polskie znaki diakrytyczne (dowod, ze 'pol' realnie dziala).
    assert any(ch in text for ch in "ążółćśęń"), f"OCR nie zwrocil polskich znakow. Otrzymano: {text!r}"
    assert body["metadata"]["content_type"] == "image/png"


def test_extract_zly_base64_daje_422(fastapi_client):
    """Nie-base64 -> 422 (walidacja wejscia w routerze, bez kontaktu z Tika)."""
    resp = fastapi_client.post("/extract", json={"content_base64": "to nie jest base64!!!"})
    assert resp.status_code == 422, resp.text
