"""Test integracyjny endpointu POST /extract (krok 2.3.3) — pelny przeplyw przez usluge.

Uderza w dzialajaca usluge FastAPI, ktora woła realna Tike (DI: ExtractionService nad
TikaClient). Dowodzi, ze granica HTTP spina sie z domena i transportem end-to-end:
  - DOCX natywny -> tekst (POI) + metadane (MIME 'wordprocessing'),
  - OCR PNG       -> tekst ze skanu (Tesseract + pakiet 'pol'); niezalezne od decyzji PUA,
  - PDF z warstwa PUA (`samples/sample_01.pdf`) -> OCR-fallback (krok 2.3.5): natywna
    warstwa to smiec (1003 glify U+F0xx), wiec serwis wymusza OCR_ONLY i zwraca tresc.

Pliki testowe (DOCX/PNG) generowane w locie (python-docx/Pillow), bez binariow w repo.
PDF to realny `samples/sample_01.pdf` (poza repo; gdy brak -> SKIP). Gdy usluga
niedostepna -> SKIP (fixture `fastapi_client`), nie fail.
"""

import base64
import io
import os
from pathlib import Path

import docx
import pytest
from PIL import Image, ImageDraw, ImageFont

# Realny problematyczny PDF (poza repo): warstwa tekstowa = smiec PUA, ratuje go OCR-fallback.
_SAMPLE_PDF = Path(__file__).resolve().parents[2] / "samples" / "sample_01.pdf"

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


def test_extract_pdf_pua_ocr_fallback_przez_endpoint(fastapi_client):
    """PDF ze smieciowa warstwa PUA -> POST /extract: OCR-fallback ratuje tresc (krok 2.3.5).

    Dowodzi END-TO-END calej decyzji 2.3.5 na realnym, zmierzonym przypadku: natywnie
    `sample_01.pdf` daje 1003 glify w Private Use Area (smiec), wiec serwis wykrywa PUA i
    wymusza OCR_ONLY. Asercje: poprawna polska tresc (a NIE PUA), znacznik `ocr_used`,
    diagnostyka stron (1 strona, bez ciecia).
    """
    if not _SAMPLE_PDF.exists():
        pytest.skip(f"Brak {_SAMPLE_PDF} (poza repo) — pomijam test OCR-fallbacku PDF.")

    resp = fastapi_client.post(
        "/extract",
        json={"content_base64": _b64(_SAMPLE_PDF.read_bytes()), "content_type": "application/pdf"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    text = body["text"]
    # Tresc po OCR — realne polskie slowa z dokumentu (zmierzone: "Twierdzenie Stolza",
    # "rozdzialu"); diakrytyki dowodza, ze poszedl OCR z pakietem 'pol', nie smiec PUA.
    assert "Stolza" in text
    assert "rozdzia" in text                       # "rozdzialu" (z 'ł' lub bez — OCR)
    # Zero (lub znikomo) znakow PUA — warstwa smieciowa zostala zastapiona OCR-em.
    pua = sum(1 for ch in text if 0xE000 <= ord(ch) <= 0xF8FF)
    assert pua == 0, f"W tekscie po OCR zostaly znaki PUA: {pua}"
    # Metadane jakosci: OCR poszedl, 1 strona, bez ciecia.
    meta = body["metadata"]
    assert meta["ocr_used"] is True
    assert meta["pages_total"] == 1
    assert meta["ocr_truncated"] is False
    assert meta["content_type"] == "application/pdf"


def test_extract_zly_base64_daje_422(fastapi_client):
    """Nie-base64 -> 422 (walidacja wejscia w routerze, bez kontaktu z Tika)."""
    resp = fastapi_client.post("/extract", json={"content_base64": "to nie jest base64!!!"})
    assert resp.status_code == 422, resp.text
