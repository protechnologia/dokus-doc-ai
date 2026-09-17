"""Prompty: teksty `*.md` + mechanizm `PromptTemplate`.

Publiczne API pakietu. Klasy promptów w domenach importują stąd — np.:
    from app.prompt import PromptTemplate
"""

from app.prompt.template import PROMPT_DIR, PromptTemplate, PromptTemplateError

__all__ = [
    # mechanizm
    "PromptTemplate",
    "PROMPT_DIR",
    # wyjatki
    "PromptTemplateError",
]
