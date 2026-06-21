"""Test integracyjny endpointu POST /extract-and-summarize (krok 2.5.2) — pelny pipeline przez usluge.

Marker: integration_fastapi. Uderza w kontener (fixture `fastapi_client`); gdy usluga
niedostepna -> SKIP. To test WIRINGU end-to-end (router -> DI obu serwisow -> ekstrakcja
przez realna Tike -> summaryzacja -> serializacja). Asercje sa PROVIDER-AGNOSTYCZNE (sam
kontrakt), wiec test przechodzi niezaleznie od `LLM_PROVIDER` kontenera: z `fake` jest
darmowy i deterministyczny (zalecane do CI), z `openai` robi realne wywolanie. Realny LLM +
jakosc promptu pokrywa `integration_llm` (`test_summarization_service.py`), realna ekstrakcja
`integration_tika`/`test_fastapi_extract.py`, a dokladne mapowanie bledow — jednostkowy
`tests/unit/test_fastapi_pipeline.py`.
"""

import base64
import io

import docx
import pytest

# Parasol `integration` + wezszy `integration_fastapi` (uderzamy w usluge FastAPI).
pytestmark = [pytest.mark.integration, pytest.mark.integration_fastapi]

# Polski pangram — dowod, ze tresc realnie przeszla przez ekstrakcje do odpowiedzi.
PL_PANGRAM = "Zażółć gęślą jaźń"


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def test_extract_and_summarize_docx_przez_endpoint(fastapi_client):
    """DOCX -> POST /extract-and-summarize: 200 + kontrakt (summary + pelny text + metadane obu etapow)."""
    document = docx.Document()
    document.add_paragraph(PL_PANGRAM)
    buf = io.BytesIO()
    document.save(buf)

    # Bez content_type/filename — autodetekcja typu po stronie Tiki.
    resp = fastapi_client.post("/extract-and-summarize", json={"content_base64": _b64(buf.getvalue())})

    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Streszczenie: cokolwiek zwrocone (fake echo lub realny model).
    assert body["summary"].strip() != ""
    # Pelny tekst z ekstrakcji niesie polskie znaki (dowod, ze ekstrakcja zasilila summaryzacje).
    assert "gęślą" in body["text"]

    # Metadane etapu ekstrakcji (realna Tika).
    ex = body["extraction"]
    assert "wordprocessing" in (ex["content_type"] or "")   # realny MIME DOCX
    assert ex["char_count"] > 0
    assert ex["word_count"] == 3

    # Metadane etapu summaryzacji.
    su = body["summarization"]
    assert su["model"]                                        # model obecny
    assert su["truncated"] is False                           # krotki tekst, bez truncacji
    assert su["usage"]["total_tokens"] > 0                    # zuzycie zmapowane


def test_extract_and_summarize_zly_base64_daje_422(fastapi_client):
    """Nie-base64 -> 422 (walidacja wejscia w routerze, bez kontaktu z Tika/LLM)."""
    resp = fastapi_client.post("/extract-and-summarize", json={"content_base64": "to nie jest base64!!!"})
    assert resp.status_code == 422, resp.text
