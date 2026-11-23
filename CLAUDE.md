# Garmin Plan Loader – pamięć projektu dla Claude

Jeden projekt do wszystkiego, co dotyczy Garmina: strona www do wgrywania planów od trenera
oraz **connector MCP**, przez który Claude w czacie claude.ai sam wysyła plany i czyta dane.
Wcześniejszy projekt FIT_ANALYZER (analiza FIT + czat) jest przenoszony tutaj i ma zostać zarchiwizowany.

## Architektura

| Plik | Rola |
|---|---|
| `web.py` | Flask: strona, API `/api/*` (logowanie, MFA, podgląd, wysyłka), rejestruje MCP |
| `mcp_serwer.py` | Serwer MCP `POST /mcp` (Streamable HTTP, bezstanowy, odpowiedzi JSON), narzędzia, autoryzacja |
| `garmin_dane.py` | Odczyt danych z Garmina → zwięzłe, defensywne podsumowania (brak pola = None, nie błąd) |
| `dysk_google.py` | Zapis FIT na Dysk Google (OAuth, zakres `drive.file`, upload multipart, bez dublowania) |
| `garmin_io.py` | Logowanie (hasło/MFA/token) i idempotentna wysyłka planów + kalendarz |
| `plan.py` | Format planu (JSON po polsku) → JSON treningu Garmin, podgląd, prompt dla AI |
| `ustawienia.py` | Strefy tętna (S1–S5), mocy (P1–P5), FTP |
| `cwiczenia.py` | Katalog ćwiczeń siłowych Garmin |
| `static/` | Frontend (bez zewnętrznych skryptów, CSP `self`) |
| `tests/` | unittest/pytest na atrapach Garmina (bez sieci) |

Cały dostęp do Garmina idzie **jedną nieoficjalną biblioteką `garminconnect`** (zapis i odczyt).
Świadoma decyzja: oficjalne Garmin Personal API jest tylko do odczytu, a zapis i tak wymaga
ścieżki nieoficjalnej – jedna biblioteka = jeden token, więcej danych (Training Readiness/Status).

## Narzędzia MCP

`format_planu`, `moje_ustawienia`, `sprawdz_plan`, `wyslij_plan`, `kalendarz`, `usun_z_kalendarza`,
`plan_treningu`, `aktywnosci`, `trening`, `serie`, `plan_vs_wykonanie`, `pobierz_fit` (Dysk Google albo
podpisane linki `/fit/<token>`, 1 h), `polacz_dysk_google` (link → `/google/polacz/<token>` → Google →
`/google/powrot` pokazuje zaszyfrowany `GOOGLE_TOKEN` do wpisania w Render), `wellness`, `gotowosc`, `surowe_dane` (diagnostyka, lista źródeł w `ZRODLA_SUROWE`). Zasada: plan najpierw `sprawdz_plan` → podgląd →
akceptacja użytkownika → `wyslij_plan`. Błędy narzędzi zwracamy jako `isError` z polskim komunikatem.
Nowe narzędzie = wpis w słowniku `NARZEDZIA` w `mcp_serwer.py` + test w `tests/test_mcp.py`.

## Konfiguracja i wdrożenie

- Hosting: **Render, plan free**, serwis `garmin-plan-loader`
  (`srv-dc1c1aetlv9c73bmggm0`), adres `https://garmin-plan-loader.onrender.com`, gałąź `main`.
- **Auto-deploy po pushu nie zadziałał** (stan na 2026-11-22) – po pushu do `main` uruchom wdrożenie
  przez API: `POST https://api.render.com/v1/services/srv-dc1c1aetlv9c73bmggm0/deploys`.
  Logi: `GET /v1/logs?ownerId=<owner>&resource=srv-dc1c1aetlv9c73bmggm0`.
