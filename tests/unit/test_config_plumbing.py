"""Spojnosc plumbingu konfiguracji: `.env.example` <-> docker-compose <-> `Settings`.

Osobno od `test_config.py` (tam: walidacja i defaulty `Settings`). Tutaj NIE testujemy kodu,
tylko czy trzy niezalezne listy pokretel nie rozjechaly sie ze soba. Dlaczego to wart osobnego
testu (TODO nr 2 w CLAUDE.md): compose przekazuje do kontenera WYLACZNIE zmienne jawnie wypisane
w `environment`, wiec rozjazd jest cichy w obie strony i oba kierunki juz sie zdarzyly:

  - `LLM_TIMEOUT_SECONDS` bylo w `Settings`, ale compose go NIE przekazywal
    -> pokretlo martwe w kontenerze (aplikacja brala default mimo wpisu w `.env`),
  - `LLM_API_VERSION` compose przekazywal, ale `Settings` takiego pola NIE mialo
    -> zmienna wstrzykiwana w prozne (pozostalosc po odlozonym kliencie Azure).

Zaden z nich nie wywalal aplikacji — dlatego zlapal je dopiero pomiar, a nie testy. Ten plik
zamienia ten pomiar w straznika.

Parsujemy pliki jako DANE (yaml/regex), nie odpalamy `docker compose config` — test ma dzialac
bez Dockera (CI, maszyna dewelopera bez demona).
"""

import re
from pathlib import Path

import yaml

from app.config import Settings

# Korzen repo: tests/unit/<ten plik> -> dwa poziomy w gore.
_REPO_ROOT = Path(__file__).resolve().parents[2]

_COMPOSE_BASE = _REPO_ROOT / "docker-compose.yml"
_ENV_EXAMPLE = _REPO_ROOT / ".env.example"

# Nazwa zmiennej na poczatku linii, takze zakomentowanej (`# MAX_OCR_PAGES=30` to nadal
# dokumentacja pokretla — .env.example celowo trzyma defaulty zakomentowane).
_ENV_VAR = re.compile(r"^#?\s*([A-Z][A-Z0-9_]*)=", re.MULTILINE)

# Interpolacja `${VAR}` / `${VAR:-default}` w dowolnym pliku compose.
_INTERPOLATION = re.compile(r"\$\{([A-Z][A-Z0-9_]*)")


def _settings_env_names() -> set[str]:
    """Nazwy ENV odpowiadajace polom `Settings` (pole `tika_url` -> `TIKA_URL`)."""
    return {name.upper() for name in Settings.model_fields}


def _compose_environment_keys() -> set[str]:
    """Klucze, ktore baza compose jawnie przekazuje do kontenera `fastapi`."""
    compose = yaml.safe_load(_COMPOSE_BASE.read_text(encoding="utf-8"))
    return set(compose["services"]["fastapi"]["environment"])


def _env_example_names() -> set[str]:
    """Nazwy zmiennych udokumentowanych w `.env.example` (takze zakomentowane)."""
    return set(_ENV_VAR.findall(_ENV_EXAMPLE.read_text(encoding="utf-8")))


def _compose_interpolated_names() -> set[str]:
    """Zmienne `${...}` uzywane w KTORYMKOLWIEK pliku compose (takze warstwach)."""
    names: set[str] = set()
    for path in sorted(_REPO_ROOT.glob("docker-compose*.yml")):
        names |= set(_INTERPOLATION.findall(path.read_text(encoding="utf-8")))
    return names


# --- Niezmienniki -----------------------------------------------------------------


def test_compose_nie_przekazuje_zmiennych_spoza_settings():
    # Scenariusz: compose wstrzykuje do kontenera zmienna, ktorej `Settings` nie czyta.
    # Oczekujemy: pusto. Inaczej mamy martwe pokretlo (przypadek `LLM_API_VERSION`) —
    # pydantic ma `extra="ignore"`, wiec aplikacja NIE zaprotestuje, a config klamie.
    martwe = _compose_environment_keys() - _settings_env_names()
    assert not martwe, f"compose przekazuje zmienne, ktorych Settings nie czyta: {sorted(martwe)}"


def test_kazde_pokretlo_settings_jest_przekazane_przez_compose():
    # Scenariusz: pole w `Settings` istnieje, ale compose go nie wypisuje w `environment`.
    # Oczekujemy: pusto. Inaczej ustawienie w `.env` nie dociera do kontenera i cicho
    # dziala default z kodu (przypadek `LLM_TIMEOUT_SECONDS`).
    nieprzekazane = _settings_env_names() - _compose_environment_keys()
    assert not nieprzekazane, f"Settings ma pola nieprzekazane przez compose: {sorted(nieprzekazane)}"


def test_kazde_pokretlo_settings_jest_udokumentowane_w_env_example():
    # Scenariusz: pokretlo istnieje w kodzie, ale nie ma go w szablonie dla wdrozeniowca.
    # Oczekujemy: pusto (wpis moze byc zakomentowany — to nadal dokumentacja).
    nieudokumentowane = _settings_env_names() - _env_example_names()
    assert not nieudokumentowane, f"Pola Settings bez wpisu w .env.example: {sorted(nieudokumentowane)}"


def test_zmienne_interpolowane_w_compose_sa_udokumentowane():
    # Scenariusz: warstwa compose uzywa `${VAR}` (np. porty, BIND_ADDR), ale szablon o niej
    # milczy. Te zmienne NIE sa polami `Settings` (czyta je compose, nie aplikacja), wiec
    # zaden inny test ich nie pilnuje. Przypadek `OLLAMA_PORT`.
    nieudokumentowane = _compose_interpolated_names() - _env_example_names()
    assert not nieudokumentowane, f"Zmienne ${{...}} z compose bez wpisu w .env.example: {sorted(nieudokumentowane)}"
