"""Testy jednostkowe mechanizmu promptów (`PromptTemplate`) — bez sieci.

Czyste helpery (`_strip_comments`/`_strip_edge_newlines`/`_find_placeholders`/
`_check_placeholders`/`_substitute`) wołane wprost na napisach. Konstruktor i `_render` na realnym
pliku `summary_user.md` z podklasami (zła deklaracja placeholderów, poprawna do składania).
"""

import pytest

from app.prompt import PromptTemplate, PromptTemplateError

# --- _strip_comments: komentarze całoliniowe -------------------------------------


def test_strip_comments_jednoliniowy_znika_razem_z_linia():
    """Komentarz w osobnej linii znika z jej znakiem nowej linii; tekst wokół bez zmian."""
    assert PromptTemplate._strip_comments("Akapit 1.\n<!-- dlaczego -->\nAkapit 2.", "p.md") == "Akapit 1.\nAkapit 2."


def test_strip_comments_wieloliniowy_na_poczatku_zostawia_odstep():
    """Wieloliniowy komentarz na początku znika; pusta linia odstępu zostaje (zdejmuje ją `_strip_edge_newlines`)."""
    raw = "<!--\nDlaczego tak:\n  tabela | pomiarów\n-->\n\nJesteś asystentem."
    assert PromptTemplate._strip_comments(raw, "p.md") == "\nJesteś asystentem."


def test_strip_comments_puste_linie_wokol_zostaja():
    """Komentarz przyklejony do akapitu w środku tekstu: puste linie autora zostają dokładnie jak były."""
    raw = "Akapit 1.\n\n<!-- o akapicie 2 -->\nAkapit 2."
    assert PromptTemplate._strip_comments(raw, "p.md") == "Akapit 1.\n\nAkapit 2."


def test_strip_comments_wciecie_i_koniec_pliku_bez_nowej_linii():
    """Wcięty komentarz i komentarz na samym końcu pliku (bez `\\n`) też znikają w całości."""
    assert PromptTemplate._strip_comments("Tekst.\n  <!-- wcięty -->\n<!-- ostatni -->", "p.md") == "Tekst.\n"


def test_strip_comments_kilka_komentarzy_bez_zjadania_tekstu_miedzy_nimi():
    """Dwa komentarze rozdzielone tekstem: znika każdy osobno, tekst między nimi zostaje."""
    raw = "<!-- a -->\nŚrodek promptu.\n<!-- b -->\nKoniec."
    assert PromptTemplate._strip_comments(raw, "p.md") == "Środek promptu.\nKoniec."


def test_strip_comments_placeholder_w_komentarzu_znika():
    """`{{…}}` wewnątrz komentarza wycinany razem z nim — nie liczy się potem jako placeholder."""
    assert PromptTemplate._strip_comments("<!-- kiedyś było {{stare}} -->\nRamka:\n{{text}}", "p.md") == "Ramka:\n{{text}}"


def test_strip_comments_bez_komentarzy_bez_zmian():
    """Tekst bez komentarzy przechodzi bajt w bajt (w tym strzałka `->` i klamry)."""
    raw = 'Wybierz -> etykietę {"label": "OPT-1"}\n'
    assert PromptTemplate._strip_comments(raw, "p.md") == raw


@pytest.mark.parametrize(
    "raw",
    [
        "Tekst <!-- inline -->\n",                       # komentarz po tekście w tej samej linii
        "<!-- inline --> tekst\n",                       # tekst po komentarzu w tej samej linii
        "<!-- niedomknięty\nJesteś asystentem.",         # brak `-->`
        "Tekst.\n-->\n",                                 # osierocone `-->`
        "<!-- a --> prompt\n<!-- b -->\n",               # tekst za pierwszym: NIE wolno zjeść „prompt" aż do drugiego
    ],
)
def test_strip_comments_niecaloliniowy_rzuca(raw):
    """Komentarza, którego nie da się bezpiecznie wyciąć całymi liniami, nie wycinamy „na oko" — błąd z nazwą pliku."""
    with pytest.raises(PromptTemplateError, match=r"p\.md.*komentarz poza osobnymi liniami"):
        PromptTemplate._strip_comments(raw, "p.md")


# --- _strip_edge_newlines: odstęp po komentarzu i końcówka z edytora -------------


def test_strip_edge_newlines_zdejmuje_brzegi_zostawia_srodek():
    """Wiodące i końcowe `\\n` (także kilka) znikają; wewnętrzne i spacje zostają."""
    assert PromptTemplate._strip_edge_newlines("\n\nRamka:\n\n{{text}}\n\n") == "Ramka:\n\n{{text}}"
    assert PromptTemplate._strip_edge_newlines("   • Pole") == "   • Pole"


