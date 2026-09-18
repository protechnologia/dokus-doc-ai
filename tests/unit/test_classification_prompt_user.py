"""Testy jednostkowe promptu użytkownika klasyfikacji (`ClassificationUserPrompt`) — bez sieci.

Czyste helpery (`_format_summaries`/`_format_entries`/`_format_entry`) wołane wprost; `render`
na realnym pliku `app/prompt/classification_user.md` i pozycjach z realnego `OptionLabeler`.
Mechanizm placeholderów: `test_prompt_template.py`.
"""

import pytest

from app.classification.prompt_user import ClassificationUserPrompt
from app.classification.service_labels import ClassificationOption, LabeledEntry, OptionLabeler
from app.prompt import PROMPT_DIR

_OPTIONS = [
    ClassificationOption(id=21, name="Skargi", description="Skargi konsumentów.", examples="Skarga na rachunek."),
    ClassificationOption(id="db-7781", name="Kadry", description="Sprawy pracownicze.", examples=None),
]

_SUMMARIES = ["• Typ pisma: skarga\n• Czego dotyczy: zawyżony rachunek", "• Typ pisma: faktura VAT"]


# --- render: cały prompt -----------------------------------------------------------


def test_render_zwraca_gotowy_prompt():
    """Pełny kształt: streszczenia i pozycje opcji, każde w swoim tagu; pozycje pod etykietami, OPT-00 na końcu."""
    prompt = ClassificationUserPrompt().render(summaries=_SUMMARIES, entries=OptionLabeler(_OPTIONS).entries)

    assert prompt == (
        "Streszczenia plików dokumentu (kolejność bez znaczenia):\n"
        "\n"
        "<streszczenia>\n"
        "<streszczenie>\n• Typ pisma: skarga\n• Czego dotyczy: zawyżony rachunek\n</streszczenie>\n"
        "<streszczenie>\n• Typ pisma: faktura VAT\n</streszczenie>\n"
        "</streszczenia>\n"
        "\n"
        "Opcje do wyboru:\n"
        "\n"
        "<opcje>\n"
        "<opcja>\nOPT-1: Skargi\nOpis: Skargi konsumentów.\nPrzykłady: Skarga na rachunek.\n</opcja>\n"
        "<opcja>\nOPT-2: Kadry\nOpis: Sprawy pracownicze.\n</opcja>\n"
        "<opcja>\nOPT-00: Brak dopasowania\nOpis: Żadna z pozostałych opcji nie pasuje do dokumentu.\n</opcja>\n"
        "</opcje>"
    )


def test_przyklad_w_komentarzu_pliku_zgodny_z_render():
    """Przykład złożonego promptu w komentarzu `classification_user.md` = wynik `render` dla tych samych danych (linia po linii, bez wcięć i pustych linii czytelności)."""
    raw     = (PROMPT_DIR / ClassificationUserPrompt.FILE).read_text(encoding="utf-8")
    example = raw.split("Przykład złożonego promptu", 1)[1].split(":\n\n", 1)[1].split("\n-->", 1)[0]

    options = [
        ClassificationOption(id=1, name="Skargi konsumenckie", description="Skargi na dostawców usług.", examples="Skarga na zawyżony rachunek."),
        ClassificationOption(id=2, name="Numeracja", description="Przydział zasobów numeracji."),
    ]
    summaries = ["• Typ pisma: skarga\n• Czego dotyczy: zawyżony rachunek za telefon"]
    rendered = ClassificationUserPrompt().render(summaries=summaries, entries=OptionLabeler(options).entries)
    assert [line.strip() for line in rendered.split("\n") if line.strip()] == [line.strip() for line in example.split("\n") if line.strip()]


def test_render_bez_surowych_id():
    """Surowe `id` klienta (liczbowe i tekstowe) nie trafiają do promptu — model widzi tylko etykiety."""
    options = [
        ClassificationOption(id=7781, name="Skargi", description="Skargi konsumentów."),
        ClassificationOption(id="db-7781", name="Kadry", description="Sprawy pracownicze."),
    ]
    prompt = ClassificationUserPrompt().render(summaries=_SUMMARIES, entries=OptionLabeler(options).entries)
    assert "7781" not in prompt


def test_render_placeholder_w_streszczeniu_zostaje_doslownie():
    """Streszczenie z `{{options}}` (treść z zewnątrz) nie ściąga listy opcji w swoje miejsce."""
    prompt = ClassificationUserPrompt().render(summaries=["Pismo cytuje {{options}}"], entries=OptionLabeler(_OPTIONS).entries)
    assert "<streszczenie>\nPismo cytuje {{options}}\n</streszczenie>" in prompt
    assert prompt.count("OPT-1: Skargi") == 1


# --- _format_summaries: tagi i kolejność ---------------------------------------------


def test_format_summaries_kazde_w_tagach_w_kolejnosci_wejscia():
    """Każde streszczenie w osobnym `<streszczenie>`, brzegowe białe znaki zdjęte, kolejność wejścia."""
    wynik = ClassificationUserPrompt._format_summaries(["  B\n", "A"])
    assert wynik == "<streszczenie>\nB\n</streszczenie>\n<streszczenie>\nA\n</streszczenie>"


# --- _format_entry / _format_entries: pozycje listy ---------------------------------


@pytest.mark.parametrize("examples", [None, "", "   \n"])
def test_format_entry_puste_przyklady_pominiete(examples):
    """`null`, pusty i biały tekst przykładów znaczą to samo: brak linii „Przykłady”."""
    entry = LabeledEntry("OPT-2", ClassificationOption(id="db-7781", name="Kadry", description="Sprawy pracownicze.", examples=examples))
    assert ClassificationUserPrompt._format_entry(entry) == "OPT-2: Kadry\nOpis: Sprawy pracownicze."


def test_format_entry_z_przykladami():
    """Niepuste przykłady w osobnej linii, po opisie; brzegowe białe znaki pól zdjęte."""
    entry = LabeledEntry("OPT-1", ClassificationOption(id=21, name=" Skargi ", description="Skargi konsumentów.\n", examples=" Skarga na rachunek. "))
    assert ClassificationUserPrompt._format_entry(entry) == "OPT-1: Skargi\nOpis: Skargi konsumentów.\nPrzykłady: Skarga na rachunek."


def test_format_entry_opt_00_ze_stalym_opisem():
    """Pozycja bez opcji (`OPT-00`) dostaje stałą nazwę i opis usługi."""
    assert ClassificationUserPrompt._format_entry(LabeledEntry("OPT-00", None)) == "OPT-00: Brak dopasowania\nOpis: Żadna z pozostałych opcji nie pasuje do dokumentu."


def test_format_entries_kazda_pozycja_w_tagach_w_kolejnosci():
    """Każda pozycja w osobnym `<opcja>`, w kolejności `entries` (OPT-00 ostatnia)."""
    wynik = ClassificationUserPrompt._format_entries(OptionLabeler(_OPTIONS).entries)
    bloki = wynik.split("\n</opcja>\n")
    assert all(blok.startswith("<opcja>\n") for blok in bloki)
    assert wynik.endswith("\n</opcja>")
    assert [blok.removeprefix("<opcja>\n").split(":")[0] for blok in bloki] == ["OPT-1", "OPT-2", "OPT-00"]
