# -*- coding: utf-8 -*-
"""
Pliki FIT na Dysku Google użytkownika (Google Drive API v3, OAuth 2.0).

Zakres domyślny: drive (pełny) - konektor musi widzieć pliki, które użytkownik wgrał sam (np. eksport
z Garmina w folderze vaulta), żeby wykrywać duplikaty i braki oraz dogrywać pliki do TEGO SAMEGO folderu.
Kod tylko czyta i dodaje pliki w folderze docelowym; niczego nie kasuje. Zakres można zawęzić zmienną
DYSK_ZAKRES (np. https://www.googleapis.com/auth/drive.file - wtedy widzi tylko własne pliki).

Konfiguracja (zmienne środowiskowe):
  GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET - klient OAuth (Google Cloud Console, typ „Aplikacja internetowa”,
      URI przekierowania: https://<serwer>/google/powrot)
  GOOGLE_TOKEN  - zaszyfrowany refresh token (strona po zalogowaniu przez /google/polacz/... go pokazuje)
  DYSK_FOLDER_ID - opcjonalnie ID folderu docelowego (pewniejsze niż ścieżka, gdy są foldery o tej samej nazwie)
  DYSK_FOLDER   - opcjonalnie ścieżka folderu od „Mój dysk”, domyślnie „Dane treningowe/FIT (surowe pliki z Garmina)”
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from urllib.parse import urlencode

import requests

ZAKRES_DOMYSLNY = "https://www.googleapis.com/auth/drive"
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


def zakres() -> str:
    return os.environ.get("DYSK_ZAKRES", "").strip() or ZAKRES_DOMYSLNY


def folder() -> str:
    return (os.environ.get("DYSK_FOLDER", "").strip() or FOLDER_DOMYSLNY).strip("/")


def id_aktywnosci(nazwa: str) -> str | None:
    """Numer aktywności Garmina z nazwy pliku: 24644475709.zip, 24644475709_ACTIVITY.fit -> 24644475709."""
    m = re.match(r"^(\d{6,})(?:[_.\-]|$)", nazwa or "")
    return m.group(1) if m else None


def resetuj_cache() -> None:
    with _lock:
        _cache.update(token=None, wygasa=0.0, foldery={})


# ----------------------------------------------------------------- OAuth

def adres_logowania(redirect_uri: str, state: str) -> str:
    cid, _ = klient()
    return URL_AUTH + "?" + urlencode({
        "client_id": cid, "redirect_uri": redirect_uri, "response_type": "code", "scope": zakres(),
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
    stale = os.environ.get("DYSK_FOLDER_ID", "").strip()
    if stale:
        return stale
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


def opis_folderu(refresh: str) -> dict:
    fid = _id_folderu(refresh)
    d = _zapytanie("GET", f"{URL_PLIKI}/{fid}", refresh, params={"fields": "id,name,webViewLink"})
    return {"id": fid, "nazwa": d.get("name"), "link": d.get("webViewLink")}


def lista_plikow(refresh: str, limit: int = 5000) -> list[dict]:
    """Pliki (bez podfolderów) w folderze docelowym, z numerem aktywności wyciągniętym z nazwy."""
    rodzic = _id_folderu(refresh)
    pliki, strona = [], None
    while len(pliki) < limit:
        params = {"q": f"'{rodzic}' in parents and trashed = false and mimeType != '{MIME_FOLDER}'",
                  "fields": "nextPageToken,files(id,name,size,md5Checksum,createdTime,webViewLink)",
                  "spaces": "drive", "pageSize": 1000, "orderBy": "name"}
        if strona:
            params["pageToken"] = strona
        d = _zapytanie("GET", URL_PLIKI, refresh, params=params)
        for f in d.get("files") or []:
            pliki.append({"nazwa": f.get("name"), "id": f.get("id"), "activity_id": id_aktywnosci(f.get("name")),
                          "rozmiar_b": int(f["size"]) if f.get("size") else None, "md5": f.get("md5Checksum"),
                          "utworzony": (f.get("createdTime") or "")[:10] or None, "link": f.get("webViewLink")})
        strona = d.get("nextPageToken")
        if not strona:
            break
    return pliki


def wyslij(refresh: str, nazwa: str, dane: bytes, mime: str = "application/zip",
           istniejace: list[dict] | None = None) -> dict:
    """Wgrywa plik do folderu docelowego. Nie dubluje: pomija, gdy jest już plik o tej samej nazwie
    albo z tym samym numerem aktywności (np. 24644475709.zip vs 24644475709_ACTIVITY.zip)."""
    rodzic = _id_folderu(refresh)
    aid = id_aktywnosci(nazwa)
    if istniejace is None:
        istniejace = lista_plikow(refresh)
    for f in istniejace:
        if f["nazwa"] == nazwa or (aid and f.get("activity_id") == aid):
            return {"nazwa": f["nazwa"], "status": "juz_byl", "id": f["id"], "link": f.get("link")}
    granica = "garmin-plan-loader-granica"
    meta = json.dumps({"name": nazwa, "parents": [rodzic]}).encode()
    cialo = (f"--{granica}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode() + meta +
             f"\r\n--{granica}\r\nContent-Type: {mime}\r\n\r\n".encode() + dane +
             f"\r\n--{granica}--\r\n".encode())
    d = _zapytanie("POST", URL_UPLOAD, refresh, params={"uploadType": "multipart", "fields": "id,name,webViewLink"},
                   data=cialo, headers={"Content-Type": f"multipart/related; boundary={granica}"})
    return {"nazwa": nazwa, "status": "wgrany", "id": d.get("id"), "link": d.get("webViewLink")}
