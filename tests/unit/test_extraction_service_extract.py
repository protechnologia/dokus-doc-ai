"""Testy orkiestracji ExtractionService.extract (krok 2.3.2) — z atrapa transportu.

`extract` jest async i wola transport (`TikaClient.extract`). Zamiast sieci podstawiamy
atrape, ktora oddaje zadany `TikaRawResult` albo rzuca zadany wyjatek. Brak
pytest-asyncio (spojnie z `test_llm.py`/`test_tika_client.py`) -> odpalamy przez
`asyncio.run`.

Sprawdzamy trzy rzeczy: (1) happy path spina normalizacje + metadane, (2) pusty wynik po
normalizacji -> `EmptyExtractionError`, (3) blad transportu propaguje (NIE jest tu lapany
— mapuje go endpoint w 2.3.3).
"""

import asyncio

import pytest

from app.extraction.client_tika import TikaRawResult, TikaUnavailableError
from app.extraction.service import EmptyExtractionError, ExtractionService


class _StubTransport:
    """Atrapa transportu: oddaje zadany TikaRawResult albo rzuca zadany wyjatek.

    Duck-typing zamiast dziedziczenia po TikaClient — serwis rozmawia tylko przez
    `extract(...)`, wiec atrapie wystarczy ta sama sygnatura (zgodne z decyzja
    "transport konkretny, bez ABC": w testach podstawiamy go po prostu kaczo).
    """

    def __init__(self, *, result: TikaRawResult | None = None, error: Exception | None = None) -> None:
        self._result = result
        self._error = error

    async def extract(self, *, data: bytes, content_type=None, filename=None) -> TikaRawResult:
        if self._error is not None:
            raise self._error
        return self._result


def test_extract_happy_path_spina_normalizacje_i_metadane():
    # Scenariusz: transport oddaje surowy tekst z nadmiarem whitespace + metadane.
    # Oczekujemy: tekst znormalizowany, metadane policzone na tekscie znormalizowanym.
    transport = _StubTransport(
        result=TikaRawResult(
            text="  Tytul \n\n\n  Tresc dalej  \n",
            metadata={"Content-Type": "application/pdf", "language": "pl"},
        )
    )
    service = ExtractionService(transport)

    result = asyncio.run(service.extract(data=b"%PDF-1.7 ..."))

    assert result.text == "Tytul\n\nTresc dalej"
    assert result.metadata.content_type == "application/pdf"
    assert result.metadata.language == "pl"
    # char_count liczony na tekscie PO normalizacji ("Tytul\n\nTresc dalej" = 18 znakow).
    assert result.metadata.char_count == len("Tytul\n\nTresc dalej")
    assert result.metadata.word_count == 3


def test_extract_pusty_po_normalizacji_rzuca_empty():
    # Scenariusz: transport oddal tekst zlozony z samych bialych znakow (np. pusty skan).
    # Oczekujemy: EmptyExtractionError (endpoint zmapuje na 422 w 2.3.3).
    transport = _StubTransport(result=TikaRawResult(text="  \n\t \n ", metadata={}))
    service = ExtractionService(transport)

    with pytest.raises(EmptyExtractionError):
        asyncio.run(service.extract(data=b"..."))


def test_extract_blad_transportu_propaguje():
    # Scenariusz: transport rzuca TikaUnavailableError (Tika nieosiagalna).
    # Oczekujemy: ten sam wyjatek wychodzi z serwisu — domena go NIE lapie (mapuje endpoint).
    transport = _StubTransport(error=TikaUnavailableError("brak polaczenia"))
    service = ExtractionService(transport)

    with pytest.raises(TikaUnavailableError):
        asyncio.run(service.extract(data=b"..."))
