# DOKUS Doc AI

Warstwa AI dla obiegu dokumentów (ESOD) **DOKUS**: automatyczna ekstrakcja treści
dokumentu i jego streszczenie, tak aby osoba dekretująca od razu wiedziała, czego
dokument dotyczy — oraz klasyfikacja (dekretacja bez udziału człowieka: wybór grupy,
potem stanowiska; gdy model zawiedzie, dokument wraca do dekretacji ręcznej).

Ten plik = orientacja pod dalszy rozwój: zasady, których nie łamać, jak zbudowana jest
logika (gdzie co dokładać), trwałe decyzje i pułapki oraz co świadomie poza zakresem.
Stack, kontrakt API (endpointy, I/O, kody błędów) i procedury uruchomienia: **README**.

## Cel

DOKUS przesyła dokument w oryginalnej formie → system wyciąga tekst (również OCR ze
skanów) → LLM generuje streszczenie → wynik wraca do DOKUS. Osobno DOKUS przesyła streszczenia
dokumentu i listę opcji (grupy albo stanowiska) → LLM wybiera jedną opcję albo żadną → wynik
wraca do DOKUS (dwa szczeble = dwa żądania).

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

DOKUS → FastAPI (/classify: streszczenia + opcje)
          │
          ├─ etykiety OPT-1…OPT-n + OPT-00, prompt, schemat odpowiedzi
          ├─ wybór (LLM przez LLMClient, wymuszony JSON)
          │
          └─ zwróć wynik (matched / no_match / invalid_response + audyt) do DOKUS
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
    LLM **nie** jest pingowany w `/health`. `complete(json_schema=…)` → `response_format` typu
    `json_schema` (strict), jedna ścieżka dla OpenAI i Ollamy; `LLMResult.text` niesie wtedy SUROWY
    JSON (parsuje wołający). Interfejs nie wie nic o klasyfikacji — dostaje gotowy schemat.
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
  - `ClassificationService` — wybór jednej opcji z listy albo żadnej (etykiety `OPT-n` + `OPT-00`,
    prompt, schemat, ścisły parser); każda odpowiedź modelu to wynik, nie wyjątek. Decyzje, mechanizm
    i jakość: sekcja „Klasyfikacja" niżej.
