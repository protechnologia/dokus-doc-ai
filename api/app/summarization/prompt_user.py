"""Prompt użytkownika summaryzacji: ramka + treść dokumentu.

Tekst w `app/prompt/summary_user.md` (placeholder `{{text}}`); tu klasa, która go wypełnia.
Mechanizm (placeholdery, sprawdzanie przy konstrukcji): `app.prompt.PromptTemplate`.
"""

from __future__ import annotations

from app.prompt import PromptTemplate


class SummaryUserPrompt(PromptTemplate):
    """Prompt użytkownika streszczenia: ramka + treść dokumentu.

    Do czego:
        Wskazuje, co streścić. Instrukcje formatu trzyma prompt systemowy (`SummarySystemPrompt`),
        więc tu tylko krótka ramka i dokument.
    """

    FILE = "summary_user.md"

    # --- Placeholdery (pełne tokeny, dokładnie jak w pliku) ---------------------------

    TEXT = "{{text}}"   # treść dokumentu pod limit znaków modelu (po truncacji, ze znacznikami pominięcia)

    PLACEHOLDERS = frozenset({TEXT})

    def render(
        self,
        *,
        text: str,   # treść dokumentu po truncacji, np. "Pismo z Urzędu Skarbowego w sprawie zaległości..."
    ) -> str:
        """Opis metody:
        Zwróć gotowy prompt użytkownika: ramka + dokument.

        Przyklad argumentow:
            text="Pismo z Urzędu Skarbowego w sprawie zaległości..."

        Przyklad wyniku:
            "Streść poniższy dokument:\\n\\nPismo z Urzędu Skarbowego w sprawie zaległości..."
        """
        return self._render({self.TEXT: text})
