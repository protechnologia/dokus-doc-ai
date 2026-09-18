"""Modele HTTP: POST /classify (kontrakt zamrozony — README "POST /classify").

Walidacja swiadomie MINIMALNA — tylko struktura (brak pol, puste listy, zly typ -> 422). Tresci
nie sprawdzamy: opcja bez opisu nie psuje wyniku (model jej nie wybierze), a odrzucenie calego
wywolania przez jedna luke w katalogu klienta byloby gorsze; powtorzone `id` tez nie psuja
mapowania (etykiety ida po pozycji). Spojnosc `option_id`/`rationale`/`error` z `outcome`
zapewnia serwis przy skladaniu wyniku, nie walidator odpowiedzi.
"""

from __future__ import annotations

from typing   import Literal
from pydantic import BaseModel, Field, StrictInt, StrictStr

from app.classification import ClassificationResult
from app.llm import LLMUsage

# Identyfikator opcji klienta. STRICT, bo kontrakt obiecuje zwrot w typie z zadania: bez tego
# pydantic zamienilby `true` na 1, a `21.0` na 21 (lax), a my odeslalibysmy inny klucz niz dostalismy.
OptionId = StrictInt | StrictStr

# Wynik klasyfikacji. Kazda odpowiedz modelu (takze bezuzyteczna) to 200 z jednym z nich — 5xx
# oznacza, ze odpowiedzi modelu nie bylo.
ClassifyOutcome = Literal["matched", "no_match", "invalid_response"]


class ClassifyOption(BaseModel):
    """Jedna opcja do wyboru w `POST /classify` — dane klienta, ktorych usluga nie interpretuje.

    `id` jest nieprzezroczysty: nie trafia do modelu (ten widzi etykiety nadane przez usluge)
    i wraca w `option_id` w typie z zadania. `name`/`description`/`examples` to wszystko, co model
    wie o opcji.
    """

    id: OptionId          = Field(description="Identyfikator opcji po stronie klienta: liczba calkowita albo string; wraca w `option_id` w tym samym typie.")
    name: str             = Field(description="Nazwa opcji.")
    description: str      = Field(description="Opis: czego dotyczy opcja.")
    examples: str | None  = Field(default=None, description="Przyklady spraw pasujacych do opcji; null, brak klucza albo pusty tekst = brak przykladow.")


class ClassifyRequest(BaseModel):
    """Wejscie `POST /classify` — streszczenia dokumentu + lista opcji; prompt buduje usluga.

    Dlugosci tu swiadomie NIE limitujemy: do okna modelu ma sie zmiescic caly zlozony prompt,
    ktorego dlugosc zna dopiero serwis (413 wzgledem `LLM_MAX_INPUT_CHARS`). Kolejnosc `summaries`
    nie ma znaczenia (brak konwencji "pierwszy = pismo glowne").
    """

    summaries: list[str]           = Field(min_length=1, description="Streszczenia plikow dokumentu, np. pola `summary` z POST /summarize; co najmniej jedno. Kolejnosc bez znaczenia.")
    options: list[ClassifyOption]  = Field(min_length=1, description="Opcje do wyboru; co najmniej jedna.")


class ClassifyMetadata(BaseModel):
    """Metadane odpowiedzi /classify — `model` i `usage` jak w `SummarizeMetadata` (to samo mapowanie po stronie klienta).

    `usage` to jedyny sygnal po fakcie dla cichych awarii: `prompt_tokens` stojace na 4096/8192
    = serwer modelu ucial prompt; `completion_tokens` rowne limitowi = odpowiedz urwana.
    """

    model: str      = Field(description="Identyfikator modelu, ktory odpowiedzial, np. 'gpt-4o-mini'.")
    usage: LLMUsage = Field(default_factory=LLMUsage, description="Zuzycie tokenow (prompt/completion/total) — diagnostyka uciecia promptu i odpowiedzi.")


class ClassifyResponse(BaseModel):
    """Wyjscie `POST /classify` — wynik, uzasadnienie, pola audytu i metadane.

    Jeden ksztalt dla wszystkich trzech wynikow, bo klient zapisuje audyt (oba prompty + surowa
    odpowiedz) takze wtedy, gdy odpowiedz modelu jest bezuzyteczna. Klient decyduje po `outcome`,
    nie po `option_id` (`null` wystepuje przy `no_match` i `invalid_response`); ktore pola sa
    wypelnione przy danym wyniku — tabela w README.
    """

    outcome: ClassifyOutcome    = Field(description="matched = wybrano opcje; no_match = zadna opcja nie pasuje; invalid_response = odpowiedzi modelu nie da sie uzyc.")
    option_id: OptionId | None  = Field(description="`id` wybranej opcji w typie z zadania; null, gdy outcome != matched.")
    rationale: str | None       = Field(description="Uzasadnienie wyboru (1-2 zdania po polsku), takze przy no_match; null przy invalid_response.")
    error: str | None           = Field(description="Przyczyna, dla ktorej odpowiedzi modelu nie da sie uzyc; null, gdy outcome != invalid_response.")
    system_prompt: str          = Field(description="Prompt systemowy wyslany do modelu (audyt; format wewnetrzny — zapisywac jako tekst, nie parsowac).")
    user_prompt: str            = Field(description="Prompt uzytkownika wyslany do modelu (audyt; format wewnetrzny).")
    raw_response: str           = Field(description="Surowa odpowiedz modelu przed parsowaniem (audyt); przy invalid_response bywa niepoprawnym JSON-em.")
    metadata: ClassifyMetadata  = Field(description="Metadane: model i zuzycie tokenow.")

    @classmethod
    def from_result(
        cls,
        result: ClassificationResult,   # domenowy wynik z ClassificationService.classify
    ) -> ClassifyResponse:
        """Opis metody:
        Zmapuj domenowy `ClassificationResult` na model odpowiedzi HTTP. Cienkie, jawne
        przepisanie pol — spojnosc pol z `outcome` niesie wynik domenowy (jego konstruktory),
        tu jej nie sprawdzamy. Etykieta wybrana przez model (`result.label`) swiadomie NIE
        wychodzi: etykiety sa wewnetrzne (klient decyduje po `outcome` i `option_id`), a do
        audytu wystarcza `raw_response`.

        Przyklad argumentow:
            result=ClassificationResult(outcome="matched", option_id=21, label="OPT-1",
                                        rationale="Skarga na operatora.", error=None,
                                        model="gpt-4o-mini", usage=LLMUsage(...),
                                        system_prompt="...", user_prompt="...", raw_response="{...}")

        Przyklad wyniku:
            ClassifyResponse(outcome="matched", option_id=21, rationale="Skarga na operatora.",
                             error=None, system_prompt="...", user_prompt="...", raw_response="{...}",
                             metadata=ClassifyMetadata(model="gpt-4o-mini", usage=LLMUsage(...)))
        """
        return cls(
            outcome       = result.outcome,
            option_id     = result.option_id,
            rationale     = result.rationale,
            error         = result.error,
            system_prompt = result.system_prompt,
            user_prompt   = result.user_prompt,
            raw_response  = result.raw_response,
            metadata      = ClassifyMetadata(model=result.model, usage=result.usage),
        )
