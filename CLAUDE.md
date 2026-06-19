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
| OCR / ekstrakcja | Apache Tika (`apache/tika:latest-full`) + pakiet językowy `pol` (w środku: Tesseract, PDFBox, POI) |
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
pliki natywne (PDF, DOCX, XLSX, e-mail) i skany. Obraz `latest-full` ma już
Tesseract — OCR działa po doinstalowaniu pakietu `pol`, bez osobnego kontenera.

## Etapy wdrożenia (kolejność prac)

1. **[ZROBIONE i ZWERYFIKOWANE] OCR / ekstrakcja** — kontener Tika-full z pakietem
   `pol`. Szczegóły niżej (sekcja „Stan kroku 1").
2. **API na FastAPI** — przyjmuje dokument, woła extract, składa prompt, woła LLM,
   zwraca wynik.
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
3. **API: czysta ekstrakcja** — proxy do Tiki: upload pliku, walidacja typu/rozmiaru,
   obsługa błędów (Tika niedostępna, plik nieobsługiwany, pusty wynik OCR).
   **Uwaga (zmierzone 2026-06-18): „PDF tekstowy" ≠ „PDF czytelny".** Wykryty realny
   przypadek (`samples/sample_01.pdf`, wydruk z **doPDF 11**, treść matematyczna z
   LaTeX-a): warstwa tekstowa istnieje, ale mapuje wszystkie 1003 glify na **Private
   Use Area (U+F0xx)** — brak/zepsuta `ToUnicode` CMap. Skutki: (a) ekstrakcja
   natywna zwraca śmieci, a NIE pusty wynik; (b) `ocrStrategy=auto` **nie odpala OCR**,
   bo „warstwa tekstowa jest" (`ocrPageCount=0`); (c) metadana
   `pdf:unmappedUnicodeChars=0` jest myląca (glify są „zmapowane" — na śmieć).
   Wymuszony `OCR_ONLY` ratuje treść poprawnie. **Wniosek: endpoint potrzebuje
   detekcji śmieciowej warstwy (np. udział znaków PUA / brak liter alfabetu powyżej
   progu) i fallbacku na OCR — „pusty wynik OCR" jako jedyny warunek błędu nie
   wystarcza.**
4. **API: czysta summaryzacja** — wejście = tekst; szablon promptu + system prompt po
   polsku (kątem osoby dekretującej); wołanie przez `LLMClient`; obsługa timeoutów/
   rate-limit. Długi dokument na teraz: prosta truncacja z logiem (chunking odłożony).
5. **API: pełny pipeline** (3 → 4) — upload → ekstrakcja → streszczenie → odpowiedź.

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
  (domyślne/override ENV, ignorowanie nieznanych ENV; `importorskip` gdy runtime nie
  zainstalowany). `tests/integration/test_fastapi_health.py` (kształt odpowiedzi, `X-Request-ID`,
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
- `llm/fake.py` — `FakeLLMClient`: deterministyczny, offline (prefiks `[FAKE-LLM]` +
  pierwsze ~40 słów wejścia). Domyślny dostawca dev/test — nic nie wychodzi na zewnątrz.
- `llm/openai_client.py` — `OpenAILLMClient` (jedyne miejsce importujące SDK `openai`;
  import **leniwy** w metodach, więc sam import pakietu nie wymaga SDK). Obsługuje zwykłe
  OpenAI (`api_key`+`model`, opcjonalny `base_url`). Mapuje wyjątki SDK → `LLMError`.
  **Azure świadomie pominięty** — gdy będzie trzeba, osobny klient, bez ruszania tego.
- `llm/factory.py` — `build_llm_client(settings)` + cache'owany `get_llm_client()`. Wybór
  po `LLM_PROVIDER` (`fake`/`openai`); walidacja braku klucza/modelu → `LLMConfigError`
  (czytelny błąd od razu, nie gołe 401 w runtime). Inny provider → `LLMConfigError`.
- `llm/__init__.py` — publiczne API pakietu (`from app.llm import get_llm_client, ...`).
- `api/requirements.txt` — dodane `openai>=1.40`. `.env.example` — sekcja LLM (fake/openai).
- Testy: marker `integration_llm` (zarejestrowany w `pyproject.toml`). Jednostkowe
  `tests/unit/test_llm_fake.py` (determinizm, kształt, ucinanie wejścia) i
  `test_llm_factory.py` (dispatch + walidacja configu — bez sieci/SDK). Integracyjny
  `tests/integration/test_llm.py` (realny OpenAI, koszt minimalny `max_tokens=5`; **skip,
  nie fail**, gdy `LLM_PROVIDER`≠`openai` lub brak `LLM_API_KEY`).
- **[DO UZUPEŁNIENIA] Test mapowania wyjątków `OpenAILLMClient`** — blok
  `except APITimeoutError/AuthenticationError/RateLimitError/APIError → LLMError` nie ma
  testu (unit pokrywa `fake`+fabrykę, integracyjny tylko happy-path). To kod, który
  zadziała wyłącznie w awarii, więc realnie niesprawdzony. Plan: jednostkowy z
  zamockowanym klientem SDK rzucającym każdy typ błędu i asercją zmapowanego `LLMError`
  (bez sieci). Tani (~20 linii), domyka jedyną nietestowaną logikę kroku 2.2.

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
  nad surowym wynikiem. Dwie odpowiedzialności:
  1. **Jakość:** detekcja śmieciowej warstwy (PUA) + polityka OCR-fallback (patrz krok 3
     i decyzja w „Kontraktach"); normalizacja whitespace; wyliczenie metadanych
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
     **DO ZWERYFIKOWANIA przy implementacji:** jak technicznie odciąć do N stron —
     własny podział pliku przed wysłaniem do Tiki (sterowalne, niezależne od formatu) czy
     natywny limit `tika-server` (sprawdzić wobec upstreamu, nie zgadywać).

> Uwaga terminologiczna: **limit zakresu ekstrakcji** (powyżej, domena ekstrakcji) to co
> innego niż **truncacja tekstu pod okno modelu** z kroku 4 (przygotowanie promptu,
> zależne od LLM, nie od dokumentu). Nie mylić tych dwóch.

### Kontrakty do ustalenia przed startem

- **Wejście** — DOKUS wysyła plik jako multipart (`UploadFile`) czy base64 w JSON?
- **Wyjście** — samo streszczenie, czy też wyekstrahowany tekst + metadane
  (typ MIME, wykryty język, długość tekstu)?
- **[ROZSTRZYGNIĘTE 2026-06-19] Dostawca LLM fazy 1** — na dev **zwykłe OpenAI**
  (`gpt-4o-mini`, klucz zweryfikowany); docelowo Azure OpenAI UE (prywatność) → Bielik
  on-prem. Implementacja: `OpenAILLMClient` (Azure odłożony do osobnego klienta).
  Szczegóły: sekcja „Stan kroku 2.2".
- **Fallback ekstrakcji dla śmieciowej warstwy tekstowej (PDF z PUA)** — DECYZJA DO
  PODJĘCIA przy kroku 3 (patrz uwaga wyżej). Do rozstrzygnięcia: (1) jak wykrywać
  śmieciową warstwę (próg udziału PUA / brak liter / heurystyka długości); (2) czy
  fallback to wymuszony `OCR_ONLY` przez nagłówek `X-Tika-PDFOcrStrategy` per-request,
  czy zmiana globalna `ocrStrategy` w `tika-config.xml` (uwaga: `OCR_AND_TEXT`/`OCR_ONLY`
  globalnie spowalnia wszystkie PDF-y); (3) jak to testować — m.in. czego asertować na
  `samples/sample_01.pdf` (test integracyjny PDF jest WSTRZYMANY do tej decyzji; test
  OCR dla `samples/sample_01.jpg` jest niezależny i może powstać wcześniej).

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
- Język projektu i komunikacji: polski.
