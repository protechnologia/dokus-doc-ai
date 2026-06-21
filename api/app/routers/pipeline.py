"""Endpoint POST /extract-and-summarize — pelny pipeline: plik -> tekst -> streszczenie (krok 2.5.2).

Domyka KROK 2: spina ekstrakcje (2.3) i summaryzacje (2.4) w JEDNO wywolanie — docelowy
endpoint pod integracje z DOKUS (krok 3). SAM nie ma logiki domenowej; komponuje
`PipelineService` nad oboma serwisami i mapuje wyjatki OBU warstw na kody HTTP (unia
mapowan z /extract i /summarize).

DI laczy oba wzorce z endpointow nizej:
  - `ExtractionService` — INLINE (`TikaClient`; Tika to jeden silnik),
  - `SummarizationService` — z FABRYKI `get_llm_client()` (LLM wymienialny: fake/openai/...->Bielik).

Mapowanie wyjatkow -> HTTP (UNIA /extract + /summarize):
  - zly base64 / pusty plik                         -> 422 (wejscie klienta),
  - plik za duzy                                     -> 413 (powyzej MAX_UPLOAD_BYTES),
  - TikaExtractionError / EmptyExtractionError       -> 422 (plik nieobslugiwany/uszkodzony/bez tresci),
  - EmptyInputError                                  -> 422 (po ekstrakcji brak tekstu do streszczenia),
  - TikaUnavailableError                             -> 502 (tika-server nieosiagalny),
  - LLMTimeoutError                                  -> 504 (dostawca LLM nie odpowiedzial w czasie),
  - LLMRateLimitError                                -> 503 (dostawca LLM dlawi/limit/kwota),
  - LLMConfigError / LLMAuthError                    -> 500 (NASZ config serwera),
  - LLMResponseError / LLMError                      -> 502 (inny blad po stronie dostawcy/odpowiedzi).
"""

import binascii
import base64

from fastapi import APIRouter, Depends, HTTPException, status

from app.config import Settings, get_settings
from app.extraction import EmptyExtractionError, ExtractionService, TikaClient, TikaExtractionError, TikaUnavailableError
from app.llm import LLMAuthError, LLMConfigError, LLMError, LLMRateLimitError, LLMResponseError, LLMTimeoutError, get_llm_client
from app.models import SummarizeDocumentRequest, SummarizeDocumentResponse
from app.pipeline import PipelineService
from app.summarization import EmptyInputError, SummarizationService

router = APIRouter(tags=["pipeline"])


def _get_pipeline_service(
    settings: Settings = Depends(get_settings),
) -> PipelineService:
    """Opis metody:
    Zbuduj `PipelineService` nad oboma serwisami: `ExtractionService` (TikaClient inline) +
    `SummarizationService` (klient LLM z fabryki). Laczy wzorce DI z /extract i /summarize.
    Bledna konfiguracja dostawcy LLM (np. brak klucza dla 'openai') -> 500 z czytelnym komunikatem.

    Przyklad argumentow:
        settings=Settings(tika_url="http://tika:9998", llm_provider="fake", max_ocr_pages=30)

    Przyklad wyniku:
        PipelineService(ExtractionService(TikaClient(...)), SummarizationService(FakeLLMClient()))

    Raises:
        HTTPException(500): niespojna konfiguracja dostawcy LLM (`LLMConfigError`).
    """
    # Ekstrakcja: transport Tika inline (jeden silnik), limiter/detektor buduje sam serwis.
    tika = TikaClient(base_url=settings.tika_url, timeout=settings.tika_timeout_seconds)
    extraction = ExtractionService(tika, max_ocr_pages=settings.max_ocr_pages)

    # Summaryzacja: klient LLM z fabryki (wymienialny); zly config -> 500 (nasz blad wdrozenia).
    try:
        client = get_llm_client()
    except LLMConfigError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Bledna konfiguracja dostawcy LLM: {exc}",
        ) from exc
    summarization = SummarizationService(client, max_input_chars=settings.llm_max_input_chars)

    return PipelineService(extraction, summarization)


