<!--
Sekcje w tagach, nie w nagłówkach `##`: nagłówek łatwo podrobić treścią pisma, a tagi wskazuje
prompt systemowy. Formatowanie pozycji: ClassificationUserPrompt (classification/prompt_user.py).

Przykład złożonego promptu (wcięcia i puste linie między opcjami tylko dla czytelności — model dostaje tekst bez nich):

Streszczenia plików dokumentu (kolejność bez znaczenia):

<streszczenia>
  <streszczenie>
    • Typ pisma: skarga
    • Czego dotyczy: zawyżony rachunek za telefon
  </streszczenie>
</streszczenia>

Opcje do wyboru:

<opcje>
  <opcja>
    OPT-1: Skargi konsumenckie
    Opis: Skargi na dostawców usług.
    Przykłady: Skarga na zawyżony rachunek.
  </opcja>

  <opcja>
    OPT-2: Numeracja
    Opis: Przydział zasobów numeracji.
  </opcja>

  <opcja>
    OPT-00: Brak dopasowania
    Opis: Żadna z pozostałych opcji nie pasuje do dokumentu.
  </opcja>
</opcje>
-->

Streszczenia plików dokumentu (kolejność bez znaczenia):

<streszczenia>
{{summaries}}
</streszczenia>

Opcje do wyboru:

<opcje>
{{options}}
</opcje>
