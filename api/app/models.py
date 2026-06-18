"""Modele wejscia/wyjscia (Pydantic).

Na razie tylko /health. Modele ekstrakcji i summaryzacji dochodza w krokach 2.3-2.4.
"""

from typing   import Literal
from pydantic import BaseModel, Field

# Status pojedynczej zaleznosci zewnetrznej (np. Tiki).
DependencyStatus = Literal["ok", "unreachable"]


class HealthResponse(BaseModel):
    """Odpowiedz /health — zdrowie samej aplikacji plus stan zaleznosci."""

    status: Literal["ok", "degraded"] = Field(
        description=(
            "ok = aplikacja i zaleznosci zdrowe; "
            "degraded = aplikacja zyje, ale jakas zaleznosc jest niedostepna."
        )
    )
    service: str = Field(description="Nazwa uslugi.")
    version: str = Field(description="Wersja aplikacji.")
    dependencies: dict[str, DependencyStatus] = Field(
        default_factory=dict,
        description="Stan zaleznosci, np. {'tika': 'ok'}.",
    )
