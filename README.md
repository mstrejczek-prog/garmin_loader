# Garmin Plan Loader

Strona do wgrywania **planu treningowego od trenera** do **Garmin Connect** – razem z datami w kalendarzu, tak żeby treningi same pojawiły się na zegarku po synchronizacji.

Działa w przeglądarce: **na komputerze i na telefonie**, bez instalowania czegokolwiek. Na telefonie można ją dodać do ekranu głównego („Dodaj do ekranu początkowego”) – otwiera się wtedy jak zwykła aplikacja.

> ⚠️ **Program nieoficjalny.** Nie jest związany z firmą Garmin ani przez nią wspierany. Korzysta z nieoficjalnej biblioteki [python-garminconnect](https://github.com/cyberjunky/python-garminconnect), która może przestać działać po zmianach po stronie Garmina. Używasz na własną odpowiedzialność, zgodnie z regulaminem Garmin Connect.

<p><img src="docs/ekran-komputer.png" alt="Strona na komputerze" width="68%"> <img src="docs/ekran-telefon.png" alt="Strona na telefonie" width="26%"></p>

## Jak używać

1. **Otwórz stronę** (adres serwera, na którym działa – patrz „Uruchomienie na serwerze”).
2. **Zaloguj się** e-mailem i hasłem do Garmin Connect. Jeśli masz włączone MFA, strona poprosi o kod z e-maila/SMS/aplikacji.
3. **Ustaw raz swoje strefy** (przycisk „Moje strefy i FTP”): strefy tętna dla biegu, roweru i pływania, FTP oraz strefy mocy roweru w watach (można je policzyć z FTP jednym przyciskiem). Zostają zapisane w tej przeglądarce i są używane przy każdym kolejnym planie.
4. **Skopiuj prompt** przyciskiem „Skopiuj prompt dla AI” i wklej go do swojego asystenta AI (ChatGPT, Claude, Gemini…) razem z planem od trenera. Na telefonie możesz też użyć „Wyślij prompt do aplikacji AI”, który otwiera systemowe menu udostępniania. Prompt już zna Twoje strefy, więc AI o nie nie pyta. AI zwróci plan w formacie JSON.
5. **Wklej JSON** w pole planu (albo wczytaj plik). Podgląd pojawia się sam: każdy trening jako karta z wykresem intensywności i listą kroków – **sprawdź go, zanim wyślesz**.
6. **Wyślij.** Treningi trafią do Garmin Connect (Trening → Treningi) i do kalendarza. Zsynchronizuj zegarek.

Przycisk „Przykład” wstawia gotowy przykładowy plan, żeby zobaczyć, jak to działa.

Kolejny tydzień? Wklej nowy plan i wyślij – treningi o tej samej nazwie nie są dublowane, a jeśli zmieniła się data, wpis w kalendarzu jest przestawiany. Zaznacz „Nadpisz”, gdy zmieniły się same kroki.

Strefy i zapamiętane logowanie są zapisane **osobno na każdym urządzeniu** (w przeglądarce). Na telefonie i na komputerze ustawiasz je raz, na każdym z osobna.

## Format planu

```json
{
  "strefy": {
    "bieg": { "S1": [80, 124], "S2": [125, 148], "S3": [149, 170], "S4": [171, 182], "S5": [183, 196] }
  },
  "treningi": [
    {
      "nazwa": "3 x 3 km @ 4:10-4:15",
      "sport": "bieg",
      "data": "2026-10-06",
      "kroki": [
        { "typ": "rozgrzewka", "czas": "15:00", "cel": "S2" },
        { "powtorz": 3, "kroki": [
            { "typ": "interwal", "dystans": "3 km", "cel": "4:10-4:15" },
            { "typ": "przerwa",  "czas": "3:00",    "cel": "S1" }
        ]},
        { "typ": "schlodzenie", "czas": "10:00", "cel": "S1" }
      ]
    },
    { "data": "2026-10-07", "wolne": true }
  ]
}
```

- **Cel kroku:** strefa (`S1`–`S5`, wg Twojej tabeli), zakres tętna (`HR 150-160`), tempo (`4:10-4:15`), dla roweru moc (`200-220W`) albo brak celu.
- Krok ma **czas** albo **dystans** albo `"przycisk": true` (trwa do naciśnięcia przycisku okrążenia), nigdy kilka naraz.
- Tempo: min/km dla biegu, marszu i turystyki (`4:10-4:15`), min/100 m dla pływania (`1:50-2:00`). Kadencja: `90-100rpm`.
- **Rower, moc:** `"cel"` może być w watach (`200-220W`), w procentach FTP (`90-95% FTP`, program przeliczy na waty z Twojego FTP; pojedyncza wartość, np. `95% FTP`, daje pasmo +/-2 punkty procentowe) albo strefą mocy `P1`-`P5` z Twojej tabeli. `S1`-`S5` to zawsze strefy tętna. FTP można też podać w samym planie (`"ftp": 220`).

## Rodzaje treningów

`"sport"` może mieć wartość: `bieg`, `rower`, `plywanie`, `silowy`, `cardio`, `joga`, `pilates`, `hiit`, `mobilnosc`, `marsz`, `turystyka`, `inny` (polskie i angielskie nazwy są rozpoznawane). Wielosportu (kilka dyscyplin w jednym treningu) program nie obsługuje - rozbij go na osobne treningi. Sporty bez własnej tabeli stref (siła, cardio, marsz itd.) używają tętna z tabeli biegowej; rower i pływanie mają własne.

### Trening siłowy

Kroki to ćwiczenia z katalogu Garmin Connect (ok. 1500 pozycji):

```json
{
  "nazwa": "Siła A",
  "sport": "silowy",
  "kroki": [
    { "typ": "rozgrzewka", "czas": "8:00" },
    { "cwiczenie": "Barbell Back Squat", "serie": 4, "powtorzenia": 6, "ciezar": "70kg", "przerwa": "2:00" },
    { "cwiczenie": "Plank", "serie": 3, "czas": "0:45", "przerwa": "1:00" },
    { "powtorz": 3, "kroki": [
      { "cwiczenie": "Pull-up", "powtorzenia": 6 },
      { "cwiczenie": "Dumbbell Row", "powtorzenia": 10, "ciezar": 20 }
    ] }
  ]
}
```

- Nazwę ćwiczenia podaj po angielsku jak w Garmin Connect (np. `Romanian Deadlift`); program rozpoznaje też popularne polskie nazwy (`przysiad ze sztangą`, `martwy ciąg`, `podciąganie`).
- Gdy ćwiczenia nie ma w katalogu, dodaj `"kategoria"` (np. `"kategoria": "LEG_CURL"`) - Garmin pokaże wtedy samą kategorię, a nazwa z planu trafi do opisu w podglądzie.
- `powtorz` robi serie łączone (obwód): kolejne ćwiczenia w rundach.
- `ciezar` jest w kilogramach i opcjonalny, `przerwa` to odpoczynek po każdej serii (także po ostatniej, jak w edytorze Garmina).

> **Uwaga:** format ćwiczeń siłowych jest zgodny z tym, co buduje biblioteka python-garminconnect. Pozostałe typy (pływanie, joga, HIIT itd.) używają zwykłych kroków i nie zostały jeszcze sprawdzone na prawdziwym koncie Garmin - jeśli Garmin coś odrzuci, strona pokaże jego komunikat w logu wysyłki.

- Sekcja `strefy` w planie jest **opcjonalna**: bez niej program użyje stref zapisanych w „Moje strefy i FTP”. Jeśli plan ją zawiera, ma pierwszeństwo.
- Prawidłowy prompt do swojego AI dostajesz przyciskiem na stronie – nie musisz pisać formatu ręcznie.

## Prywatność i bezpieczeństwo

- Hasło jest wysyłane do serwera tylko w chwili logowania, przekazywane do Garmina i **nigdzie nie zapisywane**.
- Serwer **nie ma bazy danych i niczego nie przechowuje**. Po zalogowaniu oddaje przeglądarce token logowania Garmina **zaszyfrowany kluczem serwera** (`SECRET_KEY`); przeglądarka trzyma go u siebie i dołącza do wysyłki. Bez klucza serwera token zapisany w przeglądarce jest bezużyteczny. Gdy zdejmiesz ptaszek „Zapamiętaj”, token znika po zamknięciu karty. „Wyloguj” usuwa go z przeglądarki.
- Strefy, FTP i szkic planu zostają w pamięci przeglądarki (localStorage) na danym urządzeniu.
- Kto uruchamia serwer, technicznie ma dostęp do ruchu przez niego przechodzącego – korzystaj z serwera swojego albo osoby, której ufasz. Kod jest w tym repozytorium.
- Strona nie ma telemetrii ani zewnętrznych skryptów.

## Uruchomienie na serwerze

Najprościej na [Render](https://render.com) (darmowy plan wystarcza):

1. Render → **New → Blueprint** → wskaż to repozytorium. Plik `render.yaml` ustawia wszystko sam, łącznie z losowym `SECRET_KEY`.
2. Opcjonalnie ustaw `KOD_DOSTEPU` – wtedy strona poprosi o ten kod, zanim cokolwiek zrobi (żeby obce osoby nie korzystały z Twojego serwera).
3. Otwórz adres `https://…onrender.com` na komputerze i telefonie.

Działa też na każdym hostingu z `Procfile` (Railway, Heroku, Fly.io…). Zmienne środowiskowe:

| Zmienna | Znaczenie |
|---|---|
| `SECRET_KEY` | **Wymagana.** Długi losowy tekst do szyfrowania tokenów. Jego zmiana wylogowuje wszystkich. |
| `KOD_DOSTEPU` | Opcjonalny kod dostępu do strony. |

Uwagi:

- Darmowy plan Render usypia serwer po ~15 minutach bezczynności; pierwsze otwarcie po przerwie trwa ok. minuty.
- Garmin bywa podejrzliwy wobec logowań z adresów centrów danych. Jeśli logowanie na serwerze nie przechodzi (komunikat o blokadzie lub zbyt wielu próbach), odczekaj i spróbuj ponownie albo uruchom stronę u siebie (niżej).
- Serwer ogranicza liczbę prób logowania (10 na 15 minut z jednego adresu).

## Uruchomienie u siebie

Wymaga Pythona 3.10+.

```bash
pip install -r requirements.txt
python web.py          # http://localhost:8000
```

Żeby otworzyć stronę z telefonu w tej samej sieci Wi‑Fi: `HOST=0.0.0.0 python web.py` i wejdź na `http://<adres-komputera>:8000`. (Schowek i udostępnianie w przeglądarce działają w pełni tylko przez HTTPS albo na `localhost`; bez tego prompt pokaże się do ręcznego skopiowania.)

Testy: `pip install pytest && python -m pytest tests`

## Licencja

MIT – zobacz [LICENSE](LICENSE).
