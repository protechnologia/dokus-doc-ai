"""Detekcja śmieciowej warstwy tekstowej PDF (PUA) — krok 2.3.5.

Wydzielona z `ExtractionService` jako spójna jednostka jednej decyzji: "czy wyekstrahowany
natywnie tekst to smiec, ktory trzeba ratowac OCR-em". Czysta logika domenowa (bez sieci i
bez zaleznosci zewnetrznych) — dlatego osobno, by domena orkiestrujaca byla cienka.

Kanoniczny przypadek (zmierzony 2026-06-18, patrz CLAUDE.md "2.3.5"): PDF z zepsuta/brakujaca
CMap `ToUnicode` mapuje glify na Unicode Private Use Area. Ekstrakcja natywna zwraca wtedy
"tekst", ktory jest smieciem (a NIE pusty wynik), a `ocrStrategy=auto` OCR-u nie odpala, bo
"warstwa tekstowa jest". Wykrywamy to po udziale znakow PUA i wymuszamy OCR.
"""

from __future__ import annotations

# Zakresy Unicode Private Use Area:
#   - BMP PUA:                 U+E000..U+F8FF  (tu siedzi nasz zmierzony przypadek, U+F0xx)
#   - Supplementary PUA-A/B:   U+F0000..U+FFFFD, U+100000..U+10FFFD
_PUA_RANGES = ((0xE000, 0xF8FF), (0xF0000, 0xFFFFD), (0x100000, 0x10FFFD))

# Domyslny prog: udzial znakow PUA wsrod znakow nie-bialych powyzej ktorego uznajemy warstwe
# za smieciowa. Separacja zmierzona drastyczna (warstwa PUA ~77% vs poprawny OCR 0%), wiec
# 0.30 ma duzy margines w obie strony (zwykly tekst z pojedynczym symbolem PUA nie przekroczy).
_DEFAULT_GARBAGE_THRESHOLD = 0.30


class PuaDetector:
    """Klasyfikator jakosci warstwy tekstowej: czy jest zdominowana przez znaki PUA.

    Do czego:
        Liczy udzial znakow Private Use Area w tekscie (`ratio`) i rozstrzyga, czy warstwa
        jest smieciowa i wymaga OCR-fallbacku (`is_garbage`). Prog jest konfigurowalny
        (analogicznie do `PdfPageLimiter.max_pages`), dzieki czemu mozna go nastroic/testowac
        bez zmiany logiki.
    """

    def __init__(
        self,
        threshold: float = _DEFAULT_GARBAGE_THRESHOLD,   # prog udzialu PUA, powyzej = smiec
    ) -> None:
        """Opis metody:
        Zbuduj detektor z progiem udzialu PUA (sama konfiguracja, bez I/O).

        Przyklad argumentow:
            threshold=0.30

        Przyklad wyniku:
            gotowy PuaDetector
        """
        self._threshold = threshold

    @property
    def threshold(self) -> float:
        """Prog udzialu PUA (do logow/diagnostyki w warstwie orkiestrujacej)."""
        return self._threshold

    @staticmethod
    def _is_pua(
        cp: int,   # punkt kodowy znaku (ord(ch))
    ) -> bool:
        """Opis metody:
        Czy punkt kodowy lezy w ktoryms z zakresow Private Use Area. Czysta funkcja.

        Przyklad argumentow:
            cp=0xF0DC

        Przyklad wyniku:
            True
        """
        return any(lo <= cp <= hi for lo, hi in _PUA_RANGES)

    @classmethod
    def ratio(
        cls,
        text: str,   # tekst do oceny (zwykle surowy z natywnej ekstrakcji PDF)
    ) -> float:
        """Opis metody:
        Policz udzial znakow PUA wsrod znakow NIE-bialych (biale ignorujemy — PDF z PUA ma
        tez mnostwo `\\n`, ktore nie swiadcza o jakosci). Pusty/sam whitespace -> 0.0.
        Czysta funkcja — fundament detekcji.

        Przyklad argumentow:
            text="\\uf0dc\\uf0b1\\uf0bc abc"   # 6 nie-bialych, 3 PUA

        Przyklad wyniku:
            0.5
        """
        # Liczymy tylko znaki nie-biale: whitespace nie niesie informacji o jakosci warstwy.
        non_ws = [ch for ch in text if not ch.isspace()]
        if not non_ws:
            return 0.0
        pua = sum(1 for ch in non_ws if cls._is_pua(ord(ch)))
        return pua / len(non_ws)

    def is_garbage(
        self,
        text: str,   # tekst z natywnej ekstrakcji do oceny
    ) -> bool:
        """Opis metody:
        Czy warstwa tekstowa jest smieciowa (udzial PUA powyzej progu) i wymaga OCR-fallbacku.

        Przyklad argumentow:
            text="\\uf0dc\\uf0b1\\uf0bc\\uf0bf"   # 100% PUA

        Przyklad wyniku:
            True
        """
        return self.ratio(text) > self._threshold