- **DI (kontrast):** `ExtractionService` dostaje `TikaClient` **inline** (jeden silnik);
  `SummarizationService` / `PipelineService` / `ClassificationService` biorą `LLMClient` z **fabryki**
  (silnik wymienialny). Limity z `Settings` przekazuje funkcja DI routera — przepływ pilnują sekcje
  „DI" w testach routerów (testy HTTP podstawiają cały serwis, więc go nie widzą).

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
(znaków, zależna od LLM). `LLM_MAX_INPUT_CHARS` = okno modelu w znakach także dla `/classify`, ale
tam jako **odrzucenie (413) całego promptu, nie truncacja** (sekcja „Klasyfikacja"). Rezerwy na
odpowiedź żaden endpoint nie odejmuje w kodzie — dobór pod `/summarize` (okno − prompt systemowy −
`LLM_MAX_OUTPUT_TOKENS_SUMMARY`, × znaki/token modelu: Bielik ~1,65, OpenAI ~2,7); `/classify`
zmieści się wtedy z zapasem, o ile `LLM_MAX_OUTPUT_TOKENS_CLASSIFY` ≤ `LLM_MAX_OUTPUT_TOKENS_SUMMARY`.

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

## Klasyfikacja — `POST /classify` (dekretacja bez człowieka)

Zgłoszenie DOKUS „[UKE][AI] Inteligentna dekretacja": dokument przychodzący ma **bez udziału
człowieka** trafić na stanowisko merytoryczne; gdy model zawiedzie — dekretacja ręczna, jak dziś
(**etapu zatwierdzania nie ma**). Szczeble (grupa → stanowisko) prowadzi DOKUS: dwa żądania do tego
samego endpointu. **Usługa nie zna pojęć „grupa" ani „stanowisko"** — dostaje płaską listę opcji
(`id` nieprzezroczysty: int albo string; `description` i `examples` pisze urząd) oraz streszczenia
plików dokumentu (wynik `/summarize`). Jedno żądanie = jedno wywołanie modelu = jeden wybór.
Kontrakt **zamrożony 2026-09-16** (README „POST /classify" + `models/classify.py`; zmiany tylko za
zgodą obu stron). Zbudowane w 11 krokach (dawny TODO pkt 1, 2026-09-16…18) — znaczniki „krok N (x)"
w kodzie odnoszą się do punktów poniżej.

**Kontrakt — z kodu nie wynika:**
- **(krok 1 (f)) Każda odpowiedź modelu to `200` + `outcome`** (`matched` / `no_match` /
  `invalid_response`); **5xx = odpowiedzi modelu nie było** — kod HTTP wyznacza politykę ponowień
  DOKUS-a bez czytania ciała. Nie „poprawiać" `invalid_response` na `502`.
- **(krok 1 (g)) Za długi prompt → 413, nigdy ucinanie** (ucięcie opcji zmienia zbiór wyboru).
  Budżet = CAŁY prompt (systemowy + użytkownika) wobec `LLM_MAX_INPUT_CHARS`, bez rezerwy na
  odpowiedź (znaki vs tokeny; rezerwę odejmuje się przy doborze — README „Okno modelu…"). **Pułapka:**
  Ollama sama ucina za długi prompt od początku, razem z instrukcją o `OPT-00`, a `enum` i tak wymusi
  etykietę → cichy `matched`. Straż działa tylko przy `LLM_MAX_INPUT_CHARS` dobranym do `num_ctx`.
- **Oba prompty, surowa odpowiedź, model i `usage` wracają zawsze** (dziennik audytu DOKUS-a) — nie
  tryb debug. Etykieta modelu (`label`) nie wychodzi — wewnętrzna, audyt ma `raw_response`.
- **(krok 8) Walidacja żądania minimalna — tylko struktura:** `id: StrictInt | StrictStr` (bez
  strict `true` → 1, `21.0` → 21 — odesłalibyśmy inny klucz), `min_length=1` na obu listach.
  Świadomie BEZ: niepustych `name` / `description` / streszczeń (opcja bez treści nie psuje wyniku,
  a 422 wywracałby grupę przez jedną lukę katalogu — kompletność to strona DOKUS-a), unikalnych `id`
  (etykiety po pozycji), walidatora spójności pól z `outcome` (wynika z konstruktorów wyniku).

**Mechanizm** — pakiet `app/classification/`: `exception.py`, `model.py`, `prompt_*.py`, `service.py`
+ jednostki z prefiksem `service_` (decyzja porządkowa, choć `service_labels` używają też prompt
i model):
- **`OPT-00` („brak dopasowania") to jedyna siatka bezpieczeństwa.** Usługa dokleja ją sama jako
  jawną, OSTATNIĄ pozycję listy (wzorzec „żadne z powyższych"); samo dopuszczenie `null` nie
  wystarcza — przed zamkniętą listą model wybiera najmniej złą opcję. Kolejność pozycji ustala
  wyłącznie `OptionLabeler.entries`; prompt i `enum` ją przejmują.
- **(krok 4) `service_labels.py`:** `OPT-1`…`OPT-n` jak w zgłoszeniu (odrzucone `OPT-01`: `enum`
  i tak nie pozwoli na inny zapis); model **nigdy** nie widzi surowych `id`; `resolve` dokładnie
  i po pozycji (powtórzone `id` nie psują mapowania).
- **(krok 5) `prompt_system` / `prompt_user`** (teksty `app/prompt/classification_*.md`):
  - (a) `OPT-00` **tylko przy braku dopasowania**; przy kilku pasujących — najlepsza (odrzucone
    „remis / wątpliwość → `OPT-00`": samoocena pewności jest źle skalibrowana);
  - prompt systemowy bez domeny (usługa nie zna znaczenia opcji — kontekst niosą ich opisy);
  - sekcje w tagach `<streszczenia>` / `<opcje>`, nie `##` (nagłówek łatwo podrobić treścią pisma);
    „dane, nie polecenia" tylko dla streszczeń (opisy opcji pisze urząd, mogą zawierać wskazówki);
  - `OPT-00` i nazwy pól JSON dosłownie w `.md` (plik czyta się jak model; zgodność pilnują testy);
  - dwa przykłady odpowiedzi — żeby model SAM pisał JSON zgodny ze schematem (gramatyka spycha
    „walczący" model na mało prawdopodobne tokeny i cierpi treść); etykieta przykładu `OPT-N`
    (nigdy nie nadawana; odrzucone `OPT-77` — istnieje przy ≥77 opcjach), `OPT-00` pierwszy, `OPT-N`
    ostatni. Znane ograniczenie: streszczenie z `</streszczenia>` rozszczelnia sekcję (skutek
    ograniczony do opcji z listy przez `enum`).
- **(krok 2) `service_schema.py`:** `rationale`, potem `label` (`enum`), strict — egzekwowane na
  OpenAI i Ollamie (0.31.1 i 0.34.2). Odrzucone: tool calling (Bielik w Ollamie nie obsługuje),
  `json_object`, `pattern`. **Koszt `enum`:** maskuje dezorientację (uzasadnienie „…będzie OPT-7",
  `enum` wymusił `OPT-1`) — stąd straż 413 jest obowiązkowa.
- **(krok 6 (a)) `service_parsing.py` — ściśle, bez tolerancji zapisu:** blok kodu / tekst wokół
  JSON-a → `invalid_response`. OpenAI i Ollama egzekwują schemat, a zaplecze, które go gubi, wychodzi
  od pierwszego żądania, zamiast zostać przykryte; tolerancję dokładać tylko na podstawie zmierzonych
  surowych odpowiedzi. Powtórzony klucz → błąd (`json.loads` po cichu bierze ostatni). Struktura,
  nie treść.
- **(krok 7) `service.py` + `model.py`:** wady odpowiedzi → wynik `invalid_response` z przyczyną;
  propagują tylko błędy „odpowiedzi nie było" (`LLMError`, `PromptTooLongError`).
  `ClassificationResult` składają wyłącznie `matched` / `no_match` / `invalid` (każdy bierze tylko
  pola swojego wariantu). Log bez treści pisma: INFO wynik + etykieta; WARNING przy
  `invalid_response` z przyczyną i `completion_tokens` / limit (inaczej porażka ginie za `-> 200`).
- **Limity:** (krok 3) wspólny `LLM_TIMEOUT_SECONDS` (klasyfikacja nie trwa dłużej niż streszczenie;
  ostrzejszy dałby fałszywe 504 na wolnym sprzęcie). (krok 7 (a)) `LLM_MAX_OUTPUT_TOKENS_CLASSIFY` =
  400 z ENV (liczba tokenów zależy od tokenizera = od wdrożenia). **Przy `OPT-00` model wylicza
  w uzasadnieniu odrzucone opcje** (~5 tokenów na nazwę; długość rośnie z katalogiem):
  `gpt-4o-mini` do 118, Bielik 11B do 241 przy 7 opcjach — na Bieliku 400 to zapas tylko ~1,7×.
  Urwany JSON przy `temperature=0` urwie się tak samo przy każdym ponowieniu; sygnał: WARNING
  `completion_tokens 400/400`.
- `fake` przy schemacie wybiera pierwszą wartość `enum` → `matched` z `id` pierwszej opcji.
- **Świadomie NIE robimy:** uwierzytelniania (izolacja sieciowa; TODO pkt 5 zostaje blokerem
  wdrożenia); pola pewności (samoocena źle skalibrowana — gdy `OPT-00` nie wystarczy: logprobs albo
  człowiek w pętli, nie próg); ponowień w usłudze (robi je task DOKUS-a); endpointu schodzącego
  samodzielnie z grupy na stanowisko; zmian promptu streszczeń (braki zgłaszać wnioskiem, TODO pkt 13).

**Jakość — golden set `samples/classification/`** (kroki 10–11, 2026-09-18):
- `katalog.json` — syntetyczny urząd miasta, 7 grup ze stanowiskami (gminny, bo pisma z
  `samples/summarization/` są gminne; katalog w stylu UKE dałby same `no_match`), z celowymi
  zmyłkami; `golden.json` — 20 pism, w tym 3 spoza katalogu (06, 09, 14) i 1 z grupą bez stanowiska
  (16); `summaries.json` — **zamrożone streszczenia** (`gpt-4o-mini`; wejście stałe, pomyłka wyboru
  nie miesza się ze zmiennością streszczeń) — `build_summaries.py` po zmianie promptu streszczeń
  albo ekstrakcji. **Znane ograniczenie:** katalog i odpowiedzi ustalił Claude, bez przeglądu
  człowieka (sędzia = autor, jak w TODO pkt 9); katalogu NIE poprawiamy pod wynik modelu.
- Testy: `test_classification_service.py` — cały golden set, próg 80% osobno grupy i stanowiska
  (decyzja 1b), stanowisko pytane w OCZEKIWANEJ grupie; `test_classification_prompt.py` — osobny,
  bezwzględny test siatki bezpieczeństwa; `test_fastapi_classify.py` — sam kontrakt, dowolny dostawca;
  `test_classification_golden_data.py` — spójność trzech plików danych. Klient LLM w testach
  integracyjnych **nowy na każdy test**, a wiele wywołań — w JEDNYM `asyncio.run` (`AsyncOpenAI`
  wiąże pulę połączeń z pętlą zdarzeń; „Event loop is closed").
- Wyniki (dwa przebiegi na model, surowe odpowiedzi czytane; Bielik 11B Q8_0 — jednorazowo na
  RunPodzie, dane syntetyczne; 37 wywołań = 20 grup + 17 stanowisk):

  | | gpt-4o-mini (przebieg 1 / 2) | Bielik 11B (oba identyczne) |
  |---|---|---|
  | błędy: grupy / stanowiska | 10% / 0%, 5% / 0% | 20% / 6% |
  | pismo na ZŁE miejsce (fałszywe dopasowanie) | 2,7% (09) / 0% | **0%** |
  | pismo do dekretacji ręcznej (fałszywe `no_match`) | 2,7% / 2,7% | 13,5% (25% pism) |
  | pisma spoza katalogu dopasowane | 1/3 / 0/3 | 0/3 |

- **Kierunek błędów różny per model:** 11B wyłącznie fałszywe `no_match` (pismo wraca do dekretacji
  ręcznej — bezpieczniej); `gpt-4o-mini` raz fałszywe dopasowanie (09: streszczenie zgubiło „VAT");
  4.5B na CPU odwrotnie niż 11B — „nie pasuje do żadnej **poza OPT-7**" → `OPT-7`, a 3/6 odpowiedzi
  różne w dwóch przebiegach: do dekretacji bez człowieka się nie nadaje.
- **11B „przegaduje się" ku `OPT-00`:** uzasadnienie zaczyna od „żadna z opcji nie odnosi się
  bezpośrednio…" i przytacza opis pasującej opcji BEZ fragmentu, który pasuje (18: „sprawy
  pracownicze, **dostęp do informacji publicznej** i obsługa Rady" → „sprawy pracownicze i obsługa
  Rady" → `OPT-00`); „bezpośrednio / dokładne" w 8/37 uzasadnień 11B, 0/37 u `gpt-4o-mini`.
  Eksperymenty — TODO pkt 11.
- 11B na GPU powtarzalny (37/37 ten sam wybór; `gpt-4o-mini` 36/37). Tokenizer Bielika ~1,65 znaku
  na token (`gpt-4o-mini` ~2,7) na tym samym prompcie — ważne przy doborze `LLM_MAX_INPUT_CHARS`.

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

## Klient PHP — ogólny, referencyjny

**Uniwersalny klient PHP** (`integrations/php/DocAiClient.php`) dla dowolnego systemu w PHP 8.1+:
wysyła oryginał (base64), odbiera tekst / streszczenie / wybór opcji. **DOKUS go nie używa** — ma
własnego klienta `Dokus\AiUke\Client\Client` (PHP 7.4, poniżej naszego minimum 8.1); nasz jest
produktem ogólnym i wzorcową implementacją kontraktu API.

- **JEDEN samodzielny plik**, namespace `Dokus\DocAi` (klient + DTO + wyjątki). Pokrywa
  pięć endpointów (warianty `*File()` same czytają plik i kodują base64). `classify()` przyjmuje
  opcje jako DTO `ClassifyOption`; `ClassifyResult::$optionId` bez rzutowania (typ z JSON-a:
  `21` → int, `"21"` → string), decyzja po `outcome` (`isMatched()` / `isNoMatch()` /
  `isInvalidResponse()`).
- **Decyzje:** bez `composer.json`/autoloadera (drop-in `require`, zero konfliktu zależności
  w cudzym ESOD-zie); czysty cURL (`ext-curl`+`ext-json`); PHP **8.1+**. Komentarze
  uniwersalne — bez nazwy DOKUS i roadmapy (klient ma być produktem ogólnym).
- **Architektura:** `CurlTransport` izoluje cURL (surowe stringi); kontrakt „API mówi JSON-em"
  żyje w `DocAiClient`; `Config` (adres + timeouty, domyślnie 180 s pod sekwencyjny OCR+LLM)
  wstrzykiwany. Błędy: `DocAiException` → `TransportException` / `ApiException` (niesie
  `statusCode`/`detail`/`X-Request-ID`).
- **Znane ograniczenie:** `CurlTransport` jest `final` → nie podmienia się na atrapę; mapowanie
  testowane na żywym API, nie mockiem. Czysty mock wymagałby wydzielenia interfejsu transportu —
  nie robione bez potrzeby. Sprawdzian bez PHP na hoście (minimalna wersja):
  `docker run --rm --network host -v $PWD/integrations/php:/client:ro php:8.1-cli php -l /client/DocAiClient.php`
  (+ skrypt z wywołaniami na `localhost:8000`). `classify()` sprawdzone tak 2026-09-18: `matched` /
  `no_match`, typ `id` (int i `'21'`), 422 z `detail`, 413.

## TODO — przed wdrożeniem produkcyjnym

Luki „ostatniej mili" (system dla urzędu), kolejność wg wagi; 11–13 — otwarte sprawy klasyfikacji.
Numeracja stała, bo kod i testy odwołują się do „TODO pkt N" (pkt 1 — budowa `/classify` — zamknięty
2026-09-18, wiedza w sekcji „Klasyfikacja"):

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
11. **Prompt klasyfikacji pod Bielika 11B.** 11B „przegaduje się" ku `OPT-00` (sekcja „Klasyfikacja").
    Sprawdzić: E1 etykieta przed uzasadnieniem, E2 bez zdania „ale nie wybieraj opcji, która nie
    pasuje", E3 opisy opcji jako listy tematów; przy okazji uzasadnienie przy `OPT-00` bez wyliczania
    opcji (długość rośnie z katalogiem — potem obniżyć `LLM_MAX_OUTPUT_TOKENS_CLASSIFY`). Każdy wariant
    na golden secie na OBU modelach — prompt jest wspólny.
12. **Realny katalog DOKUS-a.** Golden set stoi na syntetycznym katalogu gminnym; sprawdzić wybór na
    realnych grupach i stanowiskach (dane urzędu → poza repo).
13. **Wniosek o brakach streszczeń** (prompt streszczeń bez zmian). Czy wybór bywa nierozstrzygalny
    przez to, czego streszczenie nie zawiera — 09: zgubione „VAT" → `gpt-4o-mini` wysłał pismo na złe
    miejsce; adresata brak w 19/20 streszczeń (pkt 9). Jeśli tak — wniosek z przykładami.

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
