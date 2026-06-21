"""Warstwa ekstrakcji (krok 2.3): transport do Tiki (domena `ExtractionService` w 2.3.2).

Publiczne API pakietu. Logika importuje stad — np.:
    from app.extraction import TikaClient, TikaError
"""

from app.extraction.client_tika import (
    TikaClient,
    TikaError,
    TikaExtractionError,
    TikaRawResult,
    TikaUnavailableError,
)
from app.extraction.service import (
    EmptyExtractionError,
    ExtractionError,
    ExtractionMetadata,
    ExtractionResult,
    ExtractionService,
)

__all__ = [
    # transport
    "TikaClient",
    "TikaRawResult",
    # wyjatki domenowe transportu
    "TikaError",
    "TikaUnavailableError",
    "TikaExtractionError",
    # domena
    "ExtractionService",
    "ExtractionResult",
    "ExtractionMetadata",
    # wyjatki domenowe ekstrakcji
    "ExtractionError",
    "EmptyExtractionError",
]
