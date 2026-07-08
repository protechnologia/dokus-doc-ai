"""Punkt wejscia FastAPI.

Endpointy: /health (krok 2.1), POST /extract (krok 2.3), POST /summarize (krok 2.4) oraz
POST /extract-and-summarize — pelny pipeline ekstrakcja -> streszczenie (krok 2.5). Minimalne
logowanie z request-id (przekrojowe) — to NIE monitoring (Zabbix odlozony).
"""

import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import __version__
from app.routers import extract, health, pipeline, summarize

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("dokus")

app = FastAPI(
    title="DOKUS Doc AI",
    version=__version__,
    summary="Warstwa AI dla obiegu dokumentow DOKUS — ekstrakcja i streszczenie.",
)


@app.middleware("http")
async def request_id_logging(request: Request, call_next):
    """Nadaje/propaguje X-Request-ID i loguje metode, sciezke i status.
    Minimalne, swiadomie proste — nie zastepuje monitoringu."""
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    # Odkladamy na `state`, bo handlery wyjatkow (nizej) biegna WEWNATRZ `call_next` —
    # naglowka odpowiedzi jeszcze nie ma, a chca logowac pod tym samym request-id.
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    logger.info(
        "%s %s -> %s [req=%s]",
        request.method,
        request.url.path,
        response.status_code,
        request_id,
    )
    return response


# --- Logowanie PRZYCZYNY bledu ----------------------------------------------------
#
# Middleware wyzej widzi juz gotowa `Response` — `HTTPException` zostala zamieniona na
# odpowiedz pietro nizej, a `detail` (jedyne, co mowi DLACZEGO) zyje wylacznie w wyjatku.
# Bez tych handlerow log mowi "-> 422" i nic wiecej: diagnoza wymaga dostepu do klienta.
# Zlapane w praktyce: 422 z produkcji, ktorego przyczyny nie dalo sie ustalic z logow.
#
# Handlery NIE zmieniaja odpowiedzi — logują i oddaja sterowanie domyslnemu handlerowi
# FastAPI (kontrakt HTTP zostaje nietkniety).


def _request_id(request: Request) -> str:
    """Request-id z `state` (ustawiony w middleware); '-' gdy wyjatek poleci przed middleware."""
    return getattr(request.state, "request_id", "-")


@app.exception_handler(StarletteHTTPException)
async def log_http_exception(request: Request, exc: StarletteHTTPException):
    """Loguje `detail` kazdego HTTPException (nasze 413/422/500/502/503/504 z routerow).

    4xx = wina wejscia (WARNING), 5xx = wina nasza lub bramy w gore (ERROR) — rozroznienie
    po to, by filtr po poziomie logu oddzielal "klient przyslal smiec" od "cos u nas padlo".
    """
    poziom = logging.ERROR if exc.status_code >= 500 else logging.WARNING
    logger.log(
        poziom,
        "%s %s -> %s [req=%s] detail=%s",
        request.method,
        request.url.path,
        exc.status_code,
        _request_id(request),
        exc.detail,
    )
    return await http_exception_handler(request, exc)


@app.exception_handler(RequestValidationError)
async def log_validation_error(request: Request, exc: RequestValidationError):
    """Loguje bledy walidacji pydantic (422 zanim kod routera w ogole ruszy).

    Osobny handler, bo `RequestValidationError` NIE jest `HTTPException` — bez tego
    najczestszy 422 (zle/brakujace pole w JSON) nadal bylby niemy w logach.
    """
    logger.warning(
        "%s %s -> 422 [req=%s] walidacja=%s",
        request.method,
        request.url.path,
        _request_id(request),
        exc.errors(),
    )
    return await request_validation_exception_handler(request, exc)


app.include_router(health.router)
app.include_router(extract.router)
app.include_router(summarize.router)
app.include_router(pipeline.router)
