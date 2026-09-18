"""Etykiety opcji klasyfikacji: `OPT-1`…`OPT-n` + `OPT-00` („brak dopasowania").

Model nigdy nie widzi surowych `id` klienta (klucze z bazy DOKUS-a — nieprzezroczyste, liczbowe
albo tekstowe). Widzi krótkie etykiety nadane przez usługę w kolejności wejścia, a wybraną
etykietę serwis rozwiązuje z powrotem na `id`. Czysta logika domenowa (bez I/O, bez logów) —
analogia do `TextTruncator` w summaryzacji.

Decyzje (2026-09-17 — nie „poprawiać" bez powodu):
  - format `OPT-1`…`OPT-n` + `OPT-00`, jak w zgłoszeniu DOKUS-a. Obawa „model odda `OPT-01`" nie
    dotyczy zapleczy egzekwujących schemat (`enum` na to nie pozwala), a `OPT-00` wyróżnia się
    szerokością jako pozycja specjalna,
  - `OPT-00` OSTATNIA — wzorzec „żadne z powyższych": model ocenia opcje, a wyjście awaryjne ma
    tuż przed odpowiedzią. Kolejność pozycji ustala wyłącznie `OptionLabeler.entries`; prompt
    i `enum` schematu ją przejmują, żeby nie rozjechały się między sobą,
  - rozwiązanie etykiety PO POZYCJI, nie po `id`: powtórzone `id` w wejściu nie psują mapowania
    (dlatego model API ich nie waliduje).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import NamedTuple

from pydantic import BaseModel, Field

from app.classification.exception import UnknownLabelError

# --- Stałe -----------------------------------------------------------------------

# Etykieta „brak dopasowania" — jedyna siatka bezpieczeństwa; usługa dokleja ją zawsze sama.
NO_MATCH_LABEL = "OPT-00"

# Prefiks etykiet opcji klienta: `OPT-1`, `OPT-2`, …
_LABEL_PREFIX = "OPT-"


# --- Model domenowy opcji --------------------------------------------------------


class ClassificationOption(BaseModel):
    """Opcja do wyboru — domenowy odpowiednik `ClassifyOption` (mapowanie z modelu API robi router)."""

    id: int | str         = Field(description="Identyfikator klienta: nie trafia do modelu, wraca w oryginalnym typie.")
    name: str             = Field(description="Nazwa opcji.")
    description: str      = Field(description="Opis: czego dotyczy opcja.")
    examples: str | None  = Field(default=None, description="Przykłady spraw pasujących do opcji; None = brak.")


class LabeledEntry(NamedTuple):
    """Jedna pozycja listy w prompcie: etykieta + opcja klienta (`option=None` = pozycja `OPT-00`)."""

    label: str
    option: ClassificationOption | None


# --- Jednostka etykiet -----------------------------------------------------------


class OptionLabeler:
    """Nadaje opcjom krótkie etykiety i rozwiązuje wybraną etykietę z powrotem na `id` klienta.

    Do czego:
        Oddziela to, co widzi model (etykiety), od kluczy klienta (`id`). Jedno źródło kolejności
        pozycji dla promptu i dla `enum` schematu odpowiedzi — `OPT-00` zawsze dokładnie raz, na
        końcu. Czysta — bez I/O i bez logów; nieznaną etykietę zgłasza wyjątkiem, a zamianę na
        wynik `invalid_response` robi serwis.

    Flow:
        1. konstruktor -> `_label(pozycja)` dla każdej opcji w kolejności wejścia + `OPT-00` na końcu,
        2. `entries` -> pozycje do promptu, `labels` -> te same etykiety do `enum`,
        3. `resolve(etykieta)` -> `id` opcji w oryginalnym typie; `OPT-00` -> None; inna -> `UnknownLabelError`.
    """

    def __init__(
        self,
        options: Sequence[ClassificationOption],   # opcje klienta w kolejności z żądania, np. [ClassificationOption(id=21, ...)]
    ) -> None:
        """Opis metody:
        Nadaj etykiety opcjom w kolejności wejścia i doklej `OPT-00` na końcu (sama konfiguracja).

        Przyklad argumentow:
            options=[ClassificationOption(id=21, name="Podatki", description="..."),
                     ClassificationOption(id="db-7781", name="Kadry", description="...")]

        Przyklad wyniku:
            OptionLabeler z pozycjami OPT-1 (id 21), OPT-2 (id "db-7781"), OPT-00
        """
        # Pozycje w kolejności promptu: opcje klienta od `OPT-1`, na końcu zawsze `OPT-00` (dokładnie raz).
        entries = [LabeledEntry(self._label(position), option) for position, option in enumerate(options, start=1)]
        entries.append(LabeledEntry(NO_MATCH_LABEL, None))
        self._entries = tuple(entries)

        # Etykieta -> opcja. Klucze unikalne z konstrukcji (po pozycji), więc powtórzone `id` nie kolidują.
        self._by_label = {entry.label: entry.option for entry in self._entries}

    # --- Czysty helper — testowalny punktowo -----------------------------------------

    @staticmethod
    def _label(
        position: int,   # pozycja opcji w wejściu liczona od 1, np. 3
    ) -> str:
        """Opis metody:
        Zbuduj etykietę opcji z jej pozycji — bez dopełniania zerami.

        Przyklad argumentow:
            position=3

        Przyklad wyniku:
            "OPT-3"   # position=10 -> "OPT-10"
        """
        return f"{_LABEL_PREFIX}{position}"

    # --- Odczyt pozycji --------------------------------------------------------------

    @property
    def entries(self) -> tuple[LabeledEntry, ...]:
        """Pozycje w kolejności promptu, `OPT-00` ostatnia — np. (("OPT-1", opcja), ("OPT-00", None))."""
        return self._entries

    @property
    def labels(self) -> list[str]:
        """Etykiety w kolejności `entries` (do `enum` schematu) — np. ["OPT-1", "OPT-2", "OPT-00"]."""
        return [entry.label for entry in self._entries]

    # --- Rozwiązanie wyboru modelu ---------------------------------------------------

    def resolve(
        self,
        label: str,   # etykieta wybrana przez model, np. "OPT-2"
    ) -> int | str | None:
        """Opis metody:
        Zamień etykietę wybraną przez model na `id` opcji klienta. Dopasowanie dokładne — tolerancję
        zapisu (`opt-2`, ` OPT-2 `) rozstrzyga parser odpowiedzi, nie ta jednostka.

        Przyklad argumentow:
            label="OPT-2"

        Przyklad wyniku:
            "db-7781"   # OPT-1 -> 21 (int zostaje int); OPT-00 -> None

        Raises:
            UnknownLabelError: etykieta spoza nadanych (np. "OPT-7" przy dwóch opcjach, "OPT-01").
        """
        # Spoza listy -> głośno. NIE None: None znaczy „brak dopasowania", czyli inny wynik niż błąd modelu.
        if label not in self._by_label:
            raise UnknownLabelError(f"Etykieta {label!r} spoza nadanych ({', '.join(self.labels)}).")

        # `OPT-00` nie ma opcji -> None; etykieta opcji -> jej `id` w typie z wejścia.
        option = self._by_label[label]
        return None if option is None else option.id
