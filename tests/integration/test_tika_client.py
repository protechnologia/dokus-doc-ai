"""Test integracyjny TikaClient (krok 2.3.1) — uderza w realny kontener Tika.

Sprawdza NASZ klient transportowy (nie sama konfiguracje Tiki — to robi
`test_tika_extraction.py`): ze `extract` zwraca `TikaRawResult` z tekstem i metadanymi
z realnej ekstrakcji DOCX, a typ pliku wykrywa autodetekcja Tiki (nie podajemy MIME).
Gdy Tika niedostepna -> SKIP (fixture `tika_url`), nie fail.

Klient jest async; uruchamiamy go w sync-tescie przez `asyncio.run` (jak `test_llm.py`),
bo projekt nie uzywa pytest-asyncio.
"""

import asyncio
import io

import docx
import pytest

from app.extraction import TikaClient, TikaRawResult

# Parasol `integration` + wezszy `integration_tika`.
pytestmark = [pytest.mark.integration, pytest.mark.integration_tika]


def test_extract_docx_natywnie(tika_url):
    """DOCX -> TikaClient.extract: tekst z polskimi znakami + wykryty Content-Type."""
    document = docx.Document()
    document.add_paragraph("Zażółć gęślą jaźń")
    buf = io.BytesIO()
    document.save(buf)

    client = TikaClient(base_url=tika_url)
    # Bez content_type — dowodzimy, ze autodetekcja typu po stronie Tiki dziala.
    result = asyncio.run(client.extract(data=buf.getvalue()))

    assert isinstance(result, TikaRawResult)
    assert "gęślą" in result.text                                       # polskie znaki z ekstrakcji natywnej (POI)
    assert "wordprocessing" in result.metadata.get("Content-Type", "")  # MIME DOCX wykryty przez Tike
