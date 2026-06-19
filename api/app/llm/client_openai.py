"""Implementacja LLMClient dla OpenAI (krok 2.2).

To JEDYNE miejsce, ktore importuje SDK `openai` (zasada naczelna nr 2 — SDK ukryte
za interfejsem; izolacja = tylko ten plik dotyka SDK). Obsluguje zwykle OpenAI:
wystarczy `api_key` (+ `model`), opcjonalnie `base_url` (np. wlasny gateway zgodny
z API OpenAI). Wyjatki SDK mapujemy na domenowe `LLMError`, by logika nie znala
typow dostawcy.

`openai` jest twarda zaleznoscia projektu (api/requirements.txt), wiec importujemy
go normalnie na gorze modulu — bez lazy importow.

Struktura `complete`: czyste fragmenty (budowa wiadomosci, mapowanie odpowiedzi) sa
wydzielone do statycznych helperow `_build_messages` / `_to_result` — testowalnych
jednostkowo bez sieci. W `complete` zostaje samo I/O + `try/except`, a regula
klasyfikacji bledow w `_map_sdk_error` (testowalna bez mocka).
"""

from __future__   import annotations
from openai       import APIError, APITimeoutError, AsyncOpenAI, AuthenticationError, PermissionDeniedError, RateLimitError
from app.llm.base import LLMAuthError, LLMClient, LLMError, LLMRateLimitError, LLMResponseError, LLMResult, LLMTimeoutError, LLMUsage


