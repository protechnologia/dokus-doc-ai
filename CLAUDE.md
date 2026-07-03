# DOKUS Doc AI

Warstwa AI dla obiegu dokumentów (ESOD) **DOKUS**: automatyczna ekstrakcja treści
dokumentu i jego streszczenie, tak aby osoba dekretująca od razu wiedziała, czego
dokument dotyczy.

Ten plik = orientacja pod dalszy rozwój: zasady, których nie łamać, jak zbudowana jest
logika (gdzie co dokładać), trwałe decyzje i pułapki oraz co świadomie poza zakresem.
Stack, kontrakt API (endpointy, I/O, kody błędów) i procedury uruchomienia: **README**.

## Cel

DOKUS przesyła dokument w oryginalnej formie → system wyciąga tekst (również OCR ze
skanów) → LLM generuje streszczenie → wynik wraca do DOKUS.

## Zasady naczelne (NIE łamać bez wyraźnej decyzji)

1. **Prywatność pierwsza.** Docelowo żadne dane urzędowe nie opuszczają urzędu —
   stąd własny model na własnej maszynie. Każda decyzja architektoniczna ma ten cel
   zachować osiągalnym.
2. **Abstrakcja dostawcy LLM.** Cała logika rozmawia z interfejsem `LLMClient`,
   nigdy bezpośrednio z SDK dostawcy. Zmiana komercyjne API → Bielik on-prem ma być
   zmianą konfiguracji/implementacji klienta, nie logiki biznesowej.
3. **Modularność.** Każdy komponent wymienialny niezależnie przez Docker Compose.
4. **Komunikacja = REST (HTTP/JSON)** między wszystkimi komponentami.

## Architektura kontenerów

| Kontener | Rola | Technologia |
|---|---|---|
| `tika` | Ekstrakcja tekstu (w tym OCR, gdy trzeba) | Apache Tika |
| `fastapi` | Logika biznesowa + API | FastAPI |
| `ollama` | Lokalny model językowy (opcjonalny, warstwa compose) | Ollama + Bielik |

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
pliki natywne (PDF, DOCX, XLSX, e-mail) i skany. Obraz `full` ma już Tesseract — OCR
działa po doinstalowaniu pakietu `pol`, bez osobnego kontenera.

## Kontener `tika` — ekstrakcja / OCR

- `tika/Dockerfile` — `FROM apache/tika:3.3.0.0-full` (tag **przypięty**) + `tesseract-ocr-pol`
  (obraz full nie ma polskiego OCR); po instalacji **wracamy do non-root** `35002:35002`.
- `tika/tika-config.xml` — OCR **`pol+eng`**, PDF `ocrStrategy=auto`. Zostaje **nietknięty** —
  wymuszony OCR robimy per-request z domeny (patrz OCR-fallback niżej).

## Kontener `fastapi` — logika (transport vs domena)

Nośna zasada: NIE mieszać „rozmowy z usługą" (transport) z decyzjami o treści (domena) —
tu dokładasz nowy kod po właściwej stronie. Middleware nadaje/propaguje `X-Request-ID`
(nagłówek + logi; to **NIE** monitoring). Wejście plików = **base64 w JSON** (nie multipart);
modele API są **odrębne** od domenowych (kontrakt HTTP stoi niezależnie od ewolucji domeny).

- **Transport** — izoluje jedną zależność zewnętrzną, operuje na surowcu:
  - `TikaClient` (izoluje `httpx`; `PUT /rmeta/text`) — **konkretna klasa, nie interfejs**:
    Tika zostaje, interfejs formalizujemy dopiero przy drugim silniku. Wyjątki:
    `TikaUnavailableError` (nie odpowiada) / `TikaExtractionError` (odrzuciła plik).
  - `LLMClient` (izoluje SDK `openai`) — **abstrakcyjny interfejs** (silnik wymienialny):
    `async complete()`; impl. `FakeLLMClient` (offline, domyślny dev/test — nic nie wychodzi
    na zewnątrz), `OpenAILLMClient` (**jedyne** miejsce importujące `openai`; obsługuje też
    Ollamę — API zgodne z OpenAI; Azure świadomie odłożony do osobnego klienta). Fabryka
    `get_llm_client()` po `LLM_PROVIDER` (`fake`/`openai`/`ollama`); brak klucza/modelu/base_url →
    `LLMConfigError` (czytelny błąd od razu). Wyjątki: hierarchia `LLMError`. `temperature=0`.
    LLM **nie** jest pingowany w `/health`.
