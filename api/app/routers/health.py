"""Endpoint /health — zdrowie aplikacji oraz best-effort dostepnosc Tiki.

Kontrakt: zawsze HTTP 200, gdy sama usluga FastAPI zyje. Stan zaleznosci (Tika)
raportujemy w ciele odpowiedzi, a NIE kodem HTTP — bo niedostepna Tika nie znaczy,
ze API jest zepsute (np. Tika jeszcze wstaje). Rozni to /health od probe'a typowego
load-balancera; tu chodzi o diagnostyke, nie o ruting ruchu.

Pole `status`:
  - "ok"       — aplikacja i Tika zdrowe,
  - "degraded" — aplikacja zyje, ale Tika niedostepna.
"""

import httpx
from fastapi import APIRouter, Depends

from app import __version__
from app.config import Settings, get_settings
from app.models import HealthResponse

router = APIRouter(tags=["meta"])


async def _tika_reachable(settings: Settings) -> bool:
    """GET na tika-server z krotkim timeoutem. Kazdy blad HTTP -> niedostepna."""
    url = settings.tika_url.rstrip("/") + "/tika"
    try:
        async with httpx.AsyncClient(timeout=settings.health_check_timeout_seconds) as client:
            resp = await client.get(url)
        return resp.status_code == 200
    except httpx.HTTPError:
        return False


@router.get("/health", response_model=HealthResponse, summary="Zdrowie uslugi")
async def health(settings: Settings = Depends(get_settings)) -> HealthResponse:
    """Zwraca 200, gdy aplikacja zyje. Status 'degraded', gdy Tika niedostepna —
    sama aplikacja dalej odpowiada, wiec to nie jest blad endpointu.

    Przyklad odpowiedzi (Tika zdrowa):
        {
            "status": "ok",
            "service": "dokus-doc-ai",
            "version": "0.1.0",
            "dependencies": {"tika": "ok"}
        }

    Przyklad odpowiedzi (Tika niedostepna — kod HTTP nadal 200):
        {
            "status": "degraded",
            "service": "dokus-doc-ai",
            "version": "0.1.0",
            "dependencies": {"tika": "unreachable"}
        }
    """
    tika_ok = await _tika_reachable(settings)
    return HealthResponse(
        status="ok" if tika_ok else "degraded",
        service="dokus-doc-ai",
        version=__version__,
        dependencies={"tika": "ok" if tika_ok else "unreachable"},
    )
