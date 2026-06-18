# DOKUS Doc AI

Warstwa AI dla obiegu dokumentów DOKUS — ekstrakcja treści (w tym OCR skanów) i
streszczanie dokumentów. Opis celu, stacku i zasad: [CLAUDE.md](CLAUDE.md).

## Status

- [x] **Krok 1 — ekstrakcja / OCR** (kontener Tika-full + pakiet `pol`)
- [ ] Krok 2 — API na FastAPI (plan: [CLAUDE.md](CLAUDE.md))
  1. [x] Zalążek FastAPI (`Settings`, `/health`, Dockerfile + usługa `fastapi`, szkielet testów)
  2. [ ] `LLMClient`
  3. [ ] API: czysta ekstrakcja
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
Porty nadpiszesz przez `TIKA_PORT` / `FASTAPI_PORT`. Dostawcę LLM (krok 2.2) konfiguruje
się przez `LLM_*` — domyślnie `fake` (nic nie wychodzi na zewnątrz).

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
| `LLM_PROVIDER` | `fake` | Dostawca LLM. `fake` = nic nie wychodzi na zewnątrz (klient w 2.2). |
| `LLM_API_KEY` | — | Klucz API dostawcy LLM. |
| `LLM_BASE_URL` | — | Endpoint / bazowy URL dostawcy LLM. |
| `LLM_MODEL` | — | Nazwa modelu. |
| `LLM_API_VERSION` | — | Wersja API (tylko Azure OpenAI). |
| `LLM_TIMEOUT_SECONDS` | `60` | Timeout wołania LLM. |

## API

Bazowy adres: `http://localhost:8000` (port z `FASTAPI_PORT`). Interaktywna dokumentacja
(Swagger UI) pod `/docs`. Na razie dostępny jest tylko `/health` — endpointy ekstrakcji
i streszczania dochodzą w kolejnych krokach (2.3–2.5).

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

## Testy

### Szybki sprawdzian ręczny

```bash
curl http://localhost:9998/tika    # serwer Tika żyje
curl http://localhost:8000/health  # API żyje; zwraca też status zależności (tika)
```

### Testy integracyjne

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest tests/unit                # jednostkowe (bez usług)
pytest -m integration_tika       # tylko Tika;    wymaga: docker compose up -d tika
pytest -m integration_fastapi    # tylko FastAPI; wymaga: docker compose up -d fastapi
pytest -m integration            # wszystkie testy integracyjne (parasol)
```

Testy Tiki uderzają w działający kontener i sprawdzają trzy rzeczy: że serwer
odpowiada, że ekstrakcja natywna z DOCX zwraca tekst z polskimi znakami oraz że OCR
skanu PNG po polsku poprawnie odczytuje ą, ć, ż, ł… (czyli że pakiet językowy `pol`
działa). Pliki testowe są generowane w locie.

Testy FastAPI sprawdzają `/health` (kształt odpowiedzi, nagłówek `X-Request-ID`, status
zależności Tiki). Gdy usługa jest niedostępna, jej testy są pomijane (skip), nie failują.
Szczegóły: [tests/](tests/README.md).
