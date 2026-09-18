"""Zamrożenie streszczeń golden setu klasyfikacji: 20 pism -> POST /extract-and-summarize -> summaries.json.

Golden set mierzy KLASYFIKACJĘ na stałym wejściu (decyzja 1a, krok 10 w CLAUDE.md): streszczenia
generujemy raz i trzymamy w repo, żeby pomyłka wyboru nie mieszała się ze zmiennością streszczeń.
Idą przez działającą usługę (ekstrakcja + prompt streszczeń dokładnie jak na produkcji), więc
dostawca to ten z konfiguracji KONTENERA — sprawdź `docker compose config` przed uruchomieniem.

Kiedy uruchomić ponownie: po zmianie promptu streszczeń, ekstrakcji albo samych pism.

Uruchomienie z korzenia repo (usługa: docker compose up -d):
    .venv/bin/python samples/classification/build_summaries.py
    FASTAPI_URL=http://inny-host:8000 .venv/bin/python samples/classification/build_summaries.py
"""

from __future__ import annotations

import base64
import datetime
import json
import os
from pathlib import Path

import httpx

# --- Stałe -----------------------------------------------------------------------

_HERE      = Path(__file__).resolve().parent
_DOCUMENTS = _HERE.parent / "summarization"   # pisma golden setu streszczeń (01_…–20_….docx)
_OUTPUT    = _HERE / "summaries.json"
_BASE_URL  = os.environ.get("FASTAPI_URL", "http://localhost:8000")
_TIMEOUT   = 600.0                            # s — Bielik na CPU streszcza pismo w minutach

_OPIS = (
    "Zamrożone streszczenia 20 pism z samples/summarization/ — wejście golden setu POST /classify "
    "(katalog.json + golden.json). Wygenerowane skryptem build_summaries.py przez POST /extract-and-summarize; "
    "nie edytować ręcznie — po zmianie promptu streszczeń albo ekstrakcji wygenerować ponownie."
)


# --- Czyste helpery (bez I/O) ------------------------------------------------------


def _request_body(
    path: Path,   # plik pisma, np. Path("samples/summarization/01_skarga_na_bezczynnosc.docx")
) -> dict[str, str]:
    """Opis metody:
    Zbuduj ciało żądania /extract-and-summarize: plik w base64 + nazwa (podpowiedź typu dla Tiki).

    Przyklad argumentow:
        path=Path("samples/summarization/03_faktura_vat.docx")

    Przyklad wyniku:
        {"content_base64": "UEsDBBQABgAI...", "filename": "03_faktura_vat.docx"}
    """
    return {"content_base64": base64.b64encode(path.read_bytes()).decode("ascii"), "filename": path.name}


def _document(
    summaries: dict[str, str],   # plik -> streszczenie, np. {"01_skarga_na_bezczynnosc.docx": "• Typ pisma: ..."}
    models: set[str],            # modele, które odpowiedziały, np. {"gpt-4o-mini-2024-07-18"}
    today: datetime.date,        # data wygenerowania, np. date(2026, 9, 18)
) -> dict:
    """Opis metody:
    Złóż zawartość summaries.json. Model z metadanych odpowiedzi — jeden dla całego zbioru; więcej
    niż jeden znaczy, że konfiguracja zmieniła się w trakcie, a zbiór byłby niespójny.

    Przyklad argumentow:
        summaries={"01_...docx": "• Typ pisma: skarga"}, models={"gpt-4o-mini-2024-07-18"}, today=date(2026, 9, 18)

    Przyklad wyniku:
        {"opis": "...", "model": "gpt-4o-mini-2024-07-18", "wygenerowano": "2026-09-18",
         "streszczenia": {"01_...docx": "• Typ pisma: skarga"}}

    Raises:
        ValueError: odpowiedzi od więcej niż jednego modelu.
    """
    if len(models) != 1:
        raise ValueError(f"Streszczenia od różnych modeli ({sorted(models)}) — zbiór byłby niespójny.")
    return {"opis": _OPIS, "model": models.pop(), "wygenerowano": today.isoformat(), "streszczenia": summaries}


# --- Wywołanie (I/O) -------------------------------------------------------------


def main() -> None:
    """Opis metody:
    Streść wszystkie pisma golden setu przez działającą usługę i zapisz summaries.json.

    Przyklad argumentow:
        (brak — adres usługi z ENV FASTAPI_URL, domyślnie http://localhost:8000)

    Przyklad wyniku:
        plik samples/classification/summaries.json + postęp na stdout

    Raises:
        httpx.HTTPStatusError: usługa odpowiedziała błędem (np. 5xx dostawcy LLM) — zbiór nie powstaje.
    """
    summaries: dict[str, str] = {}
    models: set[str] = set()

    # --- Jedno pismo = jedno żądanie; błąd przerywa całość (nie zapisujemy zbioru z dziurą) ---
    with httpx.Client(base_url=_BASE_URL, timeout=_TIMEOUT) as client:
        for path in sorted(_DOCUMENTS.glob("[0-9][0-9]_*.docx")):
            response = client.post("/extract-and-summarize", json=_request_body(path))
            response.raise_for_status()
            body = response.json()
            summaries[path.name] = body["summary"]                 # streszczenie jak zwraca usługa (bez obróbki)
            models.add(body["summarization"]["model"])             # model faktycznie użyty przez kontener
            print(f"{path.name:<42} {body['summarization']['usage']['completion_tokens']:>4} tok")

    # --- Zapis: UTF-8, polskie znaki wprost, stabilna kolejność (diff w repo czytelny) ---
    document = _document(summaries, models, datetime.date.today())
    _OUTPUT.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"-> {_OUTPUT} ({len(summaries)} streszczeń, model {document['model']})")


if __name__ == "__main__":
    main()