def _decode_base64(
    content_base64: str,   # zawartosc pliku zakodowana base64 (z requestu)
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
    data: bytes,              # zdekodowane bajty pliku
    max_upload_bytes: int,    # gorny limit z konfiguracji (Settings.max_upload_bytes)
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


@router.post(
    "/extract-and-summarize",
    response_model=SummarizeDocumentResponse,
    summary="Ekstrakcja + streszczenie dokumentu (pelny pipeline)",
)
async def extract_and_summarize(
    request: SummarizeDocumentRequest,
    service: PipelineService = Depends(_get_pipeline_service),
    settings: Settings = Depends(get_settings),
) -> SummarizeDocumentResponse:
    """Wyekstrahuj tekst z przeslanego pliku (base64) i od razu go streszcz.

    Dekoduje base64 -> waliduje rozmiar -> wola `PipelineService.process` (extract ->
    summarize) -> mapuje wyjatki OBU warstw na kody HTTP (unia /extract + /summarize).

    Przyklad wejscia:
        {"content_base64": "JVBERi0xLjcK...", "content_type": "application/pdf"}

    Przyklad odpowiedzi:
        {
            "summary": "Urzad Skarbowy wzywa do zaplaty...\\n\\n• Typ: wezwanie...",
            "text": "Pelna tresc dokumentu...",
            "extraction": {"content_type": "application/pdf", "language": "pl",
                           "char_count": 4200, "word_count": 600, "ocr_used": true,
                           "pages_total": 3, "pages_processed": 3, "ocr_truncated": false},
            "summarization": {"model": "gpt-4o-mini", "input_chars": 4200, "truncated": false,
                              "usage": {"prompt_tokens": 1200, "completion_tokens": 90, "total_tokens": 1290}}
        }

    Kody bledow:
        413 — plik wiekszy niz MAX_UPLOAD_BYTES.
        422 — zly base64 / pusty plik / Tika odrzucila plik / brak tresci po ekstrakcji / puste wejscie LLM.
        500 — bledna konfiguracja dostawcy LLM / zly klucz (nasz config).
        502 — tika-server nieosiagalny / inny blad po stronie dostawcy LLM.
        503 — dostawca LLM dlawi (limit zapytan/kwota).
        504 — dostawca LLM nie odpowiedzial w czasie (timeout).
    """
    # 1. Wejscie: base64 -> bajty (zly base64 -> 422).
    data = _decode_base64(request.content_base64)

    # 2. Straznik zasobow: pusty -> 422, za duzy -> 413 (przed kontaktem z Tika).
    _validate_size(data, settings.max_upload_bytes)

    # 3. Domena: pelny pipeline; wyjatki OBU warstw -> kody HTTP (unia mapowan).
    try:
        result = await service.process(
            data=data,
            content_type=request.content_type,
            filename=request.filename,
        )
    # --- Warstwa ekstrakcji -----------------------------------------------------
    except TikaUnavailableError as exc:
        # Tika nie odpowiada — problem bramy w gore, nie wejscia klienta.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Usluga ekstrakcji (Tika) niedostepna: {exc}",
        ) from exc
    except (TikaExtractionError, EmptyExtractionError) as exc:
        # Tika odrzucila plik albo po ekstrakcji brak tresci — wina po stronie pliku.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Nie udalo sie wyekstrahowac tresci: {exc}",
        ) from exc
    # --- Warstwa summaryzacji ---------------------------------------------------
    except EmptyInputError as exc:
        # Po ekstrakcji nie zostal tekst do streszczenia — wejscie do LLM puste.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Brak tekstu do streszczenia: {exc}",
        ) from exc
    except LLMTimeoutError as exc:
        # Timeout dostawcy — brama w gore nie odpowiedziala w czasie.
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"Dostawca LLM nie odpowiedzial w czasie: {exc}",
        ) from exc
    except LLMRateLimitError as exc:
        # Dlawienie/limit — chwilowo niedostepne, klient moze ponowic.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Dostawca LLM dlawi zapytania (limit/kwota): {exc}",
        ) from exc
    except LLMAuthError as exc:
        # Zly/brakujacy klucz — to NASZ blad konfiguracji, nie wejscia klienta.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Blad uwierzytelnienia u dostawcy LLM (config serwera): {exc}",
        ) from exc
    except (LLMResponseError, LLMError) as exc:
        # Inny blad odpowiedzi dostawcy oraz catch-all bazowy LLMError -> brama w gore zawiodla.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Blad po stronie dostawcy LLM: {exc}",
        ) from exc

    return SummarizeDocumentResponse.from_result(result)
