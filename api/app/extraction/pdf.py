"""Operacje na stronach PDF (krok 2.3.5) — izolacja zależności `pypdf`.

To JEDYNE miejsce, ktore rozmawia z `pypdf` (analogia do `TikaClient` izolujacego `httpx`
oraz `OpenAILLMClient` izolujacego SDK `openai`). Domena (`ExtractionService`) NIE zna
`pypdf` — dostaje gotowy `PdfPageLimiter` i pyta go tylko o `apply(...)`.

Po co to: straznik zasobow OCR. Wielostronicowy skan (np. 100 stron) OCR-owalby sie w
calosci i moglby zatkac usluge/przekroczyc timeout. Strategia (B) "limit PRZED `auto`":
zanim cokolwiek poslemy do Tiki, dla PDF liczymy strony i — gdy za duzo — tniemy do
pierwszych N. Tika 3.3.0.0 NIE ma natywnego `maxPages` (dodany w 4.x), wiec tniemy plik
sami (`pypdf`). Limit dotyczy KAZDEGO duzego PDF (de facto MAX_PDF_PAGES), nie tylko skanow,
i NIE jest cichy (log + `PageLimit` -> metadane w odpowiedzi).
"""

from __future__ import annotations

import logging
from io import BytesIO
from typing import NamedTuple

from pypdf import PdfReader, PdfWriter
from pypdf.errors import PyPdfError

logger = logging.getLogger(__name__)

# Sygnatura pliku PDF (magic bytes). Liczymy/tniemy stronami TYLKO PDF — `%PDF-` bywa
# poprzedzone smieciem przed naglowkiem, wiec szukamy w pierwszym kilobajcie.
_PDF_MAGIC = b"%PDF-"


class PageLimit(NamedTuple):
    """Wynik `PdfPageLimiter.apply`: bajty do wyslania + diagnostyka stron/ciecia.

    `data` to bajty po ew. cieciu (dla nie-PDF/niepocietego = wejscie bez zmian).
    `pages_total`/`pages_processed` = None dla nie-PDF lub niepoliczalnego PDF — domena
    traktuje `pages_total is not None` jako "to (parsowalny) PDF".
    """

    data: bytes
    pages_total: int | None
    pages_processed: int | None
    truncated: bool


