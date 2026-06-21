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

__all__ = [
    # domena
    "SummarizationService",
    "SummarizationResult",
    "SummarizationMetadata",
    # wyjatki domenowe
    "SummarizationError",
    "EmptyInputError",
]
