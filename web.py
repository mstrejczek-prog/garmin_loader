# -*- coding: utf-8 -*-
"""
Garmin Plan Loader - wersja webowa (komputer, telefon, przeglądarka).

Serwer nie ma bazy danych i niczego nie zapisuje na dysku:
- strefy i FTP trzyma przeglądarka (localStorage) i dołącza do żądań,
- token Garmina serwer szyfruje swoim kluczem (SECRET_KEY) i oddaje przeglądarce; przeglądarka odsyła go
  przy wysyłce. Bez klucza serwera zapisany w przeglądarce token jest bezużyteczny,
- hasło jest używane tylko w żądaniu logowania i nigdzie nie trafia.

Uruchomienie lokalne:  python web.py   (http://localhost:8000)
Produkcja:             gunicorn web:app  (patrz Procfile / render.yaml)

Zmienne środowiskowe:
  SECRET_KEY    klucz do szyfrowania tokenów (wymagany na serwerze; bez niego tokeny giną przy restarcie)
  KOD_DOSTEPU   opcjonalny kod, bez którego strona nie zadziała (gdy nie chcesz udostępniać serwera wszystkim)
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import queue
import secrets
import threading
import time
from collections import defaultdict, deque
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from flask import Flask, Response, jsonify, request, send_from_directory
from werkzeug.middleware.proxy_fix import ProxyFix

import garmin_io
import mcp_serwer
import plan
import ustawienia

NAZWA = "Garmin Plan Loader"
WERSJA = "0.3.0"
KATALOG_STATIC = Path(__file__).resolve().parent / "static"

log = logging.getLogger("garmin_plan_loader")

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024            # plan to najwyżej kilkadziesiąt kB
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)  # Render/Railway/Heroku stoją za proxy

# ----------------------------------------------------------------- szyfrowanie tokenów

_klucz = os.environ.get("SECRET_KEY")
if not _klucz:
    _klucz = secrets.token_urlsafe(32)
    log.warning("Brak SECRET_KEY - używam losowego klucza; zapamiętane logowania wygasną po restarcie serwera.")
_fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(_klucz.encode()).digest()))

CZAS_MFA = 10 * 60                                       # na wpisanie kodu weryfikacyjnego


def szyfruj(rodzaj: str, dane) -> str:
    return _fernet.encrypt(json.dumps({"r": rodzaj, "d": dane}).encode()).decode()


def odszyfruj(rodzaj: str, tekst, ttl: int | None = None):
    """Odszyfrowane dane albo None (zły klucz, podróbka, inny rodzaj, przeterminowane)."""
    if not isinstance(tekst, str) or not tekst:
        return None
    try:
        obj = json.loads(_fernet.decrypt(tekst.encode(), ttl=ttl))
    except (InvalidToken, ValueError):
        return None
    return obj.get("d") if isinstance(obj, dict) and obj.get("r") == rodzaj else None


# ----------------------------------------------------------------- ochrona serwera

KOD_DOSTEPU = os.environ.get("KOD_DOSTEPU", "").strip()
LIMIT_LOGOWAN = (10, 15 * 60)                            # prób logowania z jednego adresu na 15 minut
_proby: dict[str, deque] = defaultdict(deque)
_proby_lock = threading.Lock()


def _za_duzo_prob() -> bool:
    ile, okno = LIMIT_LOGOWAN
    teraz, ip = time.monotonic(), request.remote_addr or "?"
    with _proby_lock:
        d = _proby[ip]
        while d and teraz - d[0] > okno:
            d.popleft()
        if len(d) >= ile:
            return True
        d.append(teraz)
        if len(_proby) > 10000:                          # nie pozwól słownikowi rosnąć bez końca
            for k in [k for k, v in _proby.items() if not v]:
                del _proby[k]
        return False


@app.before_request
def _sprawdz_kod():
    if not KOD_DOSTEPU or not request.path.startswith("/api/") or request.path == "/api/start":
        return None
    podany = request.headers.get("X-Kod-Dostepu", "")
    if not hmac.compare_digest(podany.encode(), KOD_DOSTEPU.encode()):
        return jsonify(blad="Nieprawidłowy kod dostępu.", kod_dostepu=True), 403
    return None


@app.after_request
def _naglowki(odp):
    odp.headers.setdefault("X-Content-Type-Options", "nosniff")
    odp.headers.setdefault("Referrer-Policy", "no-referrer")
    odp.headers.setdefault("X-Frame-Options", "DENY")
    odp.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
    if request.path.startswith("/api/"):
        odp.headers["Cache-Control"] = "no-store"
    return odp


# ----------------------------------------------------------------- pomocnicze

def _dane() -> dict:
    d = request.get_json(silent=True)
    return d if isinstance(d, dict) else {}


def _ust(d: dict) -> dict:
    """Ustawienia z przeglądarki; uszkodzone traktujemy jak brak (przeglądarka pokaże, że ich nie ma)."""
    try:
        return ustawienia.czyste(d.get("ust"))
    except plan.BladPlanu:
        return ustawienia.puste()


def _blad(tekst: str, status: int = 400, **extra):
    return jsonify(blad=tekst, **extra), status


def _wpis_json(x: dict) -> dict:
    d = x["data"]
    wynik = {"data": d.isoformat() if d else None,
             "dzien": f"{plan.DNI[d.weekday()]} {d.strftime('%d.%m')}" if d else None,
             "wolne": x["wolne"]}
    if x["wolne"]:
        return wynik
    w = x["w"]
    wynik.update(nazwa=w["workoutName"], sport=plan.ETYKIETA_SPORTU.get(x.get("sport"), ""),
                 minuty=w["estimatedDurationInSecs"] // 60,
                 profil=[[round(s, 1), p, t] for s, p, t in x["profil"]],
                 wiersze=[list(r) for r in x["wiersze"]], poziomy=x["poziomy"])
    return wynik


def _zalogowano(klient) -> dict:
    wynik = {"stan": "ok", "konto": garmin_io.nazwa_konta(klient),
             "token": szyfruj("token", garmin_io.token(klient))}
    try:
        wynik["connector"] = mcp_serwer.zapisz_logowanie_wlasciciela(klient)
    except Exception:  # noqa: BLE001 - zapis dla connectora nie może zepsuć logowania na stronie
        log.exception("zapis logowania dla connectora")
    return wynik


# ----------------------------------------------------------------- strona

@app.get("/")
def strona():
    return send_from_directory(KATALOG_STATIC, "index.html")


@app.get("/static/<path:plik>")
def statyczne(plik):
    return send_from_directory(KATALOG_STATIC, plik)


@app.get("/manifest.webmanifest")
def manifest():
    return send_from_directory(KATALOG_STATIC, "manifest.webmanifest", mimetype="application/manifest+json")


@app.get("/prywatnosc")
def prywatnosc():
    return send_from_directory(KATALOG_STATIC, "prywatnosc.html")


@app.get("/zdrowie")
def zdrowie():
    return {"ok": True}


# ----------------------------------------------------------------- API: plan i strefy

@app.get("/api/start")
def api_start():
    return jsonify(nazwa=NAZWA, wersja=WERSJA, przyklad=plan.PRZYKLAD_PLANU, kod_dostepu=bool(KOD_DOSTEPU),
                   nazwy_stref=ustawienia.NAZWY_STREF, nazwy_mocy=ustawienia.NAZWY_MOCY,
                   mcp={"wlaczony": bool(os.environ.get("MCP_TOKEN", "").strip()),
                        "token_garmina": bool(os.environ.get("GARMIN_TOKEN", "").strip())})


@app.post("/api/podglad")
def api_podglad():
    d = _dane()
    tekst = d.get("plan")
    if not isinstance(tekst, str) or not tekst.strip():
        return jsonify(treningi=[])
    try:
        wyniki = plan.wczytaj(tekst, _ust(d))
    except plan.BladPlanu as e:
        return jsonify(blad=str(e))
    return jsonify(treningi=[_wpis_json(x) for x in wyniki])


@app.post("/api/prompt")
def api_prompt():
    return jsonify(prompt=plan.prompt_ai(_ust(_dane())))


@app.post("/api/strefy")
def api_strefy():
    """Pola formularza stref -> sprawdzone ustawienia, które przeglądarka zapisze u siebie."""
    pola = _dane().get("pola")
    if not isinstance(pola, dict):
        return _blad("Brak danych formularza.")
    try:
        return jsonify(ust=ustawienia.z_pol(pola))
    except plan.BladPlanu as e:
        return _blad(str(e))


@app.post("/api/moc-z-ftp")
def api_moc_z_ftp():
    try:
        ftp = plan.sprawdz_ftp(str(_dane().get("ftp") or "").strip().replace(",", ".") or None)
    except plan.BladPlanu as e:
        return _blad(f"FTP: {e}")
    if not ftp:
        return _blad("Najpierw wpisz FTP w watach.")
    progi = ", ".join(f"{a}-{b}%" for a, b in ustawienia.PROCENTY_MOCY)
    return jsonify(moc=ustawienia.moc_z_ftp(ftp),
                   opis=f"Policzone z progów {progi} FTP (pięć pierwszych stref wg Coggana). "
                        f"Możesz je poprawić ręcznie.")


# ----------------------------------------------------------------- API: Garmin

@app.post("/api/logowanie")
def api_logowanie():
    if _za_duzo_prob():
        return _blad("Za dużo prób logowania. Odczekaj kwadrans.", 429)
    d = _dane()
    email, haslo = d.get("email"), d.get("haslo")
    if not isinstance(email, str) or not isinstance(haslo, str) or not email.strip() or not haslo:
        return _blad("Wpisz e-mail i hasło do Garmin Connect.")
    try:
        status, wynik = garmin_io.zaloguj_haslem(email, haslo)
    except garmin_io.BladGarmin as e:
        return _blad(str(e), 401)
    if status == "mfa":
        return jsonify(stan="mfa", mfa=szyfruj("mfa", wynik))
    return jsonify(_zalogowano(wynik))


@app.post("/api/mfa")
def api_mfa():
    if _za_duzo_prob():
        return _blad("Za dużo prób logowania. Odczekaj kwadrans.", 429)
    d = _dane()
    stan = odszyfruj("mfa", d.get("mfa"), ttl=CZAS_MFA)
    if stan is None:
        return _blad("Logowanie trwało zbyt długo - zacznij od nowa.", 401, od_nowa=True)
    kod = d.get("kod")
    try:
        klient = garmin_io.dokoncz_mfa(stan, kod if isinstance(kod, str) else "")
    except garmin_io.BladGarmin as e:
        return _blad(str(e), 401)
    return jsonify(_zalogowano(klient))


@app.post("/api/wyslij")
def api_wyslij():
    """Wysyła plan. Odpowiedź to strumień linii JSON: {"log": ...}, a na końcu {"koniec": ..., "token": ...}."""
    d = _dane()
    tekst = d.get("plan")
    if not isinstance(tekst, str) or not tekst.strip():
        return _blad("Wklej plan, żeby go wysłać.")
    try:
        wyniki = plan.wczytaj(tekst, _ust(d))             # plan sprawdzany ponownie na serwerze
    except plan.BladPlanu as e:
        return _blad(f"W planie są błędy: {e}")
    if not any(not x["wolne"] for x in wyniki):
        return _blad("W planie nie ma treningów do wysłania (same dni wolne).")
    tok = odszyfruj("token", d.get("token"))
    if tok is None:
        return _blad("Logowanie wygasło - zaloguj się ponownie.", 401, wygaslo=True)
    try:
        klient = garmin_io.zaloguj_tokenem(tok)
    except garmin_io.TokenWygasl as e:
        return _blad(str(e), 401, wygaslo=True)
    except garmin_io.BladGarmin as e:
        return _blad(str(e), 502)

    q: queue.Queue = queue.Queue()
    nadpisz = bool(d.get("nadpisz"))

    def praca():
        # wątek kończy wysyłkę także wtedy, gdy przeglądarka się rozłączy (np. telefon zgasił ekran)
        try:
            st = garmin_io.wyslij(klient, wyniki, nadpisz, lambda m: q.put({"log": m}))
            q.put({"koniec": st, "token": szyfruj("token", garmin_io.token(klient))})
        except Exception as e:  # noqa: BLE001
            log.exception("wysyłka przerwana")
            q.put({"blad": str(e)})
        finally:
            q.put(None)

    threading.Thread(target=praca, daemon=True).start()

    def strumien():
        yield json.dumps({"log": "Wysyłam..."}, ensure_ascii=False) + "\n"
        while (x := q.get()) is not None:
            yield json.dumps(x, ensure_ascii=False) + "\n"

    return Response(strumien(), mimetype="application/x-ndjson",
                    headers={"X-Accel-Buffering": "no", "Cache-Control": "no-store"})


mcp_serwer.zarejestruj(app, odszyfruj, szyfruj)          # connector dla Claude: POST /mcp


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "8000")), threaded=True)
