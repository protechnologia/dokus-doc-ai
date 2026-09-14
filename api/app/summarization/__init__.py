"""Warstwa summaryzacji (krok 2.4): domena `SummarizationService` nad `LLMClient`.

Publiczne API pakietu. Logika importuje stąd — np.:
    from app.summarization import SummarizationService, EmptyInputError
"""

from app.summarization.service import (
    EmptyInputError,
    SummarizationError,
    SummarizationMetadata,
    SummarizationResult,
    SummarizationService,
)
from app.summarization.truncation import DEFAULT_HEAD_PERCENT, DEFAULT_TAIL_PERCENT, OMISSION_MARKER, TextPart, TextParts, TextTruncator

__all__ = [
    # domena
    "SummarizationService",
    "SummarizationResult",
    "SummarizationMetadata",
    # trunkacja wejścia modelu (początek / środek / koniec)
    "TextTruncator",
    "TextParts",
    "TextPart",
    "DEFAULT_HEAD_PERCENT",
    "DEFAULT_TAIL_PERCENT",
    "OMISSION_MARKER",
    # wyjatki domenowe
    "SummarizationError",
    "EmptyInputError",
]
