"""Testy jednostkowe spójności danych golden setu klasyfikacji (`samples/classification/`) — bez I/O sieci.

Trzy pliki utrzymywane osobno (katalog ręcznie, golden ręcznie, streszczenia skryptem) łatwo się
rozjeżdżają: nowe pismo bez streszczenia, `id` z literówką, stanowisko przypisane do cudzej grupy.
Integracyjny test golden setu wywaliłby się wtedy KeyErrorem albo — gorzej — liczył trafność na
błędnych danych. Te testy łapią to od razu, bez modelu.
"""

import json
from pathlib import Path

from app.models import ClassifyRequest

_REPO      = Path(__file__).resolve().parents[2]
_DATA      = _REPO / "samples" / "classification"
_CATALOG   = json.loads((_DATA / "katalog.json").read_text(encoding="utf-8"))["grupy"]
_GOLDEN    = json.loads((_DATA / "golden.json").read_text(encoding="utf-8"))["pisma"]
_SUMMARIES = json.loads((_DATA / "summaries.json").read_text(encoding="utf-8"))["streszczenia"]


def test_golden_i_streszczenia_obejmuja_dokladnie_pisma_z_samples():
    """Każde pismo z `samples/summarization/` ma wpis w golden i streszczenie — i nic ponad to."""
    documents = {p.name for p in (_REPO / "samples" / "summarization").glob("[0-9][0-9]_*.docx")}

    assert [doc["plik"] for doc in _GOLDEN] == sorted(documents)   # każde raz, w kolejności plików
    assert set(_SUMMARIES) == documents
    assert all(summary.strip() for summary in _SUMMARIES.values())


def test_katalog_zgodny_z_kontraktem_classify():
    """Grupy i stanowiska przechodzą walidację `ClassifyRequest` — idą do /classify bez przekształceń."""
    ClassifyRequest(summaries=["x"], options=_CATALOG)
    for group in _CATALOG:
        ClassifyRequest(summaries=["x"], options=group["stanowiska"])


def test_id_unikalne_w_obrebie_listy():
    """Golden porównuje po `id` — powtórzone `id` na jednej liście zrobiłoby porównanie niejednoznacznym."""
    group_ids = [group["id"] for group in _CATALOG]
    assert len(group_ids) == len(set(group_ids))
    for group in _CATALOG:
        position_ids = [position["id"] for position in group["stanowiska"]]
        assert len(position_ids) == len(set(position_ids)), group["name"]


def test_golden_wskazuje_istniejace_grupy_i_ich_stanowiska():
    """Oczekiwana grupa istnieje; stanowisko należy do TEJ grupy; brak grupy => brak stanowiska."""
    positions = {group["id"]: {p["id"] for p in group["stanowiska"]} for group in _CATALOG}

    for doc in _GOLDEN:
        if doc["grupa"] is None:
            assert doc["stanowisko"] is None, doc["plik"]
        else:
            assert doc["grupa"] in positions, doc["plik"]
            assert doc["stanowisko"] is None or doc["stanowisko"] in positions[doc["grupa"]], doc["plik"]
