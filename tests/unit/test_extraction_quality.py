"""Testy jednostkowe `PuaDetector` (krok 2.3.5) — detekcja smieciowej warstwy PUA, bez sieci.

`_is_pua`/`ratio` sa czyste (static/class) — wolamy wprost na klasie. `is_garbage` zalezy od
progu instancji, wiec na `PuaDetector()` (domyslny prog 0.30).

Znaki PUA konstruujemy jawnie przez `chr(0xF0DC)` (a nie literalami w zrodle) — czytelnie i
bez polegania na niewidocznych znakach w pliku.
"""

from app.extraction.quality import PuaDetector

# Pojedynczy znak z Private Use Area (U+F0DC) — taki jak w zmierzonym sample_01.pdf.
_PUA = chr(0xF0DC)


# --- _is_pua: rozpoznanie zakresow Private Use Area ------------------------------


def test_is_pua_rozpoznaje_zakresy():
    # BMP PUA (nasz przypadek U+F0xx) oraz supplementary; zwykla litera poza zakresem.
    assert PuaDetector._is_pua(0xF0DC) is True          # BMP PUA
    assert PuaDetector._is_pua(0xF0000) is True         # supplementary PUA-A
    assert PuaDetector._is_pua(ord("a")) is False


# --- ratio: udzial PUA wsrod znakow nie-bialych ----------------------------------


def test_ratio_liczy_tylko_nie_biale():
    # 3 znaki PUA + 3 litery + biale znaki (ignorowane): udzial PUA = 3/6 = 0.5.
    text = _PUA * 3 + "abc\n\n"
    assert PuaDetector.ratio(text) == 0.5


def test_ratio_pusty_lub_sam_whitespace_to_zero():
    # Brak znakow nie-bialych -> 0.0 (nie dzielimy przez zero, nie uznajemy za smiec).
    assert PuaDetector.ratio("") == 0.0
    assert PuaDetector.ratio("\n\n   \t") == 0.0


def test_ratio_sama_warstwa_pua_to_jeden():
    # Wylacznie znaki PUA -> 1.0 (kanoniczny smiec, jak natywna warstwa sample_01.pdf).
    assert PuaDetector.ratio(_PUA * 7) == 1.0


# --- is_garbage: decyzja wzgledem progu ------------------------------------------


def test_is_garbage_powyzej_progu():
    # Warstwa zdominowana przez PUA (100%) -> smiec.
    assert PuaDetector().is_garbage(_PUA * 7) is True


def test_is_garbage_zwykly_tekst_nie_jest_smieciem():
    # Poprawny tekst (0% PUA) -> nie smiec; pusty tez nie (to osobny przypadek "pusto").
    assert PuaDetector().is_garbage("Pismo do dekretacji w sprawie podatku") is False
    assert PuaDetector().is_garbage("") is False


def test_is_garbage_pojedynczy_symbol_pua_nie_przekracza_progu():
    # 1 znak PUA wsrod wielu liter (np. egzotyczny symbol w normalnym tekscie) < 30% -> nie smiec.
    assert PuaDetector().is_garbage("Zwykly tekst pisma " + _PUA + " dalej tresc") is False


def test_is_garbage_prog_konfigurowalny():
    # Prog jest wstrzykiwalny: tekst 50% PUA jest smieciem przy progu 0.3, ale nie przy 0.8.
    text = _PUA * 3 + "abc"   # 3/6 = 50% PUA
    assert PuaDetector(threshold=0.3).is_garbage(text) is True
    assert PuaDetector(threshold=0.8).is_garbage(text) is False