class OpenAILLMClient(LLMClient):
    """Klient LLM dla OpenAI — konkretna implementacja interfejsu `LLMClient`.

    Do czego:
        Tlumaczy generyczne wywolanie `complete(...)` (uzywane przez logike biznesowa)
        na konkretne API OpenAI (`chat.completions`) i z powrotem: odpowiedz oraz bledy
        dostawcy zamienia na typy domenowe (`LLMResult` / `LLMError`). Logika nigdy nie
        rozmawia z OpenAI wprost — widzi tylko `LLMClient` (zasada naczelna nr 2).

    Flow jednego `complete(...)`:
        1. `_build_messages`  — z `user` (+ opcjonalny `system`) sklada liste wiadomosci,
        2. `AsyncOpenAI.chat.completions.create` — wlasciwe wywolanie API (jedyne I/O),
        3. blad SDK -> `_map_sdk_error` -> odpowiedni `LLMError` (timeout/auth/limit/...),
        4. sukces  -> `_to_result` -> `LLMResult` (tekst + uzyty model + zuzycie tokenow).

    Konfiguracja (api_key / model / base_url / timeout) wstrzykiwana przez fabryke z ENV;
    sam klient niczego nie czyta z konfiguracji globalnej.
    """

    def __init__(
        self,
        *,
        api_key: str,                  # klucz API, np. "sk-proj-...HNkA"
        model: str,                    # nazwa modelu, np. "gpt-4o-mini" / "gpt-4o"
        base_url: str | None = None,   # wlasny endpoint ".../v1"; None = domyslny OpenAI
        timeout: float = 60.0,         # limit czasu wywolania [s], np. 60.0
    ) -> None:
        """Opis metody:
        Zbuduj klienta OpenAI (sama konfiguracja, bez polaczenia do API).

        Przyklad argumentow:
            api_key="sk-proj-..."
            model="gpt-4o-mini"

        Przyklad wyniku:
            gotowy, skonfigurowany OpenAILLMClient
        """
        # Jeden dlugozyjacy klient SDK na instancje (reuzywalny, trzyma pule polaczen).
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
        self._model = model

    # --- Czyste helpery (bez I/O) — testowalne jednostkowo bez sieci ----------------

    @staticmethod
    def _build_messages(
        user: str,             # tresc usera, np. "Streszcz dokument:\n<tekst>"
        system: str | None,    # prompt systemowy, np. "Streszczaj po polsku."; None = brak
    ) -> list[dict[str, str]]:
        """Opis metody:
        Zloz liste wiadomosci chat: opcjonalny `system` + wymagany `user`.
        Czysta funkcja — zaden stan instancji, zero I/O.

        Przyklad argumentow:
            user="dok"
            system="Po polsku"

        Przyklad wyniku:
            [{"role": "system", "content": "Po polsku"}, {"role": "user", "content": "dok"}]
        """
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user})
        return messages

    @staticmethod
    def _to_result(
        resp,                  # obiekt odpowiedzi z chat.completions.create (ksztalt SDK)
        fallback_model: str,   # model gdy resp.model puste, np. "gpt-4o-mini"
    ) -> LLMResult:
        """Opis metody:
        Zmapuj odpowiedz SDK na domenowy `LLMResult`.
        Czysta funkcja — operuje tylko na przekazanym `resp`, bez sieci.

        Przyklad argumentow:
            resp = odpowiedz SDK z message.content="Tak.", usage=(10,3,13), model="gpt-4o-mini"
            fallback_model="gpt-4o-mini"

        Przyklad wyniku:
            LLMResult(text="Tak.", model="gpt-4o-mini", usage=LLMUsage(10, 3, 13))
        """
        choice = resp.choices[0]                    # parametrem `n` mozemy zazadac >1 wariantu; my nie ustawiamy n (default 1) -> bierzemy [0]
        text = choice.message.content or ""         # None przy tool-call lub filtrze tresci -> normalizujemy do ""
        u = resp.usage                              # obiekt zuzycia tokenow (moze byc None)
        usage = LLMUsage(
            prompt_tokens=getattr(u, "prompt_tokens", 0) or 0,          # tokeny wejscia (promptu)
            completion_tokens=getattr(u, "completion_tokens", 0) or 0,  # tokeny wygenerowane
            total_tokens=getattr(u, "total_tokens", 0) or 0,            # suma (wejscie + wyjscie)
        )
        return LLMResult(text=text, model=resp.model or fallback_model, usage=usage)

    # --- Klasyfikator bledow SDK (bez I/O; uzywa typow SDK, ale nie wymaga mocka) ---

    @staticmethod
    def _map_sdk_error(
        exc: Exception,        # instancja wyjatku SDK openai, np. RateLimitError / APITimeoutError
    ) -> LLMError:
        """Opis metody:
        Zaklasyfikuj wyjatek SDK `openai` na odpowiednik domenowy `LLMError`.
        ZWRACA (nie rzuca) wyjatek — wolajacy robi `raise ... from exc`, by zachowac
        oryginalna przyczyne. Testowalny bez sieci i bez mocka klienta: wystarczy podac
        instancje wyjatku SDK. Kolejnosc isinstance jak w hierarchii SDK — od typow
        szczegolowych do `APIError` (baza), inaczej baza zlapalaby wszystko za wczesnie.

        Przyklad argumentow:
            exc=RateLimitError(...)

        Przyklad wyniku:
            LLMRateLimitError("...")   # analogicznie: APITimeoutError -> LLMTimeoutError itd.
        """
        if isinstance(exc, APITimeoutError):
            return LLMTimeoutError(str(exc))            # timeout polaczenia/odpowiedzi
        if isinstance(exc, (AuthenticationError, PermissionDeniedError)):
            return LLMAuthError(str(exc))               # zly/brak klucza (401) / brak uprawnien (403)
        if isinstance(exc, RateLimitError):
            return LLMRateLimitError(str(exc))          # limit zapytan / wyczerpana kwota (429)
        return LLMResponseError(str(exc))               # reszta APIError (5xx, blad polaczenia, zla odpowiedz)

    # --- Wywolanie (I/O) — orkiestracja --------------------------------------------

    async def complete(
        self,
        *,
        user: str,                     # tresc usera, np. "Streszcz dokument:\n<tekst>"
        system: str | None = None,     # prompt systemowy, np. "Streszczaj po polsku."; None = brak
        max_tokens: int | None = None, # limit dlugosci odpowiedzi, np. 300; None = default OpenAI
        temperature: float = 0.0,      # losowosc 0.0-2.0, np. 0.0 (stabilnie) / 0.7 (kreatywniej)
    ) -> LLMResult:
        """Opis metody:
        Wygeneruj odpowiedz OpenAI dla pojedynczego promptu (implementacja `LLMClient`).
        Buduje wiadomosci -> wola chat.completions -> mapuje wynik na `LLMResult`.

        Przyklad argumentow:
            user="Streszcz: <tekst>"
            system="Streszczaj po polsku."
            max_tokens=300

        Przyklad wyniku:
            LLMResult(text="<streszczenie>", model="gpt-4o-mini", usage=LLMUsage(...))

        Raises:
            LLMError: po zmapowaniu bledu SDK (timeout / auth / limit / odpowiedz).
        """
        # Czysta budowa wejscia (testowana osobno).
        messages = self._build_messages(user, system)

        # --- Wywolanie API; znane bledy SDK -> LLMError przez klasyfikator ----------
        # Lapiemy TYLKO znane typy SDK; nieznane wyjatki (np. blad programu) maja
        # propagowac bez zmiany, a nie zostac polkniete.
        try:
            resp = await self._client.chat.completions.create(model=self._model, messages=messages, max_tokens=max_tokens, temperature=temperature)
        except (APITimeoutError, AuthenticationError, PermissionDeniedError, RateLimitError, APIError) as exc:
            raise self._map_sdk_error(exc) from exc

        # Czyste mapowanie odpowiedzi na LLMResult (testowane osobno).
        return self._to_result(resp, self._model)
