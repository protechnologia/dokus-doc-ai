"""Testy mapowania wyjatkow httpx -> TikaError w TikaClient (krok 2.3.1).

Klasyfikator `_map_http_error` zamienia wyjatki `httpx` na nasze domenowe `TikaError`.
Sprawdzamy kazda galaz BEZ sieci — wystarczy zbudowac instancje wyjatkow httpx i podac
je wprost. `httpx` to twarda zaleznosc projektu, wiec bez importorskip.

Granica decyzji (2.3.1): tylko DWIE klasy bledu — Tika odpowiedziala bledem na plik
(`TikaExtractionError`) albo w ogole nie dotarlismy do Tiki: brak polaczenia / timeout
(`TikaUnavailableError`). Timeout celowo traktujemy jak niedostepnosc, nie osobno.
"""

import httpx

from app.extraction.client_tika import (
    TikaClient,
    TikaExtractionError,
    TikaUnavailableError,
)

# Wspolne, sztuczne zadanie HTTP — wyjatki httpx wymagaja go w konstruktorze.
_REQ = httpx.Request("PUT", "http://tika:9998/rmeta/text")


def test_status_error_mapuje_na_extraction_error():
    # Scenariusz: Tika odpowiedziala kodem 4xx/5xx na plik (np. nieobslugiwany format).
    # Oczekujemy: TikaExtractionError (problem z plikiem, nie z dostepnoscia Tiki).
    exc = httpx.HTTPStatusError("422", request=_REQ, response=httpx.Response(422, request=_REQ))
    assert isinstance(TikaClient._map_http_error(exc), TikaExtractionError)


def test_connect_error_mapuje_na_unavailable():
    # Scenariusz: brak polaczenia z tika-server (kontener nie wstal).
    # Oczekujemy: TikaUnavailableError.
    assert isinstance(TikaClient._map_http_error(httpx.ConnectError("refused", request=_REQ)), TikaUnavailableError)


def test_timeout_mapuje_na_unavailable():
    # Scenariusz: Tika nie odpowiedziala w czasie (OCR za wolny / zawieszona).
    # Oczekujemy: TikaUnavailableError (decyzja 2.3.1: timeout = niedostepnosc).
    assert isinstance(TikaClient._map_http_error(httpx.ReadTimeout("slow", request=_REQ)), TikaUnavailableError)
