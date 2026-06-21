# DOKUS Doc AI

Warstwa AI dla obiegu dokumentów (ESOD) **DOKUS**: automatyczna ekstrakcja treści
dokumentu i jego streszczenie, tak aby osoba dekretująca od razu wiedziała, czego
dokument dotyczy.

## Cel

DOKUS przesyła dokument w oryginalnej formie → system wyciąga tekst (również OCR ze
skanów) → LLM generuje streszczenie → wynik wraca do DOKUS.

## Zasady naczelne (NIE łamać bez wyraźnej decyzji)

1. **Prywatność pierwsza.** Docelowo żadne dane urzędowe nie opuszczają urzędu —
   stąd plan przejścia na własny model na własnej maszynie. Każda decyzja
   architektoniczna ma ten cel zachować osiągalnym.
2. **Abstrakcja dostawcy LLM.** Cała logika rozmawia z interfejsem `LLMClient`,
   nigdy bezpośrednio z SDK dostawcy. Zmiana komercyjne API → Bielik na RunPod →
   Bielik on-prem ma być zmianą konfiguracji/implementacji klienta, nie logiki
   biznesowej.
3. **Modularność.** Każdy komponent wymienialny niezależnie przez Docker Compose.
4. **Komunikacja = REST (HTTP/JSON)** między wszystkimi komponentami.

## Stack

| Warstwa | Technologia |
|---|---|
| Konteneryzacja | Docker + Docker Compose |
| OCR / ekstrakcja | Apache Tika (`apache/tika:3.3.0.0-full`, tag przypięty) + pakiet językowy `pol` (w środku: Tesseract, PDFBox, POI) |
| API / orkiestracja | FastAPI + Uvicorn (Python 3.12+) |
| Walidacja / konfiguracja | Pydantic + pydantic-settings (config z ENV) |
| Klient LLM | SDK `openai` ukryte za własnym interfejsem `LLMClient` |
| LLM — faza 1 | Komercyjne API (rekomendacja: Azure OpenAI, region UE) |
| LLM — faza 4–5 | Ollama + Bielik (RunPod RTX 4090 → maszyna on-prem z GPU) |

## Architektura kontenerów

| Kontener | Rola | Technologia |
|---|---|---|
| `tika` | Ekstrakcja tekstu (w tym OCR, gdy trzeba) | Apache Tika |
| `fastapi` | Logika biznesowa + API | FastAPI |
| `llm` (faza 4–5) | Własny model językowy | Ollama + Bielik |

## Przepływ danych

```
DOKUS → FastAPI
          │
          ├─ ekstrakcja treści (Tika)
          │      ├─ obraz/skan → OCR przez Tesseract
          │      └─ tekst       → PDFBox, Apache POI itd.
          │
          ├─ generowanie streszczenia (LLM przez LLMClient)
          │
          └─ zwróć wynik do DOKUS
```

Tika wystawia REST (`tika-server`), wykrywa typ pliku i jednym wywołaniem obsługuje
pliki natywne (PDF, DOCX, XLSX, e-mail) i skany. Obraz `full` ma już
Tesseract — OCR działa po doinstalowaniu pakietu `pol`, bez osobnego kontenera.

## Etapy wdrożenia (kolejność prac)

