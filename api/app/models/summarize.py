"""Modele HTTP: POST /summarize (krok 2.4.2) — tekst -> streszczenie + metadane summaryzacji.

`SummarizeMetadata` wykorzystuje tez `/extract-and-summarize` (te same metadane summaryzacji).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.llm import LLMUsage
from app.models.truncation import TruncationParams
from app.summarization import SummarizationMetadata, SummarizationResult, TextParts


class SummarizeRequest(TruncationParams):
    """Wejscie `POST /summarize` — czysta summaryzacja: sam tekst (bez pliku/ekstrakcji) + opcjonalne proporcje trunkacji."""

    text: str = Field(description="Tekst dokumentu do streszczenia.")


class SummarizePart(BaseModel):
    """Jedna czesc wejscia modelu po trunkacji — odbicie `TextPart` z domeny na granicy HTTP."""

    percent: int       = Field(description="Zastosowana proporcja budzetu tej czesci (0–100).")
    start: int | None  = Field(default=None, description="Offset poczatku zachowanego fragmentu (wlacznie), w znakach Unicode wzgledem tekstu klienta; null gdy proporcja 0.")
    end: int | None    = Field(default=None, description="Offset konca zachowanego fragmentu (wylacznie), w znakach Unicode wzgledem tekstu klienta; null gdy proporcja 0.")


class SummarizeParts(BaseModel):
    """Trzy czesci wejscia modelu (poczatek / srodek / koniec) — odbicie `TextParts` z domeny."""

    head: SummarizePart    = Field(description="Poczatek dokumentu.")
    middle: SummarizePart  = Field(description="Ciagly fragment z geometrycznego srodka dokumentu.")
    tail: SummarizePart    = Field(description="Koniec dokumentu.")

    @classmethod
    def from_parts(
        cls,
        parts: TextParts,   # domenowe zakresy czesci z `SummarizationMetadata.parts`
    ) -> SummarizeParts:
        """Opis metody:
        Zmapuj domenowe `TextParts` na model HTTP. Cienkie, jawne przepisanie pol.

        Przyklad argumentow:
            parts=TextParts(head=TextPart(percent=45, start=0, end=40210), middle=..., tail=...)

        Przyklad wyniku:
            SummarizeParts(head=SummarizePart(percent=45, start=0, end=40210), middle=..., tail=...)
        """
        return cls(
            head   = SummarizePart(percent=parts.head.percent, start=parts.head.start, end=parts.head.end),
            middle = SummarizePart(percent=parts.middle.percent, start=parts.middle.start, end=parts.middle.end),
            tail   = SummarizePart(percent=parts.tail.percent, start=parts.tail.start, end=parts.tail.end),
        )


class SummarizeMetadata(BaseModel):
    """Metadane w odpowiedzi /summarize — odbicie `SummarizationMetadata` z domeny na granicy HTTP.

    `sent_chars`/`parts` (trunkacja poczatek/srodek/koniec) DOSZLY do kontraktu wstecznie zgodnie:
    stare pola bez zmian, nowe tylko dochodza. `parts` = null dokladnie wtedy, gdy `truncated` = false.
    """

    model: str                    = Field(description="Identyfikator modelu, ktory odpowiedzial, np. 'gpt-4o-mini'/'fake-echo'.")
    input_chars: int              = Field(description="Dlugosc wejscia (po strip), w znakach — PRZED ewentualna truncacja.")
    truncated: bool               = Field(default=False, description="Czy wejscie ucieto do limitu (streszczenie z czesci dokumentu).")
    usage: LLMUsage               = Field(default_factory=LLMUsage, description="Zuzycie tokenow (prompt/completion/total) — diagnostyka kosztu.")
    sent_chars: int               = Field(description="Dlugosc tekstu wyslanego do modelu (po truncacji, ze znacznikami pominiecia), w znakach; zawsze <= LLM_MAX_INPUT_CHARS.")
    parts: SummarizeParts | None  = Field(default=None, description="Proporcje i zakresy zachowanych fragmentow (poczatek/srodek/koniec); null, gdy tekst zmiescil sie w budzecie.")

    @classmethod
    def from_metadata(
        cls,
        meta: SummarizationMetadata,   # domenowe metadane z `SummarizationResult.metadata`
    ) -> SummarizeMetadata:
        """Opis metody:
        Zmapuj domenowe `SummarizationMetadata` na model HTTP. Wspolne dla /summarize i
        /extract-and-summarize (oba zwracaja te same metadane summaryzacji).

        Przyklad argumentow:
            meta=SummarizationMetadata(model="gpt-4o-mini", input_chars=812, truncated=False,
                                       usage=LLMUsage(...), sent_chars=812, parts=None)

        Przyklad wyniku:
            SummarizeMetadata(model="gpt-4o-mini", input_chars=812, truncated=False,
                              usage=LLMUsage(...), sent_chars=812, parts=None)
        """
        return cls(
            model       = meta.model,
            input_chars = meta.input_chars,
            truncated   = meta.truncated,
            usage       = meta.usage,
            sent_chars  = meta.sent_chars,
            parts       = SummarizeParts.from_parts(meta.parts) if meta.parts is not None else None,
        )


class SummarizeResponse(BaseModel):
    """Wyjscie `POST /summarize` — streszczenie (wypunktowanie kluczowych pól) + metadane."""

    summary: str               = Field(description="Streszczenie dokumentu (jeden tekst).")
    metadata: SummarizeMetadata = Field(description="Metadane: model, dlugosc wejscia, truncacja, zuzycie.")

    @classmethod
    def from_result(
        cls,
        result: SummarizationResult,   # domenowy wynik z SummarizationService.summarize
    ) -> SummarizeResponse:
        """Opis metody:
        Zmapuj domenowy `SummarizationResult` na model odpowiedzi HTTP. Cienkie, jawne
        przepisanie pol — granica miedzy domena a kontraktem API.

        Przyklad argumentow:
            result=SummarizationResult(summary="...", metadata=SummarizationMetadata(
                model="gpt-4o-mini", input_chars=812, truncated=False, usage=LLMUsage(...),
                sent_chars=812, parts=None))

        Przyklad wyniku:
            SummarizeResponse(summary="...", metadata=SummarizeMetadata(
                model="gpt-4o-mini", input_chars=812, truncated=False, usage=LLMUsage(...),
                sent_chars=812, parts=None))
        """
        return cls(
            summary  = result.summary,
            metadata = SummarizeMetadata.from_metadata(result.metadata),
        )