class PdfPageLimiter:
    """Straznik zasobow OCR: ogranicza liczbe stron PDF wysylanych do ekstrakcji.

    Do czego:
        Dla bajtow PDF zwraca `PageLimit` — bajty do wyslania (ew. ucięte do pierwszych N
        stron) plus diagnostyke (ile stron mial dokument, ile przetwarzamy, czy ucieto).
        Dla nie-PDF oddaje wejscie bez zmian. Izoluje cala mechanike `pypdf` (liczenie,
        ciecie, obsluga uszkodzonych plikow) od domeny.

    Flow `apply(data)`:
        1. nie-PDF -> bajty bez zmian, `pages_*`=None,
        2. PDF, ale `pypdf` nie sparsuje -> bajty bez zmian, `pages_*`=None (degradacja, log),
        3. PDF w limicie -> bajty bez zmian, `pages_processed=pages_total`,
        4. PDF za duzy -> bajty pierwszych N stron, `truncated=True` (log ostrzegawczy).
    """

    def __init__(
        self,
        max_pages: int = 30,   # limit liczby stron PDF wysylanych do Tiki (z `Settings.max_ocr_pages`)
    ) -> None:
        """Opis metody:
        Zbuduj limiter (sama konfiguracja, bez I/O).

        Przyklad argumentow:
            max_pages=30

        Przyklad wyniku:
            gotowy PdfPageLimiter
        """
        self._max_pages = max_pages

    # --- Czyste helpery (bez I/O) — testowalne jednostkowo ---------------------------

    @staticmethod
    def is_pdf(
        data: bytes,   # surowe bajty pliku
    ) -> bool:
        """Opis metody:
        Czy bajty wygladaja na PDF (magic `%PDF-` w pierwszym kilobajcie). Czysta funkcja.

        Przyklad argumentow:
            data=b"%PDF-1.7\\n..."

        Przyklad wyniku:
            True
        """
        # `%PDF-` bywa poprzedzone smieciem przed naglowkiem -> szukamy w prefiksie, nie tylko [0:5].
        return _PDF_MAGIC in data[:1024]

    @staticmethod
    def _page_count(
        data: bytes,   # bajty PDF (zwalidowane wczesniej przez `is_pdf`)
    ) -> int | None:
        """Opis metody:
        Policz strony PDF (`pypdf`, w pamieci, BEZ renderowania — tanio). Gdy `pypdf` nie da
        rady (PDF uszkodzony/zaszyfrowany) -> None: wolajacy przetworzy plik bez limitu
        (Tika sama ew. odrzuci). Nie rzuca — bledy `pypdf` tlumimy na None + log.

        Przyklad argumentow:
            data=b"%PDF-1.7 ... (3 strony)"

        Przyklad wyniku:
            3
        """
        try:
            return len(PdfReader(BytesIO(data)).pages)
        except (PyPdfError, OSError, ValueError) as exc:
            # Nie wywracamy ekstrakcji przez sam blad liczenia stron — degradujemy do "bez limitu".
            logger.warning("Nie udalo sie policzyc stron PDF (%s); przetwarzam bez limitu stron.", exc)
            return None

    @staticmethod
    def _take_first_pages(
        data: bytes,   # bajty zrodlowego PDF
        n: int,        # ile pierwszych stron zachowac (>=1)
    ) -> bytes | None:
        """Opis metody:
        Zwroc nowy PDF z pierwszymi `n` stronami (`pypdf`, w pamieci). Gdy ciecie sie nie
        powiedzie -> None: wolajacy przetworzy CALOSC (z logiem, nie cicho). Nie rzuca.

        Przyklad argumentow:
            data=b"%PDF (100 stron)", n=30

        Przyklad wyniku:
            b"%PDF (30 stron)"
        """
        try:
            reader = PdfReader(BytesIO(data))
            writer = PdfWriter()
            # Bierzemy swiadomie tylko POCZATEK dokumentu (nie chunking/map-reduce).
            for page in reader.pages[:n]:
                writer.add_page(page)
            buffer = BytesIO()
            writer.write(buffer)
            return buffer.getvalue()
        except (PyPdfError, OSError, ValueError) as exc:
            logger.warning("Nie udalo sie pociac PDF do %d stron (%s); przetwarzam calosc.", n, exc)
            return None

    # --- Wlasciwa decyzja (uzywa pypdf, nie sieci) ----------------------------------

    def apply(
        self,
        data: bytes,   # surowe bajty pliku (dowolnego typu)
    ) -> PageLimit:
        """Opis metody:
        Strategia (B) "limit PRZED auto": dla PDF policz strony i — gdy > `max_pages` — utnij
        do pierwszych N PRZED wyslaniem do Tiki. Nie-PDF / nieparsowalny PDF: bajty bez zmian
        i `pages_*`=None. Limit NIE jest cichy (log + `PageLimit`).

        Przyklad argumentow:
            data=b"%PDF (100 stron)"   # przy max_pages=30

        Przyklad wyniku:
            PageLimit(data=b"%PDF (30 stron)", pages_total=100, pages_processed=30, truncated=True)
        """
        # Nie-PDF: stron nie liczymy, niczego nie tniemy (OCR obrazu = 1 "strona").
        if not self.is_pdf(data):
            return PageLimit(data, None, None, False)

        pages_total = self._page_count(data)
        # pypdf nie policzyl (uszkodzony/zaszyfrowany) -> przetwarzamy calosc bez limitu (log w helperze).
        if pages_total is None:
            return PageLimit(data, None, None, False)

        # PDF miesci sie w limicie -> bez ciecia; processed = total.
        if pages_total <= self._max_pages:
            return PageLimit(data, pages_total, pages_total, False)

        # PDF za duzy -> tniemy do pierwszych N. Limit nie cichy: log ostrzegawczy.
        trimmed = self._take_first_pages(data, self._max_pages)
        if trimmed is None:
            # Ciecie nie wyszlo -> przetwarzamy calosc (helper juz zalogowal), bez znacznika truncated.
            return PageLimit(data, pages_total, pages_total, False)
        logger.warning(
            "PDF ma %d stron > limit %d; przetwarzam pierwsze %d (reszta pominieta).",
            pages_total, self._max_pages, self._max_pages,
        )
        return PageLimit(trimmed, pages_total, self._max_pages, True)
