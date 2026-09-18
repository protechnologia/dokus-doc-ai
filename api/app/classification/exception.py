"""Wyjątki domenowe klasyfikacji — w jednym miejscu dla całego pakietu.

Dwie grupy, różny los:
  - WEWNĘTRZNE (`UnknownLabelError`, `InvalidModelResponseError`) — odpowiedź modelu była, ale nie
    da się jej użyć. Z serwisu nie wychodzą: `ClassificationService` zamienia je na wynik
    `invalid_response`, a komunikat wyjątku trafia do pola `error` (kontrakt: `200` + `outcome`),
  - PROPAGUJĄCE (`PromptTooLongError`) — odpowiedzi modelu nie było; mapowanie na HTTP robi
    endpoint (413). Tak samo propagują błędy LLM (`LLMError` z `app.llm`).
"""

from __future__ import annotations


class UnknownLabelError(Exception):
    """Etykieta spoza nadanych — możliwa tylko na zapleczu, które nie egzekwuje `enum` ze schematu."""


class InvalidModelResponseError(Exception):
    """Odpowiedzi modelu nie da się użyć; komunikat = przyczyna do pola `error` wyniku `invalid_response`."""


class PromptTooLongError(Exception):
    """Złożony prompt dłuższy niż budżet znaków — model niewołany (endpoint -> 413)."""

    def __init__(
        self,
        prompt_chars: int,   # długość promptu systemowego + użytkownika, np. 91234
        max_chars: int,      # budżet znaków (z `Settings.llm_max_input_chars`), np. 90000
    ) -> None:
        """Opis metody:
        Zapamiętaj obie liczby (dla wołającego) i złóż z nich komunikat.

        Przyklad argumentow:
            prompt_chars=91234, max_chars=90000

        Przyklad wyniku:
            PromptTooLongError("Prompt klasyfikacji ma 91234 znaków, limit 90000.")
        """
        super().__init__(f"Prompt klasyfikacji ma {prompt_chars} znaków, limit {max_chars}.")
        self.prompt_chars = prompt_chars
        self.max_chars    = max_chars
