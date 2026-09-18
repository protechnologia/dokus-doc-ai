"""Modele HTTP: parametry trunkacji wejscia modelu — wspolna baza zadan /summarize i /extract-and-summarize.

Osobny modul, bo dziedzicza z niego zadania DWOCH endpointow — w `summarize.py` pipeline
importowalby baze z modulu cudzego endpointu.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from app.summarization import DEFAULT_HEAD_PERCENT, DEFAULT_TAIL_PERCENT


class TruncationParams(BaseModel):
    """Wspolne, OPCJONALNE parametry trunkacji wejscia modelu (poczatek / srodek / koniec).

    Dziedzicza je `SummarizeRequest` i `SummarizeDocumentRequest` — jedna definicja i jeden
    walidator sumy dla obu endpointow. Brak klucza = wartosc domyslna; `null`, wartosc spoza
    0–100 albo suma powyzej 100 -> 422 z walidacji pydantic (loguje `log_validation_error`).
    Dzialaja tylko, gdy tekst przekracza `LLM_MAX_INPUT_CHARS` — krotszy idzie do modelu w calosci.
    """

    head_percent: int = Field(default=DEFAULT_HEAD_PERCENT, ge=0, le=100, description="Proporcja budzetu LLM_MAX_INPUT_CHARS na POCZATEK dokumentu (0–100). Srodek dostaje reszte: 100 - head_percent - tail_percent.")
    tail_percent: int = Field(default=DEFAULT_TAIL_PERCENT, ge=0, le=100, description="Proporcja budzetu LLM_MAX_INPUT_CHARS na KONIEC dokumentu (0–100). 0 wylacza dana czesc.")

    @model_validator(mode="after")
    def _suma_proporcji_max_100(self) -> TruncationParams:
        """Opis metody:
        Sprawdz, ze poczatek + koniec nie przekraczaja 100 (srodek nie moze byc ujemny). Zakres
        pojedynczej wartosci pilnuja `ge`/`le` na polach.

        Przyklad argumentow:
            self=TruncationParams(head_percent=60, tail_percent=50)

        Przyklad wyniku:
            ValueError -> 422 (dla 45/55 zwraca self bez zmian)

        Raises:
            ValueError: suma `head_percent + tail_percent` powyzej 100.
        """
        # Suma > 100 -> brak miejsca na srodek; pydantic zamienia ValueError na 422.
        if self.head_percent + self.tail_percent > 100:
            raise ValueError(f"head_percent + tail_percent nie moze przekraczac 100 (jest {self.head_percent + self.tail_percent}).")
        return self
