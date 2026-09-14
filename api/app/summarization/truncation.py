"""Trunkacja tekstu pod okno modelu: początek / środek / koniec.

Wydzielona z `SummarizationService` jako spójna jednostka jednej decyzji: „jaki kawałek
dokumentu zobaczy model, gdy całość nie mieści się w `LLM_MAX_INPUT_CHARS`". Czysta logika
domenowa (bez I/O, bez logowania, bez zależności zewnętrznych) — analogia do `PuaDetector`
i `PdfPageLimiter` w ekstrakcji. Log ostrzegawczy przy cięciu robi serwis.

Po co trzy części: dawniej brany był sam POCZĄTEK, więc model nie widział zakończenia pisma —
a tam zwykle są żądanie, podpis i dane nadawcy (to, co zasila klasyfikację adresata). Teraz
budżet dzieli się proporcjami na początek, JEDEN ciągły fragment z geometrycznego środka i
koniec; w każdym miejscu pominięcia stoi jawny znacznik, żeby model nie czytał sąsiednich
fragmentów jako ciągłego tekstu.

Dwa ŚWIADOME uproszczenia (decyzja 2026-09-14 — nie „poprawiać" bez powodu):
  - cięcie „na głupio" dokładnie na wyliczonym offsecie, także w środku wyrazu — znacznik i tak
    mówi modelowi, że tekst jest nieciągły, a szukanie granic akapitu/zdania to kod i przypadki
    brzegowe bez realnego zysku,
  - stała rezerwa na DWA pełne znaczniki niezależnie od proporcji i od tego, czy oba faktycznie
    staną — kosztuje najwyżej kilkadziesiąt znaków budżetu, a `len(wynik) <= max_chars` wynika
    wprost z arytmetyki, bez liczenia po fakcie.
"""

from __future__ import annotations

from typing import NamedTuple

from pydantic import BaseModel, Field

# --- Stałe kontraktu (importowane też przez modele API i pipeline) ----------------

# Domyślne proporcje budżetu (w procentach); środek dostaje resztę do 100.
DEFAULT_HEAD_PERCENT = 45
DEFAULT_TAIL_PERCENT = 35

# Jawny znacznik pominięcia — stoi jako osobny akapit w każdym miejscu, gdzie coś wycięto.
OMISSION_MARKER = "[…pominięto fragment dokumentu…]"

# Separator akapitowy między fragmentami a znacznikami (model widzi znacznik jako osobny akapit).
_SEPARATOR = "\n\n"

# Stała rezerwa budżetu: dwa PEŁNE bloki `separator + znacznik + separator` (2 × 36 = 72 znaki).
# Więcej niż dwa znaczniki nie wystąpią: początek (gdy jest) zaczyna się na 0, koniec kończy na n,
# więc przerwy mogą być tylko przed/po środku albo w miejscu wyłączonej części.
MARKER_RESERVE = 2 * len(_SEPARATOR + OMISSION_MARKER + _SEPARATOR)


# --- Wynik domenowy trunkacji ----------------------------------------------------


class TextPart(BaseModel):
    """Jedna część wejścia modelu: zastosowana proporcja + zakres zachowanego fragmentu."""

    percent: int       = Field(description="Zastosowana proporcja budżetu tej części (0–100).")
    start: int | None  = Field(default=None, description="Offset początku zachowanego fragmentu (włącznie), w znakach; None gdy proporcja 0.")
    end: int | None    = Field(default=None, description="Offset końca zachowanego fragmentu (wyłącznie), w znakach; None gdy proporcja 0.")


class TextParts(BaseModel):
    """Trzy części wejścia modelu po trunkacji (zawsze wszystkie trzy klucze)."""

    head: TextPart    = Field(description="Początek dokumentu.")
    middle: TextPart  = Field(description="Ciągły fragment z geometrycznego środka dokumentu.")
    tail: TextPart    = Field(description="Koniec dokumentu.")


class TruncationResult(NamedTuple):
    """Wynik `TextTruncator.apply`: tekst dla modelu + diagnostyka cięcia.

    `parts` = None, gdy tekst zmieścił się w budżecie (nic nie cięto). Offsety w `parts` są
    liczone względem tekstu podanego do `apply` — przesunięcie na tekst klienta robi serwis.
    """

    text: str
    truncated: bool
    parts: TextParts | None


