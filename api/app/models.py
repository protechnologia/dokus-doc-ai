"""Modele wejscia/wyjscia (Pydantic).

Granica HTTP uslugi: /health (krok 2.1) oraz ekstrakcja (krok 2.3). Modele summaryzacji
dochodza w 2.4. Modele API sa SWIADOMIE odrebne od modeli domenowych (`ExtractionResult`/
`ExtractionMetadata` w `app.extraction`): domena moze ewoluowac (np. dojdzie info o
OCR-fallbacku w 2.3.5) bez zmiany kontraktu HTTP. Mapowanie domena -> API robi
`ExtractResponse.from_result` (cienkie, jawne).
"""

from __future__ import annotations

from typing   import Literal
from pydantic import BaseModel, Field

from app.extraction import ExtractionResult

# Status pojedynczej zaleznosci zewnetrznej (np. Tiki).
DependencyStatus = Literal["ok", "unreachable"]


class HealthResponse(BaseModel):
    """Odpowiedz /health — zdrowie samej aplikacji plus stan zaleznosci."""

    status: Literal["ok", "degraded"] = Field(
        description=(
            "ok = aplikacja i zaleznosci zdrowe; "
            "degraded = aplikacja zyje, ale jakas zaleznosc jest niedostepna."
        )
    )
    service: str = Field(description="Nazwa uslugi.")
    version: str = Field(description="Wersja aplikacji.")
    dependencies: dict[str, DependencyStatus] = Field(
        default_factory=dict,
        description="Stan zaleznosci, np. {'tika': 'ok'}.",
    )


# --- Ekstrakcja: POST /extract (krok 2.3.3) --------------------------------------


class ExtractRequest(BaseModel):
    """Wejscie `POST /extract` — plik w base64 (JSON, nie multipart; patrz CLAUDE.md "Kontrakty").

    `filename`/`content_type` to OPCJONALNE podpowiedzi typu dla Tiki; brak -> Tika sama
    wykrywa typ (jej mocna strona).
    """

    content_base64: str       = Field(description="Zawartosc pliku zakodowana base64.")
    filename: str | None      = Field(default=None, description="Opcjonalna nazwa pliku (podpowiedz typu dla Tiki), np. 'pismo.pdf'.")
    content_type: str | None  = Field(default=None, description="Opcjonalny MIME (podpowiedz dla Tiki), np. 'application/pdf'; brak = autodetekcja.")


class ExtractMetadata(BaseModel):
    """Metadane w odpowiedzi /extract — odbicie `ExtractionMetadata` z domeny na granicy HTTP."""

    content_type: str | None = Field(default=None, description="MIME wykryty przez Tike, np. 'application/pdf'.")
    language: str | None     = Field(default=None, description="Wykryty jezyk wg metadanych Tiki, np. 'pl'; None gdy nieznany.")
    char_count: int          = Field(description="Liczba znakow tekstu po normalizacji.")
    word_count: int          = Field(description="Liczba slow tekstu po normalizacji.")


class ExtractResponse(BaseModel):
    """Wyjscie `POST /extract` — wyekstrahowany tekst + metadane (nie samo streszczenie).

    Metadane przydaja sie diagnostycznie (jaki MIME wykryto, czy poszlo OCR) i pod pelny
    pipeline w 2.5.
    """

    text: str                 = Field(description="Wyekstrahowany tekst po normalizacji whitespace.")
    metadata: ExtractMetadata = Field(description="Metadane: MIME, jezyk, dlugosc.")

    @classmethod
    def from_result(
        cls,
        result: ExtractionResult,   # domenowy wynik z ExtractionService.extract
    ) -> ExtractResponse:
        """Opis metody:
        Zmapuj domenowy `ExtractionResult` na model odpowiedzi HTTP. Cienkie, jawne
        przepisanie pol — granica miedzy domena a kontraktem API (domena moze sie zmienic
        bez ruszania schematu HTTP).

        Przyklad argumentow:
            result=ExtractionResult(text="Tresc...", metadata=ExtractionMetadata(
                content_type="application/pdf", language="pl", char_count=42, word_count=6))

        Przyklad wyniku:
            ExtractResponse(text="Tresc...", metadata=ExtractMetadata(
                content_type="application/pdf", language="pl", char_count=42, word_count=6))
        """
        meta = result.metadata
        return cls(
            text=result.text,
            metadata=ExtractMetadata(
                content_type=meta.content_type,
                language=meta.language,
                char_count=meta.char_count,
                word_count=meta.word_count,
            ),
        )
