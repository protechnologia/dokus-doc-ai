"""Testy jednostkowe czystych helperow OpenAILLMClient (krok 2.2) — bez sieci.

Testujemy dwie statyczne, czyste metody:
  - `_build_messages` — sklada liste wiadomosci wysylanych do modelu,
  - `_to_result`      — przepisuje odpowiedz SDK na nasz model `LLMResult`.
Sa statyczne, wiec wolamy je wprost na klasie, bez tworzenia instancji klienta i
bez zadnego wywolania API (zero I/O). `openai` musi byc zainstalowany — to twarda
zaleznosc projektu, a import modulu klienta go pociaga.

Mapowanie wyjatkow SDK -> LLMError ma osobny plik: `test_llm_openai_errors.py`.
"""

from types import SimpleNamespace

from app.llm import LLMResult
from app.llm.client_openai import OpenAILLMClient


# --- _build_messages: skladanie listy wiadomosci ---------------------------------


def test_build_messages_bez_systemu():
    # Scenariusz: nie podajemy promptu systemowego (system jest opcjonalny).
    # Oczekujemy: lista zawiera wylacznie jedna wiadomosc — uzytkownika.
    msgs = OpenAILLMClient._build_messages("Streszcz to", None)
    assert msgs == [{"role": "user", "content": "Streszcz to"}]


def test_build_messages_z_systemem_kolejnosc():
    # Scenariusz: podajemy prompt systemowy.
    # Oczekujemy: trafia PRZED wiadomosc uzytkownika.
    # Dlaczego wazne: OpenAI traktuje wiadomosc 'system' jako pierwsza w liscie.
    msgs = OpenAILLMClient._build_messages("dok", "Streszczaj po polsku")
    assert [m["role"] for m in msgs] == ["system", "user"]
    assert msgs[0]["content"] == "Streszczaj po polsku"


# --- Atrapa odpowiedzi SDK (dla testow _to_result) -------------------------------


def _fake_resp(content, usage, model="gpt-4o-mini"):
    """Zbuduj minimalna atrape odpowiedzi OpenAI o tym samym KSZTALCIE, co prawdziwa.

    `_to_result` siega do odpowiedzi tylko w trzech miejscach:
        resp.choices[0].message.content     -> tekst odpowiedzi
        resp.usage.{prompt,completion,total}_tokens -> zuzycie tokenow
        resp.model                          -> nazwa modelu, ktory odpowiedzial
    Odwzorowujemy dokladnie te zagniezdzenia. `SimpleNamespace` to lekki obiekt,
    ktoremu nadajemy dowolne pola (tu udajemy obiekty SDK bez importu `openai`).
    """
    message = SimpleNamespace(content=content)          # resp.choices[0].message
    choice = SimpleNamespace(message=message)           # resp.choices[0]
    return SimpleNamespace(choices=[choice], usage=usage, model=model)


# --- _to_result: przepisanie odpowiedzi SDK na LLMResult -------------------------


def test_to_result_happy_path():
    # Scenariusz: kompletna, poprawna odpowiedz modelu.
    # Oczekujemy: tekst, nazwa modelu i liczniki tokenow przepisane wprost do LLMResult.
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=3, total_tokens=13)
    resp = _fake_resp("Tak.", usage, model="gpt-4o-mini")

    # fallback celowo INNY niz resp.model — by udowodnic, ze model bierzemy z odpowiedzi.
    r = OpenAILLMClient._to_result(resp, fallback_model="fallback-nieuzyty")

    assert isinstance(r, LLMResult)
    assert r.text == "Tak."
    assert r.model == "gpt-4o-mini"                      # z resp.model, nie z fallbacku
    assert (r.usage.prompt_tokens, r.usage.completion_tokens, r.usage.total_tokens) == (10, 3, 13)


def test_to_result_content_none():
    # Scenariusz: OpenAI zwraca content=None (zdarza sie przy wywolaniu narzedzia
    # albo gdy tresc zostala odfiltrowana).
    # Oczekujemy: helper zamienia None na pusty string, a nie wywala sie.
    usage = SimpleNamespace(prompt_tokens=5, completion_tokens=0, total_tokens=5)
    r = OpenAILLMClient._to_result(_fake_resp(None, usage), fallback_model="x")
    assert r.text == ""


def test_to_result_usage_none():
    # Scenariusz: odpowiedz nie zawiera sekcji `usage` (jest None).
    # Oczekujemy: liczniki tokenow sa zerami, bez wyjatku (np. AttributeError).
    r = OpenAILLMClient._to_result(_fake_resp("cos", None), fallback_model="x")
    assert r.usage.total_tokens == 0


def test_to_result_model_fallback():
    # Scenariusz: odpowiedz nie poda nazwy modelu (resp.model puste).
    # Oczekujemy: uzywamy modelu, ktorym skonfigurowano klienta (fallback_model).
    usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2)
    resp = _fake_resp("x", usage, model="")
    r = OpenAILLMClient._to_result(resp, fallback_model="gpt-4o-mini")
    assert r.model == "gpt-4o-mini"
