# tests/

```
tests/
├── unit/          # testy jednostkowe logiki Pythona (puste do kroku 2)
└── integration/   # testy uderzajace w dzialajace uslugi (np. kontener Tika)
```

## Uruchomienie

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

# integracyjne wymagaja dzialajacych uslug:
docker compose up -d tika fastapi
pytest                          # wszystko
pytest -m integration           # wszystkie integracyjne (parasol)
pytest -m integration_tika      # tylko testy Tiki
pytest -m integration_fastapi   # tylko testy FastAPI
pytest tests/unit               # tylko jednostkowe (bez uslug)
```

Jesli usluga jest niedostepna, jej testy integracyjne sa **pomijane** (skip), nie failuja.
Adresy nadpiszesz przez `TIKA_URL` (domyslnie `http://localhost:9998`) oraz
`FASTAPI_URL` (domyslnie `http://localhost:8000`).

## Markery

- `integration` — parasol nad wszystkimi testami integracyjnymi.
- `integration_tika` — testy uderzajace w kontener Tika.
- `integration_fastapi` — testy uderzajace w usluge FastAPI.

Kazda kolejna usluga dostaje wlasny marker (np. `integration_fastapi`,
`integration_llm`) i jednoczesnie marker-parasol `integration`. Markery sa
rejestrowane w `pyproject.toml`.
