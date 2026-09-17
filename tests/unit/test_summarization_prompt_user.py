"""Testy jednostkowe promptu użytkownika summaryzacji (`SummaryUserPrompt`) — bez sieci.

Dokładny kształt promptu na realnym pliku `app/prompt/summary_user.md`. Mechanizm placeholderów:
`test_prompt_template.py`.
"""

from app.summarization.prompt_user import SummaryUserPrompt


def test_user_prompt_to_ramka_i_dokument():
    """Prompt użytkownika = ramka „Streść poniższy dokument:”, pusta linia, treść dokumentu — nic więcej."""
    assert SummaryUserPrompt().render(text="Pismo w sprawie podatku") == "Streść poniższy dokument:\n\nPismo w sprawie podatku"


def test_user_prompt_stala_to_token_z_pliku():
    """Stała `TEXT` to pełny token, dokładnie jak w pliku — wyszukanie `{{text}}` trafia w oba miejsca."""
    assert SummaryUserPrompt.TEXT == "{{text}}"