1. **[ZROBIONE i ZWERYFIKOWANE] OCR / ekstrakcja** — kontener Tika-full z pakietem
   `pol`. Szczegóły niżej (sekcja „Stan kroku 1").
2. **[ZROBIONE i ZWERYFIKOWANE] API na FastAPI** — przyjmuje dokument, woła extract, składa
   prompt, woła LLM, zwraca wynik. Endpointy: `/health`, `/extract`, `/summarize`,
   `/extract-and-summarize` (pełny pipeline). Szczegóły: sekcje „Plan kroku 2.1–2.5".
3. **Integracja z DOKUS** — ESOD wysyła oryginał, dostaje zwrotnie podsumowanie.
4. **Migracja LLM na RunPod** — maszyna z GPU + własny model w chmurze.
5. **Migracja LLM na własną maszynę** — GPU w urzędzie, Ollama + Bielik lokalnie.

> Kolejne fazy LLM mają być przełączalne wyłącznie przez konfigurację `LLMClient` +
> zmienne ENV. Logika ekstrakcji/promptów/API się nie zmienia.

## Stan kroku 1 — ekstrakcja / OCR (ZROBIONE i ZWERYFIKOWANE)

Postawiona sama warstwa ekstrakcji (bez FastAPI/LLM — to krok 2). Pliki:

- `tika/Dockerfile` — `FROM apache/tika:3.3.0.0-full` (tag **przypięty**, nie `latest`).
  Obraz full ma Tesseract/PDFBox/POI, ale **nie** ma polskiego pakietu OCR, więc
  dokładamy `tesseract-ocr-pol` (oraz `curl` pod healthcheck). `apt-get` wymaga
  roota → przełączamy `USER root`, a po instalacji **wracamy do `35002:35002`**
  (UID/GID, na którym działa obraz bazowy — nie zostawiamy kontenera na root).
- `tika/tika-config.xml` — domyślny język OCR **`pol+eng`**, dla PDF
  `ocrStrategy=auto` (tekstowe PDF czytane natywnie, strony-skany OCR-owane).
  Konfig podpięty przez `CMD ["-c", "/tika-config.xml"]` (entrypoint bazowy doklejá
  to do `TikaServerCli`).
- `docker-compose.yml` — usługa `tika` (port z `TIKA_PORT`, domyślnie 9998;
  healthcheck na `GET /tika`).
- `.env.example`, `.gitignore` — utrwalenie zasady „config przez ENV"; `.env` i
  zawartość `samples/` poza repo.
- `tests/` — `pytest` z podziałem `unit/` (puste do kroku 2) i `integration/`.
  Test integracyjny `test_tika_extraction.py` uderza w działający kontener Tika: serwer
  żyje, ekstrakcja natywna DOCX (POI) oraz OCR skanu PNG po polsku. Pliki testowe
  generowane w locie (Pillow/python-docx), bez binariów w repo. Gdy Tika
  niedostępna → testy `skip`, nie fail. Markery: parasol `integration` +
  `integration_tika`. Zależności: `requirements-dev.txt`.

**Decyzje (zweryfikowane wobec upstreamu, nie zgadnięte):** najnowszy full to
`3.3.0.0`; obraz bazowy = `ubuntu:resolute`, user `35002:35002`.

**Weryfikacja (2026-06-17) — przeszła:** build OK (`tesseract-ocr-pol 4.1.0`
wjechał), kontener `healthy`, Tika 3.3.0 odpowiada. `pytest -m integration` → 3/3
PASSED. OCR skanu PNG zwrócił dokładnie „Zażółć gęślą jaźń" (pełne ą/ć/ż/ł/ó/ę/ś/ź/ń),
co potwierdza, że konfiguracja `pol+eng` jest realnie używana, a nie domyślny `eng`.
Powtórzenie: `docker compose up -d tika` + `pytest -m integration`.

## Plan kroku 2 — API na FastAPI

Kolejność „od części do całości": najpierw fundamenty i `LLMClient`, potem osobne
endpointy ekstrakcji i summaryzacji (każdy testowalny samodzielnie), na końcu pełny
pipeline. Każdy endpoint dostaje testy (marker `integration_fastapi` + jednostkowe).

### Kroki

1. **[ZROBIONE i ZWERYFIKOWANE] Zalążek FastAPI** — struktura projektu, `Settings`
   (pydantic-settings: `TIKA_URL`, konfiguracja dostawcy LLM, timeouty), modele I/O
   (Pydantic), `/health` (opcjonalnie sprawdza dostępność Tiki), `Dockerfile` + usługa
   `fastapi` w `docker-compose` (`depends_on: tika`), szkielet testów. Szczegóły:
   sekcja „Stan kroku 2.1" niżej.
2. **[ZROBIONE i ZWERYFIKOWANE] `LLMClient`** — abstrakcyjny interfejs + implementacja
   OpenAI + `FakeLLMClient` do testów i dev-u (pipeline bez prawdziwego API, bez kosztów
   i bez wysyłania danych na zewnątrz — spójne z „prywatność pierwsza"). Szczegóły:
   sekcja „Stan kroku 2.2" niżej.
3. **[ZROBIONE i ZWERYFIKOWANE] API: czysta ekstrakcja** — proxy do Tiki: upload pliku,
   walidacja typu/rozmiaru, obsługa błędów (Tika niedostępna, plik nieobsługiwany, pusty
   wynik OCR). Osobny problem rozwiązany w 2.3.5: śmieciowa warstwa tekstowa (PDF z PUA)
   wykrywana + OCR-fallback + limit stron — pełny opis i zmierzone dowody w „Plan kroku 2.3
   → 2.3.5". Cały krok 2.3 (2.3.1–2.3.5) domknięty i zweryfikowany.
4. **[ZROBIONE i ZWERYFIKOWANE] API: czysta summaryzacja** — `POST /summarize`: wejście =
   tekst; system prompt po polsku (format hybrydowy pod osobę dekretującą); wołanie przez
   `LLMClient`; mapowanie `LLMError` → HTTP; truncacja wejścia z logiem (`LLM_MAX_INPUT_CHARS`).
   Szczegóły i weryfikacja: „Plan kroku 2.4".
5. **[ZROBIONE i ZWERYFIKOWANE] API: pełny pipeline** (3 → 4) — `POST /extract-and-summarize`:
   upload → ekstrakcja → streszczenie → odpowiedź (kompozycja serwisów, bez nowej logiki).
   Domyka KROK 2. Szczegóły i weryfikacja: „Plan kroku 2.5".

### Stan kroku 2.1 — zalążek FastAPI (ZROBIONE i ZWERYFIKOWANE)

Postawiony szkielet usługi (bez `LLMClient` i endpointów ekstrakcji/summaryzacji — to
kroki 2.2+). Usługa w katalogu `api/` (symetrycznie do `tika/`, izolowany build context).

- `api/app/config.py` — `Settings` (pydantic-settings, ENV bez prefiksu, `extra="ignore"`
  by nie wywracać się na `TIKA_PORT`/`FASTAPI_PORT`). Pola: `tika_url`,
  `tika_timeout_seconds`, `health_check_timeout_seconds`, `llm_*` (konfiguracja dostawcy
  LLM gotowa, klient dochodzi w 2.2; `llm_provider` domyślnie `fake` — nic nie wychodzi
  na zewnątrz). `get_settings()` z `lru_cache`.
- `api/app/models.py` — `HealthResponse` (na razie jedyny model I/O).
- `api/app/routers/health.py` — `GET /health`; best-effort ping Tiki (krótki timeout),
  status `degraded` gdy Tika niedostępna (200, bo sama usługa żyje).
- `api/app/main.py` — aplikacja + middleware request-id (nadaje/propaguje `X-Request-ID`,
  loguje `metoda ścieżka -> status [req=…]`; minimalne, świadomie nie monitoring).
- `api/Dockerfile` — `python:3.12-slim`, **non-root** (`appuser`, zasada projektu),
  uvicorn na 8000. `api/requirements.txt` (runtime), `api/.dockerignore`.
- `docker-compose.yml` — usługa `fastapi`: `depends_on: tika (service_healthy)`,
  `TIKA_URL=http://tika:9998`, przekazanie `LLM_*`, port z `FASTAPI_PORT` (domyślnie
  8000), healthcheck przez `python urllib` (bez doinstalowywania curl).
- `.env.example` — sekcja kroku 2 (`FASTAPI_PORT`, `TIKA_URL`, `LLM_*`).
- Testy: marker `integration_fastapi` (zarejestrowany w `pyproject.toml`, plus
  `pythonpath=["api"]` by `import app` działał z testów). `tests/unit/test_config.py`
  (domyślne/override ENV, ignorowanie nieznanych ENV).
  `tests/integration/test_fastapi_health.py` (kształt odpowiedzi, `X-Request-ID`,
  wartość zależności `tika`); fixtures `fastapi_url`/`fastapi_client` w `conftest.py`
  (skip, nie fail, gdy usługa nieuruchomiona).

**Weryfikacja (2026-06-18) — przeszła:** `pytest tests/unit` → 3 PASSED; smoke
in-process `TestClient` /health → 200 z `tika:ok` (Tika realna na localhost:9998).
`docker compose build fastapi` OK; `up -d fastapi` wstaje po `tika healthy`; kontener
`healthy`; `GET :8000/health` → `{"status":"ok",...,"dependencies":{"tika":"ok"}}`
(dowód, że `TIKA_URL=http://tika:9998` w sieci compose działa); `X-Request-ID` obecny
w nagłówku i w logach; `pytest -m integration_fastapi` → 3 PASSED. Powtórzenie:
`docker compose up -d fastapi` + `pytest -m integration_fastapi`.

### Stan kroku 2.2 — `LLMClient` (ZROBIONE i ZWERYFIKOWANE)

Warstwa LLM jako **transport/generacja** (analogia do `TikaClient`): „wiadomości →
tekst + zużycie". Świadomie NIE zna promptów summaryzacji ani truncacji — to krok 2.4.
Pakiet `api/app/llm/`:

- `llm/base.py` — abstrakcyjny `LLMClient` (jedna metoda `async complete(*, user, system,
  max_tokens, temperature) -> LLMResult`), modele `LLMResult`/`LLMUsage` (Pydantic) oraz
  hierarchia wyjątków domenowych `LLMError` → `LLMAuthError`/`LLMRateLimitError`/
  `LLMTimeoutError`/`LLMResponseError` (logika mapuje je na HTTP dopiero w 2.4/2.5).
- `llm/client_fake.py` — `FakeLLMClient`: deterministyczny, offline (prefiks `[FAKE-LLM]` +
  pierwsze ~40 słów wejścia). Domyślny dostawca dev/test — nic nie wychodzi na zewnątrz.
- `llm/client_openai.py` — `OpenAILLMClient` (jedyne miejsce importujące SDK `openai` —
  import zwykły na górze modułu, bez lazy importów: `openai` to twarda zależność
  projektu, więc nie udajemy, że jest opcjonalny). Obsługuje zwykłe OpenAI
  (`api_key`+`model`, opcjonalny `base_url`). Mapuje wyjątki SDK → `LLMError`.
  **Azure świadomie pominięty** — gdy będzie trzeba, osobny klient, bez ruszania tego.
  Czyste fragmenty wydzielone do statycznych helperów: `_build_messages` / `_to_result`
  (bez sieci) oraz `_map_sdk_error` (klasyfikator wyjątek SDK → `LLMError`; bez
  sieci/mocka). W `complete` zostaje samo I/O + `try/except`.
- `llm/factory.py` — `build_llm_client(settings)` + cache'owany `get_llm_client()`. Wybór
  po `LLM_PROVIDER` (`fake`/`openai`); walidacja braku klucza/modelu → `LLMConfigError`
  (czytelny błąd od razu, nie gołe 401 w runtime). Inny provider → `LLMConfigError`.
- `llm/__init__.py` — publiczne API pakietu (`from app.llm import get_llm_client, ...`).
- `api/requirements.txt` — dodane `openai>=1.40`. `.env.example` — sekcja LLM (fake/openai).
- Testy: marker `integration_llm` (zarejestrowany w `pyproject.toml`). Jednostkowe
  `tests/unit/test_llm_fake.py` (determinizm, kształt, ucinanie wejścia),
  `test_llm_factory.py` (dispatch + walidacja configu) oraz `test_llm_openai.py` (czyste
  helpery `_build_messages`/`_to_result`: kolejność system→user, `content=None`,
  `usage=None`, fallback modelu) — wszystkie bez sieci/SDK; oraz `test_llm_openai_errors.py`
  (klasyfikator `_map_sdk_error`: timeout/401/403/429/APIError → właściwy `LLMError`; bez
  sieci/mocka). Integracyjny `tests/integration/test_llm.py`
  (realny OpenAI, koszt minimalny `max_tokens=5`; **skip, nie fail**, gdy
  `LLM_PROVIDER`≠`openai` lub brak `LLM_API_KEY`).

**Decyzje:** klient **async** (FastAPI jest async; `AsyncOpenAI`); interfejs **minimalny**
(`complete`, jeden prompt — wystarcza dla summaryzacji); `temperature=0.0` domyślnie
(streszczenia stabilne). LLM **nie** jest pingowany w `/health` (kosztuje, Fake nic nie
powie). Klient jeszcze **nie** jest podpięty pod żaden endpoint — to dochodzi w 2.4/2.5.

**Konfiguracja (`.env`, poza repo):** `LLM_PROVIDER=openai`, `LLM_MODEL=gpt-4o-mini`,
`LLM_API_KEY=sk-proj-…` (zwykłe OpenAI, **nie** Azure → dane wychodzą do OpenAI/USA;
klucz **dev-only**, docelowo Azure UE → Bielik on-prem; rozważyć rotację).

**Weryfikacja (2026-06-19) — przeszła:** `pytest tests/unit` → 9 PASSED (3 config + 6 LLM);
`get_llm_client()` z `.env` buduje `OpenAILLMClient`; `pytest -m integration_llm` → 1 PASSED
(realny OpenAI odpowiedział, `usage.total_tokens>0`). Powtórzenie: `pytest tests/unit`
oraz (z kluczem w `.env`) `pytest -m integration_llm`.

### Architektura warstwy ekstrakcji — dwie klasy (transport vs domena)

Ekstrakcję rozdzielamy na dwie odpowiedzialności (analogia do `LLMClient`: transport
oddzielony od logiki). NIE mieszamy „rozmowy z Tiką" z decyzjami o treści.

- **`TikaClient` (transport)** — `bytes + content-type → surowy tekst + surowe
  metadane`. Nagłówki, timeouty, mapowanie błędów HTTP Tiki. Żadnych decyzji o treści.
  Świadomie **konkretna klasa, nie abstrakcyjny interfejs** — w przeciwieństwie do LLM
  nie ma mapy drogowej podmiany silnika (Tika zostaje); interfejs formalizujemy dopiero
  gdyby pojawił się drugi silnik (np. odłożony OCRmyPDF).
- **`ExtractionService` (domena, niezależna od tego, że pod spodem jest Tika)** — logika
  nad surowym wynikiem. Dwie odpowiedzialności (po 2.3.5 wydzielone do osobnych jednostek
  `PuaDetector` i `PdfPageLimiter`, składanych przez serwis — patrz „2.3.5 → Struktura"):
  1. **Jakość:** detekcja śmieciowej warstwy (PUA) + polityka OCR-fallback (pełny opis:
     „Plan kroku 2.3 → 2.3.5"); normalizacja whitespace; wyliczenie metadanych
     (MIME, język, długość).
  2. **Limit zakresu ekstrakcji (strażnik zasobów)** — gdy dokument jest duży (np. 100
     stron skanu), OCR-owanie całości może nas zatkać, więc ograniczamy zakres (np.
     pierwsze N stron). Istotne **głównie na ścieżce OCR** (natywny tekst jest tani) —
     spina się z decyzją o fallbacku (ta sama warstwa rozstrzyga „czy OCR" i „ile stron").
     Próg konfigurowalny przez ENV (np. `MAX_OCR_PAGES`). **Limit nie może być cichy:**
     log + metadana w odpowiedzi (np. „przetworzono N z M stron"), żeby osoba
     dekretująca wiedziała, że streszczenie powstało z części dokumentu (spójne z zasadą
     „prosta truncacja **z logiem**"). To NIE jest chunking/map-reduce z listy „świadomie
     pominięte" — celowo bierzemy tylko początek, nie składamy całości z kawałków.
     **[ROZSTRZYGNIĘTE w 2.3.5]** jak technicznie odciąć do N stron: Tika 3.3.0.0 **nie ma**
     natywnego `maxPages` (dodany dopiero w 4.x), więc tniemy plik sami (`pypdf`) przed
     wysłaniem do Tiki — strategia (B) „limit PRZED `auto`" (`PdfPageLimiter`).

> Uwaga terminologiczna: **limit zakresu ekstrakcji** (powyżej, domena ekstrakcji) to co
> innego niż **truncacja tekstu pod okno modelu** z kroku 4 (przygotowanie promptu,
> zależne od LLM, nie od dokumentu). Nie mylić tych dwóch.

### Kontrakty do ustalenia przed startem

- **[ROZSTRZYGNIĘTE 2026-06-20] Wejście** — **base64 w JSON** (nie multipart). Endpoint
  ekstrakcji przyjmuje `content_base64` + opcjonalne `filename`/`content_type` (hinty dla
  Tiki; brak → autodetekcja typu po stronie Tiki). Rozbicie: „Plan kroku 2.3" niżej.
- **[ROZSTRZYGNIĘTE 2026-06-20] Wyjście** — **wyekstrahowany tekst + metadane** (MIME,
  wykryty język, długość), nie samo streszczenie. Metadane przydają się diagnostycznie
  (np. jaki MIME wykryto, czy poszło OCR) i pod pełny pipeline w kroku 2.5.
- **[ROZSTRZYGNIĘTE 2026-06-19] Dostawca LLM fazy 1** — na dev **zwykłe OpenAI**
  (`gpt-4o-mini`, klucz zweryfikowany); docelowo Azure OpenAI UE (prywatność) → Bielik
  on-prem. Implementacja: `OpenAILLMClient` (Azure odłożony do osobnego klienta).
  Szczegóły: sekcja „Stan kroku 2.2".
- **[ROZSTRZYGNIĘTE 2026-06-21] Fallback ekstrakcji dla śmieciowej warstwy tekstowej (PDF z
  PUA)** — zaimplementowane w 2.3.5: detekcja po udziale PUA (`> 30%`), per-request
  `X-Tika-PDFOcrStrategy: ocr_only`, limit stron strategią (B) „cięcie pypdf przed `auto`"
  (`maxPages` brak w Tika 3.3.0.0). Pełne rozstrzygnięcia i weryfikacja: „Plan kroku 2.3 →
  2.3.5". Test integracyjny PDF (`sample_01.pdf`) odwieszony i przechodzi.

### Plan kroku 2.3 — czysta ekstrakcja (rozbicie na podkroki)

Realizuje krok 3 z listy „Kroki" wyżej. Buduje na sekcjach „Architektura warstwy
ekstrakcji" (transport vs domena) i „Kontrakty". Kolejność „od części do całości":
najpierw transport, potem domena (happy path), na końcu endpoint spinający całość.

**Decyzje wejściowe (rozstrzygnięte 2026-06-20):**
- **Podejście:** najpierw **szkielet** (`TikaClient` + happy path ekstrakcji natywnej),
  dopiero potem **jakość** (detekcja PUA + OCR-fallback) i **strażnik zasobów** (limit
  stron). Te dwa ostatnie świadomie NIE wchodzą do 2.3.1–2.3.4 (happy path) — zbiera je
  osobny krok **2.3.5** niżej, bo niosą otwarte decyzje z „Kontrakty"/architektury, które
  rozstrzygamy dopiero gdy stoi działający `POST /extract`.
- **Wejście:** base64 w JSON. **Wyjście:** tekst + metadane (MIME, język, długość).
  (patrz rozstrzygnięte „Kontrakty" wyżej.)

**2.3.1 — `TikaClient` (transport). [ZROBIONE i ZWERYFIKOWANE]** Nowy pakiet
`api/app/extraction/`. **Konkretna
klasa, nie abstrakcja** (uzasadnienie w „Architektura warstwy ekstrakcji" — Tika zostaje,
interfejs formalizujemy dopiero przy drugim silniku). Kontrakt: `bytes (+ opcjonalny
content-type/filename) → surowy tekst + surowe metadane`. Realizacja przez **`PUT
/rmeta/text`** (jedno wywołanie zwraca JSON: treść pod `X-TIKA:content` + metadane, m.in.
wykryty `Content-Type`) — zamiast osobnych `/tika` + `/meta`. Struktura jak
`OpenAILLMClient`: czyste helpery parsujące JSON rmeta (`_pick_text`/`_pick_metadata`)
wydzielone od I/O; klasyfikator błędów `_map_*` (transport/HTTP Tiki → wyjątek domenowy)
testowalny bez sieci. Wyjątki: `TikaError` → `TikaUnavailableError` (transport/timeout —
Tika nie odpowiada) / `TikaExtractionError` (Tika odpowiedziała błędem na plik:
nieobsługiwany/uszkodzony). Mapowanie na HTTP dopiero w 2.3.3. Testy: jednostkowe na
helperach + klasyfikatorze (bez sieci); integracyjny `integration_tika` na realnej Tice
(DOCX natywny).

**Weryfikacja (2026-06-20) — przeszła:** `pytest tests/unit` → 32 PASSED (w tym 13 dla
`TikaClient`: `_build_headers`/`_pick_text`/`_pick_metadata` + klasyfikator
`_map_http_error` na wyjątkach `httpx`, wszystko bez sieci); `pytest -m integration_tika`
na realnym kontenerze → PASSED: `extract` DOCX **bez** podanego `content_type` zwrócił
poprawny tekst z polskimi znakami (`gęślą`), a Tika sama wykryła MIME DOCX
(`...wordprocessingml.document`) — potwierdza, że kształt `/rmeta/text` (lista; treść
kontenera w `[0]` pod `X-TIKA:content`) jest taki, jak założono. Klient async odpalany
w teście przez `asyncio.run` (brak `pytest-asyncio`, spójnie z `test_llm.py`).

**2.3.2 — `ExtractionService` (domena, happy path). [ZROBIONE i ZWERYFIKOWANE]** Logika nad surowym wynikiem,
niezależna od tego, że pod spodem Tika. W tym podejściu **tylko**: normalizacja whitespace
+ wyliczenie metadanych (MIME z metadanych Tiki; długość = znaki/słowa; **język** —
defensywnie z metadanych Tiki, gdy brak → `None`; porządna detekcja to refinement). Pusty
wynik po normalizacji → wyjątek domenowy `EmptyExtractionError`. **ŚWIADOMIE POZA
ZAKRESEM** (odłożone, patrz architektura + „Kontrakty"): detekcja śmieciowej warstwy
**PUA**, **OCR-fallback** (`X-Tika-PDFOcrStrategy`), **limit zakresu OCR** (`MAX_OCR_PAGES`)
— **zrealizowane w 2.3.5**; rekurencyjna obsługa **embedded** (rmeta children) **nadal
odłożona**. Test PDF (`samples/sample_01.pdf`) był wtedy **WSTRZYMANY** do decyzji o PUA
(odwieszony i przechodzi w 2.3.5). Testy: jednostkowe z atrapą transportu
(normalizacja, liczenie znaków/słów, wyciągnięcie MIME, pusty → wyjątek) ORAZ — symetrycznie
do `TikaClient` — integracyjny `integration_tika` na realnej Tice: serwis nad realnym
transportem dowodzi założeń domeny o metadanych Tiki (DOCX → MIME `wordprocessing`;
`text/plain` → `_pick_content_type` realnie ucina `; charset=…`, czego atrapa nie pokaże;
**język** obie ścieżki: DOCX z zadeklarowanym językiem → `dc:language` → `language='pl'`,
a dokument bez deklaracji → `None`). **Ustalenie (zweryfikowane 2026-06-21, nie zgadnięte):**
`/rmeta/text` w naszej konfiguracji **nie auto-wykrywa** języka — `language` bierze się
wyłącznie z zadeklarowanego `dc:language`; brak deklaracji = `None` (assert-sentinel
w teście zaalarmuje, gdyby Tika zaczęła wykrywać język — wtedy wracamy do decyzji o detekcji).
Pliki: `api/app/extraction/service.py` (`ExtractionService` + modele `ExtractionResult`/
`ExtractionMetadata` + wyjątki `ExtractionError`→`EmptyExtractionError`); czyste operacje
(liczenie znaków/słów, MIME, język) wydzielone do osobnych krótkich metod (testowane
punktowo). **Weryfikacja (2026-06-21):** `pytest` → 60 PASSED (49 unit, +17 wobec 2.3.1;
`pytest -m integration_tika` → 7 PASSED, w tym 3 nowe dla serwisu: DOCX, charset, język).

**2.3.3 — Modele I/O + endpoint `POST /extract`. [ZROBIONE i ZWERYFIKOWANE]** `api/app/models.py`: `ExtractRequest`
(`content_base64` + opcjonalne `filename`/`content_type`), `ExtractResponse` (tekst +
zagnieżdżone metadane) — modele API **świadomie odrębne** od domenowych (`ExtractionResult`/
`ExtractionMetadata`), mapowanie `ExtractResponse.from_result` (kontrakt HTTP stoi
samodzielnie, domena może ewoluować w 2.3.5 bez ruszania schematu). Router
`api/app/routers/extract.py`: dekodowanie base64 (`validate=True`) + walidacja rozmiaru
(nowy `Settings.max_upload_bytes`), DI `ExtractionService` **inline przez `Depends`**
(`_get_extraction_service`, bez osobnej fabryki — Tika to jeden silnik, w odróżnieniu od
wymienialnego LLM). Czyste helpery `_decode_base64`/`_validate_size` (testowalne, bez I/O).
Mapowanie wyjątków → HTTP: zły base64 → **422**; pusty plik → **422**; za duży → **413**;
`TikaUnavailableError` → **502**; `TikaExtractionError`/`EmptyExtractionError` → **422**.
Stałe statusu w nazwach nieprzestarzałych (`HTTP_422_UNPROCESSABLE_CONTENT`,
`HTTP_413_CONTENT_TOO_LARGE`). Rejestracja routera w `main.py`. Testy: jednostkowe
`tests/unit/test_fastapi_extract.py` (`TestClient` + `dependency_overrides`, atrapa serwisu —
mapowanie base64/rozmiaru/wyjątków na kody, bez sieci) ORAZ integracyjne
`tests/integration/test_fastapi_extract.py` (`integration_fastapi` przez endpoint: DOCX natywny,
**OCR PNG** generowany w locie, złe base64 → 422).

**2.3.4 — Config + dokumentacja. [ZROBIONE i ZWERYFIKOWANE]** `MAX_UPLOAD_BYTES` w `Settings`
(domyślnie 20 MiB) + `.env.example`; sekcja `POST /extract` w README (wejście/wyjście/kody
błędów + przykład `curl`); aktualizacja checklisty statusu i tabeli ustawień w README.

**Weryfikacja całości 2.3 (happy path 2.3.1–2.3.4) — przeszła (2026-06-21):** `pytest` →
**70 PASSED** (56 unit, w tym 7 nowych dla routera `/extract`; reszta integracyjne, w tym
3 nowe `/extract` — DOCX natywny, OCR PNG, złe base64). `docker compose build fastapi` OK;
oba kontenery `healthy`; `pytest -m "integration_fastapi or integration_tika"` → 13 PASSED.
Smoke przez realny kontener: `POST /extract` (`text/plain`) → `{"text":"…","metadata":
{"content_type":"text/plain","language":null,"char_count":38,"word_count":6}}` (charset
ucięty z MIME), złe base64 → 422. **Świadomie poza happy-path 2.3.1–2.3.4:** detekcja PUA /
OCR-fallback / limit stron — wówczas test PDF jeszcze WSTRZYMANY; **zrealizowane i odwieszone
w 2.3.5** (niżej).

**2.3.5 — Jakość ekstrakcji: detekcja PUA + OCR-fallback + limit stron. [ZROBIONE i
ZWERYFIKOWANE]** Warstwa jakości
w `ExtractionService`, świadomie odłożona z happy patha (2.3.2). Wchodzi DOPIERO po
działającym `POST /extract`. Trzy odpowiedzialności **spięte w jednej warstwie** (ta sama
decyzja rozstrzyga „czy OCR" i „ile stron"):
1. **Detekcja śmieciowej warstwy tekstowej (PUA)** — kanoniczny opis zmierzonego przypadku
   (2026-06-18) „PDF tekstowy ≠ PDF czytelny": `samples/sample_01.pdf` (wydruk z **doPDF 11**,
   treść matematyczna z LaTeX-a) ma warstwę tekstową, ale wszystkie **1003 glify** mapuje na
   **Private Use Area (U+F0xx)** — brak/zepsuta `ToUnicode` CMap. Skutki: (a) ekstrakcja
   natywna zwraca śmieci, a NIE pusty wynik (sam `EmptyExtractionError` nie wystarcza);
   (b) `ocrStrategy=auto` **nie odpala OCR**, bo „warstwa tekstowa jest" (`ocrPageCount=0`);
   (c) metadana `pdf:unmappedUnicodeChars=0` jest myląca (glify „zmapowane" — na śmieć).
   Wymuszony `OCR_ONLY` ratuje treść poprawnie.
2. **OCR-fallback** — wymuszenie OCR, gdy warstwa natywna jest śmieciowa.
3. **Limit zakresu OCR (`MAX_OCR_PAGES`)** — strażnik zasobów: tylko pierwsze N stron na
   ścieżce OCR; **nie cicho** — log + metadana w odpowiedzi („przetworzono N z M stron").

**Decyzje (podjęte 2026-06-21, ugruntowane pomiarem/upstreamem — nie zgadnięte):**
1. **Detekcja PUA** — próg na **udziale znaków PUA wśród znaków nie-białych** (`_pua_ratio`),
   `> 30%` = śmieć (`_is_garbage_text`). Separacja zmierzona drastyczna: warstwa PUA `sample_01.pdf`
   ~77% vs poprawny OCR 0%, więc próg ma duży margines. Sygnał „0 liter alfabetu" świadomie
   tylko jako komentarz (kruchy dla krótkich tekstów), nie warunek. Biały znak ignorowany
   (PDF z PUA ma też mnóstwo `\n`). Zakresy: BMP `U+E000..F8FF` + supplementary PUA-A/B.
2. **Mechanizm OCR-fallback** — **per-request** `X-Tika-PDFOcrStrategy: ocr_only` (potwierdzone
   u upstreamu i empirycznie). **NIE** globalna zmiana `tika-config.xml` (globalny `OCR_*`
   spowalnia każdy PDF). `tika-config.xml` z `auto` **zostaje nietknięty** — nadpisujemy tylko
   per-request z naszej domeny. `TikaClient.extract` dostał opcjonalny `ocr_strategy`.
3. **Limit stron — strategia (B) „limit PRZED `auto`":** dla każdego PDF liczymy strony (pypdf)
   i — gdy `> MAX_OCR_PAGES` — tniemy do pierwszych N **przed** wysłaniem do Tiki. Ustalenie
   (zweryfikowane wobec upstreamu): **Tika 3.3.0.0 NIE ma natywnego `maxPages`** (dodany dopiero
   w 4.x), więc własny podział pliku to jedyna droga na przypiętej wersji → **nowa zależność
   `pypdf`** (świadoma decyzja użytkownika). Wybór (B) zamiast sondy `no_ocr`: prostsze, zostawia
   `auto`, brak regresji na PDF-ach mieszanych. **Koszt zaakceptowany:** limit dotyczy KAŻDEGO
   dużego PDF (de facto `MAX_PDF_PAGES`), więc czysty długi PDF tekstowy też jest ucinany —
   łagodzi to fakt, że długie dokumenty i tak truncuje krok 4, a cięcie nie jest ciche.
4. **Przepływ** (`ExtractionService.extract`): straznik stron → `auto` → jeśli warstwa PDF to
   PUA, retry `ocr_only` na **tym samym, już uciętym** pliku → metadane. `ocr_used` z
   `pdf:ocrPageCount`>0 (zweryfikowane: dla PDF `X-TIKA:Parsed-By` NIE pokazuje Tesseracta,
   `ocrPageCount` owszem). Cięcie nie ciche: log + `pages_total`/`pages_processed`/`ocr_truncated`.
5. **Asercje** — test PDF **odwieszony**: po OCR-fallbacku `sample_01.pdf` zawiera „Stolza"/
   „rozdzia…", zero znaków PUA, `ocr_used=true`, `pages_total=1`.

Config: `MAX_OCR_PAGES` (domyślnie 30) w `Settings` + `.env.example`.

**Struktura (refactor 2026-06-21):** dwie wyspecjalizowane odpowiedzialności wydzielone z
`ExtractionService` do osobnych jednostek (`service.py` zostaje **cienkim orkiestratorem**),
symetrycznie do zasady „jedna klasa izoluje jedną zależność":
- `extraction/pdf.py` — **`PdfPageLimiter`** (+ `PageLimit`): liczenie/cięcie stron;
  **izoluje `pypdf`** (jak `TikaClient` izoluje `httpx`, `OpenAILLMClient` SDK `openai`) —
  domena nie importuje już `pypdf`. Metody `is_pdf`/`_page_count`/`_take_first_pages`/`apply`.
- `extraction/quality.py` — **`PuaDetector`**: detekcja śmieciowej warstwy (PUA), próg
  konfigurowalny w konstruktorze. Metody `_is_pua`/`ratio`/`is_garbage`.
Serwis tworzy oba wewnętrznie z configu (to mechanika domeny, nie wymienialne silniki — więc
nie wstrzykiwane; router bez zmian). Pozostałe pliki: `service.py` (orkiestracja + helpery
metadanych `_pick_*`/`_build_metadata`; nowe pola `ExtractionMetadata`), `client_tika.py`
(`ocr_strategy`→`X-Tika-PDFOcrStrategy`, typ `OcrStrategy = Literal[...]`), `models.py` (pola
jakości w `ExtractMetadata` + `from_result`), `requirements.txt` (`pypdf>=4.0`).

**Weryfikacja (2026-06-21) — przeszła:** `pytest` → **99 PASSED** (było 70). Jednostkowe wg
odpowiedzialności w osobnych plikach: `test_extraction_pdf.py` (`PdfPageLimiter`), `test_extraction_quality.py`
(`PuaDetector`, w tym konfigurowalność progu), `test_extraction_service_fallback.py` (orkiestracja
(B) na nagrywającej atrapie — retry `ocr_only` tylko przy PUA; oba wywołania dostają już ucięty
PDF). Integracyjne na realnej Tice:
`integration_tika` → 8 PASSED (w tym limit stron na **wygenerowanym 3-stronicowym skanie**
obrazowym z `max_ocr_pages=1`: tylko strona 1 zOCR-owana, metadane 1 z 3); `integration_fastapi`
→ 7 PASSED (w tym `sample_01.pdf` PUA→OCR-fallback **przez endpoint**: treść poprawna, 0 PUA,
`ocr_used=true`). Powtórzenie: `docker compose up -d fastapi` + `pytest`.

### Plan kroku 2.4 — czysta summaryzacja (rozbicie na podkroki)

Realizuje krok 4 z listy „Kroki". Buduje na `LLMClient` z 2.2 (transport/generacja **już
gotowy** — „wiadomości → tekst + zużycie") i na zasadzie transport-vs-domena. **Wejście =
tekst** (nie plik), więc endpoint jest testowalny niezależnie od ekstrakcji. Domyślny
dostawca dev/test = `FakeLLMClient` (offline — bez kosztów, nic nie wychodzi na zewnątrz,
spójne z „prywatność pierwsza").

**Architektura (analogia do ekstrakcji — transport vs domena):**
- **`LLMClient` (transport/generacja, 2.2)** — wymienialny silnik (`fake`/`openai`/…→Bielik),
  budowany przez **fabrykę** `get_llm_client()`. Nie zna promptów summaryzacji (świadomie,
  z 2.2). NIE ruszamy.
- **`SummarizationService` (domena)** — NOWA klasa, pakiet `api/app/summarization/`
  (symetrycznie do `extraction/`). Składa prompt (system + szablon z tekstem), pilnuje
  truncacji wejścia, woła `LLMClient`, zwraca streszczenie + metadane. To ona zna prompty.
  Czyste fragmenty (budowa wiadomości, truncacja, metadane) wydzielone do helperów —
  testowalne bez LLM; w metodzie async `summarize` zostaje samo I/O + złożenie.

> Kontrast DI względem ekstrakcji: `ExtractionService` dostaje `TikaClient` **inline** (Tika
> = jeden silnik). `SummarizationService` dostaje `LLMClient` z **fabryki** `get_llm_client()`
> — bo LLM jest wymienialny (to cała idea abstrakcji dostawcy).

**Decyzje (rozstrzygnięte przy implementacji 2.4):**

- **[ROZSTRZYGNIĘTE — produktowe] Kształt promptu = HYBRYDA.** System prompt po polsku, rola =
  asystent robiący streszczenie pisma **pod dekretację**; format narzucony: krótki akapit +
  wypunktowanie kluczowych elementów (typ pisma, nadawca, czego dotyczy, termin, akcja — TYLKO
  te obecne w piśmie, bez zmyślania), zwracane jako **JEDEN string `summary`** (bez JSON/
  parsowania). Prompt to **logika, nie sekret** → w kodzie (`_SYSTEM_PROMPT` w `service.py`),
  nie w ENV. `temperature=0.0` (domyślne w `LLMClient`).
- **[ROZSTRZYGNIĘTE] Truncacja wejścia.** Próg `LLM_MAX_INPUT_CHARS` (`Settings`/ENV), liczony
  w **znakach** (odporne na zmianę modelu/tokenizera). Tekst dłuższy → **pierwsze N znaków**
  (początek, nie chunking) + log + metadana `truncated`. Default **90 000** — spójnie z
  `MAX_OCR_PAGES=30` (~3000 znaków/stronę); pod mniejszy model (Bielik) obniżyć (README →
  „Spójność limitów pipeline'u"). To **truncacja pod okno modelu** — co innego niż limit stron 2.3.5.
- **[ROZSTRZYGNIĘTE] Kontrakt I/O.** `SummarizeRequest = {text}`; `SummarizeResponse =
  {summary, metadata}`, metadane: `model`, `input_chars` (po strip), `truncated`, `usage`
  (`LLMUsage` z `app.llm` — reuse, leaf value object). Modele API **odrębne** od domenowych.
  Pusty/sam-whitespace tekst → `EmptyInputError` → 422.
- **[ROZSTRZYGNIĘTE] Mapowanie `LLMError` → HTTP.** `EmptyInputError`/brak `text` → **422**;
  `LLMConfigError`/`LLMAuthError` → **500** (nasz config serwera); `LLMRateLimitError` → **503**;
  `LLMTimeoutError` → **504**; `LLMResponseError`/bazowy `LLMError` → **502**.

**Podkroki (od części do całości; każdy z testami — marker `integration_fastapi`/`integration_llm` + jednostkowe):**

**2.4.1 — `SummarizationService` (domena). [ZROBIONE i ZWERYFIKOWANE]** Pakiet
`api/app/summarization/`. Kontrakt: `tekst → streszczenie + metadane`, przez wstrzyknięty
`LLMClient`. Czyste helpery (bez sieci/LLM): `_build_user_message` (szablon usera; system
prompt to stała `_SYSTEM_PROMPT`), `_truncate` (przycięcie + flaga), `_build_metadata`. Async
`summarize` = I/O (`LLMClient.complete`) + złożenie. Wyjątki LLM propagują (mapuje endpoint w
2.4.2). Testy: jednostkowe na helperach + na `FakeLLMClient` i nagrywającej atrapie (sprawdza,
co leci do LLM: system prompt, `max_tokens`, tekst w userze; determinizm, działanie truncacji).

**2.4.2 — Modele I/O + endpoint `POST /summarize`. [ZROBIONE i ZWERYFIKOWANE]** `models.py`: `SummarizeRequest`/
`SummarizeResponse` (+ `from_result`). Router `api/app/routers/summarize.py`: DI
`SummarizationService` nad `get_llm_client()` (fabryka z 2.2). Mapowanie `LLMError` → kody
HTTP (jak wyżej). Rejestracja w `main.py`. Testy: jednostkowe (`TestClient` +
`dependency_overrides`, atrapa serwisu/Fake — mapowanie kodów) + integracyjne
(`integration_fastapi` z `FakeLLMClient`; opcjonalnie `integration_llm` z realnym OpenAI,
koszt minimalny `max_tokens` mały).

**2.4.3 — Config + dokumentacja. [ZROBIONE i ZWERYFIKOWANE]** `LLM_MAX_INPUT_CHARS` w `Settings` + `.env.example`;
sekcja `POST /summarize` w README (wejście/wyjście/kody błędów + przykład `curl`);
aktualizacja checklisty. Spójność progu z limitami ekstrakcji (README → „Spójność limitów
pipeline'u").

**Świadomie poza 2.4** (patrz „Świadomie pominięte"): chunking/map-reduce długich dokumentów
(na teraz truncacja), tokenowo-dokładny licznik (na teraz znaki), streaming odpowiedzi, cache.
Pełny pipeline (upload → ekstrakcja → streszczenie) spina dopiero **krok 2.5**.

**Weryfikacja całości 2.4 (2.4.1–2.4.3) — przeszła (2026-06-21):** `pytest` → **119 PASSED**
(było 99; +20). Jednostkowe: `test_summarization_service.py` (helpery + orkiestracja na
nagrywającej atrapie/`FakeLLMClient`), `test_fastapi_summarize.py` (router: happy + 422 +
mapowanie 500/502/503/504 na atrapie serwisu — bez sieci). Integracyjne: **oba** poziomy
(symetrycznie do ekstrakcji) — `integration_llm` `tests/integration/test_summarization_service.py`
(`SummarizationService` nad **realnym OpenAI**: prompt hybrydowy dał poprawne polskie
streszczenie, `usage`/`model` realnie wypełnione; skip bez `openai`+klucza) oraz
`integration_fastapi` `tests/integration/test_fastapi_summarize.py` (endpoint przez kontener —
kontrakt provider-agnostyczny; działa z `fake` darmowo/deterministycznie i z `openai` realnie).

**Bug znaleziony i naprawiony przy 2.4 (pusty `LLM_BASE_URL`):** endpoint w kontenerze zwracał
502 „Connection error", a `integration_llm` (in-process na hoście) działał — różnica NIE była
brakiem sieci (kontener ma pełną łączność: TCP/TLS/`AsyncOpenAI` OK), tylko: `docker-compose`
ma `LLM_BASE_URL: "${LLM_BASE_URL:-}"` → do kontenera trafia **pusty string `""`** (nie brak
zmiennej); pydantic czytał `llm_base_url=""`, fabryka przekazywała `AsyncOpenAI(base_url="")` →
`APIConnectionError`. Host budował klienta z `.env` (brak klucza `LLM_BASE_URL` → `None`), stąd
działał. Fix: walidator w `Settings` (`_puste_na_none`) normalizuje pusty/biały ENV pól
opcjonalnych (`llm_api_key`/`llm_base_url`/`llm_model`) na `None` (test
`test_puste_env_opcjonalne_na_none`). Po fixie `/summarize` na kontenerze z `openai` zwraca
realne streszczenie. Powtórzenie: `docker compose up -d fastapi` + `pytest`.

### Plan kroku 2.5 — pełny pipeline (rozbicie na podkroki)

Realizuje krok 5 z listy „Kroki": spina 2.3 (ekstrakcja) i 2.4 (summaryzacja) w JEDNO
wywołanie — **plik → tekst → streszczenie**. To docelowy endpoint pod integrację z DOKUS
(krok 3): ESOD wysyła oryginał, dostaje zwrotnie podsumowanie. **Nie wprowadza nowej logiki
domenowej — KOMPONUJE istniejące serwisy.** Domyka KROK 2 (API na FastAPI).

**Architektura (kompozycja, nie nowa logika):**
- `ExtractionService` (2.3) i `SummarizationService` (2.4) — gotowe, NIE ruszamy.
- **`PipelineService` (orkiestrator)** — NOWA cienka klasa (pakiet `api/app/pipeline/`),
  dostaje OBA serwisy wstrzyknięte; `process(data, content_type, filename) -> PipelineResult`
  = `extract` → weź `result.text` → `summarize` → złóż metadane obu etapów. Symetria do
  `ExtractionService` komponującego `PdfPageLimiter`+`PuaDetector`. **Bez własnego I/O** — całe
  I/O jest w serwisach składowych; tu tylko sekwencja + złożenie wyniku.

> DI: router buduje `PipelineService` nad `ExtractionService` (`TikaClient` inline) +
> `SummarizationService` (`LLMClient` z fabryki) — łączy wzorce DI z `/extract` i `/summarize`.

**Decyzje wejściowe (rozstrzygnięte przy realizacji 2.5):**

- **[ROZSTRZYGNIĘTE] Nazwa endpointu = `POST /extract-and-summarize`** (opisowa: robi oba
  etapy; odróżnia od `/extract` i `/summarize`, które robią po jednym).
- **[ROZSTRZYGNIĘTE] Kształt odpowiedzi = `summary` + `text` + metadane obu etapów,
  ZAGNIEŻDŻONE.** `{ summary, text, extraction: {content_type, language, char_count,
  word_count, ocr_used, pages_total, pages_processed, ocr_truncated}, summarization: {model,
  input_chars, truncated, usage} }`. `text` = PEŁNY wyekstrahowany tekst (przed truncacją pod
  LLM — `summarization.truncated` mówi, czy model widział tylko część). Zagnieżdżenie reużywa
  wprost `ExtractMetadata` + `SummarizeMetadata` i unika kolizji `char_count` (ekstrakcja) vs
  `input_chars` (summaryzacja). Modele API odrębne; mapowanie z `PipelineResult`.
- **[ROZSTRZYGNIĘTE] Wejście = base64 w JSON** (jak `/extract`): `content_base64` + opcjonalne
  `filename`/`content_type`.
- **[ROZSTRZYGNIĘTE] Mapowanie błędów = UNIA `/extract` + `/summarize`:** zły base64 / pusty plik /
  `TikaExtractionError` / `EmptyExtractionError` / `EmptyInputError` → **422**; za duży → **413**;
  `TikaUnavailableError` → **502**; `LLMTimeoutError` → **504**; `LLMRateLimitError` → **503**;
  `LLMConfigError`/`LLMAuthError` → **500**; `LLMResponseError`/`LLMError` → **502**.
- **[UWAGA] Spójność limitów.** Pipeline przepuszcza dokument przez WSZYSTKIE trzy bramki naraz
  (`MAX_UPLOAD_BYTES` → `MAX_OCR_PAGES` → `LLM_MAX_INPUT_CHARS`) — tu „Spójność limitów
  pipeline'u" (README) jest najważniejsza w praktyce. **Bez nowych ENV** (reuse). Latencja: OCR
  (do `tika_timeout`) + LLM (do `llm_timeout`) sekwencyjnie — **sync**; async/kolejka świadomie
  odłożone.

**Podkroki (od części do całości; każdy z testami):**

**2.5.1 — `PipelineService` (orkiestrator). [ZROBIONE i ZWERYFIKOWANE]** Pakiet `api/app/pipeline/`.
`process(...)` komponuje extract→summarize (przekazuje `extraction.text` jako wejście
summaryzacji); `PipelineResult` = `summary` + `text` (pełny) + metadane obu etapów
(reuse domenowych `ExtractionMetadata`/`SummarizationMetadata`). Złożenie w czystym helperze
`_build_result`. **Bez własnego I/O** — całe I/O w serwisach składowych. Wyjątki obu warstw
NIE są łapane (mapuje endpoint w 2.5.2). Świadomie BEZ testu integracyjnego na poziomie
serwisu: orkiestrator nie izoluje żadnej realnej zależności (w odróżnieniu od `ExtractionService`/
`SummarizationService`), więc realny przebieg pokrywa test endpointu (2.5.2) + warstwy niżej
(`integration_tika`/`integration_llm`). Testy: jednostkowe `test_pipeline_service.py` na ATRAPACH
obu serwisów (kolejność extract→summarize, przekazanie pełnego `text`, złożenie wyniku,
propagacja wyjątków obu warstw — przy błędzie ekstrakcji summaryzacja niewołana).

**2.5.2 — Modele I/O + endpoint. [ZROBIONE i ZWERYFIKOWANE]** `models.py`:
`SummarizeDocumentRequest`/`SummarizeDocumentResponse` (+ `from_result`; metadane API
ZAGNIEŻDŻONE — reuse `ExtractMetadata`+`SummarizeMetadata`, by uniknąć kolizji `char_count` vs
`input_chars`). Router `api/app/routers/pipeline.py`: DI łączy oba wzorce (`ExtractionService`
nad `TikaClient` inline + `SummarizationService` nad `get_llm_client()`), czyste helpery
`_decode_base64`/`_validate_size` jak w `/extract`, mapowanie błędów = UNIA (zły config LLM z
fabryki → 500 już w DI). Rejestracja w `main.py`. Testy: jednostkowe `test_fastapi_pipeline.py`
(`TestClient` + `dependency_overrides`, atrapa pipeline'u — happy path + walidacja base64/rozmiaru
+ pełna unia mapowań kodów OBU warstw, bez sieci) + integracyjne `test_fastapi_pipeline.py`
(`integration_fastapi`, DOCX end-to-end przez kontener, provider-agnostyczne + złe base64 → 422).

**2.5.3 — Dokumentacja. [ZROBIONE i ZWERYFIKOWANE]** Sekcja `POST /extract-and-summarize` w README
(wejście + przykładowe żądanie + wyjście + kody błędów + `curl`), zaktualizowany akapit `## API`
(cztery endpointy), checklista statusu (Krok 2 odznaczony, 2.5.1–2.5.3 zaznaczone), odsyłacz do
„Spójność limitów pipeline'u". Bez nowego configu (reuse istniejących limitów).

**Świadomie poza 2.5** (patrz „Świadomie pominięte"): async przez kolejkę (RabbitMQ),
uwierzytelnianie API, monitoring, streaming odpowiedzi, chunking. To domyka **krok 2**; dalej
**krok 3** (integracja z DOKUS) konsumuje ten endpoint.

**Weryfikacja całości 2.5 (2.5.1–2.5.3) — przeszła (2026-06-21):** `pytest tests/unit` →
**119 PASSED** (było 106; +13 dla routera pipeline'u, +5 wcześniej dla `PipelineService` =
liczone razem). `docker compose up -d --build fastapi` OK; oba kontenery `healthy`;
`pytest -m "integration_fastapi or integration_tika"` → **19 PASSED** (+2 dla pipeline'u). Smoke
przez kontener (realny OpenAI): `POST /extract-and-summarize` (`text/plain`) → `200` z poprawnym
polskim streszczeniem hybrydowym + zagnieżdżonymi metadanymi obu etapów (`extraction.content_type`,
`summarization.model`/`usage`). **Po 2.5 cały KROK 2 (API na FastAPI) zamknięty.** Powtórzenie:
`docker compose up -d fastapi` + `pytest`.

### Przekrojowe (dotyczą wszystkich endpointów)

Mapowanie błędów na kody HTTP (4xx/5xx zamiast gołych wyjątków), minimalne logowanie
z request-id (to NIE monitoring/Zabbix), testy zgodnie z konwencją markerów.

## Świadomie pominięte na teraz (NIE dodawać bez pytania)

- LiteLLM (unifikacja dostawców) oraz LangChain/LangGraph (orkiestracja, RAG).
- Asynchroniczne wywołania przez kolejkę (np. RabbitMQ).
- Wydzielenie OCR do osobnego kontenera (np. OCRmyPDF).
- Uwierzytelnianie / bezpieczeństwo API między DOKUS a FastAPI.
- Monitoring (np. Zabbix).
- Chunking / map-reduce długich dokumentów (na teraz: prosta truncacja tekstu).

Jeśli zadanie wydaje się wymagać któregoś z powyższych — zapytaj, zamiast wprowadzać.

## Konwencje

- Konfiguracja wyłącznie przez ENV (pydantic-settings) — żadnych sekretów ani
  endpointów na sztywno w kodzie.
- Dostawcę LLM zmieniamy przez konfigurację, nie przez edycję logiki biznesowej.
- Nowe komponenty dokładamy jako usługi w `docker-compose`, komunikacja po REST/JSON.
- Testy integracyjne każdej usługi dostają własny marker `integration_<usługa>` (np.
  `integration_fastapi`, `integration_llm`) oraz parasolowy `integration`; markery
  rejestrujemy w `pyproject.toml`.
- Ten sam plik testowy może istnieć równolegle w `tests/unit/` i `tests/integration/`
  (np. `test_extraction_service.py`) — dlatego pytest działa w `--import-mode=importlib`
  (`addopts` w `pyproject.toml`). Bez tego zbiorczy `pytest -m …` wywala „import file
  mismatch" na duplikatach nazw (tryb domyślny `prepend` wymaga `__init__.py`).
- **Importy w testach:** zadeklarowanych zależności (runtime `api/requirements.txt` oraz dev
  `requirements-dev.txt`, w tym Pillow/python-docx do fikstur) **nie** guardujemy
  `pytest.importorskip` — importujemy wprost. Brak zadeklarowanej zależności = błąd instalacji
  (głośny `ImportError`), nie cichy skip; testy odpalamy po `pip install -r requirements-dev.txt`.
  `importorskip` zostaje tylko dla zależności **faktycznie opcjonalnych** (obecnie brak). To NIE
  dotyczy „skip, nie fail" dla niedostępnych USŁUG (Tika/FastAPI/LLM) — tam skip zostaje.
- Język projektu i komunikacji: polski.
