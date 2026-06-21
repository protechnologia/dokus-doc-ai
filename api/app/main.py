"""Punkt wejscia FastAPI.

Endpointy: /health (krok 2.1), POST /extract (krok 2.3). Summaryzacja i pelny pipeline
dochodza w 2.4-2.5. Minimalne logowanie z request-id (przekrojowe) — to NIE monitoring
(Zabbix odlozony).
"""

import logging
import uuid

from fastapi import FastAPI, Request

from app import __version__
from app.routers import extract, health

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


app.include_router(health.router)
app.include_router(extract.router)
