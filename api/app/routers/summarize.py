"""Endpoint POST /summarize — czysta summaryzacja tekstu (krok 2.4.2).

Spina warstwe summaryzacji w granice HTTP: bierze tekst, wola `SummarizationService`
(domena nad `LLMClient`) i mapuje wyjatki domenowe na kody HTTP. SAM nie zna promptow ani
truncacji — to robi domena.

DI serwisu przez `Depends`, ale klient LLM bierzemy z FABRYKI `get_llm_client()` (w
odroznieniu od Tiki w /extract, ktora jest jednym silnikiem wstrzykiwanym inline) — bo
dostawca LLM jest WYMIENIALNY (fake/openai/...->Bielik), a fabryka wybiera go po konfiguracji.

Mapowanie wyjatkow -> HTTP:
  - EmptyInputError       -> 422 (puste wejscie),
  - LLMConfigError        -> 500 (zla konfiguracja dostawcy — NASZ blad, nie klienta),
  - LLMTimeoutError       -> 504 (dostawca nie odpowiedzial w czasie),
  - LLMRateLimitError     -> 503 (dostawca dlawi/limit/kwota),
  - LLMAuthError          -> 500 (zly/brakujacy klucz — NASZ config),
  - LLMResponseError / LLMError -> 502 (inny blad po stronie dostawcy/odpowiedzi).
"""

from fastapi import APIRouter, Depends, HTTPException, status

from app.config import Settings, get_settings
from app.llm import LLMAuthError, LLMConfigError, LLMError, LLMRateLimitError, LLMResponseError, LLMTimeoutError, get_llm_client
from app.models import SummarizeRequest, SummarizeResponse
from app.summarization import EmptyInputError, SummarizationService

router = APIRouter(tags=["summarization"])


def _get_summarization_service(
    settings: Settings = Depends(get_settings),
) -> SummarizationService:
    """Opis metody:
    Zbuduj `SummarizationService` nad klientem LLM z fabryki (DI). Klient jest cache'owany
    (`get_llm_client` ma `lru_cache`), wiec tworzenie serwisu per-request jest tanie.
    Bledna konfiguracja dostawcy (np. brak klucza dla 'openai') -> 500 z czytelnym komunikatem.

    Przyklad argumentow:
        settings=Settings(llm_provider="fake", llm_max_input_chars=90000)

    Przyklad wyniku:
        SummarizationService(FakeLLMClient(), max_input_chars=90000)

    Raises:
        HTTPException(500): niespojna konfiguracja dostawcy LLM (`LLMConfigError`).
    """
    # Fabryka wybiera klienta po LLM_PROVIDER; zly config -> 500 (to nasz blad wdrozenia, nie klienta).
    try:
        client = get_llm_client()
    except LLMConfigError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Bledna konfiguracja dostawcy LLM: {exc}",
        ) from exc
    return SummarizationService(client, max_input_chars=settings.llm_max_input_chars)


@router.post("/summarize", response_model=SummarizeResponse, summary="Streszczenie tekstu")
async def summarize(
    request: SummarizeRequest,
    service: SummarizationService = Depends(_get_summarization_service),
) -> SummarizeResponse:
    """Streszcz przeslany tekst i zwroc streszczenie wraz z metadanymi.

    Wola `SummarizationService` -> mapuje wyjatki domenowe/LLM na kody HTTP. Decyzje o
    promptach i truncacji sa po stronie domeny.

    Przyklad wejscia:
        {"text": "Pismo z Urzedu Skarbowego w sprawie zaleglosci podatkowej..."}

    Przyklad odpowiedzi:
        {
            "summary": "Urzad Skarbowy wzywa do zaplaty...\\n\\n• Typ: wezwanie...",
            "metadata": {
                "model": "gpt-4o-mini",
                "input_chars": 812,
                "truncated": false,
                "usage": {"prompt_tokens": 250, "completion_tokens": 90, "total_tokens": 340}
            }
        }

    Kody bledow:
        422 — puste wejscie (sam whitespace).
        500 — bledna konfiguracja dostawcy LLM / zly klucz (nasz config).
        502 — inny blad po stronie dostawcy / nieoczekiwana odpowiedz.
        503 — dostawca dlawi (limit zapytan/kwota).
        504 — dostawca nie odpowiedzial w czasie (timeout).
    """
    # Domena: streszczenie przez serwis; wyjatki domenowe/LLM -> kody HTTP.
    try:
        result = await service.summarize(text=request.text)
    # Puste wejscie — wina lezy po stronie klienta (nic do streszczenia).
    except EmptyInputError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Brak tekstu do streszczenia: {exc}",
        ) from exc
    # Timeout dostawcy — brama w gore nie odpowiedziala w czasie.
    except LLMTimeoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"Dostawca LLM nie odpowiedzial w czasie: {exc}",
        ) from exc
    # Dlawienie/limit — chwilowo niedostepne, klient moze ponowic.
    except LLMRateLimitError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Dostawca LLM dlawi zapytania (limit/kwota): {exc}",
        ) from exc
    # Zly/brakujacy klucz — to NASZ blad konfiguracji, nie wejscia klienta.
    except LLMAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Blad uwierzytelnienia u dostawcy LLM (config serwera): {exc}",
        ) from exc
    # Inny blad odpowiedzi dostawcy oraz catch-all bazowy LLMError -> brama w gore zawiodla.
    except (LLMResponseError, LLMError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Blad po stronie dostawcy LLM: {exc}",
        ) from exc

    return SummarizeResponse.from_result(result)