# --- Jednostka trunkacji ---------------------------------------------------------


class TextTruncator:
    """Trunkacja tekstu do budżetu znaków: początek / środek / koniec ze znacznikami pominięcia.

    Do czego:
        Zamienia tekst dłuższy niż `max_chars` na wejście modelu złożone z trzech części
        w zadanych proporcjach, ze znacznikiem `OMISSION_MARKER` w każdym miejscu pominięcia,
        i raportuje zakresy zachowanych fragmentów (`TextParts`). Tekst mieszczący się w
        budżecie oddaje bez zmian. Czysta — bez I/O i bez logów; `max_chars` konfigurowalny
        (analogicznie do `PdfPageLimiter.max_pages`).

    Flow `apply(text, head_percent, tail_percent)`:
        1. `len(text) <= max_chars` -> tekst bez zmian, `parts=None`,
        2. `_middle_percent` -> proporcja środka (reszta do 100; walidacja proporcji),
        3. `_part_budgets` -> budżety znaków części z budżetu pomniejszonego o `MARKER_RESERVE`,
        4. `_middle_start` -> okno środka wokół `n/2`, dosunięte, by nie wchodzić na początek/koniec,
        5. `_build_parts` / `_part` -> zakresy części (None dla proporcji 0),
        6. `_assemble` -> fragmenty sklejone ze znacznikami w miejscach pominięcia.
    """

    def __init__(
        self,
        max_chars: int,   # budżet znaków wejścia modelu (z `Settings.llm_max_input_chars`), np. 90000
    ) -> None:
        """Opis metody:
        Zbuduj trunkator z budżetem znaków (sama konfiguracja, bez I/O). Budżet musi być większy
        niż stała rezerwa na znaczniki — inaczej na treść nie zostaje nic (błąd konfiguracji).

        Przyklad argumentow:
            max_chars=90000

        Przyklad wyniku:
            gotowy TextTruncator

        Raises:
            ValueError: `max_chars` nie większy niż `MARKER_RESERVE` (brak miejsca na treść).
        """
        # Budżet w całości zjedzony przez rezerwę na znaczniki -> konfiguracja bez sensu, głośno.
        if max_chars <= MARKER_RESERVE:
            raise ValueError(f"max_chars={max_chars} musi być większe niż rezerwa na znaczniki ({MARKER_RESERVE}).")
        self._max_chars = max_chars

    @property
    def max_chars(self) -> int:
        """Budżet znaków (do logów/diagnostyki w warstwie orkiestrującej)."""
        return self._max_chars

    # --- Czyste helpery — testowalne punktowo ----------------------------------------

    @staticmethod
    def _middle_percent(
        head_percent: int,   # proporcja początku, np. 45
        tail_percent: int,   # proporcja końca, np. 35
    ) -> int:
        """Opis metody:
        Wylicz proporcję środka jako resztę do 100 i sprawdź, że proporcje są spójne. Walidacja
        HTTP (422) żyje w modelu API — tu strażnik dla wywołań domenowych z pominięciem API.

        Przyklad argumentow:
            head_percent=45, tail_percent=35

        Przyklad wyniku:
            20

        Raises:
            ValueError: proporcja spoza 0–100 albo suma początku i końca powyżej 100.
        """
        # Każda proporcja osobno w zakresie 0–100.
        if not (0 <= head_percent <= 100 and 0 <= tail_percent <= 100):
            raise ValueError(f"Proporcje muszą być w zakresie 0–100 (head={head_percent}, tail={tail_percent}).")
        # Początek + koniec nie mogą przekroczyć całości — środek byłby ujemny.
        if head_percent + tail_percent > 100:
            raise ValueError(f"Suma proporcji początku i końca przekracza 100 (head={head_percent}, tail={tail_percent}).")
        return 100 - head_percent - tail_percent

    @staticmethod
    def _part_budgets(
        content_chars: int,    # budżet na treść (max_chars - MARKER_RESERVE), np. 89928
        head_percent: int,     # proporcja początku, np. 45
        middle_percent: int,   # proporcja środka, np. 20
        tail_percent: int,     # proporcja końca, np. 35
    ) -> tuple[int, int, int]:
        """Opis metody:
        Rozdziel budżet treści na części proporcjonalnie, zaokrąglając w dół (suma nigdy nie
        przekroczy budżetu; zgubione przy zaokrągleniu 0–2 znaki są bez znaczenia).

        Przyklad argumentow:
            content_chars=100, head_percent=45, middle_percent=20, tail_percent=35

        Przyklad wyniku:
            (45, 20, 35)
        """
        return (
            content_chars * head_percent // 100,
            content_chars * middle_percent // 100,
            content_chars * tail_percent // 100,
        )

    @staticmethod
    def _middle_start(
        n: int,          # długość całego tekstu, np. 1000
        head_len: int,   # budżet początku (okno [0, head_len]), np. 45
        mid_len: int,    # budżet środka, np. 20
        tail_len: int,   # budżet końca (okno [n - tail_len, n]), np. 35
    ) -> int:
        """Opis metody:
        Wylicz offset początku okna środka: wyśrodkowane na `n/2`, ale dosunięte tak, by nie
        wchodziło na okno początku ani końca. Dosunięcie ma znaczenie przy skrajnych proporcjach
        (np. 90/0 — geometryczny środek leży wtedy wewnątrz początku) i przy tekście tuż nad
        budżetem. Miejsce zawsze jest: `head_len + mid_len + tail_len < n`, bo `n > max_chars`.

        Przyklad argumentow:
            n=1000, head_len=45, mid_len=20, tail_len=35

        Przyklad wyniku:
            490   # okno [490, 510] wokół 500
        """
        centered = (n - mid_len) // 2      # okno symetrycznie wokół geometrycznego środka
        lowest   = head_len                # nie wcześniej niż koniec okna początku
        highest  = n - tail_len - mid_len  # nie później niż tak, by skończyć przed oknem końca
        return min(max(centered, lowest), highest)

    @staticmethod
    def _part(
        percent: int,   # zastosowana proporcja części, np. 45 (0 = część wyłączona)
        start: int,     # offset początku zakresu (włącznie), np. 0
        end: int,       # offset końca zakresu (wyłącznie), np. 45
    ) -> TextPart:
        """Opis metody:
        Zbuduj opis jednej części. Proporcja 0 = część wyłączona -> `start`/`end` = None (brak
        zakresu, a nie pusty zakres — konsument rozróżnia „wyłączona" od „pusta").

        Przyklad argumentow:
            percent=0, start=490, end=490

        Przyklad wyniku:
            TextPart(percent=0, start=None, end=None)
        """
        # Część wyłączona proporcją 0 -> bez zakresu.
        if percent == 0:
            return TextPart(percent=0)
        # Część włączona -> zakres, nawet gdy przy maleńkim budżecie wyszedł pusty.
        return TextPart(percent=percent, start=start, end=end)

    @classmethod
    def _build_parts(
        cls,
        n: int,               # długość całego tekstu, np. 1000
        content_chars: int,   # budżet na treść (max_chars - MARKER_RESERVE), np. 100
        head_percent: int,    # proporcja początku, np. 45
        tail_percent: int,    # proporcja końca, np. 35
    ) -> TextParts:
        """Opis metody:
        Złóż zakresy trzech części z proporcji i budżetu. Część z proporcją 0 dostaje
        `start`/`end` = None (wyłączona); pozostałe mają zakres nawet przy zerowym budżecie.

        Przyklad argumentow:
            n=1000, content_chars=100, head_percent=45, tail_percent=35

        Przyklad wyniku:
            TextParts(head=TextPart(percent=45, start=0, end=45),
                      middle=TextPart(percent=20, start=490, end=510),
                      tail=TextPart(percent=35, start=965, end=1000))

        Raises:
            ValueError: niespójne proporcje (z `_middle_percent`).
        """
        # --- Proporcje i budżety znaków ---
        middle_percent = cls._middle_percent(head_percent, tail_percent)
        head_len, mid_len, tail_len = cls._part_budgets(content_chars, head_percent, middle_percent, tail_percent)

        # --- Zakresy: początek od 0, koniec do n, środek wokół n/2 (dosunięty) ---
        mid_start = cls._middle_start(n, head_len, mid_len, tail_len)
        return TextParts(
            head   = cls._part(head_percent, 0, head_len),
            middle = cls._part(middle_percent, mid_start, mid_start + mid_len),
            tail   = cls._part(tail_percent, n - tail_len, n),
        )

    @staticmethod
    def _assemble(
        text: str,          # cały tekst, z którego wycinamy fragmenty
        parts: TextParts,   # zakresy części z `_build_parts`
    ) -> str:
        """Opis metody:
        Sklej zachowane fragmenty w kolejności, wstawiając `OMISSION_MARKER` (jako osobny akapit)
        w każdym miejscu pominięcia: przed pierwszym fragmentem, między fragmentami i po ostatnim.
        Fragmenty, które się stykają, łączy wprost — bez znacznika i bez sztucznego `\\n\\n`
        (inaczej zmienilibyśmy treść w miejscu, gdzie nic nie wycięto).

        Przyklad argumentow:
            text="AAAABBBBCCCC", parts=TextParts(head=[0,2], middle=[5,7], tail=[10,12])

        Przyklad wyniku:
            "AA\\n\\n[…pominięto fragment dokumentu…]\\n\\nBB\\n\\n[…pominięto fragment dokumentu…]\\n\\nCC"
        """
        # Tylko części włączone i niepuste, w kolejności w tekście (head < middle < tail z konstrukcji).
        spans = [(p.start, p.end) for p in (parts.head, parts.middle, parts.tail) if p.start is not None and p.end > p.start]

        pieces: list[str] = []   # naprzemiennie: fragment / znacznik
        cursor = 0               # koniec ostatniego zachowanego fragmentu
        for start, end in spans:
            # Przerwa przed fragmentem -> znacznik, potem fragment jako nowy kawałek.
            if start > cursor:
                pieces.append(OMISSION_MARKER)
                pieces.append(text[start:end])
            # Styka się z poprzednim fragmentem -> doklejamy wprost (nic nie pominięto).
            elif pieces:
                pieces[-1] += text[start:end]
            # Pierwszy fragment od samego początku tekstu -> bez znacznika.
            else:
                pieces.append(text[start:end])
            cursor = end

        # Coś zostało za ostatnim fragmentem (np. koniec wyłączony) -> znacznik na końcu.
        if cursor < len(text):
            pieces.append(OMISSION_MARKER)
        return _SEPARATOR.join(pieces)

    # --- Wywołanie — orkiestracja czystych helperów ----------------------------------

    def apply(
        self,
        text: str,                                # tekst wejściowy (w serwisie: po strip), np. treść pisma
        *,
        head_percent: int = DEFAULT_HEAD_PERCENT,  # proporcja początku 0–100, np. 45
        tail_percent: int = DEFAULT_TAIL_PERCENT,  # proporcja końca 0–100, np. 35
    ) -> TruncationResult:
        """Opis metody:
        Przytnij tekst do budżetu: mieści się -> bez zmian; za długi -> początek / środek / koniec
        w zadanych proporcjach ze znacznikami pominięcia. Wynik zawsze `len(text) <= max_chars`.

        Przyklad argumentow:
            text="...120 000 znaków...", head_percent=45, tail_percent=35   # przy max_chars=90000

        Przyklad wyniku:
            TruncationResult(text="…początek…\\n\\n[…pominięto fragment dokumentu…]\\n\\n…środek…",
                             truncated=True,
                             parts=TextParts(head=TextPart(percent=45, start=0, end=40467), ...))

        Raises:
            ValueError: niespójne proporcje (spoza 0–100 albo suma początku i końca > 100).
        """
        # Mieści się w budżecie -> całość do modelu; proporcje sprawdzamy mimo to (spójny kontrakt).
        if len(text) <= self._max_chars:
            self._middle_percent(head_percent, tail_percent)
            return TruncationResult(text=text, truncated=False, parts=None)

        # Za długi -> zakresy części z budżetu pomniejszonego o stałą rezerwę na znaczniki.
        parts = self._build_parts(len(text), self._max_chars - MARKER_RESERVE, head_percent, tail_percent)
        return TruncationResult(text=self._assemble(text, parts), truncated=True, parts=parts)
