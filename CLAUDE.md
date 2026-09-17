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

**Przyczyna błędu w logach = osobne handlery, nie middleware.** Middleware widzi już gotową
`Response` — `detail` (jedyne „dlaczego") żyje wyłącznie w wyjątku, piętro niżej. Stąd
`log_http_exception` (4xx→WARNING, 5xx→ERROR) i `log_validation_error` (bo `RequestValidationError`
**nie** jest `HTTPException`, a to najczęstsze 422). Handlery **nie zmieniają odpowiedzi** —
logują i delegują do domyślnych. Powód: realny 422 z produkcji, którego przyczyny nie dało się
ustalić z logów bez dostępu do klienta.

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
    **Bez ponowień SDK (`max_retries=0`).** SDK `openai` domyślnie (2) po cichu ponawia timeout,
    429 i 5xx: realny limit czasu wynosił ~3 × `LLM_TIMEOUT_SECONDS`, a DOKUS ponawiał żądania już
    ponowione u nas. Ponawia wyłącznie konsument, więc stała w kodzie, nie ENV. Koszt: chwilowe
    429/5xx OpenAI wracają od razu jako 503/502. Strażnik: `tests/unit/test_llm_openai_transport.py`.
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
  - `SummarizationService` — składa **prompt PL** (rola: streszczenie pisma pod dekretację;
    **samo wypunktowanie** pięciu pól obecnych w piśmie, bez zmyślania; jako jeden string
    `summary`). Prompt = logika → w kodzie (`_SYSTEM_PROMPT`), nie w ENV. **Akapitu otwierającego
    świadomie NIE ma**: model naśladuje najbardziej konkretny wzorzec w prompcie (lista pól), a
    „napisz akapit" to tylko opis — dawał się wymusić wyłącznie przykładem, i to najpewniej dopiero
    jako tura `assistant` (zmiana interfejsu `LLMClient`). Macierz siedmiu wariantów × sześć pism ×
    dwa przebiegi: komentarz przy prompcie. Strażniki: `tests/unit/test_summarization_service.py`
    (pięć pól, zero numeracji w opisie, żadnej obietnicy akapitu).
    Pusty tekst → `EmptyInputError`. Truncację wejścia do `LLM_MAX_INPUT_CHARS` (w **znakach**,
    nie chunking) robi osobna czysta jednostka:
    - `TextTruncator` (`summarization/truncation.py`) — tekst ponad budżet → **początek / jeden
      ciągły fragment z geometrycznego środka / koniec** w proporcjach z żądania (`head_percent`/
      `tail_percent`, domyślnie 45/35, środek = reszta; 0 wyłącza część), ze znacznikiem
      `[…pominięto fragment dokumentu…]` w każdym miejscu pominięcia. Powód: przy cięciu od
      początku model nie widział zakończenia pisma (żądanie, podpis, dane nadawcy — to zasila
      klasyfikację adresata). **Dwa świadome uproszczenia (NIE „poprawiać"):** (1) cięcie „na
      głupio" dokładnie na offsecie, także w środku wyrazu — znacznik i tak sygnalizuje przerwę,
      a szukanie granic akapitu/zdania to kod bez realnego zysku; (2) **stała rezerwa na dwa
      pełne znaczniki** niezależnie od proporcji — kilkadziesiąt znaków budżetu za to, że
      `sent_chars <= LLM_MAX_INPUT_CHARS` wynika z arytmetyki. Środek dosuwany, by nie wchodził
      na początek/koniec; stykające się części sklejane bez znacznika.
    - **Kontrakt:** metadane `sent_chars` + `parts` (`null` ⇔ `truncated: false`; zakresy w
      znakach Unicode, `[start, end)`, względem `text` po stronie klienta — serwis przesuwa je o
      wiodące białe znaki zdjęte przez `strip`). Przycinamy **tylko** wejście modelu: `text`
      w `/extract-and-summarize` zostaje pełny (DOKUS zapisuje go do wyszukiwarki). Walidacja
      proporcji (422) we wspólnej bazie `TruncationParams` modeli żądań.
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

**Gotcha configu 2 — powłoka przebija `.env`.** Przy interpolacji `${VAR:-default}` Compose
stawia **zmienną środowiskową powłoki wyżej niż plik `.env`**, cicho i bez ostrzeżenia. Wystarczy
`set -a; . ./.env; set +a` (kuszące przy `curl`u diagnostycznym), by reszta sesji miała
zamrożone stare wartości: edycja `.env` przestaje cokolwiek zmieniać, a `up -d` odtwarza kontener
ze starym ENV. Stąd zasada: **weryfikuj `docker compose config`, nie zawartość `.env`** — `config`
pokazuje wynik interpolacji, czyli to, co naprawdę trafi do kontenera (procedura w README).
Do odczytu jednej zmiennej bez skutków ubocznych: `V=$(grep -oP '^VAR=\K.*' .env)`.

## Kontener `ollama` — lokalny Bielik (opcjonalny)

Lokalny model to **Ollama serwująca Bielika**, dokładana jako warstwy compose. Przełączenie
dostawcy = **tylko konfiguracja** (`LLM_PROVIDER`+ENV); logika ekstrakcji/promptów/API się nie
zmienia. Ollama wystawia endpoint **zgodny z API OpenAI** (`/v1`), więc `OpenAILLMClient` gada
z Bielikiem bez zmian w kodzie — wystarczy `LLM_BASE_URL` + `LLM_MODEL`.

**Trójwarstwowy układ compose** (rozdziela „CZY Bielik" od „JAK liczy"; bez profili; warstwa
produkcyjna jest wobec nich rozłączna — patrz „Warstwa produkcyjna"):
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
  trzyma luźno); on-prem/GPU → `bielik-11b-v3.0-instruct` (okno 32K, ściśle trzyma format —
  zmierzone: 18/18 na wypunktowaniu). Przy mniejszym oknie obniż `LLM_MAX_INPUT_CHARS`.
- **CPU liczy wolno** (rzędu minut/dokument) → podnieś `LLM_TIMEOUT_SECONDS`; na GPU domyślne
  wystarcza.
- Obraz przypięty `ollama/ollama:0.31.1`. Modele w wolumenie `ollama-models`
  (`docker compose -f docker-compose.bielik.yml exec ollama ollama pull <tag>`), przeżywają restart.

## Warstwa produkcyjna — `docker-compose.prod.yml`

Baza jest devowa **nie** przez bind-mounty (kod jest wpieczony: `COPY app ./app`; stąd konwencja
„po zmianie `api/` przebuduj obraz"), tylko przez **publikację portów usług wewnętrznych**.
`docker-compose.prod.yml` (`include` bazy, rozłączny z warstwami Bielika) to zdejmuje. Procedura:
**README → „Wdrożenie produkcyjne (serwer on-prem)"**.

**Pułapka — `ports` się SKLEJAJĄ, nie nadpisują.** Warstwa nie może „poprawić" portu z bazy:
`docker compose config` pokaże wtedy DWA wpisy, a stary `0.0.0.0` zostanie otwarty. Stąd dwa
różne mechanizmy, świadomie:
- `tika` → `ports: !reset []` (kasuje listę z bazy; wymaga Compose ≥ 2.24). Port nie ma na
  produkcji konsumenta — FastAPI woła Tikę po sieci compose (`http://tika:9998`), a Tika parsuje
  dowolne pliki bez uwierzytelnienia. Skutek uboczny: **testy integracyjne** (biją w
  `localhost:9998`) na tej warstwie nie przechodzą — zamierzone.
- `fastapi` → adres nasłuchu z ENV **w bazie** (`${BIND_ADDR:-0.0.0.0}`). `!reset` odciąłby
  jedynego klienta (DOKUS), a nadpisanie z warstwy by się skleiło — więc sterujemy tym stąd.

Ollama, gdyby wróciła na tę maszynę, wymaga własnego `!reset` (jej port też jest tylko pod debug).

**Limity zasobów świadomie pominięte** — zgadnięty limit pamięci ubija OCR dużego PDF-a przez OOM
w środku żądania. Do ustawienia po pomiarze na docelowej maszynie, nie „na oko".

Baza obrazu FastAPI przypięta **digestem** (`python:3.12-slim@sha256:...`), nie samym tagiem —
ruchomy tag dawałby przy rebuildzie na serwerze inny obraz niż testowany.

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

## TODO — przed wdrożeniem produkcyjnym

Pkt 1 = zadanie w toku (nowa funkcja); dalej luki „ostatniej mili" (system dla urzędu), kolejność wg wagi:

1. **[W TOKU] Endpoint klasyfikacji `POST /classify` — wybór jednej opcji z listy albo żadnej.**
   Zgłoszenie DOKUS „[UKE][AI] Inteligentna dekretacja": dokument przychodzący ma **bez udziału
   człowieka** trafić na stanowisko merytoryczne. Gdy model zawiedzie, dokument trafia do dekretacji
   ręcznej, jak dziś. **Etapu zatwierdzania nie ma.** Decyzję dwuszczeblową (grupa → stanowisko)
   prowadzi DOKUS: to dwa żądania do tego samego endpointu. **Usługa nie zna pojęć „grupa" ani
   „stanowisko"** — dostaje płaską listę opcji (`id` nieprzezroczysty: int albo string, traktowany jak
   klucz; `description` i `examples` pisze urząd, `examples` bywa `null`), podsumowania plików dokumentu
   (wynik naszego `/summarize`: pismo główne + załączniki) — i nic więcej. Jedno żądanie = jedno
   wywołanie modelu = jeden wybór.

   Wymogi nośne:
   - **`OPT-00` („brak dopasowania") to jedyna siatka bezpieczeństwa całego mechanizmu.** Usługa
     dokleja ją zawsze sama, jako jawną pozycję listy w prompcie; nie jest wierszem katalogu DOKUS-a.
     Samo dopuszczenie `null` w schemacie nie wystarcza: postawiony przed zamkniętą listą model
     wybiera najmniej złą opcję i podaje ją z pewnością;
   - model widzi krótkie etykiety (`OPT-1`…`OPT-n`, `OPT-00`), **nigdy** surowych `id` z bazy;
   - parametry za dok. projektowym (rozdz. 3.4): `temperature=0`, `max_tokens` tylko na to, co ma
     wrócić, limit czasu roboczo 60 s; struktura odpowiedzi wymuszona (OpenAI oraz Ollama/Bielik);
   - odpowiedź niedającą się sparsować da się odróżnić od poprawnej (DOKUS zapisuje ją w dzienniku
     i ponawia);
   - **oba prompty (systemowy + użytkownika), surowa odpowiedź i model wracają zawsze** — to stały
     element kontraktu (dziennik audytu DOKUS-a, rozdz. 5 dok. projektowego), nie tryb debug;
   - kody błędów jak w istniejących endpointach (413/422/500/502/503/504), `X-Request-ID` jak dotąd.

   **Świadomie NIE robimy (decyzje zapadły):** uwierzytelniania (izolacja sieciowa; pkt 5 zostaje
   blokerem wdrożenia); pola pewności (samoocena modelu jest źle skalibrowana — gdy `OPT-00` nie
   wystarczy, właściwa droga to logprobs albo człowiek z powrotem w pętli, nie próg); ponowień w usłudze
   (robi je task DOKUS-a); endpointu schodzącego samodzielnie z grupy na stanowisko; zmian promptu
   streszczeń (mechanizm jest jeden dla wszystkich podsumowań — braki zgłaszać wnioskiem);
   systematycznej oceny wyborów (w tym kroku wystarczy sprawdzian ręczny, golden set później).

   Kroki w kolejności wykonania; checkbox kroku = kod i testy gotowe. Decyzje, bez których kroku nie
   da się zrobić, stoją na jego początku — też do odhaczenia.

   - [x] **Krok 1. Kontrakt `POST /classify` — zamrożony 2026-09-16** (potwierdzony przez DOKUS;
     zmiany tylko za zgodą obu stron). Źródło prawdy: README „POST /classify" + modele w
     `api/app/models.py`. Status i adnotację „W przygotowaniu" w README zdjąć po implementacji
     (krok 12). Z kodu nie wynika:
     - (f) **Każda odpowiedź modelu to `200` + `outcome`, 5xx = odpowiedzi modelu nie było** — kod
       HTTP wyznacza politykę ponowień DOKUS-a bez czytania ciała. Nie „poprawiać" na `502`.
     - (g) **Za długi prompt → 413, nigdy ucinanie** (ucięcie opcji zmienia zbiór wyboru). Pułapka:
       Ollama sama ucina prompt od początku, razem z instrukcją o `OPT-00`, a `enum` i tak wymusi
       etykietę → cichy `matched`. Straż działa tylko przy `LLM_MAX_INPUT_CHARS` dobranym do realnego
       `num_ctx` (krok 12).

   - [x] **Krok 2. `LLMClient` — wymuszanie struktury odpowiedzi** (2026-09-16). `complete(json_schema=…)`
     → `response_format` typu `json_schema` (strict), jedna ścieżka w `OpenAILLMClient` dla OpenAI
     i Ollamy. Z kodu nie wynika:
     - **Etykieta jako `enum`, uzasadnienie przed etykietą** — zmierzone na Bieliku 4.5B (Ollama 0.31.1)
       i `gpt-4o-mini`: schemat egzekwowany, klucze w kolejności schematu. Odrzucone: tool calling
       (Bielik w Ollamie go nie obsługuje), `json_object` (nie gwarantuje pól ani kolejności),
       `pattern` zamiast `enum`.
     - **Świadomy koszt `enum`:** maskuje dezorientację modelu (w teście złośliwym uzasadnienie kończyło
       się „…będzie OPT-7", a `enum` wymusił `OPT-1`). Stąd straż długości promptu (krok 1 (g)) jest
       obowiązkowa, a ścieżka nieznanej etykiety w parserze zostaje tylko dla zapleczy, które schematu
       nie egzekwują.
     - `fake` przy schemacie wybiera **pierwszą** wartość `enum` — wynik na `fake` zależy od pozycji
       `OPT-00` (krok 4 (b), krok 10).

   - [x] **Krok 3. Limit czasu i ponowienia** (2026-09-17). Z kodu nie wynika:
     - **Bez osobnego limitu dla klasyfikacji — wspólny `LLM_TIMEOUT_SECONDS`.** Klasyfikacja nie trwa
       dłużej niż streszczenie na tym samym modelu (wejście pod tym samym sufitem `LLM_MAX_INPUT_CHARS`,
       odpowiedź ~100 tokenów zamiast ~300), więc limit dobrany pod streszczenia wystarcza. Ostrzejszy
       dałby tylko szybsze 504, a na wolnym sprzęcie fałszywe (Bielik 4.5B na CPU: 39–59 s na pismo).
       Parametr `timeout` w `complete()` dołożyć dopiero przy realnej potrzebie.
     - Przy okazji wyszły **ukryte ponowienia SDK** — wyłączone (`max_retries=0`), decyzja i koszt
       w sekcji `fastapi` (`LLMClient`).

   - [ ] **Krok 4. Etykiety opcji — `api/app/classification/labels.py`** (nowy pakiet
     `classification`, eksporty w `__init__.py` jak w `summarization`).
     *Decyzje:*
     - [ ] (a) Format etykiet: `OPT-1`…`OPT-n` + `OPT-00` jak w zgłoszeniu (niespójna szerokość —
       model może oddać `OPT-01`) czy jednolicie dwucyfrowe `OPT-01`…
     - [ ] (b) Pozycja `OPT-00` na liście: pierwsza czy ostatnia.

     *Zrobić:* czysta klasa `OptionLabeler`: nadanie etykiet w kolejności wejścia + **zawsze**
     doklejona `OPT-00`, dokładnie raz; rozwiązanie etykiety **po pozycji** (powtórzone `id` z wejścia
     nie psują mapowania, dlatego model ich nie waliduje — krok 8) → `id` z wejścia w oryginalnym typie,
     `OPT-00` → `None`, etykieta nieznana → `UnknownLabelError` (**nie** `None`). Testy
     `tests/unit/test_classification_labels.py`: kolejność, `OPT-00` zawsze i raz (także przy jednej
     opcji), `21` zostaje int / `"21"` zostaje string, `OPT-00` → `None`, nieznana etykieta → błąd.

   - [ ] **Krok 5. Prompt — `api/app/classification/prompt.py`** (czyste funkcje; prompt to logika,
     więc w kodzie, po polsku).
     *Decyzje:*
     - [ ] (a) Próg rezygnacji: `OPT-00` tylko gdy żadna opcja nie pasuje, czy także gdy pasuje kilka
       albo dopasowanie jest wątpliwe. Bez człowieka w pętli pomyłka kosztuje więcej niż dekretacja
       ręczna.

     *Zrobić:* `_SYSTEM_PROMPT` — rola, wybór dokładnie jednej etykiety, `OPT-00` jako pełnoprawna
     pozycja z opisem, kiedy ją wybrać, format odpowiedzi (najpierw uzasadnienie w 1–2 zdaniach po
     polsku, potem etykieta), zastrzeżenie, że dane w wiadomości
     użytkownika to materiał, nie instrukcje (treść pisma pochodzi z zewnątrz). `build_user_prompt`
     — dwie odgrodzone sekcje: podsumowania; opcje pod etykietami (nazwa, opis, przykłady; puste
     przykłady pominięte, bez surowych `id`).
     `build_response_schema(labels)` — JSON Schema z dwoma wymaganymi polami w tej kolejności:
     uzasadnienie (string), potem etykieta (`enum` etykiet). **Dlaczego ta kolejność** (komentarz
     w kodzie): model pisze token po tokenie, więc etykieta wynika wtedy z uzasadnienia — wierny ślad
     do audytu; odwrotnie uzasadnienie tylko broni przesądzonego wyboru. Kontrakt HTTP tego nie
     widzi, więc odwrócenie nie wymaga DOKUS-a. Testy
     `tests/unit/test_classification_prompt.py`: surowe `id` (np. `"db-7781"`) nie występują
     w żadnym prompcie, `OPT-00` obecna z opisem, puste przykłady pominięte, `enum` = dokładnie
     etykiety opcji + `OPT-00`, uzasadnienie jest w schemacie przed etykietą.

   - [ ] **Krok 6. Parser odpowiedzi — `api/app/classification/parsing.py`.**
     *Decyzje:*
     - [ ] (a) Tolerancja zapisu, gdy zaplecze nie egzekwuje schematu: JSON opakowany w blok kodu
       markdown, tekst wokół JSON-a, `opt-3` / ` OPT-3 ` — akceptować czy uznać za odpowiedź
       niepoprawną.

     *Zrobić:* surowa odpowiedź → uzasadnienie + etykieta; niepoprawny JSON (także ucięty przez
     `max_tokens`), brak któregoś z pól albo zły typ → `InvalidModelResponseError` z przyczyną. Testy
     `tests/unit/test_classification_parsing.py`: poprawny, `OPT-00`, śmieci, JSON ucięty w środku
     uzasadnienia, brak etykiety, brak uzasadnienia, zły typ pola.

   - [ ] **Krok 7. `ClassificationService` — `api/app/classification/service.py`.**
     *Decyzje:*
     - [ ] (a) `max_tokens`: dokładna wartość z pomiaru realnych odpowiedzi z uzasadnieniem (punkt
       wyjścia ok. 200; zapas tak, by JSON się nie urywał — `usage.completion_tokens`
       na Bieliku i OpenAI); stała w kodzie czy ENV. Dane: w teście z kroku 2 odpowiedź z uzasadnieniem
       w 1–2 zdaniach miała 43–97 tokenów (Bielik 4.5B i `gpt-4o-mini`, krótka lista opcji).

     *Zrobić:* wynik domenowy `ClassificationResult` (`outcome`, wybrane `id` / `None`, etykieta,
     uzasadnienie, przyczyna błędu, model, `usage`, prompt systemowy, prompt użytkownika, surowa
     odpowiedź). **Spójność pól z `outcome` (tabela z kroku 1 (f)) zapewnia serwis przy składaniu
     wyniku** — np. trzy konstruktory wyniku (`matched` / `no_match` / `invalid`), każdy przyjmuje
     tylko pola swojego wariantu; model odpowiedzi HTTP tego nie waliduje (krok 8). Konstruktor
     serwisu bierze `max_prompt_chars` (z `Settings.llm_max_input_chars`, krok 1 (g)).
     Flow: `OptionLabeler` → prompty i schemat → **budżet** (`len(system) + len(user) >
     max_prompt_chars` → `PromptTooLongError` z obiema liczbami, model niewołany) →
     `complete(system, user, json_schema, max_tokens, temperature=0)` (jedyne I/O; błędy LLM
     propagują) → parser → etykieta na `id`. `InvalidModelResponseError` (parser) i `UnknownLabelError`
     (etykieta) serwis łapie i zamienia na wynik `invalid_response` z przyczyną — **nie** propagują
     (krok 1 (f)). Log bez treści pisma:
     `outcome` i etykieta, a przy `invalid_response` WARNING z przyczyną (inaczej porażka ginie za
     `-> 200`). Testy `tests/unit/test_classification_service.py` z atrapą `LLMClient` nagrywającą
     argumenty: przekazane `temperature=0`, `max_tokens` i schemat; `OPT-n` → `matched`
     z `id` n-tej opcji; `OPT-00` → `no_match` z `None`; śmieci, urwany JSON i etykieta spoza listy →
     `invalid_response` z przyczyną, promptami i surową odpowiedzią, `rationale` = `None`; `LLMError`
     propaguje; prompt o znak ponad budżet → `PromptTooLongError` i zero wywołań atrapy, prompt
     równy budżetowi → wywołanie.

   - [ ] **Krok 8. Modele API — `api/app/models.py`** (odrębne od domenowych, mapowanie `from_result`).
     Modele kontraktu powstały zaraz po zamrożeniu (przed krokami 2–7), bo nie zależą od domeny;
     do kroku 7 czeka tylko mapowanie.
     *Zrobić:*
     - [x] Modele kontraktu (2026-09-16), sekcja „Klasyfikacja" w `models.py`. **Walidacja świadomie
       minimalna — tylko struktura** (tak jak `SummarizeRequest` / `ExtractRequest`, które nie mają
       walidatorów):
       - `ClassifyOption` — `id: StrictInt | StrictStr` (alias `OptionId`; strict, bo bez tego `true`
         → 1, `21.0` → 21 i odesłalibyśmy inny klucz), `name: str`, `description: str`,
         `examples: str | None`;
       - `ClassifyRequest` — `min_length=1` na `summaries` i `options` (pusta lista opcji → 422 to
         wymóg zgłoszenia); limitów długości tu **nie** ma — budżet promptu sprawdza serwis, krok 1 (g);
       - `ClassifyResponse` — `outcome` jako `Literal` (trzy wartości w OpenAPI), `metadata` jako
         `ClassifyMetadata(model, usage: LLMUsage)`; bez walidatora spójności.

       **Świadomie usunięte (pierwsza wersja miała je, 40 testów → 16):**
       - niepuste `name` / `description` / elementy `summaries` — opcja bez treści nie psuje wyniku
         (model jej nie wybierze), a 422 wywracałoby całe wywołanie dla grupy przez jedną lukę
         w katalogu; kompletność katalogu i odsiew dokumentów bez streszczeń to strona DOKUS-a (README);
       - powtórzone `id` — etykiety rozwiązujemy po pozycji, więc duplikat nie psuje mapowania
         (wcześniejszy argument „mapowanie niejednoznaczne" był błędny);
       - walidator spójności `option_id` / `rationale` / `error` z `outcome` — obrona przed własnym
         błędem w miejscu, gdzie go nie popełniamy; `ClassifyResponse` buduje wyłącznie
         `from_result`, a spójność wynika z konstrukcji wyniku w serwisie (krok 7) i jego testów.

       Testy (16): `tests/unit/test_models_classify_request.py` (przykład z README, typ `id`
       zachowany, brak / pusta lista, brak wymaganego pola opcji, `id` bez koercji)
       i `tests/unit/test_models_classify_response.py` (nazwy pól = kontrakt, `option_id` string
       w JSON, nieznany `outcome`).
     - [ ] `ClassifyResponse.from_result(ClassificationResult)` — po kroku 7 (wymaga wyniku
       domenowego) + test mapowania trzech wyników.

   - [ ] **Krok 9. Router — `api/app/routers/classify.py` + `app.include_router` w `main.py`.**
     *Zrobić:* DI jak w `/summarize` (`_get_classification_service`, klient z `get_llm_client()`,
     `LLMConfigError` → 500, `max_prompt_chars` z `Settings.llm_max_input_chars`); mapowanie:
     `PromptTooLongError` → 413, `LLMAuthError` → 500, `LLMResponseError` / `LLMError` → 502,
     `LLMRateLimitError` → 503, `LLMTimeoutError` → 504 — wszystkie przez `HTTPException` jak dziś
     (bez promptów w ciele). Odpowiedź niepoprawna to zwykłe `200` (krok 1 (f)), router nie ma dla
     niej osobnej ścieżki. Docstring modułu `main.py` (lista endpointów). Testy
     `tests/unit/test_fastapi_classify.py` (wzorzec `test_fastapi_summarize.py`, atrapa serwisu
     przez `dependency_overrides`): 200 `matched`, 200 `no_match`, 200 `invalid_response` (prompty,
     surowa odpowiedź i `error` obecne), 413, 422, 500 / 502 / 503 / 504 z `detail` tekstem,
     `X-Request-ID`.

   - [ ] **Krok 10. Testy integracyjne.**
     *Decyzje:*
     - [ ] (a) Materiał: syntetyczny mini-katalog opcji + pisma z `samples/summarization/` (w repo)
       czy realny katalog grup i stanowisk od DOKUS-a (dane urzędu → poza repo, jak `sample_01.pdf`).

     *Zrobić:* `tests/integration/test_fastapi_classify.py` (`integration` + `integration_fastapi`):
     kontrakt end-to-end na `fake`. `tests/integration/test_classification_service.py` (`integration`
     + `integration_llm`): oczywiste dopasowanie → właściwe `id`; **osobny test siatki
     bezpieczeństwa: dokument spoza wszystkich opcji → `null`**.

   - [ ] **Krok 11. Sprawdzian ręczny na realnym modelu** (Bielik 11B przez Ollamę i OpenAI; ocena
     systematyczna świadomie później).
     *Zrobić:* kilka przypadków na obu szczeblach (grupa, potem stanowisko), w tym dokumenty spoza
     wszystkich opcji; dwa niezależne przebiegi; czytać surowe odpowiedzi, nie tylko wynik (pułapki
     metodologii z pkt 9); przy długiej liście opcji sprawdzić `usage.prompt_tokens` pod kątem
     sufitu `num_ctx`; potwierdzić, że schemat jest egzekwowany na każdym zapleczu — zwłaszcza czy
     Open WebUI `/ollama/v1` przepuszcza `response_format` (niesprawdzone w kroku 2); czy uzasadnienia
     11B są spójne (4.5B przy poprawnym `OPT-00` napisał, że „OPT-00 również nie pasuje"); sprawdzić, czy
     uzasadnienie pisane przed etykietą nie „przegaduje" modelu do opcji tam, gdzie należało wybrać
     `OPT-00` (jeśli tak — kolejność pól do odwrócenia w kroku 5, kontrakt bez zmian).

   - [ ] **Krok 12. Dokumentacja.**
     *Zrobić:* README — ewentualne nowe ENV w „Konfiguracji" (krok 7 (a)), szybki sprawdzian `curl` na `/classify`;
     `LLM_MAX_INPUT_CHARS` w tabeli „Limity i jakość ekstrakcji" oraz komentarze w `.env.example`
     i `config.py` — nowe znaczenie „okno modelu w znakach" (`/summarize` przycina, `/classify`
     odrzuca 413); **wyraźne ostrzeżenie**: przy domyślnym `num_ctx` Ollamy (4096 ≈ 8 500 znaków)
     domyślne 90 000 nie chroni klasyfikacji — obniżyć albo podnieść `OLLAMA_CONTEXT_LENGTH`
     (procedura Bielika). CLAUDE.md — cel i przepływ danych (dochodzi klasyfikacja), „Limity — trzy
     bramki" (bramka `/classify` = odrzucenie, nie truncacja);
     sekcja `fastapi`: `ClassificationService` z jednostkami (etykiety / prompt / parser),
     rozszerzenie `LLMClient` (`json_schema`) i trwałe decyzje z kroków 1–7; ten punkt
     TODO zwinąć do tego, co zostaje.

   - [ ] **Krok 13. Klient PHP `integrations/php/DocAiClient.php`.**
     *Decyzje:*
     - [ ] (a) Dokładamy `classify()` + DTO wyniku czy nie. Według zgłoszenia DOKUS (PHP 7.4) ma
       własnego klienta `Dokus\AiUke\Client\Client`, a nasz wymaga PHP 8.1+, więc w tej integracji
       nie jest używany.

     *Zrobić:* urealnić sekcję „Klient PHP" w README i CLAUDE.md (kto faktycznie z niego korzysta);
     jeśli (a) = tak — metoda i DTO z polem `outcome` (krok 1 (f)).

   - [ ] **Krok 14. Wniosek o brakach streszczeń** (prompt streszczeń zostaje bez zmian).
     *Zrobić:* na materiale z kroku 11 ustalić, czy wybór bywa nierozstrzygalny przez to, czego
     streszczenie nie zawiera; jeśli tak — wniosek z uzasadnieniem i przykładami (prompty + surowe
     odpowiedzi). Punkt wyjścia: pkt 9 — adresata brakuje w 19/20 streszczeń golden setu.

2. **Limit stron PDF tnie PRZED decyzją o OCR — koniec długiego PDF-a i pełny `text` giną.**
   `PdfPageLimiter` bierze pierwsze `MAX_OCR_PAGES` stron, zanim plik trafi do Tiki (strategia (B)
   „limit PRZED auto"), a o OCR decyduje dopiero Tika wewnątrz żądania (`ocrStrategy=auto`, per
   strona; nasz PUA-fallback jeszcze później). Limit pomyślany jako ochrona przed kosztem OCR tnie
   więc **każdy** długi PDF — także czysto tekstowy, którego pełna ekstrakcja (PDFBox) jest tania.
   Skutki dla PDF > `MAX_OCR_PAGES` (DOCX/e-maili nie dotyczy — nie mają limitu stron):
   - **koniec pisma nie dociera do LLM** (żądanie, podpis, dane nadawcy — to, co zasila klasyfikację
     adresata): cięcie pod okno modelu, niezależnie od strategii (od początku czy
     początek/środek/koniec), działa na tekście już uciętym stronami — jego „koniec" to koniec
     strony 30, nie dokumentu;
   - **`text` w `/extract-and-summarize` nie jest pełny**, a DOKUS zapisuje go do wyszukiwarki
     pełnotekstowej. Jedyny sygnał: `extraction.ocr_truncated` + `pages_processed`/`pages_total`.

   Przy domyślnych limitach (30 stron ≈ 90 000 znaków = `LLM_MAX_INPUT_CHARS`) dla PDF-ów zwykle
   zadziała limit stron, nie znaków. Warianty (**kierunek, NIE zamknięta decyzja** — zmienia
   kontrakt z DOKUS):
   - **najpierw `no_ocr` na całym pliku** (najmocniejszy kandydat): dobra warstwa tekstowa → pełny
     tekst bez limitu stron; pusta albo PUA → cięcie do `MAX_OCR_PAGES` + OCR jak dziś. Koszt: +1
     szybkie wywołanie dla skanów. Otwarte: PDF mieszane (część stron to skany) — `no_ocr` zwróci
     tylko część treści, potrzebne kryterium „warstwa dobra" (np. znaki/stronę). Skany > limit
     nadal cięte;
   - **cięcie stron na początek + koniec** (`pypdf` wybiera dowolne strony): koniec pisma wraca, ale
     `text` ma lukę (znacznik trafia do wyszukiwarki) i potrzebne metadane „które strony";
   - **wyższe `MAX_OCR_PAGES`**: tylko przesuwa próg, wydłuża OCR, ryzyko timeoutu Tiki.

   Pełny tekst długich **skanów** pod wyszukiwarkę wymaga przetworzenia wszystkich stron → kolejka
   (pkt 8).
3. **Niespójny kształt błędów — ten sam `422` raz z `detail`-tekstem, raz z `detail`-tablicą.**
   Nasze `HTTPException` oddają `detail` jako **tekst**; walidacja żądania (domyślny handler FastAPI,
   do którego deleguje `log_validation_error`) oddaje **listę** błędów pydantic. Dla 413/5xx kształt
   jest stały, ale **422 ma oba**: tablica przy walidacji (brak pola, zły typ, proporcje trunkacji),
   tekst z domeny (złe base64, pusty plik, Tika odrzuciła plik, puste wejście). Skutki:
   - kod statusu nie wyznacza kształtu — konsument musi sprawdzać typ. Klient PHP robi to od
     2026-09-14 (`ApiException::formatValidationErrors` skleja listę w `loc: msg; …`), wcześniej
     `detail` przy walidacji był `null`, a przyczyna tylko w surowym ciele;
   - **OpenAPI mija się z odpowiedziami**: schemat 422 to `HTTPValidationError` (`detail: array`),
     a 422 z domeny zwraca string — klient generowany z OpenAPI wywróci się przy deserializacji.

   Kierunek (**decyzja do podjęcia**): handler walidacji zwraca `{"detail": "<loc: msg; …>",
   "errors": [...]}` — `detail` zawsze tekstem, struktura w `errors` (duch RFC 9457) — plus poprawiony
   schemat 422 w OpenAPI. Koszt: kształt 422 walidacji zmienia się niewstecznie zgodnie i to
   **świadome złamanie** zasady „handlery nie zmieniają odpowiedzi" (sekcja `fastapi` wyżej). Tanio,
   póki konsument jest jeden, a jego klient PHP obsługuje już oba kształty. Pełne RFC 9457
   (`application/problem+json` dla wszystkich błędów) rozważone — większe niż potrzeba.
4. **Cicha częściowa ekstrakcja — Tika zwraca `200` z urwanym tekstem.** Gdy parser wywróci się
   w środku pliku, Tika oddaje treść sprzed miejsca awarii, a błąd zapisuje **wyłącznie w metadanych**
   (`X-TIKA:EXCEPTION:container_exception`). `TikaClient`/`ExtractionService` kluczy
   `X-TIKA:EXCEPTION:*` **nie czytają** → niepełny tekst idzie do LLM bez flagi i bez logu, a
   streszczenie brzmi pewnie. Łamie to zasadę „cięcie nie ciche" (jak `ocr_truncated`). Kierunek:
   WARNING w logu + flaga w metadanych ekstrakcji (częściowy tekst wciąż użyteczny → nie błąd HTTP).

   Zmierzone (2026-09-14) na `samples/summarization/03_faktura_vat.docx`: generator pism wpisał do
   pogrubienia funkcję JS zamiast wartości (52× `<w:b w:val="function bold() { [native code] }"/>`) →
   Apache POI pada na tabeli, tekst urywa się tuż przed nią (brak pozycji, sumy 9 094,62 zł, kwoty
   słownie, uwag i podpisu). Wadliwy tylko ten plik (jedyny z tabelą) — **do naprawy**. **Skutek dla
   ewaluacji (pkt 9):** przykład „wartość faktury ginie" to najpewniej artefakt ekstrakcji, nie wada
   modelu — przez pipeline model tej kwoty w ogóle nie dostaje (cena oferty i wynagrodzenie
   z zaświadczenia pozostają ważne).
5. **[BLOKER] Uwierzytelnianie / autoryzacja API.** Kanał DOKUS↔FastAPI jest dziś otwarty —
   dla pism urzędowych twardy warunek wdrożenia. Do zrobienia przed resztą.
6. **Audyt plumbingu configu — częściowo zrobione.** Spójności `.env.example` ↔ compose
   `environment` ↔ `Settings` pilnuje `tests/unit/test_config_plumbing.py` (cztery niezmienniki,
   parsuje pliki jako dane — bez Dockera). Złapał już dwa realne rozjazdy: `LLM_API_VERSION`
   wstrzykiwany w próżnię (usunięty) i `OLLAMA_PORT` poza szablonem. **Zostaje**: weryfikacja, że
   pokrętło realnie *działa* w runtime (test sprawdza przepływ nazw, nie zachowanie) — np.
   nieprzekazany kiedyś `LLM_TIMEOUT_SECONDS` dziś zostałby złapany, ale zły typ/jednostka nie.
7. **Truncacja długich pism = ryzyko jakości — częściowo zrobione.** Końcówka pisma (termin,
   podpis, rygor) już dociera do modelu: cięcie początek / środek / koniec (`TextTruncator`) zamiast
   samego początku. **Zostaje:** środek dokumentu poza jednym fragmentem wciąż ginie (decyzja
   chunking/map-reduce vs świadomy limit nadal otwarta), a dla PDF-ów ponad `MAX_OCR_PAGES`
   cięcie działa na tekście już uciętym stronami (pkt 2).
   **Rozjazd bramek jest realny:** `MAX_OCR_PAGES=30` przepuszcza ~90 000 znaków ≈ 33 000 tokenów
   — nie mieści się w ŻADNYM oknie Bielika 11B (max 32 768, a i to z przelewem VRAM).
8. **Async / kolejka pod wolumen.** Pipeline jest synchroniczny i blokujący (OCR+LLM sekwencyjnie,
   rzędu minut/dokument nawet na GPU). Przy realnym ruchu ESOD potrzebna kolejka (np. RabbitMQ).
9. **Ewaluacja jakości streszczeń — pierwszy pomiar JEST, automatu (harnessu) wciąż brak.** To serce
   produktu; mierzyć, nie „na oko". Powstał **golden set 20 syntetycznych pism** w
   `samples/summarization/` (`01_…`–`20_….docx`) — syntetyczne, więc świadomie **poza `.gitignore`**,
   trzymane w repo (stary `samples/*`-ignore i README świadomie skasowane — `samples/` **nie jest już
   w ogóle ignorowane**; realny wrażliwy `samples/sample_01.pdf` z testu integracyjnego trzymamy poza
   repo **ręcznie**, gitignore już go nie chroni). Pierwszy przebieg
   **LLM-as-judge** (sędzia: Claude Fable 5, będący **zarazem autorem** pism; ocena 0–5 za
   fakty/kompletność/halucynacje/użyteczność) spisano w `raport_ewaluacja_goldenset.docx`. Średnia
   **~4,1/5**. Zastrzeżenia do metody: **jeden** przebieg, sędzia = autor dokumentów (nie niezależny),
   brak „złotych" streszczeń jako odniesienia — to zalążek, nie harness.

   Zmierzone wady (prompt 5-polowy, `temperature=0`):
   - **twardych halucynacji brak** (0/20 dopisanych faktów — „niczego nie zmyślaj" działa; kwoty,
     sygnatury, daty przepisywane bezbłędnie, proste inferencje dat poprawne), ALE **obowiązkowe pole
     wymusza konfabulację** przy piśmie o stanie dokonanym: uchwała wspólnoty (dok. 17) → „Oczekiwana
     akcja: zatwierdzenie uchwały", choć uchwała już podjęta, oraz „realizacja od maja" zamiast „wzrost
     zaliczki od maja". To ta sama wada „pięć wierszy → pięć wypełnień" z macierzy formatu (dawniej:
     zmyślone „potwierdzenie obecności");
   - **myli/rozmywa semantykę pól** — dawniej podstawa prawna („art. 28 Prawa budowlanego") w „Termin /
     data", a właściwy termin w „Oczekiwana akcja"; w raporcie **niejednoznaczne „Termin / data"** (raz
     data pisma, raz termin merytoryczny, raz oba, raz brak) → bez rozdzielenia „daty pisma" od
     „kluczowych terminów" pole daje szum w metadanych;
   - **systematyczne pomijanie kwot** — kwota przeżywa tylko, gdy zmieści się w „Czego dotyczy" /
     „Oczekiwana akcja"; poza tym ginie (wartość faktury, cena oferty, wynagrodzenie z zaświadczenia);
   - **systematyczny brak adresata** — 19/20 bez adresata, a to on decyduje o **routingu** dekretacji;
     model potrafi go wyciągnąć (dok. 16 sam dodał „Adresaci") — blokuje go zamknięta lista pól;
   - **łamanie kontraktu formatu** — 4 pisma (08/12/16/20) dołożyły pola spoza listy („Zarzuty",
     „Załącznik", „Adresaci"…): merytorycznie cenne, dla parsera groźne (niezdefiniowane klucze).

   Wady dotykają tego, po co produkt istnieje: **do kiedy**, **co zrobić**, **do kogo**, **za ile**.
   Sprawdzian formatu jest na nie ślepy. Rekomendacja raportu (**kierunek, NIE zamknięta decyzja** —
   zmiana promptu jest świadoma, patrz `SummarizationService`): rozszerzyć schemat pól (dodać
   Adresat / Sygnatura / Kwota, rozdzielić „Datę pisma" od „Kluczowych terminów", dodać „Inne istotne")
   i dać „Oczekiwanej akcji" wyjście dla pism informacyjnych/dokonanych („do wiadomości / brak wymaganej
   akcji"). Dalej: **≥2 niezależne przebiegi**, **niezależny sędzia**, „złote" streszczenia jako
   odniesienie; próbka musi zawierać pismo z tabelą, jednozdaniową notatkę i pismo długie. Osobno: 4.5B
   trzyma format luźniej niż 11B.

   **Metodologia — pułapki zmierzone na własnej skórze (2026-07-08):** walidator formatu potrafi
   potwierdzać to, czego szukasz (`akapit=TAK`, bo `Typ pisma:` zaczyna się wielką literą); trzy
   pisma to nie próbka (czwarte obaliło poprawkę); **`temperature=0` NIE gwarantuje determinizmu**
   w Ollamie — powtórzenia w jednej sesji próbkują ten sam bufor prefiksu, więc mierzą zero, a ta
   sama komórka potrafi dać `3/3` i `0/3` w dwóch przebiegach. Stąd wymóg: **dwa niezależne
   przebiegi** i **zawsze czytać surowe odpowiedzi**, nie tylko licznik.
10. **Obserwowalność.** Poza request-id brak metryk/tracingu → diagnoza „czemu streszczenie wyszło
   źle" trudna. Monitoring (np. Zabbix) + logi jakościowe.

## Świadomie pominięte (NIE dodawać bez pytania)

- Chmurowy przystanek LLM (np. RunPod) — celowo pominięty; łamałby „prywatność pierwsza",
  a on-prem jest celem końcowym.
- LiteLLM (unifikacja dostawców) oraz LangChain/LangGraph (orkiestracja, RAG).
- Wydzielenie OCR do osobnego kontenera (np. OCRmyPDF).

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
