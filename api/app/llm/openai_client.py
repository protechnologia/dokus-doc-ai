"""Implementacja LLMClient dla OpenAI (krok 2.2).

To JEDYNE miejsce, ktore importuje SDK `openai` (zasada naczelna nr 2 — SDK ukryte
za interfejsem). Obsluguje zwykle OpenAI: wystarczy `api_key` (+ `model`),
opcjonalnie `base_url` (np. wlasny gateway zgodny z API OpenAI).

Azure OpenAI swiadomie POMINIETE — gdy bedzie potrzebne, dolozymy osobnego klienta
(`AzureOpenAILLMClient`), bez ruszania tego. Fabryka rozstrzyga wybor po `LLM_PROVIDER`.

SDK importujemy LENIWIE (wewnatrz metod), zeby sam import pakietu `app.llm` nie
wymagal zainstalowanego `openai` — dzieki temu Fake i fabryka sa testowalne bez SDK.
Wyjatki SDK mapujemy na domenowe `LLMError`, by logika nie znala typow dostawcy.
"""

from __future__ import annotations

from app.llm.base import (
    LLMAuthError,
    LLMClient,
    LLMRateLimitError,
    LLMResponseError,
    LLMResult,
    LLMTimeoutError,
    LLMUsage,
)


class OpenAILLMClient(LLMClient):
    """Klient OpenAI oparty o `AsyncOpenAI`."""

    def __init__(
        self,
        *,
        api_key: str,                  # klucz API, np. "sk-proj-...HNkA"
        model: str,                    # nazwa modelu, np. "gpt-4o-mini" / "gpt-4o"
        base_url: str | None = None,   # wlasny endpoint ".../v1"; None = domyslny OpenAI
        timeout: float = 60.0,         # limit czasu wywolania [s], np. 60.0
    ) -> None:
        # Leniwy import: SDK to zaleznosc tylko tej implementacji (nie Fake/fabryki).
        from openai import AsyncOpenAI

        # Jeden dlugozyjacy klient SDK na instancje (reuzywalny, trzyma pule polaczen).
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
        self._model = model

    async def complete(
        self,
        *,
        user: str,                     # tresc usera, np. "Streszcz dokument:\n<tekst>"
        system: str | None = None,     # prompt systemowy, np. "Streszczaj po polsku."; None = brak
        max_tokens: int | None = None, # limit dlugosci odpowiedzi, np. 300; None = default OpenAI
        temperature: float = 0.0,      # losowosc 0.0-2.0, np. 0.0 (stabilnie) / 0.7 (kreatywniej)
    ) -> LLMResult:
        # Wyjatki SDK importujemy tu (leniwie), zaraz mapujemy je na domenowe LLMError.
        from openai import (
            APIError,
            APITimeoutError,
            AuthenticationError,
            PermissionDeniedError,
            RateLimitError,
        )

        # --- Budowa wiadomosci: opcjonalny system + wymagany user -------------------
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user})

        # --- Wywolanie API + mapowanie bledow dostawcy na wyjatki domenowe ----------
        try:
            resp = await self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        except APITimeoutError as exc:
            # Przekroczony timeout polaczenia/odpowiedzi.
            raise LLMTimeoutError(str(exc)) from exc
        except (AuthenticationError, PermissionDeniedError) as exc:
            # Zly/brakujacy klucz (401) lub brak uprawnien do modelu (403).
            raise LLMAuthError(str(exc)) from exc
        except RateLimitError as exc:
            # Przekroczony limit zapytan lub wyczerpana kwota (429).
            raise LLMRateLimitError(str(exc)) from exc
        except APIError as exc:
            # Bazowy blad SDK — lapie reszte (5xx, blad polaczenia, zla odpowiedz).
            raise LLMResponseError(str(exc)) from exc

        # --- Wyciagniecie tekstu i zuzycia tokenow z odpowiedzi SDK -----------------
        choice = resp.choices[0]                    # parametrem `n` mozemy zazadac >1 wariantu; my nie ustawiamy n (default 1) -> bierzemy [0]
        text = choice.message.content or ""         # None przy tool-call lub filtrze tresci -> normalizujemy do ""
        u = resp.usage                              # obiekt zuzycia tokenow (moze byc None)
        usage = LLMUsage(
            prompt_tokens=getattr(u, "prompt_tokens", 0) or 0,          # tokeny wejscia (promptu)
            completion_tokens=getattr(u, "completion_tokens", 0) or 0,  # tokeny wygenerowane
            total_tokens=getattr(u, "total_tokens", 0) or 0,            # suma (wejscie + wyjscie)
        )
        return LLMResult(text=text, model=resp.model or self._model, usage=usage)  # uzyty model z odpowiedzi
