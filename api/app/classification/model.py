"""Wynik domenowy klasyfikacji: `ClassificationResult` + trzy konstruktory wariantów.

Odrębny od modelu API (`ClassifyResponse` w `app/models.py`) — kontrakt HTTP stoi niezależnie od
ewolucji domeny, mapowanie robi `ClassifyResponse.from_result`. Spójność pól z `outcome` wynika
z konstrukcji: wynik składają wyłącznie `matched` / `no_match` / `invalid`, a każdy bierze tylko
pola swojego wariantu. Walidatora spójności świadomie nie ma — to obrona przed własnym błędem
w miejscu, gdzie go nie popełniamy (ten sam argument co przy modelu API, krok 8).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.classification.service_labels import NO_MATCH_LABEL
from app.llm import LLMResult, LLMUsage

# Wynik: każda odpowiedź modelu (także bezużyteczna) kończy się jednym z nich.
ClassificationOutcome = Literal["matched", "no_match", "invalid_response"]


class ClassificationResult(BaseModel):
    """Domenowy wynik klasyfikacji: wybór, uzasadnienie albo przyczyna błędu + zapis wymiany z modelem.

    Do czego:
        Jeden kształt dla trzech wyników, bo klient zapisuje audyt (oba prompty + surowa odpowiedź)
        także wtedy, gdy odpowiedzi modelu nie da się użyć. Które pola są wypełnione przy danym
        `outcome`, wyznaczają konstruktory — składać wyłącznie przez nie:
            matched          -> `option_id`, `label` (`OPT-n`), `rationale`; `error` = None,
            no_match         -> `label` = `OPT-00`, `rationale`; `option_id` i `error` = None,
            invalid_response -> `error`; `option_id`, `label` i `rationale` = None.
        Pola wymiany z modelem (`model`, `usage`, oba prompty, `raw_response`) — zawsze.

    Flow złożenia:
        `matched` / `no_match` / `invalid` -> pola wariantu + `_exchange` (wspólne pola wymiany) -> `cls(...)`.
    """

    outcome: ClassificationOutcome  = Field(description="matched = wybrano opcję; no_match = żadna opcja nie pasuje; invalid_response = odpowiedzi modelu nie da się użyć.")
    option_id: int | str | None     = Field(description="`id` wybranej opcji w typie z wejścia; None, gdy outcome != matched.")
    label: str | None               = Field(description="Etykieta wybrana przez model (`OPT-n` albo `OPT-00`); None przy invalid_response.")
    rationale: str | None           = Field(description="Uzasadnienie wyboru od modelu (także przy no_match); None przy invalid_response.")
    error: str | None               = Field(description="Przyczyna, dla której odpowiedzi modelu nie da się użyć; None, gdy outcome != invalid_response.")
    model: str                      = Field(description="Identyfikator modelu, który odpowiedział, np. 'gpt-4o-mini'.")
    usage: LLMUsage                 = Field(description="Zużycie tokenów — diagnostyka ucięcia promptu i odpowiedzi.")
    system_prompt: str              = Field(description="Prompt systemowy wysłany do modelu (audyt).")
    user_prompt: str                = Field(description="Prompt użytkownika wysłany do modelu (audyt).")
    raw_response: str               = Field(description="Surowa odpowiedź modelu przed parsowaniem, dosłownie (audyt).")

    # --- Konstruktory wariantów — jedyna droga złożenia wyniku ----------------------

    @classmethod
    def matched(
        cls,
        *,
        option_id: int | str,   # `id` wybranej opcji w typie z wejścia, np. 21 albo "db-7781"
        label: str,             # etykieta wybrana przez model, np. "OPT-1"
        rationale: str,         # uzasadnienie od modelu, np. "Skarga konsumenta na operatora..."
        system_prompt: str,     # prompt systemowy wysłany do modelu
        user_prompt: str,       # prompt użytkownika wysłany do modelu
        response: LLMResult,    # odpowiedź modelu (źródło `raw_response`, `model`, `usage`)
    ) -> ClassificationResult:
        """Opis metody:
        Złóż wynik `matched`: model wybrał opcję z listy.

        Przyklad argumentow:
            option_id=21, label="OPT-1", rationale="Skarga konsumenta na operatora.",
            system_prompt="Wybierz...", user_prompt="Streszczenia...",
            response=LLMResult(text='{"rationale": "...", "label": "OPT-1"}', model="gpt-4o-mini")

        Przyklad wyniku:
            ClassificationResult(outcome="matched", option_id=21, label="OPT-1",
                                 rationale="Skarga konsumenta na operatora.", error=None, ...)
        """
        return cls(outcome="matched", option_id=option_id, label=label, rationale=rationale, error=None, **cls._exchange(system_prompt, user_prompt, response))

    @classmethod
    def no_match(
        cls,
        *,
        rationale: str,         # uzasadnienie od modelu, np. "Żadna opcja nie obejmuje dotacji."
        system_prompt: str,     # prompt systemowy wysłany do modelu
        user_prompt: str,       # prompt użytkownika wysłany do modelu
        response: LLMResult,    # odpowiedź modelu (źródło `raw_response`, `model`, `usage`)
    ) -> ClassificationResult:
        """Opis metody:
        Złóż wynik `no_match`: model wybrał `OPT-00` (etykietę konstruktor wpisuje sam).

        Przyklad argumentow:
            rationale="Żadna opcja nie obejmuje dotacji.", system_prompt="...", user_prompt="...",
            response=LLMResult(text='{"rationale": "...", "label": "OPT-00"}', model="gpt-4o-mini")

        Przyklad wyniku:
            ClassificationResult(outcome="no_match", option_id=None, label="OPT-00",
                                 rationale="Żadna opcja nie obejmuje dotacji.", error=None, ...)
        """
        return cls(outcome="no_match", option_id=None, label=NO_MATCH_LABEL, rationale=rationale, error=None, **cls._exchange(system_prompt, user_prompt, response))

    @classmethod
    def invalid(
        cls,
        *,
        error: str,             # przyczyna, np. "Niepoprawny JSON w odpowiedzi modelu: Unterminated string..."
        system_prompt: str,     # prompt systemowy wysłany do modelu
        user_prompt: str,       # prompt użytkownika wysłany do modelu
        response: LLMResult,    # odpowiedź modelu (źródło `raw_response`, `model`, `usage`)
    ) -> ClassificationResult:
        """Opis metody:
        Złóż wynik `invalid_response`: odpowiedź modelu była, ale nie da się jej użyć.

        Przyklad argumentow:
            error="Pusta odpowiedź modelu.", system_prompt="...", user_prompt="...",
            response=LLMResult(text="", model="gpt-4o-mini")

        Przyklad wyniku:
            ClassificationResult(outcome="invalid_response", option_id=None, label=None,
                                 rationale=None, error="Pusta odpowiedź modelu.", raw_response="", ...)
        """
        return cls(outcome="invalid_response", option_id=None, label=None, rationale=None, error=error, **cls._exchange(system_prompt, user_prompt, response))

    # --- Czysty helper — pola wspólne dla wariantów -----------------------------------

    @staticmethod
    def _exchange(
        system_prompt: str,     # prompt systemowy wysłany do modelu
        user_prompt: str,       # prompt użytkownika wysłany do modelu
        response: LLMResult,    # odpowiedź modelu, np. LLMResult(text='{...}', model="gpt-4o-mini", usage=...)
    ) -> dict[str, Any]:
        """Opis metody:
        Pola wymiany z modelem, wspólne dla trzech wariantów. Odpowiedź dosłownie, bez strip —
        to zapis audytu, nie treść do wyświetlenia.

        Przyklad argumentow:
            system_prompt="Wybierz...", user_prompt="Streszczenia...",
            response=LLMResult(text='{"rationale": "...", "label": "OPT-1"}', model="gpt-4o-mini")

        Przyklad wyniku:
            {"model": "gpt-4o-mini", "usage": LLMUsage(...), "system_prompt": "Wybierz...",
             "user_prompt": "Streszczenia...", "raw_response": '{"rationale": "...", "label": "OPT-1"}'}
        """
        return {
            "model":         response.model,
            "usage":         response.usage,
            "system_prompt": system_prompt,
            "user_prompt":   user_prompt,
            "raw_response":  response.text,
        }
