"""Testy jednostkowe routera POST /extract (krok 2.3.3) — mapowanie wejscia/wyjatkow na HTTP.

Bez realnej Tiki: podstawiamy ATRAPE `ExtractionService` przez `dependency_overrides`
(serwis oddaje zadany `ExtractionResult` albo rzuca zadany wyjatek domenowy). Dzieki temu
testujemy WYLACZNIE warstwe HTTP routera — dekodowanie base64, walidacje rozmiaru i
mapowanie wyjatkow domenowych na kody (422/413/502) — bez sieci. Realny przeplyw przez
kontener jest w `tests/integration/test_fastapi_extract.py`.
"""

import base64

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.extraction import (
    EmptyExtractionError,
    ExtractionMetadata,
    ExtractionResult,
    TikaExtractionError,
    TikaUnavailableError,
)
from app.main import app
from app.routers.extract import _get_extraction_service


class _StubService:
    """Atrapa ExtractionService: oddaje zadany ExtractionResult albo rzuca zadany wyjatek.

    Duck-typing — router rozmawia z serwisem tylko przez async `extract(...)`.
    """

    def __init__(self, *, result: ExtractionResult | None = None, error: Exception | None = None) -> None:
        self._result = result
        self._error = error

    async def extract(self, *, data: bytes, content_type=None, filename=None) -> ExtractionResult:
        if self._error is not None:
            raise self._error
        return self._result


def _b64(data: bytes) -> str:
    """Pomocnik: bajty -> string base64 (jak przyslalby klient)."""
    return base64.b64encode(data).decode("ascii")


def _client(service: _StubService, *, max_upload_bytes: int = 20 * 1024 * 1024) -> TestClient:
    """Zbuduj TestClient z podstawiona atrapa serwisu i (opcjonalnie) malym limitem rozmiaru."""
    app.dependency_overrides[_get_extraction_service] = lambda: service
    app.dependency_overrides[get_settings] = lambda: Settings(max_upload_bytes=max_upload_bytes)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _czysc_overrides():
    """Po kazdym tescie czyscimy podstawienia DI — `app` jest wspoldzielony miedzy testami."""
    yield
    app.dependency_overrides.clear()


# --- Happy path ------------------------------------------------------------------


def test_extract_zwraca_tekst_i_metadane():
    # Scenariusz: serwis oddaje gotowy wynik domenowy.
    # Oczekujemy: 200 + kontrakt ExtractResponse (tekst + zagniezdzone metadane).
    result = ExtractionResult(
        text="Tresc pisma",
        metadata=ExtractionMetadata(content_type="application/pdf", language="pl", char_count=11, word_count=2),
    )
    client = _client(_StubService(result=result))

    resp = client.post("/extract", json={"content_base64": _b64(b"%PDF-1.7 ...")})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["text"] == "Tresc pisma"
    assert body["metadata"] == {
        "content_type": "application/pdf",
        "language": "pl",
        "char_count": 11,
        "word_count": 2,
        # Pola jakosci (2.3.5) — domyslne, bo stub-wynik domenowy ich nie ustawia.
        "ocr_used": False,
        "pages_total": None,
        "pages_processed": None,
        "ocr_truncated": False,
    }


# --- Walidacja wejscia (przed kontaktem z serwisem) ------------------------------


def test_zly_base64_daje_422():
    # Scenariusz: content_base64 to nie-base64 (znaki spoza alfabetu).
    # Oczekujemy: 422 — blad wejscia, serwis w ogole nie wolany.
    client = _client(_StubService(result=None))

    resp = client.post("/extract", json={"content_base64": "to nie jest base64!!!"})

    assert resp.status_code == 422, resp.text


def test_pusty_plik_daje_422():
    # Scenariusz: poprawny base64, ale dekoduje sie do zera bajtow.
    # Oczekujemy: 422 — pusty plik, nie ma czego ekstrahowac.
    client = _client(_StubService(result=None))

    resp = client.post("/extract", json={"content_base64": ""})

    assert resp.status_code == 422, resp.text


def test_plik_za_duzy_daje_413():
    # Scenariusz: zdekodowany plik przekracza maly limit (tu: 10 B).
    # Oczekujemy: 413 — straznik zasobow odrzuca PRZED kontaktem z Tika.
    client = _client(_StubService(result=None), max_upload_bytes=10)

    resp = client.post("/extract", json={"content_base64": _b64(b"x" * 50)})

    assert resp.status_code == 413, resp.text


# --- Mapowanie wyjatkow domenowych na HTTP ---------------------------------------


def test_tika_niedostepna_daje_502():
    # Scenariusz: serwis rzuca TikaUnavailableError (tika-server nieosiagalny).
    # Oczekujemy: 502 — problem bramy w gore, nie wejscia klienta.
    client = _client(_StubService(error=TikaUnavailableError("brak polaczenia")))

    resp = client.post("/extract", json={"content_base64": _b64(b"%PDF-1.7 ...")})

    assert resp.status_code == 502, resp.text


def test_tika_odrzucila_plik_daje_422():
    # Scenariusz: serwis rzuca TikaExtractionError (plik nieobslugiwany/uszkodzony).
    # Oczekujemy: 422 — wina po stronie pliku.
    client = _client(_StubService(error=TikaExtractionError("HTTP 422 od Tiki")))

    resp = client.post("/extract", json={"content_base64": _b64(b"\x00\x01\x02")})

    assert resp.status_code == 422, resp.text


def test_brak_tresci_po_ekstrakcji_daje_422():
    # Scenariusz: serwis rzuca EmptyExtractionError (po normalizacji brak tekstu).
    # Oczekujemy: 422 — plik bez tresci tekstowej.
    client = _client(_StubService(error=EmptyExtractionError("brak tresci")))

    resp = client.post("/extract", json={"content_base64": _b64(b"pusty skan")})

    assert resp.status_code == 422, resp.text
