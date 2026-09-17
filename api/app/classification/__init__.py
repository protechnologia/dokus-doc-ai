"""Warstwa klasyfikacji (POST /classify): wybór jednej opcji z listy albo żadnej.

Publiczne API pakietu. Logika importuje stąd — np.:
    from app.classification import OptionLabeler, UnknownLabelError
"""

from app.classification.labels import NO_MATCH_LABEL, ClassificationOption, LabeledEntry, OptionLabeler, UnknownLabelError

__all__ = [
    # etykiety opcji
    "OptionLabeler",
    "ClassificationOption",
    "LabeledEntry",
    "NO_MATCH_LABEL",
    # wyjatki domenowe
    "UnknownLabelError",
]
