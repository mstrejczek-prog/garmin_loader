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

### 2026-11-27 – konto

Stare konto Zepp Life jest logowane przez Google - bez hasła, nie da się go zalogować automatycznie i nie ma
eksportu w aplikacji. Właściciel założył drugie konto (e-mail + hasło) - docelowe źródło. Dodane logowanie kontem
Zepp e-mail (huami-token: `POST api-user-us2.zepp.com/v2/registrations/tokens` z formularzem AES-CBC ->
`POST api-mifit-us2.zepp.com/v2/client/login`, tożsamość aplikacji Zepp `com.huami.midong`; odświeżanie
`api-mifit-us2.zepp.com/v1/client/app_tokens`; host danych z pola `domains` odpowiedzi) oraz jednorazowy odczyt
historii ze starego konta tokenem z przeglądarki (`ZEPP_STARE_APPTOKEN`, `ZEPP_STARE_USER_ID`).

### 2026-11-27/28 – testy i wdrożenie

Bez wartości masy i składu ciała (repo jest publiczne) – pełne dane w narzędziu MCP `waga`.

| krok | wynik |
|---|---|
| 0 – test wylogowania | **OK.** Pierwsze logowanie łącznika i dwa kolejne logowania hasłem nie wylogowały Zepp Life na telefonie; nowy pomiar widoczny w aplikacji. Powtórka po 24 h: zapisany token, bez hasła, telefon zalogowany. |
| 1 – test IP (Render) | **OK.** Zepp i Garmin odpowiadają z serwera Render; bez 403/429/captchy/weryfikacji. Konta są na hoście regionalnym `api-mifit-de2.zepp.com`. |
| 2 – historia | 340 rekordów (nowe konto + stare konto tokenem z przeglądarki), 2020-06 → 2026-11. Z `WAGA_TYLKO_IMPEDANCJA=tak`: 288 przyjętych (pełny skład), 51 bez impedancji i 2 spoza zakresu masy tylko w backupie, 0 z obcego profilu. |
| 3 – wysyłka historii | 288 pomiarów do Garmina (źródło `INDEX_SCALE`, skład ciała) i do zakładki „Waga”, 0 duplikatów. |
| 4 – cron | `WAGA_CRON=tak`. Harmonogram GitHub Actions uruchamia się nieregularnie (pomijane przebiegi) – patrz otwarte problemy. |

**Testy akceptacyjne**

1. Test wylogowania – telefon pozostaje zalogowany. **OK.**
2. Nowe ważenie → Garmin i arkusz w ≤ 3 h – działa przy przebiegu `sync`; termin zależy od niezawodności wyzwalacza.
3. Drugi przebieg bez ważenia – `sync`: przyjęty 1, wysłane 0, duplikaty pominięte w Garminie i arkuszu. **OK.**
4. Pomiar spoza zakresu tylko w backupie – 2 pomiary w historii + test jednostkowy. **OK.**
5. Złe hasło / brak sieci – jeden fail, bez pętli, bez danych logowania (testy jednostkowe). **OK.**

### Otwarte problemy

- **Telefon unieważnia token łącznika po ważeniu.** Synchronizacja pomiaru w Zepp Life daje 401 na tokenie łącznika,
  a odświeżenie (`/v1/client/app_tokens`) nie zwraca nowego tokenu. Najbliższy przebieg loguje się hasłem (najwyżej
  raz na przebieg, ~1×/dobę; adnotacja „awaryjne logowanie” w Actions). Telefon nie traci sesji. TTL `app_token` 30 dni.
- **Harmonogram GitHub Actions jest zawodny** (opóźnienia i pomijane przebiegi). Propozycja: zewnętrzny wyzwalacz
  wołający `POST /waga/sync` z nagłówkiem `Authorization: Bearer <WAGA_TOKEN>`.
