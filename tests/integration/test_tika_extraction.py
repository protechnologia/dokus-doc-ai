"""Testy integracyjne kroku 1 — warstwa ekstrakcji/OCR (Tika).

Pokrywaja trzy sciezki:
  1. serwer zyje,
  2. ekstrakcja natywna (DOCX -> POI), polskie znaki bez OCR,
  3. OCR skanu po polsku (PNG -> Tesseract) — kluczowy dowod, ze pakiet `pol` dziala.

Pliki testowe sa generowane w locie, zeby nie trzymac binariow w repo.
"""

import io
import os

import docx
import pytest
from PIL import Image, ImageDraw, ImageFont

# Parasol `integration` (wszystkie testy integracyjne) + węższy `integration_tika`.
pytestmark = [pytest.mark.integration, pytest.mark.integration_tika]

# Polski pangram — zawiera wszystkie znaki diakrytyczne.
PL_PANGRAM = "Zażółć gęślą jaźń"

# Typowe lokalizacje fontu z polskimi glifami (Linux/macOS).
_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
]


def _find_font() -> str | None:
    """Zwraca sciezke do pierwszego dostepnego fontu TTF z polskimi glifami albo None."""
    return next((p for p in _FONT_CANDIDATES if os.path.exists(p)), None)


def _extract_text(client, data: bytes, content_type: str) -> str:
    """PUT na /tika, zwraca czysty tekst (jak `curl -T ... /tika`)."""
    resp = client.put(
        "/tika",
        content=data,
        headers={"Accept": "text/plain", "Content-Type": content_type},
    )
    assert resp.status_code == 200, resp.text
    return resp.text


def test_server_alive(tika_client):
    """tika-server odpowiada na GET /tika powitalnym komunikatem."""
    resp = tika_client.get("/tika")
    assert resp.status_code == 200
    assert "Tika" in resp.text


def test_native_extraction_docx(tika_client):
    """DOCX czytany natywnie (POI) — polskie znaki bez udzialu OCR."""
    document = docx.Document()
    document.add_paragraph(PL_PANGRAM)
    buf = io.BytesIO()
    document.save(buf)

    text = _extract_text(
        tika_client,
        buf.getvalue(),
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    assert "gęślą" in text
    assert "jaźń" in text


def test_ocr_polish_png(tika_client):
    """Skan (PNG) -> OCR. Kryterium kroku 1: poprawne polskie znaki, nie krzaki."""
    font_path = _find_font()
    if not font_path:
        pytest.skip("Brak fontu TTF z polskimi glifami (np. DejaVuSans) — pomijam OCR.")

    img = Image.new("RGB", (1100, 220), "white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(font_path, 56)
    draw.text((30, 70), PL_PANGRAM, fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, format="PNG")

    text = _extract_text(tika_client, buf.getvalue(), "image/png").lower()

    # Najwazniejsze: OCR zwrocil polskie znaki diakrytyczne (dowod, ze 'pol' wjechal).
    assert any(ch in text for ch in "ążółćśęń"), (
        f"OCR nie zwrocil polskich znakow — pakiet 'pol' moze nie dzialac. "
        f"Otrzymano: {text!r}"
    )
