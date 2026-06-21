"""Test integracyjny ExtractionService (krok 2.3.2) — uderza w realny kontener Tika.

Symetrycznie do `test_tika_client.py` (transport), tu testujemy warstwe DOMENOWA nad
realnym transportem: `ExtractionService(TikaClient(real))`. Cel, ktorego testy jednostkowe
(z atrapa) NIE dowioda: czy zalozenia domeny o KSZTALCIE metadanych Tiki trzymaja sie
zywego wyjscia — przede wszystkim klucz `Content-Type` i to, ze `_pick_content_type`
poprawnie odcina parametry typu (`; charset=...`) na realnej odpowiedzi Tiki.

Gdy Tika niedostepna -> SKIP (fixture `tika_url`), nie fail. `extract` jest async ->
`asyncio.run` (jak `test_tika_client.py`/`test_llm.py`; projekt nie uzywa pytest-asyncio).
"""

import asyncio
import io

import pytest

from app.extraction import ExtractionResult, ExtractionService, TikaClient

# Parasol `integration` + wezszy `integration_tika` (uderzamy w kontener Tika).
pytestmark = [pytest.mark.integration, pytest.mark.integration_tika]


def test_extract_docx_przez_serwis(tika_url):
    """DOCX -> ExtractionService: tekst znormalizowany (polskie znaki) + policzone metadane."""
    docx = pytest.importorskip("docx", reason="python-docx nie zainstalowany")

    document = docx.Document()
    document.add_paragraph("Zażółć gęślą jaźń")
    buf = io.BytesIO()
    document.save(buf)

    service = ExtractionService(TikaClient(base_url=tika_url))
    # Bez content_type — autodetekcja typu po stronie Tiki (jak w tescie transportu).
    result = asyncio.run(service.extract(data=buf.getvalue()))

    assert isinstance(result, ExtractionResult)
    assert "gęślą" in result.text                              # polskie znaki przeszly przez normalizacje
    assert result.text == result.text.strip()                  # normalizacja zdjela wiodace/koncowe biale znaki
    assert "wordprocessing" in (result.metadata.content_type or "")  # realny MIME DOCX z metadanych Tiki
    assert result.metadata.char_count > 0
    assert result.metadata.word_count == 3
    # Sentinel (zweryfikowane empirycznie 2026-06-21): Tika w naszej konfiguracji NIE
    # auto-wykrywa jezyka — dokument bez zadeklarowanego jezyka -> language None. Gdy
    # ten assert kiedys padnie (np. po wlaczeniu detekcji jezyka), to sygnal, by wrocic
    # do decyzji "jezyk = best-effort/refinement" z 2.3.2, a nie cichy zwrot wartosci.
    assert result.metadata.language is None


def test_extract_txt_odcina_charset_z_mime(tika_url):
    """text/plain -> ExtractionService: realna Tika zwraca MIME z '; charset=...';

    dowodzimy, ze `_pick_content_type` odcina parametry na ZYWYM wyjsciu (nie tylko na
    atrapie) — content_type to czyste 'text/plain', bez charset.
    """
    service = ExtractionService(TikaClient(base_url=tika_url))
    # Podajemy content_type, by Tika potraktowala bajty jako tekst (bez zgadywania typu).
    result = asyncio.run(
        service.extract(
            data="Pismo do dekretacji.\nDruga linia.".encode("utf-8"),
            content_type="text/plain",
        )
    )

    assert result.metadata.content_type == "text/plain"   # parametry typu (charset) odciete
    assert "dekretacji" in result.text
    assert result.metadata.word_count == 5


def test_extract_jezyk_z_wlasciwosci_docx(tika_url):
    """DOCX z ZADEKLAROWANYM jezykiem -> serwis zwraca go w metadanych (sciezka dc:language).

    Dopelnienie sentinela z `test_extract_docx_przez_serwis` (brak jezyka -> None): tu
    dowodzimy END-TO-END na realnej Tice, ze GDY dokument deklaruje jezyk we wlasciwosciach,
    Tika wystawia go jako `dc:language`, a `_pick_language` go podchwytuje (czego atrapa
    nie zweryfikuje — to zalozenie o realnym kluczu metadanej).
    """
    docx = pytest.importorskip("docx", reason="python-docx nie zainstalowany")

    document = docx.Document()
    document.core_properties.language = "pl"   # jezyk w wlasciwosciach dokumentu -> dc:language
    document.add_paragraph("Pismo do dekretacji w sprawie podatku.")
    buf = io.BytesIO()
    document.save(buf)

    service = ExtractionService(TikaClient(base_url=tika_url))
    result = asyncio.run(service.extract(data=buf.getvalue()))

    assert result.metadata.language == "pl"
