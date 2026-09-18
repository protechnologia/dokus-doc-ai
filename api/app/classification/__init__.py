"""Warstwa klasyfikacji (POST /classify): wybór jednej opcji z listy albo żadnej.

Publiczne API pakietu. Logika importuje stąd — np.:
    from app.classification import ClassificationService, PromptTooLongError
"""

from app.classification.exception import InvalidModelResponseError, PromptTooLongError, UnknownLabelError
from app.classification.model import ClassificationOutcome, ClassificationResult
from app.classification.service import DEFAULT_MAX_OUTPUT_TOKENS, ClassificationService
from app.classification.service_labels import NO_MATCH_LABEL, ClassificationOption, LabeledEntry, OptionLabeler
from app.classification.service_parsing import ParsedResponse, parse_response

__all__ = [
    # domena
    "ClassificationService",
    "ClassificationResult",
    "ClassificationOutcome",
    "DEFAULT_MAX_OUTPUT_TOKENS",
    # etykiety opcji
    "OptionLabeler",
    "ClassificationOption",
    "LabeledEntry",
    "NO_MATCH_LABEL",
    # parser odpowiedzi modelu
    "parse_response",
    "ParsedResponse",
    # wyjatki domenowe
    "PromptTooLongError",
    "UnknownLabelError",
    "InvalidModelResponseError",
]
