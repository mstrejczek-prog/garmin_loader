# Waga Mi Scale 2 → Zepp → Garmin + arkusz „Pomiary/Waga” – wykonanie

Do przeniesienia do notatki w vaulcie
(„plany/2026-11-26 Waga Mi Scale 2 - lacznik Zepp do Garmina i arkusza (specyfikacja dla Claude Code)”).

## Architektura (zastępuje wariant Home Assistant / AppDaemon)

- Kod w repo **`garmin_loader`** (Plan Loader): `zepp.py` (Zepp Life) i `waga.py` (łącznik), testy `tests/test_waga.py`.
- Synchronizacja działa na serwerze Plan Loadera (Render, `POST /waga/<tryb>`). **GitHub Actions** (`.github/workflows/waga.yml`)
  tylko ją uruchamia: cron `17 */3 * * *` (włączany zmienną repo `WAGA_CRON=tak`) i ręcznie z wyborem trybu
  `test` / `historia` / `wyslij_historie` / `sync`.
- Dlaczego nie wszystko w Actions: (1) Garmin rotuje refresh token – osobna kopia tokenu w Actions i na Render
  unieważniałyby się nawzajem; łącznik używa tego samego, najnowszego tokenu connectora z Dysku Google;
  (2) repo jest publiczne – logi Actions są jawne, więc masa i skład ciała nie mogą tam trafić.
- Arkusz: Sheets API przez istniejące połączenie OAuth z Google (zakres `drive`) – bez konta serwisowego.
- Stan (tokeny Zepp, czas ostatniego pomiaru, raport, logowania hasłem) i backup CSV: zaszyfrowane Fernetem
  (`SECRET_KEY`) na Dysku Google właściciela (`.waga-sync-stan.json`, `.waga-sync-backup.json`).
- Raport z historii i pomiary: narzędzie MCP `waga` (tylko dla właściciela, w czacie z Claude).
- Błąd = jeden fail workflow i e-mail z GitHuba; bez ponowień w pętli.

## Wykonanie

### 2026-11-26 – kod

**Endpointy Zepp/Xiaomi (z kodu SmartScaleConnect `pkg/zepp`, `pkg/xiaomi` i huami-token; do potwierdzenia testem)**

| krok | endpoint |
|---|---|
| OAuth2 Xiaomi | `GET https://account.xiaomi.com/oauth2/authorize?client_id=428135909242707968&pt=1&response_type=code&redirect_uri=https://api-mifit-cn.huami.com/huami.health.loginview.do&_json=true` → `data.oauthLoginUrl` → `_sign/qs/callback` |
| hasło | `POST https://account.xiaomi.com/pass/serviceLoginAuth2` (hash MD5 hasła) → `location` → przekierowania do `redirect_uri?code=…` |
| logowanie Zepp | `POST https://account.zepp.com/v2/client/login` (`grant_type=request_token`, `third_name=xiaomi-hm-mifit`, `app_name=com.xiaomi.hm.health`) → `login_token`, `app_token`, `user_id` |
| odświeżenie | `GET https://account.zepp.com/v1/client/app_tokens?login_token=…` → nowy `app_token` (bez nowej sesji) |
| rekordy | `GET https://api-mifit.zepp.com/users/{user_id}/members/-1/weightRecords?limit=200&toTime=<unix>` (nagłówek `apptoken`) |

Pola rekordu: `generatedTime` (unix), `weightType` (≠0 = uszkodzony, pomijany), `memberId`, `summary`:
`weight`, `bmi`, `fatRate`, `muscleRate` (kg), `bodyWaterRate`, `boneMass`, `metabolism`, `visceralFat`,
`muscleAge`, `bodyStyle`, `impedance`.

Strategia anty-wylogowanie: hasło raz (pierwszy `test`), potem `app_token` do wygaśnięcia (`app_ttl`) albo do 401,
odświeżanie z `login_token`; hasło awaryjnie najwyżej raz na przebieg, odnotowane w stanie i w podsumowaniu
workflow; nigdy wylogowania; stały `device_id`.

Testy jednostkowe (atrapy, bez sieci): 24 testy, w tym odpowiedniki testów akceptacyjnych 3 (brak duplikatów
w drugim przebiegu), 4 (pomiar spoza zakresu tylko w backupie) i 5 (złe hasło / brak sieci → jeden błąd,
bez danych logowania w odpowiedzi i logach).

### Do zrobienia (kolejność ze specyfikacji)

- [ ] **Krok 0 – test wylogowania**: Actions → Waga → `test` (pierwsze logowanie). Potem ważenie i sprawdzenie,
      czy Zepp Life na telefonie jest zalogowany. Powtórka `test` po 24 h. Wynik: …
- [ ] **Krok 1 – test IP**: wynik `test` z serwera (Render) – Zepp: …, Garmin: …
- [ ] **Krok 2 – historia**: raport (narzędzie MCP `waga`) – akceptacja: …
- [ ] **Krok 3 – wyslij_historie**: …
- [ ] **Krok 4 – `WAGA_CRON=tak`**, test akceptacyjny 2 (ważenie → Garmin i arkusz w ≤ 3 h): …

### Otwarte problemy

- Endpointy Zepp pochodzą z kodu referencyjnego; `app_tokens` (odświeżanie) nie jest użyte w SmartScaleConnect
  – jeśli nie zadziała, łącznik użyje awaryjnie hasła (widoczne w podsumowaniu) i trzeba będzie to poprawić.
- Filtr profilu: zakładamy, że rekordy głównego profilu mają `memberId` = -1 (albo `user_id`); jeśli raport
  pokaże Twoje pomiary jako „inny_profil”, ustaw `WAGA_PROFIL`.
- Ryzyko blokady IP Render/captchy Xiaomi – plan B: uruchomienie `waga.przebieg()` na Home Assistant / Railway.
