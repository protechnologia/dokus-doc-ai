"""Mechanizm promptów: tekst z pliku `app/prompt/*.md` + placeholdery `{{nazwa}}`.

Tekst każdego promptu leży w pliku w tym katalogu, a klasa promptu — w pakiecie domeny, która
go używa (np. `app.summarization.prompt_user`). Klasa dziedziczy `PromptTemplate`, deklaruje
`FILE` i placeholdery jako stałe (pełny token, np. `TEXT = "{{text}}"`), a publiczne
`render(...)` z jawnymi argumentami zwraca w pełni gotowy prompt. Ten moduł nie importuje nic
z domen — zależności idą w jedną stronę (domena -> prompt).

Decyzje (2026-09-17 — nie „poprawiać" bez powodu):
  - plik `.md` to SUROWY tekst: model dostaje bajty pliku, nie wyrenderowany Markdown. Każda
    „kosmetyka" w pliku (wcięcia, nagłówki, pogrubienia) to zmiana promptu,
  - uzasadnienie treści (pomiary, decyzje) mieszka w pliku, obok tekstu, którego dotyczy — jako
    komentarz HTML `<!-- … -->` wycinany przy wczytaniu, zanim cokolwiek trafi do modelu.
    Komentarz zajmuje CAŁE linie (także wieloliniowy) i znika razem z nimi; puste linie wokół
    zostają, więc komentarz w środku tekstu przyklejamy do akapitu. Komentarz w linii z tekstem
    albo niedomknięty -> błąd przy starcie (wycięcie „na oko" mogłoby zjeść kawałek promptu).
    Wycinamy wyłącznie z SZABLONU — wartości (treść pisma z `<!--`) przechodzą nietknięte,
    a `{{…}}` wewnątrz komentarza nie jest placeholderem,
  - placeholder `{{nazwa}}`, nie `str.format` — `format` wywraca się na każdym `{` w stałym
    tekście (np. przykład JSON-a w prompcie),
  - stała w klasie = PEŁNY token, dokładnie jak w pliku: wyszukanie `{{text}}` znajduje i plik,
    i klasę. Format pilnuje się sam — stała bez klamer (`"text"`) nie zgodzi się ze zbiorem
    znalezionym w pliku, więc wywali start (a zwykłe `replace("text", …)` podmieniłoby słowo),
  - podstawienie JEDNYM przebiegiem (`re.sub` z funkcją): dopasowania szukane tylko w szablonie,
    wartości lądują w osobnym buforze wyniku. Wartość z zewnątrz (treść pisma) zawierająca `{{…}}`
    zostaje dosłownie — kolejne `.replace()` przeszukałyby wynik poprzedniego i podmieniły ją,
  - zbiór placeholderów w pliku = `PLACEHOLDERS` klasy, sprawdzany przy konstrukcji. Instancje
    powstają przy imporcie serwisu, więc brak pliku albo rozjazd wywala start aplikacji, a nie
    pierwsze żądanie (i nie wysyła modelowi dosłownego `{{literowka}}`),
  - wiodące i końcowe znaki nowej linii zdejmowane — edytor dopisuje końcowe, a komentarz na
    początku pliku zostawia pustą linię odstępu. CRLF blokuje `.gitattributes` (`eol=lf`).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import ClassVar

# --- Stałe -----------------------------------------------------------------------

# Katalog z tekstami promptów (ten pakiet). Wpieczony w obraz przez `COPY app ./app`.
PROMPT_DIR = Path(__file__).parent

# Token placeholdera: `{{nazwa}}` — bez spacji w środku, nazwa jak identyfikator. Bez grupy:
# porównujemy i podstawiamy PEŁNE tokeny, tak jak stoją w stałych klas.
_PLACEHOLDER = re.compile(r"\{\{\w+\}\}")

# Komentarz zajmujący całe linie: od początku linii (dopuszczalne wcięcie) `<!--`, treść BEZ `-->`
# (inaczej dopasowanie mogłoby przeskoczyć do kolejnego komentarza, zjadając tekst promptu między
# nimi), `-->`, do końca linii razem ze znakiem nowej linii (albo końcem pliku).
_COMMENT_LINES = re.compile(r"^[ \t]*<!--(?:(?!-->).)*-->[ \t]*(?:\n|\Z)", re.DOTALL | re.MULTILINE)

# Znaczniki komentarza, które nie mogą przetrwać wycięcia (komentarz w linii z tekstem albo niedomknięty).
_COMMENT_MARKERS = ("<!--", "-->")


# --- Wyjątki ---------------------------------------------------------------------


class PromptTemplateError(Exception):
    """Plik promptu niepoprawny — komentarz poza osobnymi liniami albo placeholdery inne niż w `PLACEHOLDERS`."""


# --- Baza klas promptów ----------------------------------------------------------


class PromptTemplate:
    """Baza klas promptów: wczytuje tekst z pliku, pilnuje placeholderów, podstawia wartości.

    Do czego:
        Oddziela tekst promptu (plik `.md`, czytelny i porównywalny jak zwykły tekst) od kodu,
        który go wypełnia. Podklasa (w pakiecie domeny) deklaruje `FILE`, placeholdery jako stałe
        z pełnym tokenem i komentarzem oraz własne `render(...)` z jawnymi argumentami, które woła
        `_render`. Baza nie ma publicznego `render` — sygnatura zależy od promptu.

    Flow:
        1. konstruktor -> odczyt `PROMPT_DIR / FILE` -> `_strip_comments` (komentarze `<!-- … -->`)
           -> `_strip_edge_newlines` -> `_check_placeholders` (rozjazd z `PLACEHOLDERS` -> `PromptTemplateError`),
        2. `render(...)` podklasy -> `_render({STAŁA: wartość})` -> `_substitute` jednym przebiegiem.
    """

    # --- Deklaracja podklasy ---------------------------------------------------------

    FILE: ClassVar[str]                                    # nazwa pliku w `PROMPT_DIR`, np. "summary_user.md"
    PLACEHOLDERS: ClassVar[frozenset[str]] = frozenset()   # pełne tokeny w pliku, np. {"{{text}}"}; pusty = prompt stały

    def __init__(self) -> None:
        """Opis metody:
        Wczytaj tekst promptu z pliku podklasy i sprawdź, że jego placeholdery zgadzają się
        z deklaracją. Jedyne I/O (odczyt pliku) — raz, przy tworzeniu instancji.

        Przyklad argumentow:
            (brak — plik i placeholdery z atrybutów klasy, np. FILE="summary_user.md",
             PLACEHOLDERS=frozenset({"{{text}}"}))

        Przyklad wyniku:
            instancja z tekstem "Streść poniższy dokument:\\n\\n{{text}}" gotowym do `_render`

        Raises:
            FileNotFoundError:   brak pliku `FILE` w `PROMPT_DIR`.
            PromptTemplateError: komentarz poza osobnymi liniami albo placeholdery inne niż `PLACEHOLDERS`.
        """
        # Odczyt jawnie w UTF-8 — polskie znaki nie mogą zależeć od locale kontenera.
        raw = (PROMPT_DIR / self.FILE).read_text(encoding="utf-8")

        # Komentarze PRZED sprawdzeniem placeholderów — `{{…}}` w uzasadnieniu nie jest placeholderem.
        uncommented = self._strip_comments(raw, self.FILE)

        # Odstęp po komentarzu na początku i `\n` dopisane przez edytor nie są częścią promptu.
        template = self._strip_edge_newlines(uncommented)

        # Rozjazd pliku z klasą -> głośno teraz (import serwisu), nie przy pierwszym żądaniu.
        self._check_placeholders(template, self.PLACEHOLDERS, self.FILE)
        self._template = template

    # --- Czyste helpery (bez I/O) — testowalne punktowo ------------------------------

    @staticmethod
    def _strip_comments(
        raw: str,    # surowa zawartość pliku, np. "<!--\nDlaczego tak...\n-->\n\nJesteś asystentem..."
        file: str,   # nazwa pliku do komunikatu, np. "summary_system.md"
    ) -> str:
        """Opis metody:
        Wytnij komentarze `<!-- … -->` zajmujące całe linie (razem z tymi liniami). Puste linie
        wokół zostają. Czysta funkcja.

        Przyklad argumentow:
            raw="<!--\\nDlaczego tak...\\n-->\\n\\nJesteś asystentem...", file="summary_system.md"

        Przyklad wyniku:
            "\\nJesteś asystentem..."   # pusta linia odstępu zostaje; zdejmuje ją `_strip_edge_newlines`

        Raises:
            PromptTemplateError: po wycięciu zostaje `<!--` albo `-->` (komentarz w linii z tekstem,
                niedomknięty albo z `-->` w środku).
        """
        # Jeden przebieg po pliku: każdy komentarz całoliniowy znika razem ze swoimi liniami.
        uncommented = _COMMENT_LINES.sub("", raw)

        # Resztka znacznika = komentarz, którego nie da się bezpiecznie wyciąć -> głośno, nie „na oko".
        leftovers = [marker for marker in _COMMENT_MARKERS if marker in uncommented]
        if leftovers:
            raise PromptTemplateError(f"Prompt {file}: komentarz poza osobnymi liniami albo niedomknięty (zostało: {', '.join(leftovers)}).")
        return uncommented

    @staticmethod
    def _strip_edge_newlines(
        text: str,   # tekst po wycięciu komentarzy, np. "\nStreść poniższy dokument:\n\n{{text}}\n"
    ) -> str:
        """Opis metody:
        Zdejmij wiodące i końcowe znaki nowej linii (odstęp po komentarzu na początku, `\\n` dopisane
        przez edytor). Wewnętrzne zostają. Czysta funkcja.

        Przyklad argumentow:
            text="\\nStreść poniższy dokument:\\n\\n{{text}}\\n"

        Przyklad wyniku:
            "Streść poniższy dokument:\\n\\n{{text}}"
        """
        return text.strip("\n")

    @staticmethod
    def _find_placeholders(
        template: str,   # tekst promptu, np. "Opcje:\n{{options}}\n\nPisma:\n{{summaries}}"
    ) -> frozenset[str]:
        """Opis metody:
        Zbierz pełne tokeny `{{nazwa}}` występujące w tekście (powtórzenia liczą się raz;
        pojedyncze klamry, np. w przykładzie JSON-a, to nie placeholder). Czysta funkcja.

        Przyklad argumentow:
            template="Opcje:\\n{{options}}\\n\\nPisma:\\n{{summaries}}"

        Przyklad wyniku:
            frozenset({"{{options}}", "{{summaries}}"})
        """
        return frozenset(_PLACEHOLDER.findall(template))

    @classmethod
    def _check_placeholders(
        cls,
        template: str,                # tekst promptu, np. "Streść poniższy dokument:\n\n{{text}}"
        expected: frozenset[str],     # tokeny zadeklarowane w klasie, np. frozenset({"{{text}}"})
        file: str,                    # nazwa pliku do komunikatu, np. "summary_user.md"
    ) -> None:
        """Opis metody:
        Sprawdź, że tokeny placeholderów w tekście to dokładnie te zadeklarowane. Czysta funkcja.

        Przyklad argumentow:
            template="Streść poniższy dokument:\\n\\n{{tekst}}", expected=frozenset({"{{text}}"}),
            file="summary_user.md"

        Przyklad wyniku:
            None   # przy zgodności; tu -> PromptTemplateError (brak: {{text}}; nieznane: {{tekst}})

        Raises:
            PromptTemplateError: w pliku brakuje zadeklarowanego tokenu albo jest nieznany.
        """
        found = cls._find_placeholders(template)

        # Zgodność -> nic do zgłoszenia.
        if found == expected:
            return

        # Obie strony rozjazdu w komunikacie: literówka w pliku daje naraz „brak" i „nieznane".
        missing = ", ".join(sorted(expected - found)) or "—"
        unknown = ", ".join(sorted(found - expected)) or "—"
        raise PromptTemplateError(f"Prompt {file}: placeholdery niezgodne z klasą (brak: {missing}; nieznane: {unknown}).")

    @staticmethod
    def _substitute(
        template: str,                # tekst promptu, np. "Streść poniższy dokument:\n\n{{text}}"
        values: Mapping[str, str],    # wartość dla każdego tokenu, np. {"{{text}}": "Pismo w sprawie..."}
    ) -> str:
        """Opis metody:
        Podstaw wartości za tokeny JEDNYM przebiegiem — dopasowania szukane tylko w szablonie,
        wstawiona wartość nie jest ponownie przeszukiwana, więc `{{…}}` w treści pisma zostaje
        dosłownie. Czysta funkcja.

        Przyklad argumentow:
            template="Streść poniższy dokument:\\n\\n{{text}}", values={"{{text}}": "Pismo {{options}}"}

        Przyklad wyniku:
            "Streść poniższy dokument:\\n\\nPismo {{options}}"

        Raises:
            KeyError: brak wartości dla tokenu (błąd `render` podklasy).
        """
        return _PLACEHOLDER.sub(lambda match: values[match.group()], template)

    # --- Złożenie dla podklas --------------------------------------------------------

    def _render(
        self,
        values: Mapping[str, str],   # wartości po tokenie, np. {SummaryUserPrompt.TEXT: "Pismo..."}
    ) -> str:
        """Opis metody:
        Złóż gotowy prompt z wczytanego tekstu. Woła je publiczne `render(...)` podklasy.

        Przyklad argumentow:
            values={"{{text}}": "Pismo z Urzędu Skarbowego..."}

        Przyklad wyniku:
            "Streść poniższy dokument:\\n\\nPismo z Urzędu Skarbowego..."

        Raises:
            KeyError: brak wartości dla tokenu.
        """
        return self._substitute(self._template, values)
