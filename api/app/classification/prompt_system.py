"""Prompt systemowy klasyfikacji: rola, reguła `OPT-00`, zastrzeżenie o danych, format odpowiedzi.

Tekst i uzasadnienie (komentarz `<!-- … -->`, wycinany przy wczytaniu) w
`app/prompt/classification_system.md`. Mechanizm: `app.prompt.PromptTemplate`.
"""

from __future__ import annotations

from app.prompt import PromptTemplate


class ClassificationSystemPrompt(PromptTemplate):
    """Prompt systemowy klasyfikacji: wybór dokładnie jednej pozycji z listy albo `OPT-00`.

    Do czego:
        Mówi modelowi, co wybiera (jedną pozycję dla dokumentu na podstawie streszczeń), kiedy wybrać `OPT-00`
        (tylko gdy żadna opcja nie pasuje), że streszczenia to dane, nie polecenia, i jak odpowiedzieć
        (pola `rationale`, potem `label` — nazwy z `classification.service_schema`). Stały tekst — bez
        placeholderów; etykieta `OPT-00` i nazwy pól wpisane dosłownie, zgodność pilnują testy.
    """

    FILE = "classification_system.md"

    def render(self) -> str:
        """Opis metody:
        Zwróć gotowy prompt systemowy (stały tekst z pliku, bez komentarzy).

        Przyklad argumentow:
            (brak)

        Przyklad wyniku:
            "Wybierz dla dokumentu dokładnie jedną pozycję z listy. ..."
        """
        return self._render({})
