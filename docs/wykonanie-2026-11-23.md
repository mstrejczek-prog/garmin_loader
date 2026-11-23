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