# --- _find_placeholders: co jest placeholderem ------------------------------------


def test_find_placeholders_zbiera_pelne_tokeny_bez_powtorzen():
    """Każdy token `{{…}}` raz, w pełnej postaci (z klamrami), niezależnie od liczby wystąpień."""
    assert PromptTemplate._find_placeholders("{{options}} i {{summaries}}, znów {{options}}") == frozenset({"{{options}}", "{{summaries}}"})


def test_find_placeholders_pomija_pojedyncze_klamry_i_spacje():
    """Klamry z przykładu JSON-a, `{text}` i `{{ text }}` (ze spacjami) to nie placeholdery."""
    assert PromptTemplate._find_placeholders('Zwróć {"label": "OPT-1"}, nie {text} ani {{ text }}.') == frozenset()


# --- _check_placeholders: zgodność pliku z klasą ----------------------------------


def test_check_placeholders_zgodne_przechodzi():
    """Te same tokeny w tekście i w deklaracji; także prompt stały bez placeholderów."""
    PromptTemplate._check_placeholders("Ramka:\n\n{{text}}", frozenset({"{{text}}"}), "u.md")
    PromptTemplate._check_placeholders("Stały tekst.", frozenset(), "s.md")


def test_check_placeholders_literowka_zglasza_brak_i_nieznany():
    """Literówka w pliku -> błąd z nazwą pliku, brakującym i nieznanym tokenem."""
    with pytest.raises(PromptTemplateError, match=r"u\.md.*brak: \{\{text\}\}; nieznane: \{\{tekst\}\}"):
        PromptTemplate._check_placeholders("Ramka:\n\n{{tekst}}", frozenset({"{{text}}"}), "u.md")


def test_check_placeholders_przypadkowy_placeholder_w_prompcie_stalym():
    """Przypadkowe `{{…}}` w prompcie zadeklarowanym jako stały -> błąd, a nie dosłowny tekst u modelu."""
    with pytest.raises(PromptTemplateError, match=r"nieznane: \{\{text\}\}"):
        PromptTemplate._check_placeholders("Stały {{text}}", frozenset(), "s.md")


def test_check_placeholders_stala_bez_klamer_rzuca():
    """Stała zadeklarowana jako nazwa bez klamer (`"text"`) nie zgadza się z tokenem w pliku -> błąd."""
    with pytest.raises(PromptTemplateError, match=r"brak: text; nieznane: \{\{text\}\}"):
        PromptTemplate._check_placeholders("Ramka:\n\n{{text}}", frozenset({"text"}), "u.md")


# --- _substitute: podstawienie jednym przebiegiem ---------------------------------


def test_substitute_wstawia_kazde_wystapienie():
    """Wszystkie wystąpienia tokenu zastąpione; klamry w stałym tekście nietknięte."""
    wynik = PromptTemplate._substitute('{{a}} {"k": 1} {{b}} {{a}}', {"{{a}}": "X", "{{b}}": "Y"})
    assert wynik == 'X {"k": 1} Y X'


def test_substitute_nie_podstawia_w_wstawionej_wartosci():
    """Treść pisma z `{{options}}` zostaje dosłownie — wstawiona wartość nie jest ponownie przeszukiwana."""
    wynik = PromptTemplate._substitute("{{summaries}}\n---\n{{options}}", {"{{summaries}}": "Pismo {{options}}", "{{options}}": "OPT-1"})
    assert wynik == "Pismo {{options}}\n---\nOPT-1"


# --- __init__ + _render: odczyt pliku, sprawdzenie, złożenie ----------------------


class _UserPrompt(PromptTemplate):
    """Podklasa testowa na realnym `summary_user.md` — poprawna deklaracja."""

    FILE         = "summary_user.md"
    PLACEHOLDERS = frozenset({"{{text}}"})


def test_konstruktor_rozjazd_pliku_z_klasa_rzuca():
    """Podklasa deklaruje inne placeholdery niż plik -> `PromptTemplateError` już przy konstrukcji."""

    class _ZlaDeklaracja(PromptTemplate):
        FILE         = "summary_user.md"
        PLACEHOLDERS = frozenset({"{{document}}"})

    with pytest.raises(PromptTemplateError, match=r"summary_user\.md.*brak: \{\{document\}\}; nieznane: \{\{text\}\}"):
        _ZlaDeklaracja()


def test_render_nie_wycina_komentarzy_ani_placeholderow_z_wartosci():
    """Komentarze wycinane tylko z szablonu: `<!-- … -->` i `{{…}}` w treści pisma trafiają do promptu dosłownie."""
    tresc = "Pismo z HTML-em <!-- ukryte -->\n<!-- linia -->\ni {{text}}"
    assert _UserPrompt()._render({"{{text}}": tresc}).endswith(tresc)
