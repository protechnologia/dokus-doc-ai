# dokus-doc-ai

Warstwa AI dla obiegu dokumentów DOKUS firmy Tensoft. Obsługuje ekstrakcję treści (w tym OCR) z różnorodnych plików (np. PDF, DOCX, JPG) oraz jej streszczanie za pomocą LLM (komercyjne API w chmurze, np. OpenAI, lub lokalny Bielik przez Ollama). Usługa jest całkowicie niezależna od systemu obiegu dokumentów i może być wykorzystana również do innych celów, choć jej prompty są zoptymalizowane pod zadania typowe dla obiegów, czyli dekretację i akceptację dokumentów.

## Algorytm działania

Pełny przebieg (endpoint `POST /extract-and-summarize`) krok po kroku:

1. **Przesłanie dokumentu** — plik trafia do API jako base64 w JSON. Obsługiwane są
   wszystkie formaty rozpoznawane przez Apache Tika (m.in. PDF, DOCX, XLSX, e-mail,
   obrazy/skany) — [pełna lista wspieranych formatów](https://tika.apache.org/3.3.0/formats.html).
2. **Kontrola rozmiaru** — jeżeli zdekodowany plik jest większy niż `MAX_UPLOAD_BYTES`,
   przetwarzanie jest przerywane (HTTP `413`).
3. **Limit stron PDF** — jeżeli to PDF, dokument jest obcinany do pierwszych
   `MAX_OCR_PAGES` stron (strażnik zasobów przed kosztownym OCR; cięcie nie jest ciche —
   trafia do logu i metadanych odpowiedzi).
4. **Bezpośrednia ekstrakcja** — Apache Tika wyciąga tekst natywnie z dokumentów
   posiadających warstwę tekstową (PDFBox, Apache POI itd.).
5. **OCR (fallback)** — jeżeli bezpośrednia ekstrakcja nie jest możliwa lub zwraca błędne
   wyniki (np. PDF z uszkodzoną warstwą tekstową — glify w Private Use Area), uruchamiany
   jest OCR (Tesseract, `pol+eng`).
6. **Truncacja pod model** — wyekstrahowany tekst jest obcinany do `LLM_MAX_INPUT_CHARS`
   znaków (dopasowanie do okna kontekstu modelu; flaga `truncated` w metadanych).
7. **Streszczenie** — obcięty tekst trafia do LLM (przez `LLMClient`) z prośbą o
   wygenerowanie podsumowania pod dekretację.
8. **Odpowiedź** — pełny tekst, podsumowanie oraz metadane obu etapów wracają do
   użytkownika w formacie JSON.

## Przykład działania

Poniższy przykład pokazuje realne podsumowanie wygenerowane przez naszą usługę
(`POST /summarize`, dostawca `openai`, model `gpt-4o-mini`) dla fikcyjnego pisma.

**Wejście (treść pisma):**

```
Nadawca:
Prof. Antoni Zagubiony
Instytut Fizyki Niekonwencjonalnej
ul. Paradoksalna 404
00-001 Nibylandia

Adresat:
Ministerstwo ds. Kontroli Anomalii Czasoprzestrzennych
Departament Pętli i Zakrzywień
Al. Wieczności 1
00-999 Warszawa

DOTYCZY: Skargi na permanentną pętlę czasu w rejonie ul. Paradoksalnej

Szanowni Państwo,

Niniejszym składam oficjalną skargę na bezczynność Departamentu Pętli i Zakrzywień w sprawie usunięcia lokalnej anomalii temporalnej. Od dokładnie 15 dni każdy mój poranek zaczyna się na nowo w dniu 22 czerwca 2026 roku o godzinie 06:00, co uniemożliwia mi wyjście do pracy oraz odebranie awizo z poczty. Próby zgłoszenia problemu drogą mailową skutkują tym, że wiadomości cofają się do folderu "wersje robocze". Wzywam Urząd do natychmiastowego wysłania ekipy technicznej z generatorem antygrawitacyjnym w celu przywrócenia naturalnego biegu czasu.

Z poważaniem,
Prof. Antoni Zagubiony
```

**Wygenerowane podsumowanie** (pole `summary` w odpowiedzi):

> Pismo dotyczy skargi prof. Antoniego Zagubionego na bezczynność Ministerstwa ds.
> Kontroli Anomalii Czasoprzestrzennych w sprawie lokalnej anomalii czasowej, która
> uniemożliwia mu normalne funkcjonowanie. Wnioskodawca wzywa do natychmiastowego
> działania.
>
> - **Typ pisma:** Skarga
> - **Nadawca:** Prof. Antoni Zagubiony
> - **Czego dotyczy:** Bezczynność w sprawie anomalii czasowej
> - **Termin / data:** 15 dni od 22 czerwca 2026 roku
> - **Oczekiwana akcja:** Wysłanie ekipy technicznej z generatorem antygrawitacyjnym

Streszczenie zachowuje narzucony przez prompt format hybrydowy (krótki akapit +
wypunktowane kluczowe elementy pod dekretację) i nie zmyśla danych spoza pisma.

## Uruchomienie

Wymagania: Docker + Docker Compose.

```bash
cp .env.example .env  # nadpisz domyślną konfigurację
docker compose build  # zbuduj obrazy wszystkich usług
docker compose up -d  # uruchom całą kompozycję
```

Domyślny `docker compose up -d` uruchamia tryb podstawowy (Tika + API, dostawca LLM `fake` —
offline). Lokalny model **Bielik (Ollama)** to **osobne warstwy compose**, które przez `include`
dziedziczą warstwę niższą — dlatego wybierasz je **jednym** `-f`:

```bash
# tryb podstawowy (fake, offline) — domyślny, lekki:
docker compose up -d

# + lokalny Bielik na CPU (dev / serwer bez GPU):
docker compose -f docker-compose.bielik.yml up -d

# + Bielik na GPU (maszyna z NVIDIA + nvidia-container-toolkit — faza 4-5):
docker compose -f docker-compose.bielik.gpu.yml up -d
```

Usługa `ollama` istnieje wyłącznie w warstwie `docker-compose.bielik.yml` (nie w bazie), więc
tryb `fake` pozostaje lekki — nie ma potrzeby profili. Warstwa GPU jest osobna, bo aktywna
rezerwacja GPU **twardo wywala** start kontenera na maszynie bez runtime nvidia. Przełączenie API
na Bielika robi się osobno, **jawnie w `.env`** (patrz „Zmiana dostawcy LLM na lokalnego
Bielika").

## Konfiguracja

Cała konfiguracja odbywa się przez zmienne środowiskowe.

Źródłem prawdy o dostępnych opcjach są dwa pliki: [.env.example](.env.example) (szablon
zmiennych dla Docker Compose) oraz [api/app/config.py](api/app/config.py) (typowane
ustawienia aplikacji wczytywane z ENV przez pydantic-settings).

Zmienne wykorzystywane przez **Docker Compose**:

| Zmienna | Domyślnie | Opis |
|---|---|---|
| `TIKA_PORT` | `9998` | Port Apache Tika wystawiony na hoście. |
| `FASTAPI_PORT` | `8000` | Port API wystawiony na hoście. |
| `OLLAMA_PORT` | `11434` | Port kontenera Ollamy (warstwa `docker-compose.bielik.yml`) wystawiony na hoście — tylko pod debug z hosta (np. `curl localhost:11434/api/tags`). FastAPI go NIE używa: gada z Ollamą po sieci compose (`http://ollama:11434`). |

Zmienne wykorzystywane przez **logikę aplikacji**:

| Zmienna | Domyślnie | Opis |
|---|---|---|
| `TIKA_URL` | `http://localhost:9998` | Adres Apache Tika widziany przez API. Nadpisywany w `docker-compose.yml` na `http://tika:9998`. |
| `TIKA_TIMEOUT_SECONDS` | `120` | Timeout ekstrakcji w Apache Tika (OCR bywa wolny). |
| `HEALTH_CHECK_TIMEOUT_SECONDS` | `3` | Maksymalny czas na odpowiedź Apache Tika podczas wywoływania endpointa `/health`. |
| `MAX_UPLOAD_BYTES` | `20971520` | Maksymalny rozmiar zdekodowanego pliku w `POST /extract` i `POST /extract-and-summarize`. Powyżej tego rozmiaru API zwraca kod błędu `413`. |
| `MAX_OCR_PAGES` | `30` | Limit stron PDF wysyłanych do ekstrakcji. Apache Tika samodzielnie decyduje, czy zastosować zwykłą ekstrakcję, czy OCR. Limit ma ochronić przed zbyt długim czasem trwania OCR. |
| `LLM_PROVIDER` | `fake` | Dostawca LLM: `fake` lub `openai`. |
| `LLM_API_KEY` | — | Klucz API dla LLM (wymagany dla `openai`). |
| `LLM_MODEL` | — | Nazwa modelu: `gpt-4o-mini` etc. (wymagana dla `openai`). |
| `LLM_BASE_URL` | — | Opcjonalny własny endpoint zgodny z API OpenAI (`.../v1`). |
| `LLM_TIMEOUT_SECONDS` | `60` | Timeout wołania LLM. |
| `LLM_MAX_INPUT_CHARS` | `90000` | Limit znaków tekstu wysyłanego do LLM w `POST /summarize` i `POST /extract-and-summarize`. Ustawienie należy dostosować do rozmiaru okna kontekstu modelu lub do optymalizacji kosztów. |

## API

Bazowy adres: `http://localhost:8000` (port z `FASTAPI_PORT`). 

Interaktywna dokumentacja (Swagger UI) pod `/docs`.

Dostępne endpointy:

| Endpoint | Rola |
|---|---|
| `GET /health` | Zdrowie usługi (best-effort dostępność Tiki). |
| `POST /extract` | Czysta ekstrakcja: plik → tekst + metadane. |
| `POST /summarize` | Czysta summaryzacja: tekst → streszczenie. |
| `POST /extract-and-summarize` | Pełny pipeline: plik → tekst → streszczenie. |

Każda odpowiedź niesie nagłówek `X-Request-ID` (propagowany z żądania albo generowany) —
ten sam identyfikator trafia do logów, co ułatwia korelację.

### `GET /health`

Zdrowie usługi (w tym dostępność podusługi Apache Tika).

Wywołanie:

```bash
curl http://localhost:8000/health
```

Przykładowa odpowiedź — Tika zdrowa:

```json
{
  "status": "ok",
  "service": "dokus-doc-ai",
  "version": "0.1.0",
  "dependencies": { "tika": "ok" }
}
```

Przykładowa odpowiedź — Tika niedostępna (kod HTTP nadal `200`):

```json
{
  "status": "degraded",
  "service": "dokus-doc-ai",
  "version": "0.1.0",
  "dependencies": { "tika": "unreachable" }
}
```

### `POST /extract`

Czysta ekstrakcja tekstu z pliku (bez streszczania). Plik przesyłasz jako **base64 w JSON**
(nie multipart). `filename`/`content_type` to opcjonalne podpowiedzi typu dla Tiki — gdy ich
brak, Tika sama wykrywa typ. Endpoint pilnuje **limitu rozmiaru** i **limitu stron PDF**, a
dla PDF-ów ze śmieciową warstwą tekstową robi **OCR-fallback** — szczegóły w sekcji „Limity i
jakość ekstrakcji" niżej.

Wejście (`ExtractRequest`):

| Pole | Wymagane | Opis |
|---|---|---|
| `content_base64` | tak | Zawartość pliku zakodowana base64. |
| `filename` | nie | Nazwa pliku (podpowiedź typu), np. `pismo.pdf`. |
| `content_type` | nie | MIME (podpowiedź), np. `application/pdf`; brak → autodetekcja. |

Przykładowe żądanie (`content_base64` skrócony):

```json
{
  "content_base64": "JVBERi0xLjcKJeLjz9MKMyAwIG9iago...",
  "filename": "pismo.pdf",
  "content_type": "application/pdf"
}
```

Wyjście (`ExtractResponse`) — wyekstrahowany tekst + metadane (MIME, język, długość oraz
diagnostyka OCR i liczby stron):

```json
{
  "text": "Treść dokumentu...",
  "metadata": {
    "content_type": "application/pdf",
    "language": "pl",
    "char_count": 42,
    "word_count": 6,
    "ocr_used": true,
    "pages_total": 100,
    "pages_processed": 30,
    "ocr_truncated": true
  }
}
```

Pola metadanych:

| Pole | Opis |
|---|---|
| `ocr_used` | Czy treść powstała (w całości lub części) przez OCR. |
| `pages_total` | Liczba stron źródłowego PDF; `null` dla nie-PDF. |
| `pages_processed` | Ile pierwszych stron PDF realnie przetworzono (`=pages_total`, gdy bez cięcia); `null` dla nie-PDF. |
| `ocr_truncated` | Czy PDF ucięto do `MAX_OCR_PAGES` (pominięto dalsze strony). |

Kody błędów:

| Kod | Kiedy |
|---|---|
| `413` | Plik większy niż `MAX_UPLOAD_BYTES`. |
| `422` | Złe base64 / pusty plik / Tika odrzuciła plik (nieobsługiwany/uszkodzony) / brak treści po ekstrakcji. |
| `502` | `tika-server` niedostępny. |

Wywołanie (plik → base64 → JSON):

```bash
B64=$(base64 -w0 pismo.pdf)
curl -X POST http://localhost:8000/extract \
  -H 'Content-Type: application/json' \
  -d "{\"content_base64\": \"$B64\", \"content_type\": \"application/pdf\"}"
```

### `POST /summarize`

Czysta summaryzacja: wejściem jest **sam tekst** (bez pliku — ekstrakcja to osobny endpoint).
Serwis składa polski prompt pod osobę dekretującą, woła model przez `LLMClient` i zwraca
streszczenie.

Wejście (`SummarizeRequest`):

| Pole | Wymagane | Opis |
|---|---|---|
| `text` | tak | Tekst dokumentu do streszczenia. |

Przykładowe żądanie:

```json
{
  "text": "Urząd Skarbowy w Krakowie wzywa Jana Kowalskiego do zapłaty zaległości w podatku od nieruchomości za 2025 r. w kwocie 1 240 zł w terminie 14 dni od doręczenia pisma, pod rygorem egzekucji."
}
```

Wyjście (`SummarizeResponse`) — streszczenie (**hybryda: krótki akapit + wypunktowane
kluczowe elementy**) jako jeden tekst + metadane:

```json
{
  "summary": "Urząd Skarbowy wzywa do zapłaty zaległego podatku...\n\n• Typ: wezwanie do zapłaty\n• Nadawca: Urząd Skarbowy w Krakowie\n• Termin: 14 dni od doręczenia\n• Akcja: opłata lub odwołanie",
  "metadata": {
    "model": "gpt-4o-mini",
    "input_chars": 812,
    "truncated": false,
    "usage": {
      "prompt_tokens": 250,
      "completion_tokens": 90,
      "total_tokens": 340
    }
  }
}
```

Pola metadanych:

| Pole | Opis |
|---|---|
| `model` | Model, który wygenerował streszczenie (kto odpowiedział). |
| `input_chars` | Długość wejścia po `strip` (w znakach). |
| `truncated` | Czy tekst ucięto do `LLM_MAX_INPUT_CHARS`. |
| `usage` | Zużycie tokenów (`prompt_tokens` / `completion_tokens` / `total_tokens`) — diagnostyka kosztu. |

Kody błędów:

| Kod | Kiedy |
|---|---|
| `422` | Puste wejście (sam whitespace) lub brak pola `text`. |
| `500` | Błędna konfiguracja dostawcy LLM / zły klucz (nasz config serwera). |
| `502` | Inny błąd po stronie dostawcy / nieoczekiwana odpowiedź. |
| `503` | Dostawca dławi (limit zapytań / kwota). |
| `504` | Dostawca nie odpowiedział w czasie (timeout). |

Wywołanie:

```bash
curl -X POST http://localhost:8000/summarize \
  -H 'Content-Type: application/json' \
  -d '{"text": "Urząd Skarbowy wzywa do zapłaty zaległości podatkowej w terminie 14 dni..."}'
```

> Długie wejście jest **ucinane** do `LLM_MAX_INPUT_CHARS` znaków (truncacja pod okno modelu,
> z metadaną `truncated`) — to co innego niż limit stron ekstrakcji (`MAX_OCR_PAGES`). Oba
> limity warto trzymać spójnie: patrz „Limity i jakość ekstrakcji”.

### `POST /extract-and-summarize`

Pełny pipeline w jednym wywołaniu: **plik → tekst → streszczenie**. Łączy `POST /extract`
(ekstrakcja, w tym OCR i jakość) z `POST /summarize` (streszczenie pod dekretację) — to
docelowy endpoint pod integrację z DOKUS. Wejście jak w `/extract` (**base64 w JSON**), wyjście
jak w `/summarize` **plus** pełny wyekstrahowany tekst i metadane **obu** etapów.

Wejście (`SummarizeDocumentRequest`):

| Pole | Wymagane | Opis |
|---|---|---|
| `content_base64` | tak | Zawartość pliku zakodowana base64. |
| `filename` | nie | Nazwa pliku (podpowiedź typu), np. `pismo.pdf`. |
| `content_type` | nie | MIME (podpowiedź), np. `application/pdf`; brak → autodetekcja. |

Przykładowe żądanie (`content_base64` skrócony):

```json
{
  "content_base64": "JVBERi0xLjcKJeLjz9MKMyAwIG9iago...",
  "filename": "pismo.pdf",
  "content_type": "application/pdf"
}
```

Wyjście (`SummarizeDocumentResponse`) — streszczenie + **pełny** wyekstrahowany tekst +
**zagnieżdżone** metadane obu etapów (`extraction` jak w `/extract`, `summarization` jak w
`/summarize`):

```json
{
  "summary": "Urząd Skarbowy wzywa do zapłaty zaległości...\n\n• Typ pisma: wezwanie\n• Nadawca: Urząd Skarbowy\n• Termin: 14 dni\n• Akcja: zapłata",
  "text": "Pełna treść wyekstrahowanego dokumentu...",
  "extraction": {
    "content_type": "application/pdf",
    "language": "pl",
    "char_count": 4200,
    "word_count": 600,
    "ocr_used": true,
    "pages_total": 3,
    "pages_processed": 3,
    "ocr_truncated": false
  },
  "summarization": {
    "model": "gpt-4o-mini",
    "input_chars": 4200,
    "truncated": false,
    "usage": {
      "prompt_tokens": 1200,
      "completion_tokens": 90,
      "total_tokens": 1290
    }
  }
}
```

Pola odpowiedzi:

| Pole | Opis |
|---|---|
| `summary` | Streszczenie pod dekretację (hybryda: krótki akapit + wypunktowane kluczowe elementy). |
| `text` | **Pełny** wyekstrahowany tekst (przed truncacją pod okno modelu); gdy był dłuższy niż `LLM_MAX_INPUT_CHARS`, `summarization.truncated` mówi, że model widział tylko początek. |
| `extraction` | Metadane etapu ekstrakcji — pola jak w `POST /extract` wyżej. |
| `summarization` | Metadane etapu streszczenia — pola jak w `POST /summarize` wyżej. |

Kody błędów = **unia** `/extract` i `/summarize` (dokument przechodzi przez obie warstwy):

| Kod | Kiedy |
|---|---|
| `413` | Plik większy niż `MAX_UPLOAD_BYTES`. |
| `422` | Złe base64 / pusty plik / Tika odrzuciła plik / brak treści po ekstrakcji / puste wejście do LLM. |
| `500` | Błędna konfiguracja dostawcy LLM / zły klucz (nasz config serwera). |
| `502` | `tika-server` niedostępny / inny błąd po stronie dostawcy LLM. |
| `503` | Dostawca LLM dławi (limit zapytań / kwota). |
| `504` | Dostawca LLM nie odpowiedział w czasie (timeout). |

Wywołanie (plik → base64 → JSON):

```bash
B64=$(base64 -w0 pismo.pdf)
curl -X POST http://localhost:8000/extract-and-summarize \
  -H 'Content-Type: application/json' \
  -d "{\"content_base64\": \"$B64\", \"content_type\": \"application/pdf\"}"
```

> Dokument przechodzi przez **wszystkie trzy** bramki naraz (`MAX_UPLOAD_BYTES` → `MAX_OCR_PAGES`
> → `LLM_MAX_INPUT_CHARS`) — tu spójność tych limitów jest najważniejsza w praktyce: patrz
> „Limity i jakość ekstrakcji”.

## Integracje

Gotowi klienci do konsumowania API z innych systemów.

### Klient PHP

[`integrations/php/DocAiClient.php`](integrations/php/DocAiClient.php) — uniwersalny klient PHP do
API. **Jeden samodzielny plik**, bez composera i bez autoloadera: wystarczy `require`. Transport na
czystym cURL (zero zależności runtime; wymaga rozszerzeń `ext-curl` i `ext-json`), PHP **8.1+**.

Pokrywa wszystkie cztery endpointy: `health()`, `extract()`/`extractFile()`,
`summarize()`, `extractAndSummarize()`/`extractAndSummarizeFile()`. Konfiguracja (adres API,
timeouty) jest wstrzykiwana przez `Config`. Błędy: `ApiException` (odpowiedź HTTP 4xx/5xx, niesie
`statusCode`, `detail` i `X-Request-ID`) oraz `TransportException` (nie udało się dobić do API —
sieć/timeout).

Przykład wywołania (plik → tekst → streszczenie):

```php
<?php
require __DIR__ . '/integrations/php/DocAiClient.php';

use Dokus\DocAi\DocAiClient;
use Dokus\DocAi\Config;
use Dokus\DocAi\ApiException;
use Dokus\DocAi\TransportException;

$client = new DocAiClient(new Config('http://localhost:8000'));

try {
    $wynik = $client->extractAndSummarizeFile('/sciezka/pismo.pdf');

    echo $wynik->summary;                         // streszczenie (akapit + punkty)
    echo $wynik->extraction->contentType;         // np. 'application/pdf'
    echo $wynik->summarization->usage->totalTokens;
} catch (ApiException $e) {
    // API odpowiedziało błędem HTTP — np. plik za duży (413) albo Tika niedostępna (502).
    fprintf(STDERR, "Błąd API %d: %s\n", $e->statusCode, $e->detail ?? $e->getMessage());
} catch (TransportException $e) {
    // Nie udało się w ogóle dobić do API (sieć/timeout).
    fprintf(STDERR, "Błąd transportu: %s\n", $e->getMessage());
}
```

Sama ekstrakcja albo samo streszczanie tekstu:

```php
$ekstrakcja = $client->extractFile('/sciezka/pismo.pdf');   // POST /extract
echo $ekstrakcja->text;

$streszczenie = $client->summarize('Długa treść pisma...');  // POST /summarize
echo $streszczenie->summary;
```

## Uwagi techniczne

#### Limity i jakość ekstrakcji

Usługa nie jest wyłącznie warstwą pośredniczącą do Apache Tika i LLM — na każdym etapie
kontroluje zużycie zasobów oraz jakość wyniku, od przyjęcia pliku po przygotowanie wejścia dla
modelu. Na przebieg przetwarzania wpływają cztery ustawienia: limit rozmiaru pliku, limit
liczby stron PDF, próg udziału znaków PUA (powyżej którego wymuszany jest OCR-fallback) oraz
limit znaków tekstu przekazywanego do LLM.

| Ustawienie | Wartość domyślna | Wpływ |
|---|---|---|
| `MAX_UPLOAD_BYTES` | 20 MiB | Maksymalny rozmiar zdekodowanego pliku. Sprawdzany na zdekodowanych bajtach, przed kontaktem z Apache Tika. Powyżej → `413`. |
| `MAX_OCR_PAGES` | 30 | Maksymalna liczba stron PDF przekazywanych do Apache Tika. Dłuższy PDF jest obcinany do pierwszych N stron przed wysłaniem (ochrona przed kosztownym OCR). |
| Próg PUA | 30% | Udział znaków Private Use Area w warstwie tekstowej, powyżej którego warstwa jest uznawana za wadliwą i wymuszany jest OCR-fallback (`ocr_only`). |
| `LLM_MAX_INPUT_CHARS` | 90 000 | Maksymalna liczba znaków tekstu przekazywanego do LLM (`POST /summarize` i `POST /extract-and-summarize`). Dłuższy tekst jest obcinany do pierwszych N znaków przed wysłaniem do modelu (dopasowanie do okna kontekstu). |

`MAX_UPLOAD_BYTES`, `MAX_OCR_PAGES` i `LLM_MAX_INPUT_CHARS` ustawia się przez ENV (sekcja
„Konfiguracja"). Próg PUA jest wartością wewnętrzną komponentu `PuaDetector` (nie ENV);
separacja zmierzonych przypadków jest na tyle wyraźna (warstwa wadliwa ok. 77% wobec poprawnego
OCR 0%), że wartość 30% ma duży margines.

Uzupełnienie do powyższych ustawień:

- **Limit stron nie jest cichy.** Obcięcie PDF raportują metadane `pages_total` /
  `pages_processed` / `ocr_truncated`, więc konsument wie, że treść powstała z części
  dokumentu. Limit obejmuje każdy duży PDF, nie tylko skany.
- **Limit znaków do LLM to truncacja pod okno modelu, nie limit ekstrakcji.** Działa na tekście
  już wyekstrahowanym, tuż przed streszczeniem, dlatego pole `text` w odpowiedzi zawiera pełną
  treść, a metadana `truncated` wskazuje, czy model otrzymał jedynie jej początek.
- **Detekcja wadliwej warstwy tekstowej (PUA).** Niektóre PDF-y (np. wydruki z części drukarek
  PDF) mają warstwę tekstową, ale jej znaki należą do Private Use Area (uszkodzona mapa
  `ToUnicode`). Ekstrakcja natywna zwraca wtedy nieczytelny tekst zamiast pustego wyniku, a
  `ocrStrategy=auto` nie uruchamia OCR. Serwis wykrywa taką warstwę i wymusza OCR (`ocr_only`)
  na tym samym pliku, zwracając czytelną treść; w metadanych `ocr_used: true`.

Trzy limity liczbowe (`MAX_UPLOAD_BYTES` → `MAX_OCR_PAGES` → `LLM_MAX_INPUT_CHARS`) działają na
kolejnych etapach tego samego dokumentu, dlatego warto utrzymywać je w jednym rzędzie wielkości
— w przeciwnym razie jeden etap wykonuje pracę, którą następny i tak odrzuca:

- **Strony a znaki.** Jedna strona A4 to około 3000 znaków, więc `MAX_OCR_PAGES` stron daje
  mniej więcej `MAX_OCR_PAGES × 3000` znaków. Jeżeli wartość ta znacznie przekracza
  `LLM_MAX_INPUT_CHARS`, OCR przetwarza strony, których model i tak nie otrzyma — należy wtedy
  obniżyć `MAX_OCR_PAGES` albo podnieść `LLM_MAX_INPUT_CHARS`. Przykładowo
  `LLM_MAX_INPUT_CHARS=50000` odpowiada około 17 stronom, więc przy `MAX_OCR_PAGES=30` OCR
  obejmuje około 13 stron ponad potrzebę.
- **Rozmiar a strony.** `MAX_UPLOAD_BYTES` powinien swobodnie pomieścić dokument o
  `MAX_OCR_PAGES` stronach (skan potrafi zajmować kilka MB), w przeciwnym razie pliki są
  odrzucane, zanim limit stron zdąży zadziałać.

Przy zmianie modelu na mniejszy (np. Bielik — węższe okno kontekstu, a więc niższy
`LLM_MAX_INPUT_CHARS`) zwykle obniża się również `MAX_OCR_PAGES`, aby etapy pozostały spójne.

#### Rozmiar kontekstu wybranych modeli

Orientacyjne okna kontekstu w przeliczeniu na strony A4 (przyjmując ~800 tokenów/stronę po
polsku; realnie 750–1000):

| Model | Okno (tokeny) | ~strony A4 (PL) | Uwaga |
|---|---:|---:|---|
| **gpt-4o-mini** | 128 000 | ~160 | obecny domyślny (dev) |
| gpt-4o | 128 000 | ~160 | |
| gpt-4.1 / -mini / -nano | 1 047 576 | ~1 300 | okno 1M |
| o3 / o3-mini | 200 000 | ~250 | modele „reasoning" |
| gpt-3.5-turbo | 16 385 | ~20 | starszy |
| **Bielik-11B-v2.x** | 32 768 | ~40 | cel on-prem (Ollama) |
| Bielik-7B v1 | 4 096 | ~5 | starsza wersja |

> „Strony" = **całe** okno (wejście + system prompt + odpowiedź). Użyteczne wejście pod
> dokument jest mniejsze — stąd `LLM_MAX_INPUT_CHARS` z zapasem. Strona gęsta (tabele,
> pisma prawnicze) zajmuje więcej tokenów niż luźny tekst.

## Testy

### Szybki sprawdzian ręczny

```bash
curl http://localhost:9998/tika    # serwer Tika żyje
curl http://localhost:8000/health  # API żyje; zwraca też status zależności (tika)
```

### Testy

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest                           # wszystko (usługi nieuruchomione -> ich testy skip)
pytest tests/unit                # tylko jednostkowe (bez usług)
pytest -m integration_tika       # tylko Tika;    wymaga: docker compose up -d tika
pytest -m integration_fastapi    # tylko FastAPI; wymaga: docker compose up -d fastapi
pytest -m integration_llm        # tylko LLM;     wymaga: LLM_PROVIDER=openai + klucz (koszt!)
pytest -m integration            # wszystkie testy integracyjne (parasol)
```

Testy Tiki uderzają w działający kontener i sprawdzają trzy rzeczy: że serwer
odpowiada, że ekstrakcja natywna z DOCX zwraca tekst z polskimi znakami oraz że OCR
skanu PNG po polsku poprawnie odczytuje ą, ć, ż, ł… (czyli że pakiet językowy `pol`
działa). Pliki testowe są generowane w locie.

Testy FastAPI sprawdzają `/health` (kształt odpowiedzi, nagłówek `X-Request-ID`, status
zależności Tiki). Gdy usługa jest niedostępna, jej testy są pomijane (skip), nie failują.

Test LLM (`integration_llm`) robi jedno minimalne wywołanie realnego dostawcy (OpenAI),
by potwierdzić, że klucz i mapowanie odpowiedzi działają — pomijany, gdy `LLM_PROVIDER`
nie jest `openai` lub brak klucza.

> Adresy usług w testach integracyjnych nadpiszesz przez `TIKA_URL` (domyślnie
> `http://localhost:9998`) i `FASTAPI_URL` (domyślnie `http://localhost:8000`).

## Typowe procedury

### Zmiana dostawcy LLM na lokalnego Bielika (Ollama, maszyna z GPU)

Zakłada świeżą maszynę **Ubuntu/Debian** z GPU NVIDIA.

1. **Zainstaluj Docker Engine + Compose:**
   ```bash
   curl -fsSL https://get.docker.com | sh
   ```

2. **Zainstaluj sterownik NVIDIA** (jeśli `nvidia-smi` nie działa) i zweryfikuj:
   ```bash
   sudo ubuntu-drivers autoinstall   # następnie reboot
   nvidia-smi                        # musi wypisać GPU
   ```

3. **Zainstaluj NVIDIA Container Toolkit i podłącz go do Dockera** ([dokumentacja](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)):
   ```bash
   curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
     | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
   curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
     | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
     | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
   sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit
   sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker
   ```

4. **Sprawdź GPU w kontenerze:**
   ```bash
   sudo docker run --rm --runtime=nvidia --gpus all ubuntu nvidia-smi
   ```

5. **Podnieś stack** (baza + Bielik + GPU jednym plikiem):
   ```bash
   docker compose -f docker-compose.bielik.gpu.yml up -d
   ```

6. **Zaciągnij model do kontenera:**
   ```bash
   docker compose -f docker-compose.bielik.gpu.yml \
     exec ollama ollama pull SpeakLeash/bielik-11b-v3.0-instruct:Q4_K_M
   ```

7. **Przełącz dostawcę w `.env`** (klucz zbędny — Ollama go ignoruje):
   ```env
   LLM_PROVIDER=ollama
   LLM_BASE_URL=http://ollama:11434/v1
   LLM_MODEL=SpeakLeash/bielik-11b-v3.0-instruct:Q4_K_M
   ```

8. **Dostosuj `LLM_MAX_INPUT_CHARS` w `.env`** pod węższe okno Bielika (~32k tok.):
   ```env
   LLM_MAX_INPUT_CHARS=50000
   ```

9. **Restart:**
   ```bash
   docker compose -f docker-compose.bielik.gpu.yml up -d
   ```
