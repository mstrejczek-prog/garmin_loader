# -*- coding: utf-8 -*-
"""
Odczyt pomiarów wagi Xiaomi Mi Body Composition Scale 2 (XMTZC05HM) z chmury Zepp Life (dawniej Mi Fit).

Protokół (nieoficjalny) ustalony na podstawie kodu AlexxIT/SmartScaleConnect (pkg/xiaomi, pkg/zepp)
i argrento/huami-token - tylko jako dokumentacja, bez użycia tych programów:

1. Konto Xiaomi, OAuth2: GET account.xiaomi.com/oauth2/authorize (client_id aplikacji Zepp Life)
   -> oauthLoginUrl -> _sign/qs/callback -> POST /pass/serviceLoginAuth2 (hash MD5 hasła)
   -> location -> przekierowania aż do redirect_uri z parametrem code.
2. POST account.zepp.com/v2/client/login (grant_type=request_token, third_name=xiaomi-hm-mifit)
   -> token_info: login_token (długi), app_token (krótki), user_id.
3. Odświeżenie bez ponownego logowania: GET account.zepp.com/v1/client/app_tokens?login_token=...
   -> nowy app_token. Logowania hasłem używamy tylko awaryjnie.
4. Rekordy: GET api-mifit.zepp.com/users/{user_id}/members/-1/weightRecords?limit=200&toTime=<unix>
   (nagłówek apptoken; -1 = główny profil konta; stronicowanie polem next).

Konto Zepp z e-mailem i hasłem (ZEPP_KONTO=email, domyślnie) - sekwencja z huami-token:
1. POST api-user-us2.zepp.com/v2/registrations/tokens (formularz zaszyfrowany AES-CBC stałym kluczem
   aplikacji Zepp) -> 303 z access/refresh w adresie przekierowania.
2. POST api-mifit-us2.zepp.com/v2/client/login (grant_type=access_token, third_name=huami) -> login_token,
   app_token, user_id. Logujemy się tożsamością aplikacji Zepp (com.huami.midong), nie Zepp Life - telefon
   z Zepp Life ma osobną sesję.
Konto Xiaomi (ZEPP_KONTO=xiaomi) - jak niżej (punkty 1-2). Konta założonego przez Google nie da się
zalogować automatycznie.

RYZYKO: SmartScaleConnect opisuje, że synchronizacja wylogowuje aplikację na telefonie. Hipoteza: robi to
każde pełne logowanie (nowy login_token). Dlatego logujemy się raz, potem tylko odświeżamy app_token
i nigdy nie wywołujemy wylogowania.

Bezpieczeństwo: żadna funkcja nie loguje haseł, tokenów ani treści odpowiedzi. Błędy mają kategorię
(blokada, captcha, weryfikacja, zle_haslo, siec, token, protokol) i krótki opis bez danych.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
import uuid
from urllib.parse import parse_qs, urljoin, urlparse

import requests

URL_TOKENY_EMAIL = "https://api-user-us2.zepp.com/v2/registrations/tokens"
URL_LOGIN_EMAIL = "https://api-mifit-us2.zepp.com/v2/client/login"
URL_APP_TOKENS_EMAIL = "https://api-mifit-us2.zepp.com/v1/client/app_tokens"
KLUCZ_ZEPP = b"xeNtBVqzDc6tuNTh"                     # stała aplikacji Zepp (z huami-token), nie sekret użytkownika
IV_ZEPP = b"MAAAYAAAAAAAAABg"
WERSJA_ZEPP = "9.12.5"
NAGLOWKI_ZEPP = {"app_name": "com.huami.midong", "appname": "com.huami.midong", "cv": "151689_9.12.5", "v": "2.0",
                 "appplatform": "android_phone", "vb": "202509151347", "vn": WERSJA_ZEPP,
                 "user-agent": "Zepp/9.12.5 (Pixel 4; Android 12; Density/2.75)", "x-hm-ekv": "1",
                 "content-type": "application/x-www-form-urlencoded; charset=UTF-8"}
CLIENT_ID = "428135909242707968"                    # Zepp Life w OAuth2 Xiaomi
REDIRECT_URI = "https://api-mifit-cn.huami.com/huami.health.loginview.do"
URL_AUTHORIZE = "https://account.xiaomi.com/oauth2/authorize"
URL_AUTH2 = "https://account.xiaomi.com/pass/serviceLoginAuth2"
URL_LOGIN = "https://account.zepp.com/v2/client/login"
URL_APP_TOKENS = "https://account.zepp.com/v1/client/app_tokens"
URL_REKORDY = "https://api-mifit.zepp.com/users/{uid}/members/-1/weightRecords"
APP_NAME = "com.xiaomi.hm.health"
APP_VERSION = "6.14.0"
DN = "api-mifit.zepp.com"
UA = "MiFit/6.14.0 (Android 12; Pixel 4)"
PREFIKS = "&&&START&&&"
CZAS = 30                                           # s na jedno zapytanie
MAX_STRON = 100                                     # 100 x 200 rekordów = 20 000 pomiarów


class BladZepp(Exception):
    """Błąd Zepp/Xiaomi z kategorią. Treść nie zawiera danych logowania ani pomiarów."""

    def __init__(self, kategoria: str, opis: str):
        super().__init__(f"Zepp [{kategoria}]: {opis}")
        self.kategoria = kategoria
        self.opis = opis


_http = requests.Session()                          # podmieniane w testach


def _nowa_sesja():
    """Osobna sesja z ciasteczkami na logowanie Xiaomi (podmieniane w testach)."""
    return requests.Session()


def _zapytanie(metoda: str, url: str, sesja=None, **kw) -> requests.Response:
    kw.setdefault("timeout", CZAS)
    try:
        r = (sesja or _http).request(metoda, url, **kw)
    except requests.RequestException as e:
        raise BladZepp("siec", f"brak połączenia z {urlparse(url).hostname} ({type(e).__name__})") from None
    if r.status_code in (403, 429):
        raise BladZepp("blokada", f"{urlparse(url).hostname} odpowiedział {r.status_code} "
                                  "(blokada adresu IP albo limit zapytań)")
    return r


def _json_xiaomi(r: requests.Response) -> dict:
    tekst = r.text or ""
    if tekst.startswith(PREFIKS):
        tekst = tekst[len(PREFIKS):]
    try:
        d = json.loads(tekst)
    except ValueError:
        raise BladZepp("protokol", f"nieoczekiwana odpowiedź Xiaomi (HTTP {r.status_code})") from None
    return d if isinstance(d, dict) else {}


# ----------------------------------------------------------------- logowanie hasłem (awaryjne)

def _kod_oauth(email: str, haslo: str) -> str:
    """Konto Xiaomi -> kod OAuth2 dla Zepp Life."""
    s = _nowa_sesja()
    s.cookies.set("deviceId", secrets.token_hex(8), domain=".xiaomi.com")
    par = {"_json": "true", "client_id": CLIENT_ID, "pt": "1", "redirect_uri": REDIRECT_URI,
           "response_type": "code"}
    with s:
        d1 = _json_xiaomi(_zapytanie("GET", URL_AUTHORIZE, s, params=par))
        url_logowania = (d1.get("data") or {}).get("oauthLoginUrl")
        if not url_logowania:
            raise BladZepp("protokol", "Xiaomi nie zwrócił adresu logowania OAuth2")
        d2 = _json_xiaomi(_zapytanie("GET", url_logowania, s))
        if not all(d2.get(k) for k in ("_sign", "qs", "callback")):
            raise BladZepp("protokol", "brak parametrów _sign/qs/callback w odpowiedzi Xiaomi")
        form = {"_json": "true", "hash": hashlib.md5(haslo.encode()).hexdigest().upper(),
                "sid": d2.get("sid") or "", "callback": d2["callback"], "_sign": d2["_sign"],
                "qs": d2["qs"], "user": email}
        d3 = _json_xiaomi(_zapytanie("POST", URL_AUTH2, s, data=form))
        if d3.get("captchaUrl"):
            raise BladZepp("captcha", "Xiaomi żąda captchy - zaloguj się raz w przeglądarce i spróbuj później")
        if d3.get("notificationUrl") or d3.get("securityStatus") not in (None, 0):
            raise BladZepp("weryfikacja", "Xiaomi żąda dodatkowej weryfikacji konta (e-mail/SMS)")
        if d3.get("code") not in (0, None) or not d3.get("location"):
            kod = d3.get("code")
            if kod in (70016, 70002):
                raise BladZepp("zle_haslo", "Xiaomi odrzucił e-mail albo hasło")
            raise BladZepp("protokol", f"logowanie Xiaomi nieudane (kod {kod})")
        url = d3["location"]
        for _ in range(6):                          # przekierowania aż do redirect_uri?code=...
            r = _zapytanie("GET", url, s, allow_redirects=False)
            nast = r.headers.get("Location")
            if not nast:
                break
            url = urljoin(url, nast)
            kod = parse_qs(urlparse(url).query).get("code")
            if url.startswith(REDIRECT_URI) and kod:
                return kod[0]
        raise BladZepp("protokol", "nie dostałem kodu OAuth2 od Xiaomi")


def _szyfruj_aes(dane: bytes) -> bytes:
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    uzup = padding.PKCS7(128).padder()
    blok = uzup.update(dane) + uzup.finalize()
    szyfr = Cipher(algorithms.AES(KLUCZ_ZEPP), modes.CBC(IV_ZEPP)).encryptor()
    return szyfr.update(blok) + szyfr.finalize()


def _host_api(d: dict) -> str | None:
    """Host API danych z odpowiedzi logowania (konta są regionalne, np. api-mifit-de2)."""
    for dom in d.get("domains") or []:
        if not isinstance(dom, dict):
            continue
        for h in [dom.get("host")] + list(dom.get("cnames") or []):
            if isinstance(h, str) and h.startswith("api-mifit") and "." in h:
                return h
    return None


def zaloguj_email(email: str, haslo: str, device_id: str) -> dict:
    """Konto Zepp (e-mail + hasło)."""
    form = {"emailOrPhone": email, "state": "REDIRECTION", "client_id": "HuaMi", "password": haslo,
            "redirect_uri": "https://s3-us-west-2.amazonaws.com/hm-registration/successsignin.html",
            "region": "us-west-2", "token": ["access", "refresh"], "country_code": "US"}
    from urllib.parse import urlencode
    r = _zapytanie("POST", URL_TOKENY_EMAIL, data=_szyfruj_aes(urlencode(form, doseq=True).encode()),
                   headers=NAGLOWKI_ZEPP, allow_redirects=False)
    gdzie = r.headers.get("Location") or ""
    par = parse_qs(urlparse(gdzie).query)
    if r.status_code != 303 or not par.get("access"):
        blad = (par.get("error") or [""])[0]
        if r.status_code in (400, 401) or blad in ("0106", "0115", "0117", "0118"):
            raise BladZepp("zle_haslo", f"Zepp odrzucił e-mail albo hasło (HTTP {r.status_code}{', kod ' + blad if blad else ''})")
        raise BladZepp("protokol", f"logowanie Zepp: brak tokenu w przekierowaniu (HTTP {r.status_code})")
    form2 = {"code": par["access"][0], "device_id": device_id, "device_model": "android_phone",
             "app_version": WERSJA_ZEPP, "dn": "api-mifit.zepp.com,api-user.zepp.com,api-mifit.zepp.com,"
             "api-watch.zepp.com,app-analytics.zepp.com,auth.zepp.com,api-analytics.zepp.com",
             "third_name": "huami", "source": "com.huami.watch.hmwatchmanager:9.12.5:151689",
             "app_name": "com.huami.midong", "country_code": "US", "grant_type": "access_token",
             "allow_registration": "false", "lang": "en"}
    r2 = _zapytanie("POST", URL_LOGIN_EMAIL, data=form2, headers={
        "app_name": "com.huami.webapp", "appname": "com.huami.webapp", "origin": "https://user.zepp.com",
        "referer": "https://user.zepp.com/", "content-type": "application/x-www-form-urlencoded; charset=UTF-8"})
    try:
        d = r2.json()
    except ValueError:
        raise BladZepp("protokol", f"nieoczekiwana odpowiedź logowania Zepp (HTTP {r2.status_code})") from None
    ti = d.get("token_info") or {}
    if not (ti.get("app_token") and ti.get("login_token") and ti.get("user_id")):
        raise BladZepp("protokol", f"Zepp nie zwrócił tokenów (wynik: {str(d.get('result'))[:40]})")
    stan = _stan_tokenow(ti, device_id)
    stan["konto"] = "email"
    if (host := _host_api(d)):
        stan["host_api"] = host
    return stan


def zaloguj_haslem(email: str, haslo: str, device_id: str | None = None, konto: str | None = None) -> dict:
    """Pełne logowanie. Zwraca stan tokenów (do zaszyfrowanego zapisu). Może wylogować telefon!"""
    if not email or not haslo:
        raise BladZepp("zle_haslo", "brak ZEPP_EMAIL / ZEPP_PASSWORD na serwerze")
    device_id = device_id or str(uuid.uuid4())
    konto = (konto or os.environ.get("ZEPP_KONTO", "") or "email").strip().lower()
    if konto == "email":
        return zaloguj_email(email, haslo, device_id)
    if konto != "xiaomi":
        raise BladZepp("konfiguracja", "ZEPP_KONTO musi mieć wartość email albo xiaomi")
    kod = _kod_oauth(email, haslo)
    form = {"app_name": APP_NAME, "app_version": APP_VERSION, "code": kod, "country_code": "CN",
            "device_id": device_id, "device_model": "phone", "dn": DN, "grant_type": "request_token",
            "third_name": "xiaomi-hm-mifit"}
    r = _zapytanie("POST", URL_LOGIN, data=form)
    try:
        d = r.json()
    except ValueError:
        raise BladZepp("protokol", f"nieoczekiwana odpowiedź logowania Zepp (HTTP {r.status_code})") from None
    ti = d.get("token_info") or {}
    if not (ti.get("app_token") and ti.get("login_token") and ti.get("user_id")):
        raise BladZepp("protokol", f"Zepp nie zwrócił tokenów (wynik: {str(d.get('result'))[:40]})")
    stan = _stan_tokenow(ti, device_id)
    stan["konto"] = "xiaomi"
    if (host := _host_api(d)):
        stan["host_api"] = host
    return stan


def _stan_tokenow(ti: dict, device_id: str, poprzedni: dict | None = None) -> dict:
    teraz = int(time.time())
    stan = dict(poprzedni or {})
    stan.update({"user_id": str(ti.get("user_id") or stan.get("user_id")), "app_token": ti["app_token"],
                 "device_id": device_id, "app_token_od": teraz})
    if ti.get("login_token"):
        stan["login_token"] = ti["login_token"]
    ttl = ti.get("app_ttl")
    stan["app_ttl"] = ttl if isinstance(ttl, (int, float)) else None
    stan["app_token_do"] = teraz + int(ttl) if isinstance(ttl, (int, float)) and ttl > 0 else None
    return stan


# ----------------------------------------------------------------- odświeżanie bez logowania

def odswiez(stan: dict) -> dict:
    """Nowy app_token z login_token (bez hasła i bez nowej sesji)."""
    if not stan.get("login_token"):
        raise BladZepp("token", "brak login_token w stanie")
    if stan.get("konto") == "email":
        url, par = URL_APP_TOKENS_EMAIL, {"app_name": "com.huami.midong", "dn": "api-mifit.zepp.com",
                                          "login_token": stan["login_token"], "os_version": "4.1.0"}
    else:
        url, par = URL_APP_TOKENS, {"app_name": APP_NAME, "dn": DN, "login_token": stan["login_token"],
                                    "os_version": "4.1.0"}
    r = _zapytanie("GET", url, params=par)
    try:
        d = r.json()
    except ValueError:
        raise BladZepp("token", f"odświeżenie tokenu nieudane (HTTP {r.status_code})") from None
    ti = d.get("token_info") or {}
    if r.status_code != 200 or not ti.get("app_token"):
        raise BladZepp("token", f"odświeżenie tokenu nieudane (HTTP {r.status_code})")
    return _stan_tokenow(ti, stan.get("device_id") or str(uuid.uuid4()), stan)


def token_wygasa(stan: dict, zapas_s: int = 24 * 3600) -> bool:
    do = stan.get("app_token_do")
    return isinstance(do, (int, float)) and do - time.time() < zapas_s


# ----------------------------------------------------------------- rekordy

def _strona_rekordow(stan: dict, do_czasu: int) -> dict:
    url = URL_REKORDY.format(uid=stan["user_id"])
    if stan.get("host_api"):
        url = url.replace("api-mifit.zepp.com", stan["host_api"], 1)
    r = _zapytanie("GET", url, params={"limit": 200, "toTime": do_czasu},
                   headers={"apptoken": stan["app_token"], "appname": APP_NAME, "User-Agent": UA})
    if r.status_code == 401:
        raise BladZepp("token", "app_token odrzucony (401)")
    if r.status_code != 200:
        raise BladZepp("protokol", f"weightRecords zwrócił HTTP {r.status_code}")
    try:
        d = r.json()
    except ValueError:
        raise BladZepp("protokol", "weightRecords: odpowiedź nie jest JSON-em") from None
    return d if isinstance(d, dict) else {}


def rekordy(stan: dict, od_czasu: int = 0) -> list[dict]:
    """Surowe rekordy (słowniki z Zepp) od od_czasu (unix s) do teraz, od najnowszych."""
    wynik, do_czasu = [], int(time.time()) + 60
    for _ in range(MAX_STRON):
        d = _strona_rekordow(stan, do_czasu)
        elementy = d.get("items") or []
        wynik.extend(x for x in elementy if isinstance(x, dict))
        nast = d.get("next")
        najstarszy = min((x.get("generatedTime") or 0 for x in elementy), default=0)
        if not elementy or not isinstance(nast, (int, float)) or nast <= 0 or nast >= do_czasu \
                or najstarszy < od_czasu:
            break
        do_czasu = int(nast)
    return [x for x in wynik if (x.get("generatedTime") or 0) >= od_czasu]


# ----------------------------------------------------------------- sesja: token -> odświeżenie -> hasło

DIAGNOZA: list[str] = []                            # kroki, które zawiodły (kody HTTP, bez danych) - ostatni przebieg


def _diag(tekst: str) -> None:
    DIAGNOZA.append(tekst[:120])


def polacz(stan: dict | None, email: str, haslo: str, pozwol_haslo: bool = True) -> tuple[dict, str]:
    """Ważny stan tokenów i sposób, w jaki go zdobyto: 'token', 'odswiezenie' albo 'haslo'.
    Każda ścieżka jest próbowana najwyżej raz (bez pętli ponowień)."""
    stan = dict(stan or {})
    if stan.get("app_token") and stan.get("user_id"):
        if not token_wygasa(stan):
            return stan, "token"
        _diag(f"token wygasa wg ttl (app_token_do-teraz={int(stan['app_token_do'] - time.time())} s)")
        try:
            return odswiez(stan), "odswiezenie"
        except BladZepp as e:
            _diag("odswiezenie: " + e.opis)
            if e.kategoria not in ("token",):
                raise
    if not pozwol_haslo:
        raise BladZepp("token", "brak ważnego tokenu, a logowanie hasłem jest wyłączone w tym trybie")
    return zaloguj_haslem(email, haslo, stan.get("device_id")), "haslo"


def rekordy_z_sesja(stan: dict | None, email: str, haslo: str, od_czasu: int = 0,
                    pozwol_haslo: bool = True) -> tuple[list[dict], dict, list[str]]:
    """Pobiera rekordy; przy 401 jedno odświeżenie, a gdy i ono zawiedzie - jedno awaryjne logowanie hasłem.
    Zwraca (rekordy, nowy_stan, kroki) - kroki: lista użytych metod, np. ['token', 'odswiezenie']."""
    DIAGNOZA.clear()
    stan, sposob = polacz(stan, email, haslo, pozwol_haslo)
    kroki = [sposob]
    try:
        return rekordy(stan, od_czasu), stan, kroki
    except BladZepp as e:
        _diag(f"rekordy po '{sposob}': " + e.opis)
        if e.kategoria != "token":
            raise
    if sposob == "token":
        try:
            stan = odswiez(stan)
            kroki.append("odswiezenie")
            return rekordy(stan, od_czasu), stan, kroki
        except BladZepp as e:
            _diag("odswiezenie po 401: " + e.opis)
            if e.kategoria != "token":
                raise
    if sposob == "haslo" or not pozwol_haslo:
        raise BladZepp("token", "Zepp odrzuca token także po odświeżeniu")
    stan = zaloguj_haslem(email, haslo, stan.get("device_id"))
    kroki.append("haslo")
    return rekordy(stan, od_czasu), stan, kroki
