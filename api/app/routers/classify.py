"""Endpoint POST /classify — wybór jednej opcji z listy albo żadnej (krok 9 /classify).

Spina domenę klasyfikacji w granice HTTP: zamienia opcje z modelu API na domenowe, woła
`ClassificationService` i mapuje wyjątki na kody HTTP. SAM nie zna promptów, etykiet ani parsera
— to robi domena. Kontrakt zamrożony: README „POST /classify".

DI jak w /summarize: klient LLM z FABRYKI `get_llm_client()` (dostawca wymienialny), limity
z `Settings` — `LLM_MAX_INPUT_CHARS` jako budżet całego promptu, `LLM_MAX_OUTPUT_TOKENS_CLASSIFY`
jako limit odpowiedzi.

Każda odpowiedź modelu to `200` + `outcome` — także `invalid_response`, więc router nie ma dla niej
osobnej ścieżki (kontrakt, krok 1 (f)): kod HTTP wyznacza politykę ponowień klienta bez czytania
ciała, a 5xx znaczy „odpowiedzi modelu nie było".

Mapowanie wyjątków -> HTTP (ciało `{"detail": "..."}`, bez promptów):
  - PromptTooLongError          -> 413 (prompt dłuższy niż LLM_MAX_INPUT_CHARS; model niewołany),
  - LLMConfigError              -> 500 (zła konfiguracja dostawcy — NASZ błąd, nie klienta),
  - LLMTimeoutError             -> 504 (dostawca nie odpowiedział w czasie),
  - LLMRateLimitError           -> 503 (dostawca dławi / limit / kwota),
  - LLMAuthError                -> 500 (zły / brakujący klucz — NASZ config),
  - LLMResponseError / LLMError -> 502 (inny błąd po stronie dostawcy).
"""

from fastapi import APIRouter, Depends, HTTPException, status

from app.classification import ClassificationOption, ClassificationService, PromptTooLongError
from app.config import Settings, get_settings
from app.llm import LLMAuthError, LLMConfigError, LLMError, LLMRateLimitError, LLMResponseError, LLMTimeoutError, get_llm_client
from app.models import ClassifyOption, ClassifyRequest, ClassifyResponse

router = APIRouter(tags=["classification"])


def _get_classification_service(
    settings: Settings = Depends(get_settings),
) -> ClassificationService:
    """Opis metody:
    Zbuduj `ClassificationService` nad klientem LLM z fabryki (DI). Klient jest cache'owany
    (`get_llm_client` ma `lru_cache`), więc serwis per-request jest tani. Błędna konfiguracja
    dostawcy (np. brak klucza dla 'openai') -> 500 z czytelnym komunikatem.

    Przyklad argumentow:
        settings=Settings(llm_provider="fake", llm_max_input_chars=90000, llm_max_output_tokens_classify=400)

    Przyklad wyniku:
        ClassificationService(FakeLLMClient(), max_prompt_chars=90000, max_output_tokens=400)

    Raises:
        HTTPException(500): niespójna konfiguracja dostawcy LLM (`LLMConfigError`).
    """
    # Fabryka wybiera klienta po LLM_PROVIDER; zły config -> 500 (to nasz błąd wdrożenia, nie klienta).
    try:
        client = get_llm_client()
    except LLMConfigError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Błędna konfiguracja dostawcy LLM: {exc}",
        ) from exc
    return ClassificationService(client, max_prompt_chars=settings.llm_max_input_chars, max_output_tokens=settings.llm_max_output_tokens_classify)


def _to_domain_options(
    options: list[ClassifyOption],   # opcje z żądania HTTP, np. [ClassifyOption(id=21, name="Skargi", ...)]
) -> list[ClassificationOption]:
    """Opis metody:
    Zamień opcje z modelu API na domenowe — pole po polu, w kolejności żądania (od niej zależą
    etykiety `OPT-n`). `id` przechodzi w typie z żądania (int zostaje int, string stringiem).
    Czysta funkcja.

    Przyklad argumentow:
        options=[ClassifyOption(id=21, name="Skargi", description="Skargi konsumentów.", examples=None)]

    Przyklad wyniku:
        [ClassificationOption(id=21, name="Skargi", description="Skargi konsumentów.", examples=None)]
    """
    return [ClassificationOption(id=o.id, name=o.name, description=o.description, examples=o.examples) for o in options]


