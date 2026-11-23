## Wykonanie

### 2026-11-23 – Priorytet 1 (wdrożone)

Kod: repo `mstrejczek-prog/garmin_loader`, gałąź `main` (commity d9968f9 i następny), serwer
`https://garmin-plan-loader.onrender.com` (Render, zdalnie). Nowe narzędzia są tylko do odczytu.

**Rozpoznanie kodu**
- Python 3.12, Flask + gunicorn; serwer MCP napisany ręcznie (`mcp_serwer.py`, `POST /mcp`, JSON-RPC, bez SDK).
- Garmin: nieoficjalna biblioteka `garminconnect` (ta sama do wysyłki planów i odczytu), token w zmiennej `GARMIN_TOKEN`.
- Serwer działa **zdalnie** na Render (plan free) – nie ma dostępu do dysku `E:` ani do `treningi.duckdb`.
- Narzędzie = wpis w słowniku `NARZEDZIA` (`fn`, opis, JSON Schema, `ro`), logika odczytu w `garmin_dane.py`.

**Nowe narzędzia i testy akceptacyjne (żywe API, 23.11.2026)**

| Narzędzie | Test | Wynik |
|---|---|---|
| `serie(activity_id)` | `25162650914` | 31 serii aktywnych, 362 powtórzenia (notatka: ≈363), tonaż 5534 kg, 9 ćwiczeń |
| `plan_treningu(workout_id)` | `1769460084` | 8 bloków Siłowni A (ściąganie, skos, straight-arm, jednorącz ×2, face pull, uginanie, core ×2, dead bug) z ciężarami i notatką |
| `plan_vs_wykonanie(activity_id)` | `25162650914` | wszystkie 31 serii dopasowane do kroków; odchylenia m.in. ściąganie 45 vs 42,5 kg, uginanie 7/10/8 vs 12 powt. |
| `plan_vs_wykonanie(activity_id)` | `25145798508` | komunikat „brak associatedWorkoutId” zamiast błędu |
| `pobierz_fit(activity_id \| od, do)` | `25162650914` | link 1 h → zip 40 kB z `25162650914_ACTIVITY.fit` (nazwa zgodna z `collect_fit_paths`) |
| `surowe_dane(zrodlo, id)` | – | diagnostyka: surowe odpowiedzi Garmina (whitelist źródeł) |

Wybrano opcję A (`plan_treningu` jako osobne narzędzie) – pasuje do istniejącego podziału narzędzi.

**Zweryfikowane endpointy**
- `GET /activity-service/activity/{id}/exerciseSets` – zgodnie z notatką. Ciężar w **gramach**
  (`45000.0` = 45 kg), `startTime` w **UTC** (pole w odpowiedzi: `start_utc`), `wktStepIndex` w numeracji
  pliku FIT (krok powtórzenia stoi **po** swoich dzieciach) – konektor dobiera numerację automatycznie.
- `GET /workout-service/workout/{workoutId}` – zgodnie z notatką; `weightValue` w gramach.
- `GET /download-service/files/activity/{id}` – zip z oryginalnym FIT (`<id>_ACTIVITY.fit`).
- `associatedWorkoutId` jest w `summaryDTO` (zgodnie z notatką).
- Plik FIT Siłowni A zawiera: `set` ×52, `workout` ×1, `workout_step` ×24, `exercise_title` ×8
  (`set.weight` w kg, `category`/`category_subtype` jako krotki kodów liczbowych).

**Otwarte problemy / czego nie zrobiono**
- `pobierz_fit` nie zapisuje do `Dane treningowe/FIT (surowe pliki z Garmina)/` i nie uruchamia
  `ingest_fit.py` (`zasil_baze`), bo serwer działa zdalnie. Zamiast tego zwraca podpisane linki;
  zapis lokalny + `python ingest_fit.py treningi.duckdb <pliki>` robi się na komputerze. Alternatywa na
  przyszłość: zapis na Dysk Google.
- Cele strefowe (np. „HR strefa 2”) nie są oceniane w `plan_vs_wykonanie` – tylko cele liczbowe (bpm/W).
- W wytrzymałości kroki planu łączone z okrążeniami po kolejności; ręczny LAP przesuwa dopasowanie
  (konektor zgłasza to w polu `uwaga`).