- **Domena** — niezależna od silnika pod spodem:
  - `ExtractionService` — normalizacja + metadane (MIME, długość; **język wyłącznie z
    `dc:language`** — nasza Tika NIE auto-wykrywa, brak → `None`); pusty wynik →
    `EmptyExtractionError`. Cienki orkiestrator nad dwiema jednostkami:
    - `PuaDetector` — udział znaków PUA (BMP `E000–F8FF` + supplementary) wśród nie-białych
      `> 30%` = śmieciowa warstwa (zmierzone: wadliwy PDF ~77% vs poprawny OCR 0%). Powód:
      niektóre PDF (np. wydruk doPDF) mają warstwę tekstową mapowaną na Private Use Area
      (zepsuta `ToUnicode`) → ekstrakcja natywna zwraca śmieci, a `ocrStrategy=auto` **nie**
      odpala OCR (myśli, że warstwa jest).
    - `PdfPageLimiter` (izoluje `pypdf`) — tnie PDF do pierwszych `MAX_OCR_PAGES` stron
      **przed** wysłaniem (Tika 3.3.0.0 nie ma `maxPages` — dodany w 4.x). Dotyczy KAŻDEGO
      dużego PDF; cięcie **nie ciche** (metadane `pages_total`/`pages_processed`/`ocr_truncated`).
    - **OCR-fallback**: przy wykrytej PUA retry z per-request `X-Tika-PDFOcrStrategy: ocr_only`
      na już uciętym pliku. `ocr_used` z `pdf:ocrPageCount > 0`.
  - `SummarizationService` — składa **prompt hybrydowy PL** (rola: streszczenie pisma pod
    dekretację; krótki akapit + wypunktowane elementy obecne w piśmie, bez zmyślania; jako
    jeden string `summary`). Prompt = logika → w kodzie (`_SYSTEM_PROMPT`), nie w ENV.
    Truncacja wejścia do `LLM_MAX_INPUT_CHARS` (liczona w **znakach**; pierwsze N, nie chunking;
    flaga `truncated`). Pusty tekst → `EmptyInputError`.
  - `PipelineService` — orkiestrator `extract` → `summarize`; **bez własnego I/O**; odpowiedź
    z zagnieżdżonymi metadanymi obu etapów.
- **DI (kontrast):** `ExtractionService` dostaje `TikaClient` **inline** (jeden silnik);
  `SummarizationService`/`PipelineService` biorą `LLMClient` z **fabryki** (silnik wymienialny).

