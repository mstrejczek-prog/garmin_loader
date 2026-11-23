# -*- coding: utf-8 -*-
"""
Zapis plików FIT na Dysk Google użytkownika (Google Drive API v3, OAuth 2.0, zakres drive.file).

drive.file = konektor widzi i zmienia WYŁĄCZNIE pliki i foldery, które sam utworzył - nie ma dostępu
do reszty Dysku. Dlatego folder docelowy jest zakładany przez konektor (nawet jeśli masz już folder
o tej samej nazwie, powstanie osobny - konektor go nie widzi).

Konfiguracja (zmienne środowiskowe):
  GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET - klient OAuth (Google Cloud Console, typ „Aplikacja internetowa”,
      URI przekierowania: https://<serwer>/google/powrot)
  GOOGLE_TOKEN  - zaszyfrowany refresh token (strona po zalogowaniu przez /google/polacz/... go pokazuje)
  DYSK_FOLDER   - opcjonalnie ścieżka folderu, domyślnie „Dane treningowe/FIT (surowe pliki z Garmina)”
"""
from __future__ import annotations

import json
import os
import threading
import time
from urllib.parse import urlencode

import requests

ZAKRES = "https://www.googleapis.com/auth/drive.file"
URL_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
URL_TOKEN = "https://oauth2.googleapis.com/token"
URL_PLIKI = "https://www.googleapis.com/drive/v3/files"
URL_UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
FOLDER_DOMYSLNY = "Dane treningowe/FIT (surowe pliki z Garmina)"
MIME_FOLDER = "application/vnd.google-apps.folder"

_http = requests.Session()          # podmieniane w testach
_lock = threading.Lock()
_cache: dict = {"token": None, "wygasa": 0.0, "foldery": {}}


class BladDysku(Exception):
    pass


def klient() -> tuple[str, str]:
    cid = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
    sec = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
    return cid, sec


def ma_klienta() -> bool:
    return all(klient())


def folder() -> str:
    return (os.environ.get("DYSK_FOLDER", "").strip() or FOLDER_DOMYSLNY).strip("/")


def resetuj_cache() -> None:
    with _lock:
        _cache.update(token=None, wygasa=0.0, foldery={})


# ----------------------------------------------------------------- OAuth

def adres_logowania(redirect_uri: str, state: str) -> str:
    cid, _ = klient()
    return URL_AUTH + "?" + urlencode({
        "client_id": cid, "redirect_uri": redirect_uri, "response_type": "code", "scope": ZAKRES,
        "access_type": "offline", "prompt": "consent", "include_granted_scopes": "true", "state": state,
    })


def _post_token(dane: dict) -> dict:
    cid, sec = klient()
    try:
        r = _http.post(URL_TOKEN, data={**dane, "client_id": cid, "client_secret": sec}, timeout=30)
    except requests.RequestException as e:
        raise BladDysku(f"Brak połączenia z Google: {e}") from e
    if r.status_code != 200:
        try:
            opis = r.json().get("error_description") or r.json().get("error")
        except ValueError:
            opis = r.text[:200]
        if "invalid_grant" in str(r.text):
            raise BladDysku("Google odrzucił logowanie (token cofnięty albo wygasł) - połącz Dysk Google "
                            "ponownie narzędziem polacz_dysk_google i podmień GOOGLE_TOKEN.")
        raise BladDysku(f"Google zwrócił błąd {r.status_code}: {opis}")
    return r.json()


def wymien_kod(kod: str, redirect_uri: str) -> str:
    """Kod z przekierowania -> refresh token."""
    d = _post_token({"code": kod, "grant_type": "authorization_code", "redirect_uri": redirect_uri})
    if not d.get("refresh_token"):
        raise BladDysku("Google nie zwrócił refresh tokenu - usuń dostęp aplikacji w ustawieniach konta "
                        "Google (Bezpieczeństwo → Aplikacje innych firm) i połącz ponownie.")
    return d["refresh_token"]


def _token_dostepu(refresh: str) -> str:
    with _lock:
        if _cache["token"] and _cache["wygasa"] - time.time() > 60:
            return _cache["token"]
    d = _post_token({"refresh_token": refresh, "grant_type": "refresh_token"})
    with _lock:
        _cache["token"] = d["access_token"]
        _cache["wygasa"] = time.time() + float(d.get("expires_in", 3600))
    return d["access_token"]


# ----------------------------------------------------------------- Drive

def _zapytanie(metoda: str, url: str, refresh: str, **kw) -> dict:
    nag = {"Authorization": f"Bearer {_token_dostepu(refresh)}", **kw.pop("headers", {})}
    try:
        r = _http.request(metoda, url, headers=nag, timeout=60, **kw)
    except requests.RequestException as e:
        raise BladDysku(f"Brak połączenia z Dyskiem Google: {e}") from e
    if r.status_code == 401:
        resetuj_cache()
    if r.status_code >= 400:
        raise BladDysku(f"Dysk Google zwrócił błąd {r.status_code}: {r.text[:200]}")
    return r.json() if r.content else {}


def _esc(t: str) -> str:
    return t.replace("\\", "\\\\").replace("'", "\\'")


def _znajdz(refresh: str, nazwa: str, rodzic: str, mime: str | None = None) -> dict | None:
    q = f"name = '{_esc(nazwa)}' and '{rodzic}' in parents and trashed = false"
    if mime:
        q += f" and mimeType = '{mime}'"
    d = _zapytanie("GET", URL_PLIKI, refresh,
                   params={"q": q, "fields": "files(id,name,webViewLink)", "spaces": "drive", "pageSize": 10})
    pliki = d.get("files") or []
    return pliki[0] if pliki else None


def _id_folderu(refresh: str) -> str:
    sciezka = folder()
    with _lock:
        if sciezka in _cache["foldery"]:
            return _cache["foldery"][sciezka]
    rodzic = "root"
    for czesc in [c for c in sciezka.split("/") if c]:
        f = _znajdz(refresh, czesc, rodzic, MIME_FOLDER)
        if not f:
            f = _zapytanie("POST", URL_PLIKI, refresh, params={"fields": "id"},
                           json={"name": czesc, "mimeType": MIME_FOLDER, "parents": [rodzic]})
        rodzic = f["id"]
    with _lock:
        _cache["foldery"][sciezka] = rodzic
    return rodzic


def wyslij(refresh: str, nazwa: str, dane: bytes, mime: str = "application/zip") -> dict:
    """Wgrywa plik do folderu docelowego; plik o tej samej nazwie nie jest dublowany."""
    rodzic = _id_folderu(refresh)
    istniejacy = _znajdz(refresh, nazwa, rodzic)
    if istniejacy:
        return {"nazwa": nazwa, "status": "juz_byl", "id": istniejacy["id"], "link": istniejacy.get("webViewLink")}
    granica = "garmin-plan-loader-granica"
    meta = json.dumps({"name": nazwa, "parents": [rodzic]}).encode()
    cialo = (f"--{granica}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode() + meta +
             f"\r\n--{granica}\r\nContent-Type: {mime}\r\n\r\n".encode() + dane +
             f"\r\n--{granica}--\r\n".encode())
    d = _zapytanie("POST", URL_UPLOAD, refresh, params={"uploadType": "multipart", "fields": "id,name,webViewLink"},
                   data=cialo, headers={"Content-Type": f"multipart/related; boundary={granica}"})
    return {"nazwa": nazwa, "status": "wgrany", "id": d.get("id"), "link": d.get("webViewLink")}
