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
| `ollama` (faza 4–5) | Własny model językowy | Ollama + Bielik |

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

## Etapy wdrożenia (kolejność prac)

1. **[ZROBIONE]** OCR / ekstrakcja — kontener Tika-full z pakietem `pol`.
2. **[ZROBIONE]** API na FastAPI — endpointy `/health`, `/extract`, `/summarize`,
   `/extract-and-summarize` (pełny pipeline).
3. **[W TOKU]** Integracja z DOKUS — ESOD wysyła oryginał, dostaje zwrotnie podsumowanie.
   Pierwszy element (strona konsumenta): uniwersalny **klient PHP** w `integrations/php/`.
4. **Migracja LLM na RunPod** — maszyna z GPU + własny model w chmurze.
5. **Migracja LLM na własną maszynę** — GPU w urzędzie, Ollama + Bielik lokalnie.

> Kolejne fazy LLM mają być przełączalne wyłącznie przez konfigurację `LLMClient` +
> zmienne ENV. Logika ekstrakcji/promptów/API się nie zmienia. Warstwy Docker Compose dla
> lokalnego Bielika (Ollama) są już przygotowane — patrz „Przygotowanie do fazy 4–5".

## Co stoi (kroki 1–2, ZROBIONE i ZWERYFIKOWANE)

Kontrakt I/O endpointów i kody błędów: **README (`## API`)** — tu tylko trwałe decyzje
projektowe. Wejście plików = **base64 w JSON** (nie multipart); modele API są **odrębne**
od domenowych (kontrakt HTTP stoi niezależnie od ewolucji domeny).

### Ekstrakcja / OCR — kontener `tika`

- `tika/Dockerfile` — `FROM apache/tika:3.3.0.0-full` (tag **przypięty**) + `tesseract-ocr-pol`
  (obraz full nie ma polskiego OCR); po instalacji **wracamy do non-root** `35002:35002`.
- `tika/tika-config.xml` — OCR **`pol+eng`**, PDF `ocrStrategy=auto`. Zostaje **nietknięty** —
  wymuszony OCR robimy per-request z domeny (patrz OCR-fallback niżej).

### API — kontener `fastapi`

Middleware nadaje/propaguje `X-Request-ID` (nagłówek + logi; to **NIE** monitoring).

**Architektura logiki — transport vs domena** (nośna zasada: NIE mieszać „rozmowy z usługą"
z decyzjami o treści).

- **Transport** — izoluje jedną zależność zewnętrzną, operuje na surowcu:
  - `TikaClient` (izoluje `httpx`; `PUT /rmeta/text`) — **konkretna klasa, nie interfejs**:
    Tika zostaje, interfejs formalizujemy dopiero przy drugim silniku. Wyjątki:
    `TikaUnavailableError` (nie odpowiada) / `TikaExtractionError` (odrzuciła plik).
  - `LLMClient` (izoluje SDK `openai`) — **abstrakcyjny interfejs** (silnik wymienialny):
    `async complete()`; impl. `FakeLLMClient` (offline, domyślny dev/test — nic nie wychodzi
    na zewnątrz) i `OpenAILLMClient` (**jedyne** miejsce importujące `openai`; Azure świadomie
    odłożony do osobnego klienta). Fabryka `get_llm_client()` po `LLM_PROVIDER`; brak
    klucza/modelu → `LLMConfigError` (czytelny błąd od razu). Wyjątki: hierarchia `LLMError`.
    `temperature=0`. LLM **nie** jest pingowany w `/health`.
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

## Stan kroku 3 — integracja z DOKUS (W TOKU)

Pierwszy artefakt: **uniwersalny klient PHP** (`integrations/php/DocAiClient.php`) — strona
konsumenta API. To jeszcze NIE pełna integracja (DOKUS realnie wysyłający oryginał).

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

## Przygotowanie do fazy 4–5 — warstwy compose dla lokalnego Bielika (Ollama)

Groundwork pod fazy 4 (RunPod) i 5 (on-prem): kontener LLM realizujemy jako **Ollama serwująca
Bielika**, dołączaną jako warstwy compose. Kod (logika/prompty/API) NIE rusza — tylko
konfiguracja (zasada nr 2). Ollama wystawia endpoint **zgodny z API OpenAI** (`/v1`), więc
`OpenAILLMClient` gada z Bielikiem bez zmian — wystarczy `LLM_BASE_URL` + `LLM_MODEL`
(zweryfikowane empirycznie 2026-07-03: kształt odpowiedzi = to, co parsuje `_to_result`;
host-Ollama jako ścieżka dev porzucona przez tarcia WSL↔Windows — stąd kontener).

**Trójwarstwowy układ** (rozdziela „CZY Bielik" od „JAK liczy"; bez profili):
- `docker-compose.yml` (baza) — `tika`+`fastapi`, LLM domyślnie `fake`. **Nie zawiera** Ollamy → domyślny `up` lekki.
- `docker-compose.bielik.yml` — dokłada usługę `ollama` (kontener `dokus-ollama`, `base_url=http://ollama:11434/v1`) + wolumen `ollama-models`.
- `docker-compose.bielik.gpu.yml` — cienka nakładka: rezerwacja GPU na `ollama`.

Warstwy dziedziczą przez **`include`** (child→parent), więc **jeden** `-f` podnosi łańcuch
(`-f docker-compose.bielik.gpu.yml` = baza + Bielik + GPU) — bez multi-`-f` i bez `COMPOSE_FILE`.

**Decyzje (świadome, z użytkownikiem):**
1. **GPU osobną warstwą, nie w bazie.** Aktywna rezerwacja `nvidia` **twardo wywala** start bez
   runtime nvidia (brama urządzeń Dockera, nie Ollama — ona sama spadłaby na CPU). Baza CPU-only
   przenośna; GPU dokłada się tam, gdzie realnie jest.
2. **Zero magii `LLM_*` w warstwach.** `bielik.yml` NIE dotyka `fastapi`. Provider przełącza się
   **jawnie w `.env`** (`LLM_PROVIDER`/`LLM_BASE_URL`/`LLM_MODEL`, które baza przekazuje). Bez
   `depends_on` na `ollama` (klient LLM budowany leniwie).
3. **`include`, nie multi-`-f`/`COMPOSE_FILE`.** Dziedziczenie deklaratywne w YAML, jeden `-f`,
   zero ukrytego stanu w env. Odrzucone: kotwice YAML (tylko w obrębie jednego pliku), `extends`
   (per-usługa, ignoruje `depends_on`).
4. **Provider = `ollama` w fabryce (wariant B) — ZROBIONE.** `LLM_PROVIDER=ollama` buduje
   `OpenAILLMClient` na Ollamę **bez wymogu klucza** (atrapa `"ollama"`; SDK wymaga wartości,
   Ollama ją ignoruje), z wymogiem `LLM_MODEL` + `LLM_BASE_URL` → `LLMConfigError` gdy brak.
   Wariant A (`openai` + atrapa klucza) nadal działa jako alternatywa.

**Świadome długi (następne kroki):**
- Przypiąć tag obrazu `ollama/ollama` (teraz `latest`).
- Wybrać i `pull` mniejszego Bielika pod dev/CPU (cel: 4.5B v3.0) → do `.env`, nie do compose.
- Weryfikacja end-to-end: FastAPI z `LLM_PROVIDER=ollama` → Bielik streszcza realny dokument.

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
