"""Testy jednostkowe kontraktu HTTP trunkacji — walidacja proporcji i mapowanie metadanych, bez sieci.

`TruncationParams` (wspólna baza `SummarizeRequest` i `SummarizeDocumentRequest`) sprawdzana
bezpośrednio na modelach: `ValidationError` z pydantic to w endpoincie 422 (samo podpięcie do
endpointów pokrywają `test_fastapi_summarize.py` / `test_fastapi_pipeline.py`). Mapowanie
domena -> API (`SummarizeMetadata.from_metadata`) dla obu stanów `parts`: None i wypełnione.
"""

import pytest
from pydantic import ValidationError

from app.llm import LLMUsage
from app.models import SummarizeDocumentRequest, SummarizeMetadata, SummarizeRequest
from app.summarization import SummarizationMetadata, TextPart, TextParts


# --- TruncationParams: wartości domyślne i poprawne ------------------------------


def test_brak_proporcji_daje_domyslne_45_35():
    """Klucze pominięte -> domyślne 45/35 (środek 20)."""
    req = SummarizeRequest(text="Pismo")
    assert (req.head_percent, req.tail_percent) == (45, 35)


@pytest.mark.parametrize("head, tail", [(0, 0), (45, 55), (100, 0), (0, 100)])
def test_proporcje_brzegowe_sa_poprawne(head, tail):
    """Zera i suma równa 100 są dozwolone (0 wyłącza część, 45/55 = bez środka)."""
    req = SummarizeRequest(text="Pismo", head_percent=head, tail_percent=tail)
    assert (req.head_percent, req.tail_percent) == (head, tail)


# --- TruncationParams: odrzucane wartości (422) ----------------------------------


def test_suma_ponad_100_odrzucona():
    """`head_percent + tail_percent > 100` -> `ValidationError` (w endpoincie 422)."""
    with pytest.raises(ValidationError):
        SummarizeRequest(text="Pismo", head_percent=60, tail_percent=41)


@pytest.mark.parametrize("field, value", [("head_percent", -1), ("head_percent", 101), ("tail_percent", -5), ("tail_percent", 150)])
def test_wartosc_spoza_zakresu_odrzucona(field, value):
    """Pojedyncza proporcja spoza 0–100 -> `ValidationError`."""
    with pytest.raises(ValidationError):
        SummarizeRequest.model_validate({"text": "Pismo", field: value})


def test_null_odrzucony():
    """Jawny `null` to NIE brak klucza -> `ValidationError` (domyślna tylko przy pominięciu klucza)."""
    with pytest.raises(ValidationError):
        SummarizeRequest.model_validate({"text": "Pismo", "head_percent": None})


def test_model_dokumentu_dziedziczy_walidacje():
    """`SummarizeDocumentRequest` ma te same proporcje i ten sam walidator sumy."""
    req = SummarizeDocumentRequest(content_base64="eA==")
    assert (req.head_percent, req.tail_percent) == (45, 35)
    with pytest.raises(ValidationError):
        SummarizeDocumentRequest(content_base64="eA==", head_percent=90, tail_percent=20)


# --- SummarizeMetadata.from_metadata: domena -> API ------------------------------


def test_from_metadata_bez_trunkacji_parts_null():
    """Bez cięcia -> `parts` None, `sent_chars` przepisane; stare pola bez zmian."""
    meta = SummarizationMetadata(model="fake-echo", input_chars=42, truncated=False, usage=LLMUsage(total_tokens=15), sent_chars=42)

    api = SummarizeMetadata.from_metadata(meta)

    assert api.model_dump() == {
        "model": "fake-echo", "input_chars": 42, "truncated": False,
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 15},
        "sent_chars": 42, "parts": None,
    }


def test_from_metadata_z_trunkacja_przepisuje_czesci():
    """Po cięciu -> trzy części z proporcją i zakresem; wyłączona część ma `start`/`end` None."""
    parts = TextParts(head=TextPart(percent=45, start=0, end=40467), middle=TextPart(percent=0), tail=TextPart(percent=55, start=65000, end=120000))
    meta = SummarizationMetadata(model="m", input_chars=120000, truncated=True, usage=LLMUsage(), sent_chars=89998, parts=parts)

    api = SummarizeMetadata.from_metadata(meta)

    assert api.parts.model_dump() == {
        "head":   {"percent": 45, "start": 0, "end": 40467},
        "middle": {"percent": 0, "start": None, "end": None},
        "tail":   {"percent": 55, "start": 65000, "end": 120000},
    }
