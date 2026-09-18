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
modele API są **odrębne** od domenowych (kontrakt HTTP stoi niezależnie od ewolucji domeny) —
pakiet `app/models/`, moduł na endpoint jak w `app/routers/` (+ `truncation.py`, baza żądań dwóch
endpointów), importy przez re-eksport w `__init__`.

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
    `summary`). Prompt = logika → w repo i obrazie, nie w ENV (`SummarySystemPrompt` /
    `SummaryUserPrompt`, patrz „Prompty" niżej). **Akapitu otwierającego
    świadomie NIE ma**: model naśladuje najbardziej konkretny wzorzec w prompcie (lista pól), a
    „napisz akapit" to tylko opis — dawał się wymusić wyłącznie przykładem, i to najpewniej dopiero
    jako tura `assistant` (zmiana interfejsu `LLMClient`). Macierz siedmiu wariantów × sześć pism ×
    dwa przebiegi: komentarz w `app/prompt/summary_system.md`. Strażniki: `tests/unit/test_summarization_prompt_system.py`
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

**Prompty — teksty w `app/prompt/`, klasy w domenach** (2026-09-17). Tekst promptu to plik
`app/prompt/<domena>_<rola>.md`; klasa, która go wypełnia, leży w pakiecie domeny — **plik na
prompt** (`summarization/prompt_system.py`, `prompt_user.py`), dziedziczy `PromptTemplate`
(`app/prompt/template.py`), deklaruje `FILE`, placeholdery jako stałe z komentarzem i własne
`render(...)` z jawnymi argumentami — i **zwraca w pełni gotowy prompt** (formatowanie danych, np.
listy opcji, też w klasie, nie w serwisie). Serwis tworzy instancje przy imporcie → brak pliku /
rozjazd placeholderów / zły komentarz wywala start.
- **`.md` = surowy tekst**: model dostaje bajty pliku, nie wyrenderowany Markdown — każda
  „kosmetyka" (wcięcia, `##`, pogrubienia) to zmiana promptu, a forma promptu przechodzi na wyjście
  (macierz w `summary_system.md`). LF wymusza `.gitattributes`; `\n` na brzegach zdejmowane.
- **Uzasadnienie treści w pliku, jako komentarz `<!-- … -->`** — obok tekstu, którego dotyczy;
  wycinany przy wczytaniu, przed sprawdzeniem placeholderów. Zwięźle: decyzja + dowód, bez eseju.
  Komentarz zajmuje **całe linie** i znika razem z nimi (puste linie wokół zostają → w środku
  tekstu przyklejać do akapitu); w linii z tekstem / niedomknięty / z `-->` w treści → błąd, nie
  wycinanie „na oko". Wycinamy tylko z szablonu — `<!--` w treści pisma przechodzi nietknięte.
- **Placeholder `{{nazwa}}`, stała = pełny token** (`TEXT = "{{text}}"`, wyszukanie trafia w plik
  i klasę; stała bez klamer nie zgodzi się z plikiem → błąd startu). Nie `str.format` (wywraca się
  na `{` w tekście); podstawienie **jednym przebiegiem** (`re.sub` z funkcją) — `{{…}}` w treści
  pisma zostaje dosłownie, a kolejne `replace` przeszukałyby wynik poprzedniego. Zbiór tokenów
  w pliku = `PLACEHOLDERS`.
- **Dlaczego klasy w domenach, nie w `app/prompt/`:** prompt klasyfikacji formatuje typy domeny
  (`LabeledEntry`), a serwis importuje prompt; `__init__` pakietów re-eksportuje serwisy, więc klasa
  w `app/prompt/` dałaby cykl importów zależny od kolejności (zmierzone na makiecie). Zależności
  w jedną stronę: domeny → `app.prompt`, nigdy odwrotnie. Odrzucone: import pod `TYPE_CHECKING`
  (ukrywa dwukierunkową zależność), puste `__init__` (zmiana konwencji całego projektu), `Protocol`
  na wejście promptu (drugi opis pól opcji).

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
     `api/app/models/classify.py`. Status i adnotację „W przygotowaniu" w README zdjąć po implementacji
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
     - `fake` przy schemacie wybiera **pierwszą** wartość `enum`. Przy `OPT-00` na końcu (krok 4) to
       `OPT-1`, więc na `fake` wychodzi `matched` z `id` pierwszej opcji (krok 10).

   - [x] **Krok 3. Limit czasu i ponowienia** (2026-09-17). Z kodu nie wynika:
     - **Bez osobnego limitu dla klasyfikacji — wspólny `LLM_TIMEOUT_SECONDS`.** Klasyfikacja nie trwa
       dłużej niż streszczenie na tym samym modelu (wejście pod tym samym sufitem `LLM_MAX_INPUT_CHARS`,
       odpowiedź ~100 tokenów zamiast ~300), więc limit dobrany pod streszczenia wystarcza. Ostrzejszy
       dałby tylko szybsze 504, a na wolnym sprzęcie fałszywe (Bielik 4.5B na CPU: 39–59 s na pismo).
       Parametr `timeout` w `complete()` dołożyć dopiero przy realnej potrzebie.
     - Przy okazji wyszły **ukryte ponowienia SDK** — wyłączone (`max_retries=0`), decyzja i koszt
       w sekcji `fastapi` (`LLMClient`).

   - [x] **Krok 4. Etykiety opcji — `classification/service_labels.py`** (2026-09-17). `OptionLabeler`
     + domenowy `ClassificationOption` (domena nie importuje modeli API). Z kodu nie wynika:
     - **`OPT-1`…`OPT-n` + `OPT-00`, jak w zgłoszeniu.** Odrzucone jednolite `OPT-01`…: obawa „model
       odda `OPT-01`" nie dotyczy zapleczy z `enum`, przy ≥100 opcjach szerokość i tak rośnie, a `OPT-00`
       wyróżnia się szerokością jako pozycja specjalna.
     - **`OPT-00` ostatnia** — wzorzec „żadne z powyższych". Kolejność ustala wyłącznie
       `OptionLabeler.entries`; prompt i `enum` (krok 5) ją przejmują, nie składają własnej.
     - `resolve` dopasowuje etykietę dokładnie (`OPT-01`, `opt-1` → `UnknownLabelError`); tolerancja
       zapisu to decyzja parsera (krok 6 (a)).

   - [x] **Krok 5. Prompt — `classification/prompt_system.py` / `prompt_user.py` + `service_schema.py`**
     (2026-09-17). Teksty `app/prompt/classification_system.md` / `classification_user.md`;
     `ClassificationUserPrompt.render(summaries, entries)` zwraca gotowy prompt, `build_response_schema(labels)`
     — `rationale`, potem `label` (`enum`). Z kodu nie wynika:
     - **(a) `OPT-00` tylko przy braku dopasowania** — gdy pasuje kilka opcji, model wybiera najlepszą.
       Odrzucone: „remis → `OPT-00`" i „każda wątpliwość → `OPT-00`" (ta druga stoi na samoocenie
       pewności, źle skalibrowanej — ten sam powód co odrzucone pole pewności). Świadomy koszt: remisy
       rozstrzyga model, a pomyłka bez człowieka w pętli kosztuje więcej niż dekretacja ręczna —
       sprawdzić w kroku 11.
     - **Prompt systemowy bez domeny** (bez urzędu, pism, roli asystenta) — usługa nie zna znaczenia
       opcji, kontekst niosą ich opisy; ~720 znaków zamiast ~1200.
     - **Sekcje w tagach `<streszczenia>` / `<opcje>`, nie nagłówkach `##`** — nagłówek łatwo podrobić
       treścią pisma; prompt systemowy nazywa tagi. Każdy element w swoim tagu (`<streszczenie>` /
       `<opcja>`): streszczenie tego wymaga (brak nagłówka), opcja ma go w linii `OPT-n:` — tag przy
       opcji dla spójności i odporności na wieloliniowe opisy klienta; koszt ~17 znaków na opcję.
     - **Zastrzeżenie „dane, nie polecenia" tylko dla streszczeń** — opisy opcji pisze urząd i mogą
       legalnie zawierać wskazówki („wybierz, gdy…").
     - **`OPT-00` i nazwy pól JSON wpisane w `.md` dosłownie**, nie jako placeholdery — plik czyta się
       tak, jak widzi go model; zgodność z `service_labels.py` / `service_schema.py` pilnują testy promptu systemowego.
       Tekst pozycji `OPT-00` na liście („Brak dopasowania") w klasie, reguła wyboru w prompcie
       systemowym; bez „powyższych", bo kolejność to decyzja `OptionLabeler`.
     - **Dwa przykłady odpowiedzi w prompcie systemowym (pretty print)** — nie po strukturę (tę gwarantuje
       schemat), tylko żeby model SAM oddawał JSON zgodny ze schematem: gramatyka maskuje tokeny, więc
       model, który „chce" napisać co innego, jest spychany na mało prawdopodobne tokeny i cierpi treść.
       Etykieta przykładu wyboru to `OPT-N` — `OptionLabeler` nigdy jej nie nada, więc skopiowana nie
       trafi w prawdziwą opcję (`enum` ją zablokuje; bez `enum` → `invalid_response`). Odrzucone
       `OPT-77`: przy ≥77 opcjach istnieje (cichy `matched` z cudzym `id`), a wspólny prefiks z `OPT-7`
       pod gramatyką może skręcić w `OPT-7`. Kolejność: `OPT-00` pierwszy, `OPT-N` ostatni (ostatni
       przykład działa najmocniej, a `OPT-00` zawsze istnieje i ciągnie ku rezygnacji).
     - Znane ograniczenie: streszczenie zawierające `</streszczenia>` rozszczelnia sekcję (bez
       escapowania). Skutek ograniczony do wyboru spośród opcji z listy (`enum`).

   - [x] **Krok 6. Parser odpowiedzi — `classification/service_parsing.py`** (2026-09-17). `parse_response(raw)`
     → `ParsedResponse(rationale, label)`; każda wada → `InvalidModelResponseError`, komunikat = `error`
     odpowiedzi. Etykiety nie rozwiązuje (zwraca dosłownie, listę zna `OptionLabeler`). Z kodu nie wynika:
     - **(a) Ściśle, bez tolerancji zapisu** — blok kodu markdown, tekst wokół JSON-a → niepoprawny
       JSON; `opt-3` / ` OPT-3 ` odpadają na `resolve`. OpenAI i Ollama egzekwują schemat (tolerancja
       nigdy by się tam nie uruchomiła); zaplecze gubiące schemat (Open WebUI, krok 11) wychodzi od
       pierwszego żądania, zamiast zostać przykryte. Tolerancję dokładać na podstawie zmierzonych
       surowych odpowiedzi, nie zgadywanych. Odrzucone: wyłuskiwanie pierwszego obiektu z tekstu (przy
       dwóch obiektach bierze nie ten → zły wybór bez człowieka w pętli).
     - Z tego samego powodu **powtórzony klucz → błąd** (`json.loads` po cichu bierze ostatni).
     - **Struktura, nie treść** (jak model API): pola nadmiarowe ignorowane, puste uzasadnienie przyjęte.
       Pusta odpowiedź (odmowa / filtr → `""`) ma własną przyczynę zamiast mylącego „Expecting value".

   - [x] **Krok 7. `ClassificationService` — `classification/service.py`** (2026-09-18). Wynik
     domenowy `ClassificationResult` w `model.py` (trzy konstruktory: `matched` / `no_match` /
     `invalid`, każdy bierze tylko pola swojego wariantu), wyjątki pakietu w `exception.py`.
     **Układ plików pakietu:** `exception.py`, `model.py`, `prompt_*.py`, `service.py` + jednostki
     z prefiksem `service_` (`service_labels.py`, `service_parsing.py`, `service_schema.py`; wcześniej
     bez prefiksu) — decyzja porządkowa, mimo że `service_labels` używają też `prompt_user` i `model`.
     Z kodu nie wynika:
     - **(a) `max_tokens` = 400, z ENV `LLM_MAX_OUTPUT_TOKENS_CLASSIFY`** (tak samo streszczenia:
       `LLM_MAX_OUTPUT_TOKENS_SUMMARY` = 600, wcześniej stała w konstruktorze). Pierwotnie stała w kodzie
       („długość wyznacza prompt"); przeniesione do ENV 2026-09-18, bo liczba tokenów tego samego tekstu
       zależy od tokenizera modelu, czyli od wdrożenia, a limit wlicza się do okna obok
       `LLM_MAX_INPUT_CHARS` — oba pokrętła stoją teraz obok siebie. Pomiar (`gpt-4o-mini`
       i Bielik 4.5B, 6 pism, katalog 8 opcji, dwa przebiegi): wybór opcji 44–62 tokeny, **ale przy
       `OPT-00` model wylicza w uzasadnieniu odrzucone opcje** (~5 tokenów na nazwę) — `gpt-4o-mini`:
       8 opcji → 84, 14 → 118 (wszystkie nazwy), 20 → 62 („itp."); Bielik do 112. 400 ≈ 3,4 ×
       maksimum. Urwany JSON przy `temperature=0` urwie się tak samo przy każdym ponowieniu DOKUS-a,
       więc zapas jest tani, a za ciasny limit — nie. Sygnał: WARNING `completion_tokens 400/400`.
     - **Budżet liczy tylko prompt, bez rezerwy na odpowiedź** — znaki vs tokeny (przelicznik zależy
       od tokenizera), a 413 w zamrożonym kontrakcie to „prompt > `LLM_MAX_INPUT_CHARS`". Rezerwę
       odejmuje się przy doborze `LLM_MAX_INPUT_CHARS` (krok 12).
     - Z serwisu propagują tylko błędy „odpowiedzi nie było" (`LLMError`, `PromptTooLongError`);
       wady odpowiedzi → wynik `invalid_response`. Log: INFO wynik + etykieta (bez uzasadnienia — cytuje
       pismo), WARNING przy `invalid_response` z przyczyną i `completion_tokens`/limit.

     **Do rozważenia (poza krokiem 7): uzasadnienie przy `OPT-00` bez wyliczania opcji.** Wyliczanie
     nic nie wnosi do audytu (lista jest w `user_prompt`), powstaje PO decyzji („…nie pasuje do
     żadnej z: …"), a jego długość rośnie z katalogiem bez sufitu. Prawdopodobny wyzwalacz:
     „(przy OPT-00: dlaczego nic nie pasuje)" w `classification_system.md` — przykład `OPT-00` jest
     krótki, a model i tak wylicza. Kierunek: sformułowanie pozytywne („jednym zdaniem: czego dotyczy
     dokument i czego brakuje w opcjach"), nie zakaz; pomiar przed / po (dwa modele, dwa przebiegi,
     katalog 8 / 14 / 20+); potem obniżyć default `LLM_MAX_OUTPUT_TOKENS_CLASSIFY` (~250).

   - [x] **Krok 8. Modele API — `api/app/models/classify.py`** (2026-09-18; modele kontraktu
     2026-09-16, mapowanie `ClassifyResponse.from_result` po kroku 7). Z kodu nie wynika:
     - **Walidacja świadomie minimalna — tylko struktura** (jak `SummarizeRequest` / `ExtractRequest`):
       `id: StrictInt | StrictStr` (strict, bo bez tego `true` → 1, `21.0` → 21 i odesłalibyśmy inny
       klucz), `min_length=1` na `summaries` i `options` (pusta lista opcji → 422 to wymóg zgłoszenia),
       bez limitów długości (budżet promptu sprawdza serwis, krok 1 (g)).
     - **Świadomie usunięte (pierwsza wersja miała je, 40 testów → 16):** niepuste `name` /
       `description` / elementy `summaries` (opcja bez treści nie psuje wyniku, a 422 wywracałoby
       wywołanie dla grupy przez jedną lukę w katalogu — kompletność katalogu to strona DOKUS-a);
       powtórzone `id` (etykiety rozwiązujemy po pozycji); walidator spójności pól z `outcome`
       (spójność wynika z konstruktorów `ClassificationResult`, krok 7).
     - `from_result` nie wystawia `label` — etykiety są wewnętrzne, audyt ma `raw_response`.

     Testy: `test_models_classify_request.py`, `test_models_classify_response.py` (nazwy pól =
     kontrakt, `option_id` string w JSON, nieznany `outcome`, `from_result` — trzy wyniki wg tabeli
     z README, audyt i metadane dosłownie).

   - [ ] **Krok 9. Router — `api/app/routers/classify.py` + `app.include_router` w `main.py`.**
     *Zrobić:* DI jak w `/summarize` (`_get_classification_service`, klient z `get_llm_client()`,
     `LLMConfigError` → 500, `max_prompt_chars` z `Settings.llm_max_input_chars`, `max_output_tokens`
     z `Settings.llm_max_output_tokens_classify` — przepływ w sekcji „DI" testu routera, jak w
     `test_fastapi_summarize.py`); `ClassifyOption`
     → `ClassificationOption` (domena); mapowanie błędów:
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
     *Już jest (2026-09-17):* oba przypadki na poziomie promptu —
     `tests/integration/test_classification_prompt.py` (prompty + schemat + `OptionLabeler` + realny
     klient, `openai` i `ollama`; syntetyczny katalog z README). Po kroku 7 przenieść na serwis.
     Pułapka: klient LLM **nowy na każdy test** — `AsyncOpenAI` wiąże pulę połączeń z pętlą zdarzeń,
     a każdy `asyncio.run` to nowa pętla; wspólny klient wywala drugi test („Event loop is closed").

   - [ ] **Krok 11. Sprawdzian ręczny na realnym modelu** (Bielik 11B przez Ollamę i OpenAI; ocena
     systematyczna świadomie później).
     *Zrobić:* kilka przypadków na obu szczeblach (grupa, potem stanowisko), w tym dokumenty spoza
     wszystkich opcji; dwa niezależne przebiegi; czytać surowe odpowiedzi, nie tylko wynik (pułapki
     metodologii z pkt 9); przy długiej liście opcji sprawdzić `usage.prompt_tokens` pod kątem
     sufitu `num_ctx`; potwierdzić, że schemat jest egzekwowany na każdym zapleczu — zwłaszcza czy
     Open WebUI `/ollama/v1` przepuszcza `response_format` (niesprawdzone w kroku 2; jeśli nie, ścisły
     parser da `invalid_response` na każdą odpowiedź zapisaną inaczej niż gołym JSON-em — krok 6 (a));
     czy uzasadnienia 11B są spójne (4.5B przy poprawnym `OPT-00` napisał, że „OPT-00 również nie
     pasuje"); sprawdzić, czy uzasadnienie pisane przed etykietą nie „przegaduje" modelu do opcji tam,
     gdzie należało wybrać `OPT-00` (jeśli tak — kolejność pól do odwrócenia w kroku 5, kontrakt bez zmian).
     **Zmierzone przy kroku 7 (a) na 4.5B — przegadanie JEST:** pismo spoza katalogu (awaria serwera)
     → „nie pasuje do żadnej z wymienionych kategorii **poza OPT-7**… może być związany z informacją
     publiczną" → `OPT-7`; uchwała wspólnoty → uzasadnienie krąży między trzema opcjami, wybór różny
     w dwóch przebiegach (3 z 6 odpowiedzi niedeterministyczne mimo `temperature=0`). `gpt-4o-mini`
     na tym samym materiale: `OPT-00` trafione, oba przebiegi identyczne. 4.5B do dekretacji bez
     człowieka się nie nadaje — pytanie, czy 11B powtarza wzorzec „żadna… poza X".

   - [ ] **Krok 12. Dokumentacja.**
     *Zrobić:* README — szybki sprawdzian `curl` na `/classify` (`LLM_MAX_OUTPUT_TOKENS_*` już w „Konfiguracji");
     `LLM_MAX_INPUT_CHARS` w tabeli „Limity i jakość ekstrakcji" oraz komentarze w `.env.example`
     i `config.py` — nowe znaczenie „okno modelu w znakach" (`/summarize` przycina, `/classify`
     odrzuca 413); **wyraźne ostrzeżenie**: przy domyślnym `num_ctx` Ollamy (4096 ≈ 8 500 znaków)
     domyślne 90 000 nie chroni klasyfikacji — obniżyć albo podnieść `OLLAMA_CONTEXT_LENGTH`
     (procedura Bielika). **Reguła doboru (rezerwa na odpowiedź, krok 7):** okno obejmuje prompt
     I odpowiedź, a żaden endpoint nie rezerwuje odpowiedzi w kodzie — dobierać pod `/summarize`
     (okno − prompt systemowy − `LLM_MAX_OUTPUT_TOKENS_SUMMARY`, × znaki/token); `/classify` zmieści się
     wtedy z zapasem, bo jego budżet obejmuje już prompt systemowy — o ile
     `LLM_MAX_OUTPUT_TOKENS_CLASSIFY` ≤ `LLM_MAX_OUTPUT_TOKENS_SUMMARY` (domyślnie 400 ≤ 600). CLAUDE.md — cel i przepływ danych (dochodzi klasyfikacja), „Limity — trzy
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
   Pierwszy krok: sekcje „DI" w `test_fastapi_summarize.py` / `test_fastapi_pipeline.py` sprawdzają,
   że limity LLM z `Settings` docierają z routera do serwisu streszczeń (testy HTTP podstawiają cały
   serwis, więc tego nie widzą). Bez takiego testu zostają ustawienia ekstrakcji (`TIKA_URL`,
   `TIKA_TIMEOUT_SECONDS`, `MAX_OCR_PAGES`, `MAX_UPLOAD_BYTES`).
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

   **Do rozważenia: streszczenie z wymuszonym schematem** (jak klasyfikacja, `json_schema`). Rozwiązałoby
   wady formatu: pola spoza listy (`additionalProperties: false`), „brak" zamiast pominięcia pola
   (zmierzone 2026-09-17 na `gpt-4o-mini` i Bieliku 4.5B: oba wpisały „Termin / data: brak"). Koszty
   i pułapki: kontrakt `summary` to jeden string — JSON trzeba składać do tekstu po naszej stronie
   albo zmienić kontrakt z DOKUS-em; strict wymaga każdego pola w `required`, więc pole nieobecne
   w piśmie musi być `null` (typ `["string", "null"]`), inaczej schemat wymusi konfabulację; gramatyka
   może pogorszyć treść, gdy model „walczy" ze schematem (lekcja z kroku 5 — przykłady w prompcie).
   Łączyć z rozszerzeniem pól z rekomendacji wyżej i mierzyć przed/po na golden secie.

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