@router.post("/classify", response_model=ClassifyResponse, summary="Wybór jednej opcji z listy albo żadnej")
async def classify(
    request: ClassifyRequest,
    service: ClassificationService = Depends(_get_classification_service),
) -> ClassifyResponse:
    """Wybierz dla dokumentu (opisanego streszczeniami) jedną opcję z listy albo żadną.

    Jedno żądanie = jedno wywołanie modelu. Model widzi opcje pod etykietami nadanymi przez
    usługę (nie `id`), a lista zawsze kończy się pozycją „brak dopasowania". Każda odpowiedź
    modelu to 200 z `outcome`; kody 5xx znaczą, że odpowiedzi modelu nie było.

    Przyklad wejscia:
        {"summaries": ["• Typ pisma: skarga\\n• Czego dotyczy: zawyżony rachunek operatora"],
         "options": [{"id": 21, "name": "Skargi i interwencje konsumenckie",
                      "description": "Skargi konsumentów na dostawców usług.", "examples": null},
                     {"id": 22, "name": "Rynek pocztowy",
                      "description": "Sprawy operatorów pocztowych.", "examples": null}]}

    Przyklad odpowiedzi:
        {
            "outcome": "matched",
            "option_id": 21,
            "rationale": "Skarga konsumenta na operatora odpowiada opisowi opcji.",
            "error": null,
            "system_prompt": "Wybierz dla dokumentu dokładnie jedną pozycję z listy. ...",
            "user_prompt": "Streszczenia plików dokumentu (kolejność bez znaczenia): ...",
            "raw_response": "{\\"rationale\\": \\"...\\", \\"label\\": \\"OPT-1\\"}",
            "metadata": {"model": "gpt-4o-mini",
                         "usage": {"prompt_tokens": 1180, "completion_tokens": 58, "total_tokens": 1238}}
        }

    Kody bledow:
        413 — złożony prompt (prompt systemowy + streszczenia + opcje) dłuższy niż LLM_MAX_INPUT_CHARS.
        422 — brak wymaganego pola, pusta lista `summaries` / `options`, zły typ pola.
        500 — błędna konfiguracja dostawcy LLM / zły klucz (nasz config).
        502 — inny błąd po stronie dostawcy.
        503 — dostawca dławi (limit zapytań / kwota).
        504 — dostawca nie odpowiedział w czasie (timeout).
    """
    # Domena: wybór przez serwis; niepoprawna odpowiedź modelu to wynik (200), nie wyjątek.
    try:
        result = await service.classify(summaries=request.summaries, options=_to_domain_options(request.options))
    # Za długi prompt — odrzucamy, nie ucinamy (ucięcie opcji zmieniłoby zbiór wyboru); model niewołany.
    except PromptTooLongError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"Za długie wejście klasyfikacji (streszczenia + opcje) względem LLM_MAX_INPUT_CHARS: {exc}",
        ) from exc
    # Timeout dostawcy — brama w górę nie odpowiedziała w czasie.
    except LLMTimeoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"Dostawca LLM nie odpowiedział w czasie: {exc}",
        ) from exc
    # Dławienie / limit — chwilowo niedostępne, klient może ponowić.
    except LLMRateLimitError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Dostawca LLM dławi zapytania (limit/kwota): {exc}",
        ) from exc
    # Zły / brakujący klucz — to NASZ błąd konfiguracji, nie wejścia klienta.
    except LLMAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Błąd uwierzytelnienia u dostawcy LLM (config serwera): {exc}",
        ) from exc
    # Inny błąd odpowiedzi dostawcy oraz catch-all bazowy LLMError -> brama w górę zawiodła.
    except (LLMResponseError, LLMError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Błąd po stronie dostawcy LLM: {exc}",
        ) from exc

    return ClassifyResponse.from_result(result)
