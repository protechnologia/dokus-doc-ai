"""Atrapa klienta LLM — dev/test bez sieci i bez kosztow (krok 2.2).

Domyslny provider ('fake'): pipeline dziala end-to-end, ale NIC nie wychodzi na
zewnatrz — spojne z zasada "prywatnosc pierwsza". Odpowiedz jest deterministyczna
(zalezna od wejscia), zeby testy mogly cokolwiek asertowac, a dev widzial, ze
prompt naprawde dolecial.
"""

from __future__ import annotations

from app.llm.base import LLMClient, LLMResult, LLMUsage

FAKE_MODEL = "fake-echo"
_PREFIX = "[FAKE-LLM]"


class FakeLLMClient(LLMClient):
    """Zwraca prefiks + skrocone wejscie. Bez I/O, deterministyczna."""

    def __init__(
        self,
        *,
        model: str = FAKE_MODEL,   # etykieta modelu w wyniku, np. "fake-echo"
    ) -> None:
        """Opis metody:
        Zbuduj atrape (bez sieci, bez kosztow).

        Przyklad argumentow:
            model="fake-echo"   (domyslny)

        Przyklad wyniku:
            atrapa zwracajaca echo wejscia jako "streszczenie"
        """
        self._model = model

    async def complete(
        self,
        *,
        user: str,                     # tresc usera, np. "Pismo w sprawie podatku..."
        system: str | None = None,     # ignorowany w atrapie (jest dla zgodnosci interfejsu)
        max_tokens: int | None = None, # ignorowany w atrapie
        temperature: float = 0.0,      # ignorowany w atrapie
    ) -> LLMResult:
        """Opis metody:
        Zwroc deterministyczne "streszczenie": prefiks + pierwsze ~40 slow wejscia.

        Przyklad argumentow:
            user="Pismo w sprawie podatku od nieruchomosci"

        Przyklad wyniku:
            LLMResult(text="[FAKE-LLM] Pismo w sprawie podatku od nieruchomosci",
                      model="fake-echo", usage=LLMUsage(...))
        """
        # "Streszczenie": pierwsze ~40 slow wejscia, znormalizowany whitespace.
        snippet = " ".join(user.split()[:40])
        text = f"{_PREFIX} {snippet}".strip()
        usage = LLMUsage(
            prompt_tokens=len(user.split()),
            completion_tokens=len(text.split()),
            total_tokens=len(user.split()) + len(text.split()),
        )
        return LLMResult(text=text, model=self._model, usage=usage)
