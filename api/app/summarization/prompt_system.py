"""Prompt systemowy summaryzacji: rola pod dekretację + format odpowiedzi.

Tekst i uzasadnienie treści (pomiary formatu, decyzja „bez akapitu") w `app/prompt/summary_system.md`
— uzasadnienie jako komentarz `<!-- … -->`, wycinany przy wczytaniu. Mechanizm: `app.prompt.PromptTemplate`.

Prompt to LOGIKA, nie sekret: wersjonowany i wpieczony w obraz, nie w ENV; nie zmienia się przy
zmianie dostawcy LLM.
"""

from __future__ import annotations

from app.prompt import PromptTemplate


class SummarySystemPrompt(PromptTemplate):
    """Prompt systemowy streszczenia: rola pod dekretację + format = SAMO WYPUNKTOWANIE pól.

    Do czego:
        Mówi modelowi, dla kogo streszcza (osoba dekretująca) i w jakiej formie: pięć pól jako
        punkty „• ", tylko te obecne w piśmie. Stały tekst — bez placeholderów. Dlaczego bez
        akapitu otwierającego (macierz pomiarów na Bieliku 11B): komentarz w `summary_system.md`.
    """

    FILE = "summary_system.md"

    def render(self) -> str:
        """Opis metody:
        Zwróć gotowy prompt systemowy (stały tekst z pliku, bez komentarzy).

        Przyklad argumentow:
            (brak)

        Przyklad wyniku:
            "Jesteś asystentem przygotowującym zwięzłe streszczenia pism dla osoby dekretującej..."
        """
        return self._render({})
