# DOKUS Doc AI

Warstwa AI dla obiegu dokumentów DOKUS — ekstrakcja treści (w tym OCR skanów) i
streszczanie dokumentów. Opis celu, stacku i zasad: [CLAUDE.md](CLAUDE.md).

## Status

- [x] **Krok 1 — ekstrakcja / OCR** (kontener Tika-full + pakiet `pol`)
- [ ] Krok 2 — API na FastAPI (plan: [CLAUDE.md](CLAUDE.md))
  1. [x] Zalążek FastAPI (`Settings`, `/health`, Dockerfile + usługa `fastapi`, szkielet testów)
  2. [x] `LLMClient` (interfejs + implementacja OpenAI + `FakeLLMClient` + fabryka)
  3. [x] API: czysta ekstrakcja
     1. [x] `TikaClient` — transport do Tiki (surowy tekst + metadane, mapowanie błędów Tiki na wyjątki)
     2. [x] `ExtractionService` — domena: normalizacja + metadane (happy path; jakość PUA / OCR-fallback / limit stron → ppkt 5)
     3. [x] `POST /extract` — wejście base64 (JSON), wyjście tekst + metadane, mapowanie błędów na kody HTTP
     4. [x] Config (`MAX_UPLOAD_BYTES`) + dokumentacja `POST /extract`
     5. [x] Jakość ekstrakcji — detekcja PUA + OCR-fallback + limit stron (`MAX_OCR_PAGES`)
  4. [ ] API: czysta summaryzacja
  5. [ ] API: pełny pipeline
- [ ] Krok 3 — integracja z DOKUS
- [ ] Krok 4 — migracja LLM na RunPod
- [ ] Krok 5 — migracja LLM na własną maszynę

## Uruchomienie

Wymagania: Docker + Docker Compose.

```bash
cp .env.example .env          # uzupełnij konfigurację (np. TIKA_PORT, FASTAPI_PORT)
docker compose build          # buduje obrazy wszystkich usług
docker compose up -d          # uruchamia całą kompozycję (fastapi wstaje po Tika healthy)
```

Usługi po starcie: Tika na `:9998` (ekstrakcja/OCR), FastAPI na `:8000` (API + logika).
Porty nadpiszesz przez `TIKA_PORT` / `FASTAPI_PORT`. Dostawcę LLM konfiguruje się przez
`LLM_*` — obsługiwane `fake` (domyślnie, nic nie wychodzi na zewnątrz) oraz `openai`.

## Konfiguracja

Cała konfiguracja przez ENV (zasada projektu). Poniżej przegląd; **źródłem prawdy** są
[.env.example](.env.example) (szablon) oraz [api/app/config.py](api/app/config.py)
(`Settings` — domyślne wartości aplikacji). Wartości w kolumnie „Domyślnie" są
orientacyjne.

Zmienne **compose** (mapowanie portów na hoście; aplikacja ich nie czyta):

| Zmienna | Domyślnie | Opis |
|---|---|---|
| `TIKA_PORT` | `9998` | Port Tiki wystawiony na hoście. |
| `FASTAPI_PORT` | `8000` | Port API wystawiony na hoście. |

Ustawienia **aplikacji** (`Settings`):

| Zmienna | Domyślnie | Opis |
|---|---|---|
| `TIKA_URL` | `http://localhost:9998` | Adres Tiki widziany przez API. W compose: `http://tika:9998`. |
| `TIKA_TIMEOUT_SECONDS` | `120` | Timeout ekstrakcji w Tice (OCR bywa wolny). |
| `HEALTH_CHECK_TIMEOUT_SECONDS` | `3` | Krótki ping Tiki w `/health`. |
| `MAX_UPLOAD_BYTES` | `20971520` | Górny limit rozmiaru zdekodowanego pliku w `POST /extract` (20 MiB); powyżej → `413`. |
| `MAX_OCR_PAGES` | `30` | Limit stron PDF wysyłanych do Tiki (strażnik OCR). PDF powyżej N stron jest cięty do pierwszych N **przed** ekstrakcją (cięcie raportowane w metadanych). |
| `LLM_PROVIDER` | `fake` | Dostawca LLM: `fake` (offline, nic nie wychodzi na zewnątrz) lub `openai`. |
| `LLM_API_KEY` | — | Klucz API dostawcy LLM (wymagany dla `openai`). |
| `LLM_MODEL` | — | Nazwa modelu, np. `gpt-4o-mini` (wymagana dla `openai`). |
| `LLM_BASE_URL` | — | Opcjonalny własny endpoint zgodny z API OpenAI (`.../v1`). |
| `LLM_TIMEOUT_SECONDS` | `60` | Timeout wołania LLM. |

## API

Bazowy adres: `http://localhost:8000` (port z `FASTAPI_PORT`). Interaktywna dokumentacja
(Swagger UI) pod `/docs`. Dostępne endpointy: `GET /health` i `POST /extract` (czysta
ekstrakcja). Streszczanie i pełny pipeline dochodzą w kolejnych krokach (2.4–2.5).

Każda odpowiedź niesie nagłówek `X-Request-ID` (propagowany z żądania albo generowany) —
ten sam identyfikator trafia do logów, co ułatwia korelację.

### `GET /health`

Zdrowie usługi i — best-effort — dostępność Tiki. **Zawsze zwraca HTTP 200, gdy API
żyje;** stan zależności jest w ciele odpowiedzi, nie w kodzie HTTP (niedostępna Tika to
`status: degraded`, nie błąd). Pole `status`: `ok` (API i Tika zdrowe) albo `degraded`
(API żyje, Tika niedostępna).

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
diagnostyka OCR/stron z kroku 2.3.5):

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

Pola metadanych jakości (krok 2.3.5):

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

#### Limity i jakość ekstrakcji

Endpoint nie jest „głupim proxy" do Tiki — pilnuje zasobów i jakości wyniku (krok 2.3.5):

- **Limit rozmiaru pliku (`MAX_UPLOAD_BYTES`, domyślnie 20 MiB).** Sprawdzany na
  **zdekodowanych** bajtach, **przed** kontaktem z Tiką (nie obciążamy OCR plikami spoza
  limitu). Powyżej → **`413`**.
- **Limit stron PDF (`MAX_OCR_PAGES`, domyślnie 30).** PDF z większą liczbą stron jest
  **cięty do pierwszych N przed** wysłaniem do Tiki (strażnik zasobów — wielostronicowy skan
  potrafiłby zatkać OCR). Dotyczy **każdego** dużego PDF, nie tylko skanów. Cięcie **nie jest
  ciche**: raportują je `pages_total` / `pages_processed` / `ocr_truncated` w metadanych, więc
  konsument wie, że treść powstała z części dokumentu.
- **Detekcja śmieciowej warstwy (PUA) + OCR-fallback.** Niektóre PDF-y (np. wydruki z części
  drukarek PDF) mają warstwę tekstową, ale jej znaki to **Private Use Area** (zepsuta mapa
  `ToUnicode`) — natywna ekstrakcja zwraca śmieć, a nie pusty wynik, i `ocrStrategy=auto` sam
  OCR-u **nie** odpala. Serwis wykrywa taką warstwę (udział znaków PUA powyżej progu) i
  **wymusza OCR** (`ocr_only`) na tym samym pliku, zwracając czytelną treść; w metadanych
  `ocr_used: true`.

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
