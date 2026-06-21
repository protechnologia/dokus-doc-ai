"""Testy orkiestracji warstwy jakosci ExtractionService.extract (krok 2.3.5, strategia B).

Sprawdzamy DECYZJE orkiestracji nad atrapa transportu (bez sieci/Tiki):
  - limit stron PRZED ekstrakcja (PDF > N -> do Tiki ida tylko pierwsze N stron),
  - retry `ocr_only` TYLKO gdy warstwa PDF jest smieciowa (PUA),
  - brak retry, gdy tekst czysty albo plik nie jest PDF-em,
  - metadane (`ocr_used`/`pages_*`/`ocr_truncated`) odbijaja przebieg.

Atrapa NAGRYWA wywolania (kolejnosc, `ocr_strategy`, przekazane bajty), wiec asercje ida
na faktyczny przeplyw, nie tylko wynik. PDF generujemy prawdziwy (pypdf) — bo `PdfPageLimiter.apply`
realnie liczy/tnie strony. Brak pytest-asyncio -> `asyncio.run` (jak w pozostalych testach).
"""

import asyncio
from io import BytesIO

from pypdf import PdfReader, PdfWriter

from app.extraction.client_tika import TikaRawResult
from app.extraction.service import ExtractionService

# Tekst zdominowany przez Private Use Area — odwzorowuje smieciowa warstwe sample_01.pdf.
_PUA_GARBAGE = ""  # 7x U+F0xx, 100% PUA > prog 30%


def _make_pdf(n_pages: int) -> bytes:
    writer = PdfWriter()
    for _ in range(n_pages):
        writer.add_blank_page(width=200, height=200)
    buf = BytesIO()
    writer.write(buf)
    return buf.getvalue()


class _RecordingTransport:
    """Atrapa transportu: oddaje kolejne zadane `TikaRawResult` i nagrywa kazde wywolanie.

    Duck-typing (jak `_StubTransport` w 2.3.2): serwis rozmawia tylko przez `extract(...)`,
    wiec wystarczy ta sama sygnatura — tu z `ocr_strategy`, ktorego serwis uzywa w retry.
    """

    def __init__(self, *responses: TikaRawResult) -> None:
        self.calls: list[dict] = []
        self._responses = list(responses)

    async def extract(self, *, data, content_type=None, filename=None, ocr_strategy=None):
        self.calls.append(
            {"data": data, "content_type": content_type, "filename": filename, "ocr_strategy": ocr_strategy}
        )
        return self._responses[len(self.calls) - 1]


# --- Czysta warstwa tekstowa: jedno wywolanie, bez OCR ---------------------------


def test_czysty_pdf_jedno_wywolanie_bez_ocr():
    # Scenariusz: PDF z dobra warstwa tekstowa (auto czyta natywnie, ocrPageCount=0).
    # Oczekujemy: dokladnie jedno wywolanie, bez ocr_strategy; ocr_used False.
    transport = _RecordingTransport(
        TikaRawResult(text="Pismo do dekretacji w sprawie podatku.",
                      metadata={"Content-Type": "application/pdf", "pdf:ocrPageCount": 0})
    )
    service = ExtractionService(transport, max_ocr_pages=30)

    result = asyncio.run(service.extract(data=_make_pdf(2), content_type="application/pdf"))

    assert len(transport.calls) == 1
    assert transport.calls[0]["ocr_strategy"] is None
    assert result.metadata.ocr_used is False
    assert result.metadata.pages_total == 2 and result.metadata.pages_processed == 2
    assert result.metadata.ocr_truncated is False
    assert "dekretacji" in result.text


# --- Smieciowa warstwa PUA: wymuszony retry ocr_only -----------------------------


def test_smieciowa_warstwa_pua_wymusza_ocr_only():
    # Scenariusz: natywnie wraca smiec (PUA); OCR_ONLY zwraca poprawna tresc + ocrPageCount=1.
    # Oczekujemy: DWA wywolania; drugie z ocr_strategy="ocr_only"; wynik = tekst z OCR; ocr_used True.
    transport = _RecordingTransport(
        TikaRawResult(text=_PUA_GARBAGE, metadata={"Content-Type": "application/pdf", "pdf:ocrPageCount": 0}),
        TikaRawResult(text="Twierdzenie Stolza i dowod.",
                      metadata={"Content-Type": "application/pdf", "pdf:ocrPageCount": 1}),
    )
    service = ExtractionService(transport, max_ocr_pages=30)

    result = asyncio.run(service.extract(data=_make_pdf(1), content_type="application/pdf"))

    assert len(transport.calls) == 2
    assert transport.calls[0]["ocr_strategy"] is None        # 1. proba: auto (globalny config)
    assert transport.calls[1]["ocr_strategy"] == "ocr_only"  # 2. proba: wymuszony OCR
    assert result.text == "Twierdzenie Stolza i dowod."      # wynik z OCR, nie smiec
    assert result.metadata.ocr_used is True


def test_smieciowa_warstwa_ale_nie_pdf_bez_retry():
    # Scenariusz: tekst wyglada na PUA, ale plik NIE jest PDF (brak magic) -> detekcji PUA
    # nie odpalamy (jest_pdf gate). Oczekujemy: jedno wywolanie, bez OCR.
    transport = _RecordingTransport(
        TikaRawResult(text=_PUA_GARBAGE, metadata={"Content-Type": "application/octet-stream"})
    )
    service = ExtractionService(transport)

    result = asyncio.run(service.extract(data=b"PK\x03\x04 nie-pdf"))

    assert len(transport.calls) == 1
    assert result.metadata.pages_total is None               # nie-PDF: bez stron


# --- Limit stron: ciecie PRZED wyslaniem do Tiki ---------------------------------


def test_limit_stron_wysyla_tylko_pierwsze_n():
    # Scenariusz: 5-stronicowy PDF, limit 2 (czysta warstwa -> jedno wywolanie).
    # Oczekujemy: do transportu ida bajty z 2 stronami; metadane: total 5, processed 2, truncated.
    transport = _RecordingTransport(
        TikaRawResult(text="Tresc pierwszych stron.",
                      metadata={"Content-Type": "application/pdf", "pdf:ocrPageCount": 2})
    )
    service = ExtractionService(transport, max_ocr_pages=2)

    result = asyncio.run(service.extract(data=_make_pdf(5), content_type="application/pdf"))

    sent_pages = len(PdfReader(BytesIO(transport.calls[0]["data"])).pages)
    assert sent_pages == 2                                    # Tika dostala juz pociety PDF
    assert result.metadata.pages_total == 5
    assert result.metadata.pages_processed == 2
    assert result.metadata.ocr_truncated is True


def test_limit_stron_i_pua_ciecie_obowiazuje_oba_wywolania():
    # Scenariusz: duzy PDF ZE smieciowa warstwa -> limit tnie, A retry OCR idzie na ten sam
    # juz uciety plik (nie na oryginal). Oczekujemy: oba wywolania dostaja 2-stronicowy PDF.
    transport = _RecordingTransport(
        TikaRawResult(text=_PUA_GARBAGE, metadata={"Content-Type": "application/pdf", "pdf:ocrPageCount": 0}),
        TikaRawResult(text="Poprawna tresc po OCR.",
                      metadata={"Content-Type": "application/pdf", "pdf:ocrPageCount": 2}),
    )
    service = ExtractionService(transport, max_ocr_pages=2)

    asyncio.run(service.extract(data=_make_pdf(5), content_type="application/pdf"))

    assert len(transport.calls) == 2
    for call in transport.calls:
        assert len(PdfReader(BytesIO(call["data"])).pages) == 2
