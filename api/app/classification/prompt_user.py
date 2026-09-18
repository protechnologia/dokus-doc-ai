"""Prompt użytkownika klasyfikacji: streszczenia dokumentu + opcje pod etykietami.

Szkielet (sekcje w tagach) w `app/prompt/classification_user.md`; tu klasa, która formatuje obie
listy i zwraca w pełni gotowy prompt. Mechanizm: `app.prompt.PromptTemplate`.

Import `LabeledEntry` z tego samego pakietu nie tworzy cyklu: `__init__` pakietu wykonuje się,
zanim ruszy którykolwiek jego moduł, a `service_labels.py` nie importuje promptu ani serwisu.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.classification.service_labels import LabeledEntry
from app.prompt import PromptTemplate


class ClassificationUserPrompt(PromptTemplate):
    """Prompt użytkownika klasyfikacji: streszczenia w tagach + pozycje listy pod etykietami.

    Do czego:
        Podaje modelowi dane do wyboru. Model widzi wyłącznie etykiety (`OPT-1`…, `OPT-00`), nazwy,
        opisy i przykłady — nigdy surowych `id` klienta. Kolejność pozycji przejmuje z
        `OptionLabeler.entries` (nie składa własnej); `OPT-00` rozpoznaje po `option is None`.

    Flow `render(...)`:
        1. `_format_summaries` -> każde streszczenie w `<streszczenie>`,
        2. `_format_entries` -> `_format_entry` dla każdej pozycji, w `<opcja>` (puste przykłady pominięte),
        3. `_render` -> oba bloki w szkielet z pliku.
    """

    FILE = "classification_user.md"

    # --- Placeholdery (pełne tokeny, dokładnie jak w pliku) ---------------------------

    SUMMARIES = "{{summaries}}"   # streszczenia plików dokumentu, każde w <streszczenie>
    OPTIONS   = "{{options}}"     # pozycje listy pod etykietami, każda w <opcja>, OPT-00 na końcu (kolejność z OptionLabeler)

    PLACEHOLDERS = frozenset({SUMMARIES, OPTIONS})

    # --- Tekst pozycji OPT-00 (reguła wyboru jest w prompcie systemowym) ---------------

    # Bez „powyższych": kolejność pozycji to decyzja `OptionLabeler`, nie tekstu pozycji.
    _NO_MATCH_NAME        = "Brak dopasowania"
    _NO_MATCH_DESCRIPTION = "Żadna z pozostałych opcji nie pasuje do dokumentu."

    def render(
        self,
        *,
        summaries: Sequence[str],           # streszczenia plików dokumentu, np. ["• Typ pisma: skarga\n• Nadawca: ..."]
        entries: Sequence[LabeledEntry],    # pozycje z `OptionLabeler.entries`, np. (("OPT-1", opcja), ("OPT-00", None))
    ) -> str:
        """Opis metody:
        Zwróć gotowy prompt użytkownika: streszczenia i pozycje listy w sekcjach szkieletu.

        Przyklad argumentow:
            summaries=["• Typ pisma: skarga\\n• Czego dotyczy: zawyżony rachunek"]
            entries=OptionLabeler([ClassificationOption(id=21, name="Skargi", description="Skargi konsumentów.")]).entries

        Przyklad wyniku:
            "Streszczenia plików dokumentu (kolejność bez znaczenia):\\n\\n<streszczenia>\\n<streszczenie>\\n"
            "• Typ pisma: skarga\\n• Czego dotyczy: zawyżony rachunek\\n</streszczenie>\\n</streszczenia>\\n\\n"
            "Opcje do wyboru:\\n\\n<opcje>\\n<opcja>\\nOPT-1: Skargi\\nOpis: Skargi konsumentów.\\n</opcja>\\n"
            "<opcja>\\nOPT-00: Brak dopasowania\\nOpis: Żadna z pozostałych opcji nie pasuje do dokumentu.\\n</opcja>\\n</opcje>"
        """
        return self._render({
            self.SUMMARIES: self._format_summaries(summaries),
            self.OPTIONS:   self._format_entries(entries),
        })

    # --- Czyste helpery (bez I/O) — testowalne punktowo ------------------------------

    @staticmethod
    def _format_summaries(
        summaries: Sequence[str],   # streszczenia plików dokumentu, np. ["• Typ pisma: skarga", "• Typ pisma: faktura"]
    ) -> str:
        """Opis metody:
        Opakuj każde streszczenie w `<streszczenie>` (brzegowe białe znaki zdjęte), w kolejności
        wejścia. Czysta funkcja.

        Przyklad argumentow:
            summaries=["• Typ pisma: skarga\\n", "• Typ pisma: faktura"]

        Przyklad wyniku:
            "<streszczenie>\\n• Typ pisma: skarga\\n</streszczenie>\\n<streszczenie>\\n• Typ pisma: faktura\\n</streszczenie>"
        """
        return "\n".join(f"<streszczenie>\n{summary.strip()}\n</streszczenie>" for summary in summaries)

    @classmethod
    def _format_entries(
        cls,
        entries: Sequence[LabeledEntry],   # pozycje z `OptionLabeler.entries`, OPT-00 na końcu
    ) -> str:
        """Opis metody:
        Sformatuj wszystkie pozycje w kolejności wejścia, każdą w `<opcja>` — tak jak streszczenia
        (spójność sekcji). Czysta funkcja.

        Przyklad argumentow:
            entries=(LabeledEntry("OPT-1", ClassificationOption(id=21, name="Skargi", description="Skargi konsumentów.")),
                     LabeledEntry("OPT-00", None))

        Przyklad wyniku:
            "<opcja>\\nOPT-1: Skargi\\nOpis: Skargi konsumentów.\\n</opcja>\\n"
            "<opcja>\\nOPT-00: Brak dopasowania\\nOpis: Żadna z pozostałych opcji nie pasuje do dokumentu.\\n</opcja>"
        """
        return "\n".join(f"<opcja>\n{cls._format_entry(entry)}\n</opcja>" for entry in entries)

    @classmethod
    def _format_entry(
        cls,
        entry: LabeledEntry,   # jedna pozycja, np. LabeledEntry("OPT-2", ClassificationOption(id="db-7781", ...))
    ) -> str:
        """Opis metody:
        Sformatuj jedną pozycję: etykieta i nazwa, opis, przykłady (tylko niepuste). Surowe `id`
        nie trafia do tekstu. Czysta funkcja.

        Przyklad argumentow:
            entry=LabeledEntry("OPT-2", ClassificationOption(id="db-7781", name="Kadry",
                                                            description="Sprawy pracownicze.", examples="Wniosek o urlop."))

        Przyklad wyniku:
            "OPT-2: Kadry\\nOpis: Sprawy pracownicze.\\nPrzykłady: Wniosek o urlop."
        """
        option = entry.option

        # OPT-00: pozycja usługi, nie klienta — stały tekst (kiedy ją wybrać, mówi prompt systemowy).
        if option is None:
            return f"{entry.label}: {cls._NO_MATCH_NAME}\nOpis: {cls._NO_MATCH_DESCRIPTION}"

        # Opcja klienta: nazwa i opis zawsze (także puste — kompletność katalogu to strona klienta).
        lines = [f"{entry.label}: {option.name.strip()}", f"Opis: {option.description.strip()}"]

        # Przykłady: null, brak i pusty/biały tekst znaczą to samo (kontrakt) -> linia pominięta.
        examples = (option.examples or "").strip()
        if examples:
            lines.append(f"Przykłady: {examples}")
        return "\n".join(lines)
