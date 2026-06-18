# tests/unit/

Testy jednostkowe logiki Pythona — bez uruchamiania usług (nie wymagają Tiki ani
FastAPI). Nie mają markera `integration`; odpalisz je samym `pytest tests/unit`.

Stan:

- `test_config.py` — `Settings` (pydantic-settings): domyślne wartości, nadpisanie
  przez ENV, ignorowanie nieznanych zmiennych. Gdy runtime aplikacji nie jest
  zainstalowany (`pip install -r api/requirements.txt`), test jest pomijany (skip).

W kolejnych krokach dojdą tu testy m.in.: implementacji `LLMClient` (z `FakeLLMClient`),
składania promptu, mapowania odpowiedzi/błędów Tiki.