**Limity — trzy bramki tego samego dokumentu** (spójność ważna, README „Spójność limitów
pipeline'u"): `MAX_UPLOAD_BYTES` (→ 413) → `MAX_OCR_PAGES` → `LLM_MAX_INPUT_CHARS`. Rozróżnienie:
**limit zakresu ekstrakcji** (stron, zależny od dokumentu) ≠ **truncacja pod okno modelu**
(znaków, zależna od LLM).

**Gotcha configu:** `docker-compose` dla niezdefiniowanego `${VAR:-}` wstawia **pusty string**,
nie brak zmiennej → walidator `_puste_na_none` w `Settings` normalizuje pusty/biały ENV pól
opcjonalnych (`llm_api_key`/`llm_base_url`/`llm_model`) na `None` (inaczej
`AsyncOpenAI(base_url="")` → `APIConnectionError`).

## Kontener `ollama` — lokalny Bielik (opcjonalny)

Lokalny model to **Ollama serwująca Bielika**, dokładana jako warstwy compose. Przełączenie
dostawcy = **tylko konfiguracja** (`LLM_PROVIDER`+ENV); logika ekstrakcji/promptów/API się nie
zmienia. Ollama wystawia endpoint **zgodny z API OpenAI** (`/v1`), więc `OpenAILLMClient` gada
z Bielikiem bez zmian w kodzie — wystarczy `LLM_BASE_URL` + `LLM_MODEL`.

**Trójwarstwowy układ compose** (rozdziela „CZY Bielik" od „JAK liczy"; bez profili):
- `docker-compose.yml` (baza) — `tika`+`fastapi`, LLM domyślnie `fake`. **Nie zawiera** Ollamy → domyślny `up` lekki.
- `docker-compose.bielik.yml` — dokłada usługę `ollama` (kontener `dokus-ollama`, `base_url=http://ollama:11434/v1`) + wolumen `ollama-models`.
- `docker-compose.bielik.gpu.yml` — cienka nakładka: rezerwacja GPU na `ollama`.

Warstwy dziedziczą przez **`include`** (child→parent), więc **jeden** `-f` podnosi łańcuch
(`-f docker-compose.bielik.gpu.yml` = baza + Bielik + GPU) — bez multi-`-f` i bez `COMPOSE_FILE`.
Procedura krok po kroku: **README → „Zmiana dostawcy LLM na lokalnego Bielika"**.

**Decyzje (świadome — nie „upraszczać" bez powodu):**
1. **GPU osobną warstwą, nie w bazie.** Aktywna rezerwacja `nvidia` **twardo wywala** start bez
   runtime nvidia (brama urządzeń Dockera, nie Ollama — ona sama spadłaby na CPU). Baza CPU-only
   przenośna; GPU dokłada się tam, gdzie realnie jest.
2. **Zero magii `LLM_*` w warstwach.** `bielik.yml` NIE dotyka `fastapi`. Provider przełącza się
   **jawnie w `.env`** (`LLM_PROVIDER`/`LLM_BASE_URL`/`LLM_MODEL`, które baza przekazuje). Bez
   `depends_on` na `ollama` (klient LLM budowany leniwie).
3. **`include`, nie multi-`-f`/`COMPOSE_FILE`.** Dziedziczenie deklaratywne w YAML, jeden `-f`,
   zero ukrytego stanu w env. Odrzucone: kotwice YAML (tylko w obrębie jednego pliku), `extends`
   (per-usługa, ignoruje `depends_on`).
4. **Provider `ollama` w fabryce** buduje `OpenAILLMClient` na Ollamę **bez wymogu klucza**
   (atrapa `"ollama"`; SDK wymaga wartości, Ollama ją ignoruje), z wymogiem `LLM_MODEL` +
   `LLM_BASE_URL`. Alternatywa: `openai` + `LLM_BASE_URL` na Ollamę + atrapa klucza.

**Uwagi praktyczne:**
- **Model:** dev/CPU → mniejszy (`SpeakLeash/bielik-4.5b-v3.0-instruct:Q8_0`, okno 8K, format
  hybrydowy trzyma luźno); on-prem/GPU → `bielik-11b-v3.0-instruct` (okno 32K, ściśle trzyma
  format). Przy mniejszym oknie obniż `LLM_MAX_INPUT_CHARS`.
- **CPU liczy wolno** (rzędu minut/dokument) → podnieś `LLM_TIMEOUT_SECONDS`; na GPU domyślne
  wystarcza.
- Obraz przypięty `ollama/ollama:0.31.1`. Modele w wolumenie `ollama-models`
  (`docker compose -f docker-compose.bielik.yml exec ollama ollama pull <tag>`), przeżywają restart.

## Klient PHP — integracja z DOKUS

Integrację po stronie konsumenta realizuje **uniwersalny klient PHP**
(`integrations/php/DocAiClient.php`): DOKUS (lub inny ESOD) woła nasze API, wysyła oryginał
(base64) i odbiera streszczenie.

- **JEDEN samodzielny plik**, namespace `Dokus\DocAi` (klient + DTO + wyjątki). Pokrywa
  cztery endpointy (warianty `*File()` same czytają plik i kodują base64).
- **Decyzje:** bez `composer.json`/autoloadera (drop-in `require`, zero konfliktu zależności
  w cudzym ESOD-zie); czysty cURL (`ext-curl`+`ext-json`); PHP **8.1+**. Komentarze
  uniwersalne — bez nazwy DOKUS i roadmapy (klient ma być produktem ogólnym).
- **Architektura:** `CurlTransport` izoluje cURL (surowe stringi); kontrakt „API mówi JSON-em"
  żyje w `DocAiClient`; `Config` (adres + timeouty, domyślnie 180 s pod sekwencyjny OCR+LLM)
  wstrzykiwany. Błędy: `DocAiException` → `TransportException` / `ApiException` (niesie
  `statusCode`/`detail`/`X-Request-ID`).
- **Znane ograniczenie:** `CurlTransport` jest `final` → nie podmienia się na atrapę; pełne
  mapowanie testowane realnym 422, nie mockiem. Czysty mock wymagałby wydzielenia interfejsu
  transportu — nie robione bez potrzeby.

## Świadomie pominięte (NIE dodawać bez pytania)

- Chmurowy przystanek LLM (np. RunPod) — celowo pominięty; łamałby „prywatność pierwsza",
  a on-prem jest celem końcowym.
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
- **Po zmianie kodu `api/` przebuduj obraz:** `docker compose up -d --build fastapi` — sam
  `up`/recreate używa starego obrazu (kod się nie odświeży).
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
