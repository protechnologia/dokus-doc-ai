"""Endpoint POST /extract — czysta ekstrakcja tekstu z pliku (krok 2.3.3, happy path).

Spina warstwy ekstrakcji w granice HTTP: dekoduje base64, pilnuje rozmiaru, wola
`ExtractionService` (domena nad transportem `TikaClient`) i mapuje wyjatki domenowe na
kody HTTP. SAM nie podejmuje decyzji o tresci — to robi domena.

DI serwisu jest INLINE przez `Depends` (`_get_extraction_service`), bez osobnej fabryki:
Tika to jeden silnik (w odroznieniu od wymienialnego LLM, ktory ma `build_llm_client`).
Klient i serwis sa lekkie (httpx tworzony per-wywolanie), wiec budujemy je per-request.

Mapowanie wyjatkow -> HTTP (patrz CLAUDE.md "Plan kroku 2.3 -> 2.3.3"):
  - zly base64            -> 422 (klient przyslal nie-base64),
  - pusty plik            -> 422 (po dekodowaniu zero bajtow),
  - plik za duzy          -> 413 (powyzej MAX_UPLOAD_BYTES),
  - TikaUnavailableError  -> 502 (tika-server nieosiagalny — blad bramy w gore),
  - TikaExtractionError   -> 422 (Tika odrzucila plik: nieobslugiwany/uszkodzony),
  - EmptyExtractionError  -> 422 (po normalizacji brak tresci).
"""

import binascii
import base64

from fastapi import APIRouter, Depends, HTTPException, status

from app.config import Settings, get_settings
from app.extraction import (
    EmptyExtractionError,
    ExtractionService,
    TikaClient,
    TikaExtractionError,
    TikaUnavailableError,
)
from app.models import ExtractRequest, ExtractResponse

router = APIRouter(tags=["extraction"])


def _get_extraction_service(
    settings: Settings = Depends(get_settings),
) -> ExtractionService:
    """Opis metody:
    Zbuduj `ExtractionService` nad `TikaClient` z konfiguracji (DI inline, bez fabryki).
    Klient lekki (httpx per-wywolanie), wiec tworzenie per-request jest tanie.

    Przyklad argumentow:
        settings=Settings(tika_url="http://tika:9998", tika_timeout_seconds=120.0)

    Przyklad wyniku:
        ExtractionService(TikaClient(base_url="http://tika:9998", timeout=120.0))
    """
    client = TikaClient(base_url=settings.tika_url, timeout=settings.tika_timeout_seconds)
    return ExtractionService(client)


def _decode_base64(
    content_base64: str,   # zawartosc pliku zakodowana base64 (z ExtractRequest)
) -> bytes:
    """Opis metody:
    Zdekoduj base64 na bajty; nie-base64 -> HTTP 422. `validate=True`, by smieci w wejsciu
    realnie wywalaly blad (domyslnie base64 cicho ignoruje nieprawidlowe znaki).

    Przyklad argumentow:
        content_base64="SGVsbG8="

    Przyklad wyniku:
        b"Hello"

    Raises:
        HTTPException(422): wejscie nie jest poprawnym base64.
    """
    try:
        return base64.b64decode(content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        # Klient przyslal cos, co nie jest base64 — to blad wejscia (422), nie nasz.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"content_base64 nie jest poprawnym base64: {exc}",
        ) from exc


def _validate_size(
    data: bytes,                # zdekodowane bajty pliku
    max_upload_bytes: int,      # gorny limit z konfiguracji (Settings.max_upload_bytes)
) -> None:
    """Opis metody:
    Sprawdz rozmiar zdekodowanego pliku: pusty -> 422, za duzy -> 413. Straznik zasobow
    PRZED kontaktem z Tika (nie obciazamy OCR plikami spoza limitu).

    Przyklad argumentow:
        data=b"%PDF-1.7 ...", max_upload_bytes=20971520

    Przyklad wyniku:
        None (gdy rozmiar w zakresie 1..max)

    Raises:
        HTTPException(422): plik pusty (zero bajtow).
        HTTPException(413): plik wiekszy niz max_upload_bytes.
    """
    # Pusty plik = brak czegokolwiek do ekstrakcji -> blad wejscia.
    if not data:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Pusty plik (content_base64 zdekodowal sie do zera bajtow).",
        )
    # Powyzej limitu -> 413 (kanoniczny kod dla zbyt duzego ladunku).
    if len(data) > max_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"Plik za duzy: {len(data)} B > limit {max_upload_bytes} B.",
        )


@router.post("/extract", response_model=ExtractResponse, summary="Ekstrakcja tekstu z pliku")
async def extract(
    request: ExtractRequest,
    service: ExtractionService = Depends(_get_extraction_service),
    settings: Settings = Depends(get_settings),
) -> ExtractResponse:
    """Wyekstrahuj tekst z przeslanego pliku (base64) i zwroc go wraz z metadanymi.

    Dekoduje base64 -> waliduje rozmiar -> wola `ExtractionService` -> mapuje wyjatki
    domenowe na kody HTTP. Decyzje o tresci (normalizacja, metadane) sa po stronie domeny.

    Przyklad wejscia:
        {"content_base64": "JVBERi0xLjcK...", "content_type": "application/pdf"}

    Przyklad odpowiedzi:
        {
            "text": "Tresc dokumentu...",
            "metadata": {
                "content_type": "application/pdf",
                "language": "pl",
                "char_count": 42,
                "word_count": 6
            }
        }

    Kody bledow:
        413 — plik wiekszy niz MAX_UPLOAD_BYTES.
        422 — zly base64 / pusty plik / Tika odrzucila plik / brak tresci po ekstrakcji.
        502 — tika-server nieosiagalny.
    """
    # 1. Wejscie: base64 -> bajty (zly base64 -> 422).
    data = _decode_base64(request.content_base64)

    # 2. Straznik zasobow: pusty -> 422, za duzy -> 413 (przed kontaktem z Tika).
    _validate_size(data, settings.max_upload_bytes)

    # 3. Domena: ekstrakcja przez serwis; wyjatki domenowe -> kody HTTP.
    try:
        result = await service.extract(
            data=data,
            content_type=request.content_type,
            filename=request.filename,
        )
    except TikaUnavailableError as exc:
        # Tika nie odpowiada — to problem bramy w gore, nie wejscia klienta.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Usluga ekstrakcji (Tika) niedostepna: {exc}",
        ) from exc
    except (TikaExtractionError, EmptyExtractionError) as exc:
        # Tika odrzucila plik albo po ekstrakcji brak tresci — wina lezy po stronie pliku.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Nie udalo sie wyekstrahowac tresci: {exc}",
        ) from exc

    return ExtractResponse.from_result(result)
