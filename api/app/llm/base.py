"""Interfejs klienta LLM + modele wyniku + wyjatki domenowe (krok 2.2).

Granica odpowiedzialnosci: to TRANSPORT/generacja — "wiadomosci -> tekst + zuzycie".
NIE zna promptow summaryzacji, systemu po polsku ani truncacji pod okno modelu —
to dochodzi w kroku 2.4. Analogia do przyszlego `TikaClient`: oddzielamy "rozmowe
z dostawca" od logiki domenowej.

Zasada naczelna nr 2: cala logika biznesowa rozmawia z tym interfejsem, NIGDY
bezposrednio z SDK dostawcy. Zmiana OpenAI -> Azure -> Bielik ma byc zmiana
implementacji + konfiguracji, nie logiki.
"""

from __future__ import annotations
from abc        import ABC, abstractmethod
from pydantic   import BaseModel, Field


# --- Modele wyniku generacji -----------------------------------------------------


class LLMUsage(BaseModel):
    """Zuzycie tokenow w jednym wywolaniu (do logow/metadanych, nie do biznesu)."""

    prompt_tokens:     int = 0
    completion_tokens: int = 0
    total_tokens:      int = 0


class LLMResult(BaseModel):
    """Wynik generacji: tekst + uzyty model + zuzycie tokenow."""

    text: str       = Field(description="Wygenerowany tekst (np. streszczenie).")
    model: str      = Field(description="Identyfikator modelu, ktory faktycznie odpowiedzial.")
    usage: LLMUsage = Field(default_factory=LLMUsage)


# --- Wyjatki domenowe (niezalezne od SDK dostawcy) -------------------------------
# Logika mapuje je na kody HTTP dopiero w krokach 2.4/2.5 — tu tylko klasyfikujemy.


class LLMError(Exception):
    """Bazowy blad warstwy LLM."""


class LLMAuthError(LLMError):
    """Zly lub brakujacy klucz, brak uprawnien (401/403)."""


class LLMRateLimitError(LLMError):
    """Przekroczony limit zapytan lub kwota (429)."""


class LLMTimeoutError(LLMError):
    """Przekroczony czas oczekiwania na odpowiedz dostawcy."""


class LLMResponseError(LLMError):
    """Inny blad dostawcy / nieoczekiwana odpowiedz (5xx itd.)."""


# --- Interfejs klienta -----------------------------------------------------------


class LLMClient(ABC):
    """Abstrakcyjny klient LLM. Jedyna metoda: `complete` (jeden prompt -> tekst).

    Celowo minimalna i generyczna — wystarcza dla summaryzacji (krok 2.4), a nie
    przywiazuje sie do zadnego dostawcy. Bogatszy interfejs (lista `messages`,
    streaming, narzedzia) dolozymy dopiero, gdy logika tego naprawde zazada.
    """

    @abstractmethod
    async def complete(
        self,
        *,
        user:        str,                # tresc usera, np. "Streszcz dokument:\n<tekst>"
        system:      str | None = None,  # prompt systemowy (rola/ton; ustawiany w 2.4); None = brak
        max_tokens:  int | None = None,  # limit dlugosci odpowiedzi, np. 300; None = default dostawcy
        temperature: float = 0.0,        # losowosc; 0.0 = stabilnie (streszczenia maja byc powtarzalne)
    ) -> LLMResult:
        """Opis metody:
        Wygeneruj odpowiedz dla pojedynczego promptu uzytkownika.

        Przyklad argumentow:
            user="Streszcz: <tekst>"
            system="Po polsku"

        Przyklad wyniku:
            LLMResult(text="<streszczenie>", model="<model>", usage=LLMUsage(...))

        Raises:
            LLMError: dowolny blad warstwy LLM (auth/limit/timeout/odpowiedz).
        """
        ...