- Zmienne środowiskowe (wartości tylko w Render, nigdy w repo – repo jest publiczne):
  - `SECRET_KEY` – szyfruje tokeny; zmiana unieważnia `GARMIN_TOKEN`,
  - `MCP_TOKEN` – hasło connectora; claude.ai wysyła `Authorization: Bearer <MCP_TOKEN>`,
  - `GARMIN_TOKEN` – zaszyfrowany token Garmina (strona → „Connector dla Claude”),
  - `USTAWIENIA` – opcjonalnie strefy/FTP (JSON w formacie `ustawienia.py`),
  - `KOD_DOSTEPU` – opcjonalny kod do strony,
  - `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_TOKEN`, `DYSK_FOLDER` – Dysk Google dla FIT.
- Connector w claude.ai: URL `.../mcp`, **Authentication: „No sign-in”** + nagłówek `Authorization`.
  claude.ai domyślnie proponuje OAuth („Sign in now”), bo `/mcp` bez tokenu zwraca 401 – trzeba to zmienić.
- Środowisko Claude Code ma dopuszczony host `garmin-plan-loader.onrender.com` (od 2026-11-23): narzędzia
  można wywołać wprost `POST /mcp` z nagłówkiem `Authorization: Bearer <MCP_TOKEN>` (token zna właściciel;
  nie zapisywać go w repo). Lista narzędzi `mcp__Garmin__*` w sesji odświeża się dopiero po ponownym połączeniu.
- Zweryfikowane endpointy (2026-11-23, żywe API): `/activity-service/activity/{id}/exerciseSets` (ciężar w gramach,
  `startTime` w UTC, `wktStepIndex` w numeracji FIT - krok powtórzenia po dzieciach), `/workout-service/workout/{id}`
  (`weightValue` w gramach), `/download-service/files/activity/{id}` (zip z `<id>_ACTIVITY.fit`).

## Znane ryzyka

- **Rotacja refresh tokenu Garmina**: biblioteka może dostać nowy refresh token, a zmiennej
  `GARMIN_TOKEN` nie da się nadpisać z kodu. Jeśli po uśpieniu serwera pojawia się „logowanie wygasło”,
  plan B: trzymać token w małej bazie (np. darmowy Redis/Upstash) i zapisywać po odświeżeniu.
- Darmowy Render usypia serwer po ~15 min; pierwsze wywołanie może przekroczyć czas.
- Odpowiedzi Garmina są nieudokumentowane – pola w `garmin_dane.py` mogą wymagać poprawek;
  `trening` z `surowe=true` zwraca pełną odpowiedź do diagnozy.
- Limity zapytań Garmina (429) – stąd limity zakresów: aktywności 92 dni, wellness 14 dni.

## Plan prac

- [x] Etap 1: connector MCP (plany + odczyt danych), wdrożony i sprawdzony na prawdziwym koncie (2026-11-22).
- [x] Specyfikacja 2026-11-23, P1: `serie`, `plan_treningu`, `plan_vs_wykonanie`, `pobierz_fit` (wdrożone, testy akceptacyjne OK).
- [x] `ingest_fit.py` (repo FIT_ANALYZER, prywatne): tabele `sets`, `lengths`, `workout_steps`, `--uzupelnij`,
      `progi_zmierzone` + widok `sessions_progi` (2026-11-23).
- [x] Dysk Google dla `pobierz_fit`: połączony i sprawdzony 2026-11-23 (aplikacja OAuth opublikowana, „In production”).
- [ ] Specyfikacja 2026-11-23: P2, P3.
- [ ] Etap 2: `analiza_treningu` z pliku FIT (NP, TSS, IF, decoupling, krzywa mocy – kod w
      FIT_ANALYZER `ingest_fit.py`) i `obciazenie` (CTL/ATL/TSB).
- [ ] Etap 3: README, archiwizacja repo FIT_ANALYZER (robi właściciel w ustawieniach GitHub).

## Konwencje

- Kod, komunikaty i nazwy po polsku (bez polskich znaków w identyfikatorach).
- Testy: `python -m pytest -q tests` (CI: `.github/workflows/testy.yml`, Python 3.12).
- Nie dodawać zależności bez potrzeby; MCP jest napisany ręcznie we Flasku (bez SDK), zgodność
  sprawdzona oficjalnym klientem `mcp`.
