"""Testy jednostkowe promptu systemowego summaryzacji (`SummarySystemPrompt`) — bez sieci.

Strażniki treści (format zmierzony na Bieliku — macierz w komentarzu `app/prompt/summary_system.md`)
na realnym pliku, więc łapią też wyciek komentarza z uzasadnieniem do promptu. Mechanizm:
`test_prompt_template.py`.
"""

import re

from app.summarization.prompt_system import SummarySystemPrompt


def test_system_prompt_zada_wypunktowania_wszystkich_pol():
    """Kontrakt formatu: pięć pól, każde jako punkt „• ”. Bez tego streszczenie traci strukturę."""
    prompt = SummarySystemPrompt().render()
    for pole in ("Typ pisma", "Nadawca", "Czego dotyczy", "Termin / data", "Oczekiwana akcja"):
        assert f"• {pole}" in prompt


def test_system_prompt_nie_numeruje_wlasnego_opisu_formatu():
    """Strażnik przed nawrotem defektu: numeracja W OPISIE formatu przecieka do WYJŚCIA.

    Historyczny błąd (2026-07-08): opis brzmiał „1. streszczenie… 2. wypunktowanie…”, a Bielik
    brał tę numerację za wzór odpowiedzi i zwracał listę `1.`–`9.` zamiast punktów. Model
    naśladuje najbardziej konkretny wzorzec w prompcie — a numerowana lista nim jest.
    """
    linie = SummarySystemPrompt().render().splitlines()
    numerowane = [l for l in linie if re.match(r"^\s*\d+[.)]\s", l)]
    assert not numerowane, f"opis formatu znów zawiera numerację: {numerowane}"


def test_system_prompt_nie_obiecuje_akapitu():
    """Akapit otwierający świadomie usunięty (macierz pomiarów w komentarzu `summary_system.md`).

    Model go nie oddaje bez przykładu jako tury `assistant`; obietnica w prompcie bez pokrycia
    w wyjściu to gorzej niż jej brak — dokumentacja i kontrakt zaczynają kłamać. Komentarz
    z uzasadnieniem mówi o akapicie wprost, więc test łapie też jego wyciek do promptu.
    """
    prompt = SummarySystemPrompt().render()
    assert "akapit" not in prompt.lower()
    assert "streszczenie naturalnym językiem" not in prompt


def test_system_prompt_bez_komentarza_i_odstepow_na_brzegach():
    """Do modelu idzie sam tekst promptu: zaczyna się od roli, bez komentarza i bez `\\n` na brzegach."""
    prompt = SummarySystemPrompt().render()
    assert prompt.startswith("Jesteś asystentem")
    assert "<!--" not in prompt and "-->" not in prompt
    assert not prompt.endswith("\n")
