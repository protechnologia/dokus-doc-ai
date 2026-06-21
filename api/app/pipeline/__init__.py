"""Warstwa pełnego pipeline'u (krok 2.5): orkiestrator `PipelineService` nad ekstrakcją + summaryzacją.

Publiczne API pakietu. Logika importuje stąd — np.:
    from app.pipeline import PipelineService, PipelineResult
"""

from app.pipeline.service import (
    PipelineResult,
    PipelineService,
)

__all__ = [
    "PipelineService",
    "PipelineResult",
]
