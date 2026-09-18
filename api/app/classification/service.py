"""Domena klasyfikacji — wybór jednej opcji z listy albo żadnej (POST /classify, krok 7).

Warstwa DOMENOWA nad wstrzykniętym `LLMClient` (analogia do `SummarizationService`). Składa
czyste jednostki pakietu: etykiety (`OptionLabeler`), prompty (`ClassificationSystemPrompt` /
`ClassificationUserPrompt`), schemat odpowiedzi (`build_response_schema`), parser
(`parse_response`) i wynik (`ClassificationResult`, `model.py`). Jedno `classify` = jedno
wywołanie modelu = jeden wybór.

Decyzje (2026-09-18 — nie „poprawiać" bez powodu):
  - każda odpowiedź modelu to WYNIK, nie wyjątek: niesparsowalna odpowiedź albo etykieta spoza
    listy -> `invalid_response` z przyczyną (kontrakt: `200` + `outcome`, a 5xx = odpowiedzi
    modelu nie było). Z serwisu propagują tylko błędy, przy których odpowiedzi nie było:
    `LLMError` (wywołanie) i `PromptTooLongError` (model niewołany) — patrz `exception.py`,
  - za długi prompt -> `PromptTooLongError` PRZED wywołaniem, nigdy ucinanie (ucięcie opcji
    zmienia zbiór wyboru). Budżet liczony na CAŁYM prompcie (systemowy + użytkownika), bo do okna
    modelu musi wejść całość — inaczej niż w summaryzacji, gdzie limit dotyczy samego tekstu,
  - `max_tokens` stałą w kodzie, nie ENV — uzasadnienie przy `DEFAULT_MAX_OUTPUT_TOKENS`,
  - log bez treści pisma i opcji: wynik + etykieta; `invalid_response` -> WARNING z przyczyną
    (inaczej porażka ginie za `-> 200` w logu żądań).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from app.classification.exception import InvalidModelResponseError, PromptTooLongError, UnknownLabelError
from app.classification.model import ClassificationResult
from app.classification.prompt_system import ClassificationSystemPrompt
from app.classification.prompt_user import ClassificationUserPrompt
from app.classification.service_labels import ClassificationOption, OptionLabeler
from app.classification.service_parsing import parse_response
from app.classification.service_schema import build_response_schema
from app.llm import LLMClient, LLMResult

logger = logging.getLogger(__name__)

# --- Stałe -----------------------------------------------------------------------

# Limit długości odpowiedzi modelu (tokeny). Stała w kodzie, nie ENV: długość odpowiedzi wyznacza
# prompt („1–2 zdania") i schemat, czyli logika w repo — zmienia się razem z promptem, nie z
# wdrożeniem. Pomiar 2026-09-18 (`usage.completion_tokens`, dwa przebiegi, katalog 8 opcji):
# wybór opcji 44–62 na gpt-4o-mini i Bieliku 4.5B, ale przy `OPT-00` model wylicza w uzasadnieniu
# odrzucone opcje (~5 tokenów na nazwę), więc długość rośnie z katalogiem — gpt-4o-mini: 8 opcji
# -> 84, 14 -> 118 (wszystkie nazwy), 20 -> 62 („itp."); Bielik 4.5B do 112 (uzasadnienie
# krążące między opcjami). 400 ≈ 3,4 × maksimum: urwany JSON przy `temperature=0` urwie się tak
# samo przy każdym ponowieniu, a limit ogranicza jedynie model zapętlony w uzasadnieniu (tokeny
# powstają tylko, gdy model je pisze). Sygnał za ciasnego limitu: WARNING z `completion_tokens` 400/400.
DEFAULT_MAX_OUTPUT_TOKENS = 400

# --- Prompty (teksty w app/prompt/*.md; wczytane raz, przy imporcie) ---------------
# Brak pliku albo rozjazd placeholderów wywala import serwisu, czyli start aplikacji.
_SYSTEM_PROMPT = ClassificationSystemPrompt()
_USER_PROMPT   = ClassificationUserPrompt()


# --- Serwis domenowy -------------------------------------------------------------


class ClassificationService:
    """Domena klasyfikacji nad wstrzykniętym `LLMClient`.

    Do czego:
        Zamienia streszczenia dokumentu i listę opcji na `ClassificationResult`: nadaje etykiety,
        składa prompty i schemat, pilnuje budżetu promptu, woła model, a odpowiedź parsuje
        i rozwiązuje na `id` opcji. Nie wie, który dostawca odpowiada — dostaje go z fabryki
        (`get_llm_client()`), bo LLM jest wymienialny.

    Flow jednego `classify(...)`:
        1. `OptionLabeler` -> prompty (`render`) i schemat z jednej kolejności pozycji,
        2. `_check_budget` -> za długi prompt: `PromptTooLongError`, model niewołany,
        3. `LLMClient.complete` z `json_schema` (jedyne I/O; błędy LLM propagują),
        4. `_interpret` -> `parse_response` + `OptionLabeler.resolve` -> jeden z trzech wyników,
        5. log wyniku (bez treści pisma) -> `ClassificationResult`.
    """

    def __init__(
        self,
        client: LLMClient,                                    # transport/generacja LLM (z fabryki); w testach atrapa
        *,
        max_prompt_chars: int = 90_000,                       # budżet znaków całego promptu (z `Settings.llm_max_input_chars`)
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,   # limit długości odpowiedzi modelu, np. 400
    ) -> None:
        """Opis metody:
        Zbuduj serwis nad wstrzykniętym klientem LLM (sama konfiguracja, bez I/O).

        Przyklad argumentow:
            client=FakeLLMClient()
            max_prompt_chars=90000

        Przyklad wyniku:
            gotowy ClassificationService
        """
        self._client            = client
        self._max_prompt_chars  = max_prompt_chars
        self._max_output_tokens = max_output_tokens

    # --- Czyste helpery (bez I/O) — testowalne jednostkowo bez LLM -------------------

    def _check_budget(
        self,
        system: str,   # gotowy prompt systemowy, np. "Wybierz dla dokumentu..."
        user: str,     # gotowy prompt użytkownika, np. "Streszczenia plików dokumentu..."
    ) -> None:
        """Opis metody:
        Sprawdź, czy cały prompt mieści się w budżecie znaków. Równy budżetowi — mieści się.

        Przyklad argumentow:
            system="<ok. 1100 znaków>", user="<5000 znaków>"   (budżet 90000)

        Przyklad wyniku:
            None   # mieści się; inaczej wyjątek

        Raises:
            PromptTooLongError: `len(system) + len(user)` > budżet.
        """
        prompt_chars = len(system) + len(user)
        if prompt_chars > self._max_prompt_chars:
            raise PromptTooLongError(prompt_chars, self._max_prompt_chars)

    @staticmethod
    def _interpret(
        labeler: OptionLabeler,   # etykiety tego żądania (rozwiązanie wybranej na `id`)
        response: LLMResult,      # odpowiedź modelu, np. LLMResult(text='{"rationale": "...", "label": "OPT-2"}', ...)
        system: str,              # prompt systemowy wysłany do modelu (do audytu w wyniku)
        user: str,                # prompt użytkownika wysłany do modelu (do audytu w wyniku)
    ) -> ClassificationResult:
        """Opis metody:
        Zamień odpowiedź modelu na wynik: sparsuj, rozwiąż etykietę na `id`, wybierz wariant.
        Każda wada odpowiedzi daje `invalid_response` z przyczyną — nie wyjątek. Czysta funkcja.

        Przyklad argumentow:
            labeler=OptionLabeler([opcja id 21, opcja id "db-7781"])
            response=LLMResult(text='{"rationale": "Sprawy kadrowe.", "label": "OPT-2"}', model="gpt-4o-mini")
            system="Wybierz...", user="Streszczenia..."

        Przyklad wyniku:
            ClassificationResult(outcome="matched", option_id="db-7781", label="OPT-2",
                                 rationale="Sprawy kadrowe.", error=None, ...)
        """
        # --- Parsowanie i rozwiązanie etykiety ---------------------------------------------
        # Obie wady to odpowiedź, której nie da się użyć (kontrakt: 200 + invalid_response):
        # parser — pusta odpowiedź, zły / urwany JSON, brak pola, zły typ; resolve — etykieta
        # spoza nadanych (tylko zaplecze nieegzekwujące `enum`). Komunikat wyjątku = przyczyna.
        try:
            parsed    = parse_response(response.text)
            option_id = labeler.resolve(parsed.label)
        except (InvalidModelResponseError, UnknownLabelError) as exc:
            return ClassificationResult.invalid(error=str(exc), system_prompt=system, user_prompt=user, response=response)

        # --- Wariant wyniku: `resolve` daje None wyłącznie dla OPT-00 ------------------------
        if option_id is None:
            return ClassificationResult.no_match(rationale=parsed.rationale, system_prompt=system, user_prompt=user, response=response)
        return ClassificationResult.matched(
            option_id     = option_id,
            label         = parsed.label,
            rationale     = parsed.rationale,
            system_prompt = system,
            user_prompt   = user,
            response      = response,
        )

    # --- Wywołanie (I/O przez LLMClient) — orkiestracja ----------------------------

    async def classify(
        self,
        *,
        summaries: Sequence[str],                  # streszczenia plików dokumentu, np. ["• Typ pisma: skarga\n• ..."]
        options: Sequence[ClassificationOption],   # opcje w kolejności z żądania, np. [ClassificationOption(id=21, ...)]
    ) -> ClassificationResult:
        """Opis metody:
        Wybierz dla dokumentu jedną opcję z listy albo żadną: jedno wywołanie modelu.

        Przyklad argumentow:
            summaries=["• Typ pisma: skarga\\n• Czego dotyczy: zawyżony rachunek"]
            options=[ClassificationOption(id=21, name="Skargi", description="Skargi konsumentów."),
                     ClassificationOption(id=22, name="Rynek pocztowy", description="Sprawy operatorów pocztowych.")]

        Przyklad wyniku:
            ClassificationResult(outcome="matched", option_id=21, label="OPT-1",
                                 rationale="Skarga na operatora odpowiada opcji skarg.", error=None,
                                 model="gpt-4o-mini", usage=..., system_prompt="...",
                                 user_prompt="...", raw_response='{"rationale": "...", "label": "OPT-1"}')

        Raises:
            PromptTooLongError: prompt systemowy + użytkownika dłuższy niż `max_prompt_chars` (model niewołany).
            LLMError:           dowolny błąd warstwy LLM (auth/limit/timeout/odpowiedź) — propaguje.
        """
        # --- Prompty i schemat z jednej kolejności pozycji (OptionLabeler) -------------------
        labeler = OptionLabeler(options)
        system  = _SYSTEM_PROMPT.render()
        user    = _USER_PROMPT.render(summaries=summaries, entries=labeler.entries)

        # --- Budżet PRZED wywołaniem: za długi prompt odrzucamy, nie ucinamy -----------------
        self._check_budget(system, user)

        # --- Jedyne I/O: jedno wywołanie modelu; błędy LLM propagują (odpowiedzi nie było) ---
        response = await self._client.complete(
            user        = user,
            system      = system,
            max_tokens  = self._max_output_tokens,
            temperature = 0.0,                                     # wybór ma być powtarzalny
            json_schema = build_response_schema(labeler.labels),   # enum etykiet w kolejności promptu
        )

        # --- Odpowiedź -> wynik (także bezużyteczna odpowiedź to wynik, nie wyjątek) ---------
        result = self._interpret(labeler, response, system, user)

        # --- Log bez treści pisma i opcji ----------------------------------------------------
        # Odpowiedź nie do użycia -> WARNING z przyczyną; completion_tokens równe limitowi = urwana.
        if result.outcome == "invalid_response":
            logger.warning(
                "Klasyfikacja: odpowiedzi modelu %s nie da się użyć (completion_tokens %d/%d): %s",
                result.model, result.usage.completion_tokens, self._max_output_tokens, result.error,
            )
        # Poprawny wybór -> INFO: wynik i etykieta (bez uzasadnienia — cytuje treść pisma).
        else:
            logger.info("Klasyfikacja: %s, etykieta %s (opcji: %d).", result.outcome, result.label, len(options))
        return result