- Przy treningu na zmianę stron (jednorącz) plan ma dwa kroki tej samej kategorii (`ROW`) – dopasowanie
  działa przez `wktStepIndex`, nie przez nazwę.
- Do zrobienia: zmiany w `ingest_fit.py` (tabele `sets`, `lengths`, `workout_steps`, `--uzupelnij`),
  Priorytet 2 i 3.


### 2026-11-23 – Dysk Google i Priorytet 2 (wdrożone)

**Dysk Google**: konektor widzi folder „Obsidian Vault/Dane treningowe/FIT (surowe pliki z Garmina)” (zakres
`drive`, folder po ID). Nowe narzędzie `stan_dysku`: duplikaty po numerze aktywności (także kopie „123 (2).zip”),
braki względem Garmina, `uzupelnij`. Stan po porządkach: 358 plików = 358 aktywności, 0 duplikatów, brak luk
od maja 2025 (dogrywanie 27 plików, 15 kopii w koszu, błędny folder w koszu).

**Priorytet 2**

| Narzędzie | Test na żywym API | Wynik |
|---|---|---|
| `trening` (bieg) | 24559234030 | RPE 8, samopoczucie „słabo”, zgodność z planem 53%, GCT 253 ms, oscylacja 7,8 cm, krok 1,14 m, strefy mocy 5, pogoda 16,1 °C / 16 km/h NE, buty Boston 12 |
| `trening` (basen) | 25124549994 | 18 długości, SWOLF 84, styl per okrążenie, przerwy jako „przerwa”, tempo 4:50 (z danymi zegarka) i mediana 2:47/100 m |
| `strumienie` | 24559234030, krok 60 s | 103 punkty, HR/moc/kadencja/prędkość/GCT |
| `historia_metryk` | 2025-11-23..2026-11-22 | 53 tygodnie; VO2max bieg 37 tyg., rower 25, FTP 6 pomiarów, próg 26 (tempo 4:05–4:15) |
| `sklad_ciala` | rok | 2 ręczne pomiary masy (brak wagi z analizą składu) |
| `wellness` | 30 dni, pola sen+hrv | 30 dni w ~12 s |

**Zweryfikowane / poprawione względem notatki**
- Pogoda w °F i mph (przeliczane), RPE 10–100, samopoczucie 0–100.
- Prędkość progu mleczanowego w (m/s)/10.
- `trainingstatus/weekly` zwraca dane dzienne; gotowość ma kilka wpisów dziennie (brany poranny).
- Lista aktywności nie zawiera RPE – jest tylko w `trening` (pola w `aktywnosci` będą null).
- Zegarek zapisuje odpoczynek przy ścianie bez przycisku jako długość (np. 25 m w 217 s) – stąd mediana tempa.

**Otwarte**
- `strumienie`: Garmin próbkuje nierówno (gęściej na początku) – `krok_s` jest przybliżony.


### 2026-11-23 – Priorytet 3 (wdrożone)

| Narzędzie | Wynik na żywym API |
|---|---|
| `sprzet` | Adidas Adizero Boston 12: 1141 km przy limicie 644 km (177%), 134 aktywności; Scott Speedster Gravel: 1794 km / 10 000 km (18%) |
| `rekordy` | 20 rekordów: bieg 1 km 3:49, 5 km 20:00, 10 km 41:02, półmaraton 1:28:10 (Cracovia 11.10.2026); rower 100 km, 1056 m przewyższenia, 208 W / 20 min, 40 km w 1:11:10; basen 2,17 km, 400 m 9:57, 1000 m 35:49; kroki |
| `prognozy` | 5 km 19:44, 10 km 40:50, półmaraton 1:28:21, maraton 3:25:07; trend miesięczny półmaratonu 1:34:37 (VI) → 1:28:16 (XI) |

Zweryfikowane: `typeId` rekordów nie ma etykiet w odpowiedzi - mapowanie w `TYPY_REKORDOW` (bieg 1–7, rower 8–11,
kroki 12–16, pływanie 17–25). Rekord pływacki „100 m” 1:03 odstaje od reszty (400 m 2:29/100 m) - możliwy
błąd zegarka w tej aktywności (2026-01-10); warto sprawdzić w Garmin Connect.
