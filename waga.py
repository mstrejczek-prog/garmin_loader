# -*- coding: utf-8 -*-
"""
Łącznik wagi: Xiaomi Mi Body Composition Scale 2 -> chmura Zepp Life -> Garmin Connect + arkusz Google
„Pomiary” (zakładka „Waga”).

Uruchamiany z GitHub Actions (cron co 3 h) przez POST /waga/<tryb> z nagłówkiem
Authorization: Bearer <WAGA_TOKEN>. Tryby:
  test             - tylko odczyt z Zepp i z Garmina (pierwsze logowanie do Zepp, jeśli nie ma tokenu)
  historia         - cała historia z Zepp -> zaszyfrowany backup + raport (bez wysyłki)
  wyslij_historie  - wysyłka zaakceptowanej historii do Garmina i arkusza, partiami, rosnąco po dacie
  sync             - nowe pomiary (od ostatniego przesłanego minus 24 h) -> Garmin + arkusz

Dane i stan (tokeny Zepp, czas ostatniego pomiaru, raport, backup CSV) są zaszyfrowane kluczem serwera
(SECRET_KEY, Fernet) i leżą na Dysku Google właściciela - obok tokenu Garmina connectora. Odpowiedź
endpointu (trafia do publicznego logu Actions) zawiera tylko liczby i daty, bez masy i składu ciała;
pełny raport pokazuje narzędzie MCP `waga`.

Skład ciała jest brany z Zepp bez przeliczeń. Zmienne serwera: ZEPP_EMAIL, ZEPP_PASSWORD, WAGA_TOKEN,
WAGA_MIN, WAGA_MAX, WAGA_ARKUSZ_ID (domyślnie arkusz „Pomiary”), WAGA_PARTIA (domyślnie 25),
WAGA_PROFIL (opcjonalnie: dodatkowe memberId uznawane za Twój profil, po przecinku).
"""
from __future__ import annotations

import csv
import datetime as dt
import hmac
import io
import json
import logging
import os
import threading
import time
from urllib.parse import quote
from zoneinfo import ZoneInfo

from flask import Blueprint, jsonify, request

import dysk_google
import mcp_serwer
import zepp

log = logging.getLogger("garmin_plan_loader.waga")

STREFA = ZoneInfo("Europe/Warsaw")
ARKUSZ_DOMYSLNY = "1VLf2q9H0y6SldUSp65fbDZALs2-ND-GS3PPcuybN_DU"
ZAKLADKA = "Waga"
NAGLOWKI = ["Data", "Godzina", "Masa [kg]", "Tkanka tłuszczowa [%]", "Masa mięśniowa [kg]", "Woda [%]",
            "Masa kostna [kg]", "BMI", "Źródło"]
ZRODLO = "Zepp → łącznik"
PLIK_STANU = ".waga-sync-stan.json"
PLIK_BACKUPU = ".waga-sync-backup.json"
URL_ARKUSZE = "https://sheets.googleapis.com/v4/spreadsheets"
TOL_CZAS_S = 60                                     # Garmin: ten sam pomiar = czas ±60 s ...
TOL_MASA_KG = 0.05                                  # ... i masa ±0,05 kg
PRZERWA_S = 1.0                                     # między wysyłkami do Garmina
OKNO_SYNC_S = 24 * 3600                             # sync pobiera od ostatniego przesłanego minus 24 h
MAX_ZAKRES_DNI = 90                                 # jedna partia historii obejmuje najwyżej tyle dni
SKOK_KG = 2.0
TRYBY = ("test", "historia", "wyslij_historie", "sync")
POLA_CSV = ["ts", "data", "godzina", "masa", "tluszcz", "miesnie", "woda", "kosci", "bmi", "metabolizm",
            "trzewny", "wiek_metaboliczny", "budowa", "impedancja", "profil", "status"]

_blokada = threading.Lock()
_spij = time.sleep                                  # podmieniane w testach


class BladWagi(Exception):
    def __init__(self, kategoria: str, opis: str):
        super().__init__(opis)
        self.kategoria = kategoria


# ----------------------------------------------------------------- pomiary

def _liczba(v):
    """Wartość z Zepp bez przeliczeń; 0 i braki -> None (Zepp pomija albo zeruje pola, których nie ma)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v == 0:
        return None
    return v


def normalizuj(rekord: dict) -> dict | None:
    """Surowy rekord Zepp -> pomiar. None dla rekordów bez czasu/masy albo uszkodzonych (weightType != 0)."""
    if not isinstance(rekord, dict) or (rekord.get("weightType") or 0) != 0:
        return None
    s = rekord.get("summary") or {}
    if isinstance(s, str):
        try:
            s = json.loads(s)
        except ValueError:
            s = {}
    ts, masa = rekord.get("generatedTime"), _liczba(s.get("weight"))
    if not isinstance(ts, (int, float)) or ts <= 0 or masa is None:
        return None
    imp = s.get("impedance")
    ma_impedancje = (isinstance(imp, (int, float)) and imp > 0) or (imp is None and _liczba(s.get("fatRate")) is not None)
    czas = dt.datetime.fromtimestamp(int(ts), STREFA)
    p = {"ts": int(ts), "data": czas.strftime("%Y-%m-%d"), "godzina": czas.strftime("%H:%M"), "masa": masa,
         "bmi": _liczba(s.get("bmi")), "impedancja": bool(ma_impedancje),
         "profil": str(rekord.get("memberId") if rekord.get("memberId") is not None else "-1")}
    sklad = {"tluszcz": s.get("fatRate"), "miesnie": s.get("muscleRate"), "woda": s.get("bodyWaterRate"),
             "kosci": s.get("boneMass"), "metabolizm": s.get("metabolism"), "trzewny": s.get("visceralFat"),
             "wiek_metaboliczny": s.get("muscleAge"), "budowa": s.get("bodyStyle")}
    for k, v in sklad.items():
        p[k] = _liczba(v) if ma_impedancje else None    # bez impedancji: tylko masa (i BMI z masy)
    return p


def zakres_masy() -> tuple[float, float]:
    try:
        lo, hi = float(os.environ["WAGA_MIN"]), float(os.environ["WAGA_MAX"])
    except (KeyError, ValueError):
        raise BladWagi("konfiguracja", "Ustaw na serwerze WAGA_MIN i WAGA_MAX (zakres masy, np. 65 i 85).") from None
    if lo >= hi:
        raise BladWagi("konfiguracja", "WAGA_MIN musi być mniejsze niż WAGA_MAX.")
    return lo, hi


def filtruj(pomiary: list[dict], lo: float, hi: float, uid=None) -> tuple[list[dict], list[dict]]:
    """(przyjęte, odrzucone). Tylko główny profil konta i masa w [lo, hi]. Odrzucone mają pole status."""
    uids = [uid] if isinstance(uid, str) else list(uid or [])
    moje = {"-1", "0", ""} | {str(u) for u in uids if u}
    moje |= {x.strip() for x in os.environ.get("WAGA_PROFIL", "").split(",") if x.strip()}
    ok, zle = [], []
    for p in pomiary:
        if p["profil"] not in moje:
            zle.append({**p, "status": "odrzucony:inny_profil"})
        elif not lo <= p["masa"] <= hi:
            zle.append({**p, "status": "odrzucony:zakres_masy"})
        else:
            ok.append({**p, "status": "przyjety"})
    return ok, zle


def z_rekordow(surowe: list[dict]) -> list[dict]:
    wynik = {}
    for r in surowe:
        p = normalizuj(r)
        if p and p["ts"] not in wynik:
            wynik[p["ts"]] = p
    return [wynik[k] for k in sorted(wynik)]


# ----------------------------------------------------------------- raport z historii

def raport(przyjete: list[dict], odrzucone: list[dict]) -> dict:
    if not przyjete:
        return {"pomiarow": 0, "odrzuconych": len(odrzucone)}
    po_dniach: dict[str, list[float]] = {}
    for p in przyjete:
        po_dniach.setdefault(p["data"], []).append(p["masa"])
    dni = sorted(po_dniach)
    sr = {d: sum(v) / len(v) for d, v in po_dniach.items()}
    skoki = []
    for a, b in zip(dni, dni[1:]):
        roznica = sr[b] - sr[a]
        if abs(roznica) > SKOK_KG:
            przerwa = (dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days
            skoki.append({"z": a, "na": b, "dni_miedzy": przerwa, "roznica_kg": round(roznica, 2)})
    mn, mx = min(przyjete, key=lambda p: p["masa"]), max(przyjete, key=lambda p: p["masa"])
    powody: dict[str, int] = {}
    for p in odrzucone:
        powody[p["status"]] = powody.get(p["status"], 0) + 1
    return {"pomiarow": len(przyjete), "dni_z_pomiarem": len(dni), "od": dni[0], "do": dni[-1],
            "min": {"masa_kg": mn["masa"], "data": mn["data"]}, "max": {"masa_kg": mx["masa"], "data": mx["data"]},
            "bez_impedancji": sum(1 for p in przyjete if not p["impedancja"]),
            "skoki_ponad_2kg_dzien_do_dnia": skoki, "odrzuconych": len(odrzucone), "odrzucone_powody": powody,
            "uwaga": "Skoki liczone ze średniej dziennej między kolejnymi dniami z pomiarem (wyliczenie łącznika)."}


# ----------------------------------------------------------------- stan i backup (zaszyfrowane, Dysk Google)

def _refresh() -> str:
    r = mcp_serwer._refresh_google()
    if not r:
        raise BladWagi("konfiguracja", "Dysk Google nie jest połączony (GOOGLE_TOKEN) - stan łącznika nie ma gdzie leżeć.")
    return r


def _szyfruj(rodzaj: str, dane) -> str:
    if not mcp_serwer._szyfruj:
        raise BladWagi("konfiguracja", "Brak szyfrowania na serwerze.")
    return mcp_serwer._szyfruj(rodzaj, dane)


def _czytaj(refresh: str, plik: str, rodzaj: str, domyslnie):
    tekst = dysk_google.odczytaj_tekst(refresh, plik)
    if not tekst:
        return domyslnie
    try:
        zawartosc = json.loads(tekst).get("dane")
    except (ValueError, AttributeError):
        zawartosc = None
    dane = mcp_serwer._odszyfruj(rodzaj, zawartosc) if mcp_serwer._odszyfruj else None
    if dane is None:
        raise BladWagi("stan", f"Nie da się odszyfrować {plik} (zmieniony SECRET_KEY?). Usuń plik z Dysku "
                               "i zacznij od trybu test.")
    return dane


def _zapisz(refresh: str, plik: str, rodzaj: str, dane) -> None:
    dysk_google.zapisz_tekst(refresh, plik, json.dumps({"dane": _szyfruj(rodzaj, dane)}))


def wczytaj_stan(refresh: str) -> dict:
    return _czytaj(refresh, PLIK_STANU, "waga_stan", {})


def zapisz_stan(refresh: str, stan: dict) -> None:
    _zapisz(refresh, PLIK_STANU, "waga_stan", stan)


def csv_z(pomiary: list[dict]) -> str:
    bufor = io.StringIO()
    w = csv.DictWriter(bufor, fieldnames=POLA_CSV, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    for p in sorted(pomiary, key=lambda x: x["ts"]):
        w.writerow({k: ("" if p.get(k) is None else p.get(k)) for k in POLA_CSV})
    return bufor.getvalue()


def _z_csv_liczba(v: str):
    if v in ("", None):
        return None
    try:
        f = float(v)
    except ValueError:
        return v
    return int(f) if f.is_integer() and "." not in v else f


def pomiary_z_csv(tekst: str) -> list[dict]:
    wynik = []
    for w in csv.DictReader(io.StringIO(tekst or "")):
        p = {k: _z_csv_liczba(w.get(k, "")) for k in POLA_CSV}
        p["ts"] = int(p["ts"])
        for k in ("data", "godzina", "profil", "status"):
            p[k] = w.get(k, "")
        p["impedancja"] = w.get("impedancja") == "True"
        wynik.append(p)
    return wynik


def wczytaj_backup(refresh: str) -> list[dict]:
    return pomiary_z_csv(_czytaj(refresh, PLIK_BACKUPU, "waga_backup", ""))


def scal_backup(stary: list[dict], nowe: list[dict]) -> list[dict]:
    po_ts = {p["ts"]: p for p in stary}
    for p in nowe:
        po_ts[p["ts"]] = p
    return [po_ts[k] for k in sorted(po_ts)]


def zapisz_backup(refresh: str, pomiary: list[dict]) -> None:
    _zapisz(refresh, PLIK_BACKUPU, "waga_backup", csv_z(pomiary))


# ----------------------------------------------------------------- Garmin

def _garmin_istniejace(klient, od: str, do: str) -> list[tuple[float, float]]:
    """(czas unix s, masa kg) pomiarów, które Garmin już ma w zakresie dat."""
    d = klient.get_body_composition(od, do) or {}
    wynik = []
    for x in d.get("dateWeightList") or []:
        ms = x.get("timestampGMT") or x.get("date")
        g = x.get("weight")
        if isinstance(ms, (int, float)) and isinstance(g, (int, float)):
            wynik.append((ms / 1000, g / 1000))
    return wynik


def _jest_w_garminie(p: dict, istniejace: list[tuple[float, float]]) -> bool:
    return any(abs(t - p["ts"]) <= TOL_CZAS_S and abs(m - p["masa"]) <= TOL_MASA_KG + 1e-9 for t, m in istniejace)


def _do_garmina(klient, p: dict):
    czas = dt.datetime.fromtimestamp(p["ts"], STREFA).isoformat()
    return klient.add_body_composition(
        czas, weight=p["masa"], percent_fat=p.get("tluszcz"), percent_hydration=p.get("woda"),
        bone_mass=p.get("kosci"), muscle_mass=p.get("miesnie"), basal_met=p.get("metabolizm"),
        visceral_fat_rating=p.get("trzewny"), metabolic_age=p.get("wiek_metaboliczny"),
        physique_rating=p.get("budowa"), bmi=p.get("bmi"))


def wyslij_do_garmina(pomiary: list[dict]) -> dict:
    if not pomiary:
        return {"wyslane": 0, "pominiete_duplikaty": 0}
    dni = [dt.date.fromisoformat(p["data"]) for p in pomiary]
    od, do = (min(dni) - dt.timedelta(days=1)).isoformat(), (max(dni) + dt.timedelta(days=1)).isoformat()
    istniejace = mcp_serwer._z_garminem(lambda k: _garmin_istniejace(k, od, do))
    wyslane = pominiete = 0
    for p in sorted(pomiary, key=lambda x: x["ts"]):
        if _jest_w_garminie(p, istniejace):
            pominiete += 1
            continue
        if wyslane:
            _spij(PRZERWA_S)
        mcp_serwer._z_garminem(lambda k, p=p: _do_garmina(k, p))
        istniejace.append((p["ts"], p["masa"]))
        wyslane += 1
    return {"wyslane": wyslane, "pominiete_duplikaty": pominiete}


# ----------------------------------------------------------------- arkusz Google (Sheets API, OAuth z Dysku)

def _id_arkusza() -> str:
    return os.environ.get("WAGA_ARKUSZ_ID", "").strip() or ARKUSZ_DOMYSLNY


def _komorka_daty(v) -> str:
    if isinstance(v, (int, float)):                  # numer seryjny daty arkusza
        return (dt.date(1899, 12, 30) + dt.timedelta(days=int(v))).isoformat()
    return str(v or "").strip()


def _komorka_godziny(v) -> str:
    if isinstance(v, (int, float)):                  # ułamek doby
        minuty = round((v % 1) * 24 * 60)
        return f"{minuty // 60:02d}:{minuty % 60:02d}"
    t = str(v or "").strip()
    return t[:5] if len(t) >= 5 and t[2] == ":" else (("0" + t)[:5] if len(t) == 4 and t[1] == ":" else t)


def _komorka_masy(v):
    if isinstance(v, (int, float)):
        return round(float(v), 2)
    try:
        return round(float(str(v).replace(",", ".").strip()), 2)
    except ValueError:
        return None


def klucz(data: str, godzina: str, masa) -> tuple:
    return (data, godzina, None if masa is None else round(float(masa), 2))


def _arkusz_get(refresh: str) -> list[list]:
    zakres = quote(f"{ZAKLADKA}!A:I", safe="")
    d = dysk_google._zapytanie("GET", f"{URL_ARKUSZE}/{_id_arkusza()}/values/{zakres}", refresh,
                               params={"valueRenderOption": "UNFORMATTED_VALUE",
                                       "dateTimeRenderOption": "SERIAL_NUMBER"})
    return d.get("values") or []


def wiersz(p: dict) -> list:
    def v(x):
        return "" if x is None else x
    return [p["data"], p["godzina"], p["masa"], v(p.get("tluszcz")), v(p.get("miesnie")), v(p.get("woda")),
            v(p.get("kosci")), v(p.get("bmi")), ZRODLO]


def wyslij_do_arkusza(refresh: str, pomiary: list[dict]) -> dict:
    wiersze = _arkusz_get(refresh)
    naglowek = [str(x).strip() for x in (wiersze[0] if wiersze else [])]
    if naglowek[:len(NAGLOWKI)] != NAGLOWKI:
        raise BladWagi("arkusz", f"Zakładka „{ZAKLADKA}” ma inne nagłówki w wierszu 1 niż oczekiwane - "
                                 "niczego nie zapisano.")
    jest = {klucz(_komorka_daty(w[0] if w else ""), _komorka_godziny(w[1] if len(w) > 1 else ""),
                  _komorka_masy(w[2]) if len(w) > 2 else None) for w in wiersze[1:]}
    nowe, pominiete = [], 0
    for p in sorted(pomiary, key=lambda x: x["ts"]):
        k = klucz(p["data"], p["godzina"], p["masa"])
        if k in jest:
            pominiete += 1
            continue
        jest.add(k)
        nowe.append(wiersz(p))
    if nowe:
        zakres = quote(f"{ZAKLADKA}!A:I", safe="")
        dysk_google._zapytanie("POST", f"{URL_ARKUSZE}/{_id_arkusza()}/values/{zakres}:append", refresh,
                               params={"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"},
                               json={"majorDimension": "ROWS", "values": nowe})
    return {"wyslane": len(nowe), "pominiete_duplikaty": pominiete}


# ----------------------------------------------------------------- przebiegi

def _teraz() -> str:
    return dt.datetime.now(STREFA).isoformat(timespec="seconds")


def _pobierz_z_zepp(stan: dict, od_czasu: int, pozwol_haslo: bool = True) -> tuple[list[dict], dict]:
    email, haslo = os.environ.get("ZEPP_EMAIL", "").strip(), os.environ.get("ZEPP_PASSWORD", "")
    mial_token = bool((stan.get("zepp") or {}).get("app_token"))
    if (h := os.environ.get("ZEPP_HOST", "").strip()) and stan.get("zepp"):
        stan["zepp"]["host_api"] = h
    try:
        surowe, tokeny, kroki = zepp.rekordy_z_sesja(stan.get("zepp"), email, haslo, od_czasu, pozwol_haslo)
    except zepp.BladZepp as e:
        if zepp.DIAGNOZA:
            raise zepp.BladZepp(e.kategoria, e.opis + " | diagnoza: " + "; ".join(zepp.DIAGNOZA)) from None
        raise
    stan["zepp"] = tokeny
    info = {"kroki": kroki, "rekordow": len(surowe), "logowanie_haslem": None, "host": tokeny.get("host_api"),
            "diagnoza": list(zepp.DIAGNOZA), "app_ttl_s": tokeny.get("app_ttl")}
    if "haslo" in kroki:
        info["logowanie_haslem"] = "awaryjne" if mial_token else "pierwsze"
        stan.setdefault("logowania_haslem", []).append({"kiedy": _teraz(), "rodzaj": info["logowanie_haslem"]})
    return surowe, info


def _wyslij(refresh: str, pomiary: list[dict]) -> dict:
    g = wyslij_do_garmina(pomiary)
    a = wyslij_do_arkusza(refresh, pomiary)
    return {"garmin": g, "arkusz": a}


def tryb_test(refresh: str, stan: dict) -> dict:
    od = int(time.time()) - 30 * 86400
    pierwsze = not (stan.get("zepp") or {}).get("app_token")
    surowe, info = _pobierz_z_zepp(stan, od, pozwol_haslo=pierwsze)   # test: hasło tylko przy pierwszym logowaniu
    zapisz_stan(refresh, stan)                      # tokeny po pierwszym logowaniu
    pomiary = z_rekordow(surowe)
    dzis = dt.date.today()
    garmin = mcp_serwer._z_garminem(lambda k: _garmin_istniejace(
        k, (dzis - dt.timedelta(days=30)).isoformat(), dzis.isoformat()))
    return {"zepp": {**info, "pomiarow_30_dni": len(pomiary),
                     "ostatni_pomiar": pomiary[-1]["data"] + " " + pomiary[-1]["godzina"] if pomiary else None},
            "garmin": {"pomiarow_30_dni": len(garmin)}}


def _stare_konto() -> dict | None:
    """Jednorazowy odczyt historii ze starego konta (np. logowanego przez Google): token z przeglądarki.
    Tylko odczyt, bez odświeżania i bez logowania."""
    tok, uid = os.environ.get("ZEPP_STARE_APPTOKEN", "").strip(), os.environ.get("ZEPP_STARE_USER_ID", "").strip()
    if not tok and not uid:
        return None
    if not (tok and uid):
        raise BladWagi("konfiguracja", "Dla starego konta ustaw oba: ZEPP_STARE_APPTOKEN i ZEPP_STARE_USER_ID.")
    stan = {"user_id": uid, "app_token": tok}
    if (host := os.environ.get("ZEPP_STARE_HOST", "").strip()):
        stan["host_api"] = host
    return stan


def tryb_historia(refresh: str, stan: dict) -> dict:
    lo, hi = zakres_masy()
    surowe, info = _pobierz_z_zepp(stan, 0)
    uids = [(stan.get("zepp") or {}).get("user_id")]
    if (stare := _stare_konto()):
        try:
            stare_rek = zepp.rekordy(stare, 0)
        except zepp.BladZepp as e:
            raise zepp.BladZepp(e.kategoria, "stare konto: " + e.opis) from None
        info["stare_konto_rekordow"] = len(stare_rek)
        surowe = list(surowe) + stare_rek
        uids.append(stare["user_id"])
    pomiary = z_rekordow(surowe)
    ok, zle = filtruj(pomiary, lo, hi, uids)
    zapisz_backup(refresh, scal_backup(wczytaj_backup(refresh), ok + zle))
    stan["historia"] = {"pobrano": _teraz(), "raport": raport(ok, zle)}
    stan.pop("historia_kursor", None)
    zapisz_stan(refresh, stan)
    r = stan["historia"]["raport"]
    return {"zepp": info, "pomiarow": r.get("pomiarow", 0), "odrzuconych": r.get("odrzuconych", 0),
            "od": r.get("od"), "do": r.get("do"), "skokow_ponad_2kg": len(r.get("skoki_ponad_2kg_dzien_do_dnia") or []),
            "raport": "pełny raport (z masą) pokazuje narzędzie MCP `waga` w czacie z Claude"}


def tryb_wyslij_historie(refresh: str, stan: dict) -> dict:
    if not stan.get("historia"):
        raise BladWagi("kolejnosc", "Najpierw uruchom tryb historia i zaakceptuj raport.")
    if stan.get("historia_zakonczona"):
        return {"pozostalo": 0, "uwaga": "Historia była już wysłana."}
    partia_max = max(1, int(os.environ.get("WAGA_PARTIA", "25") or 25))
    kursor = stan.get("historia_kursor") or 0
    zaleglosci = [p for p in wczytaj_backup(refresh) if p["status"] == "przyjety" and p["ts"] > kursor]
    partia = []
    for p in zaleglosci:
        if len(partia) >= partia_max or (partia and p["ts"] - partia[0]["ts"] > MAX_ZAKRES_DNI * 86400):
            break
        partia.append(p)
    wynik = _wyslij(refresh, partia) if partia else {"garmin": {"wyslane": 0}, "arkusz": {"wyslane": 0}}
    pozostalo = len(zaleglosci) - len(partia)
    if partia:
        stan["historia_kursor"] = partia[-1]["ts"]
        stan["ostatni_ts"] = max(stan.get("ostatni_ts") or 0, partia[-1]["ts"])
    if pozostalo == 0:
        stan["historia_zakonczona"] = _teraz()
    zapisz_stan(refresh, stan)
    return {**wynik, "w_partii": len(partia), "pozostalo": pozostalo,
            "partia_od": partia[0]["data"] if partia else None, "partia_do": partia[-1]["data"] if partia else None}


def tryb_sync(refresh: str, stan: dict) -> dict:
    if not (stan.get("zepp") or {}).get("app_token"):
        raise BladWagi("kolejnosc", "Brak tokenu Zepp - najpierw tryb test (krok 0).")
    if not stan.get("historia_zakonczona"):
        return {"pominieto": True, "uwaga": "Sync startuje po wysłaniu historii (tryb wyslij_historie)."}
    lo, hi = zakres_masy()
    od = max(0, int(stan.get("ostatni_ts") or 0) - OKNO_SYNC_S)
    surowe, info = _pobierz_z_zepp(stan, od)
    ok, zle = filtruj(z_rekordow(surowe), lo, hi, (stan.get("zepp") or {}).get("user_id"))
    zapisz_stan(refresh, stan)                      # odświeżony token, zanim cokolwiek pójdzie dalej
    wynik = _wyslij(refresh, ok)
    zapisz_backup(refresh, scal_backup(wczytaj_backup(refresh), ok + zle))
    if ok:
        stan["ostatni_ts"] = max(stan.get("ostatni_ts") or 0, max(p["ts"] for p in ok))
    zapisz_stan(refresh, stan)
    return {"zepp": info, "przyjetych": len(ok), "odrzuconych": len(zle), **wynik}


PRZEBIEGI = {"test": tryb_test, "historia": tryb_historia, "wyslij_historie": tryb_wyslij_historie,
             "sync": tryb_sync}


def przebieg(tryb: str) -> dict:
    if tryb not in PRZEBIEGI:
        raise BladWagi("konfiguracja", f"Nieznany tryb: {tryb}")
    if not _blokada.acquire(blocking=False):
        raise BladWagi("zajety", "Poprzedni przebieg jeszcze trwa.")
    try:
        refresh = _refresh()
        stan = wczytaj_stan(refresh)
        start = time.time()
        wynik = PRZEBIEGI[tryb](refresh, stan)
        stan = wczytaj_stan(refresh)
        stan["ostatni_przebieg"] = {"tryb": tryb, "kiedy": _teraz(), "ok": True}
        zapisz_stan(refresh, stan)
        return {"ok": True, "tryb": tryb, "czas_s": round(time.time() - start, 1), **wynik}
    finally:
        _blokada.release()


# ----------------------------------------------------------------- MCP: podgląd dla właściciela

def t_waga(a: dict) -> dict:
    """Narzędzie MCP: raport z historii, stan łącznika albo pomiary z backupu (z masą - tylko dla właściciela)."""
    try:
        refresh = _refresh()
        stan = wczytaj_stan(refresh)
        widok = a.get("widok") or "raport"
        if widok == "pomiary":
            dzis = dt.date.today()
            od = a.get("od") or (dzis - dt.timedelta(days=30)).isoformat()
            do = a.get("do") or dzis.isoformat()
            pom = [p for p in wczytaj_backup(refresh) if od <= p["data"] <= do]
            if a.get("odrzucone") is not True:
                pom = [p for p in pom if p["status"] == "przyjety"]
            return {"od": od, "do": do, "pomiary": pom[-400:], "uwaga": "Wartości z Zepp Life bez przeliczeń."}
        return {"historia": stan.get("historia"), "historia_zakonczona": stan.get("historia_zakonczona"),
                "historia_kursor": stan.get("historia_kursor"),
                "ostatni_przeslany": dt.datetime.fromtimestamp(stan["ostatni_ts"], STREFA).isoformat()
                if stan.get("ostatni_ts") else None,
                "ostatni_przebieg": stan.get("ostatni_przebieg"),
                "logowania_haslem_do_zepp": stan.get("logowania_haslem") or [],
                "zepp_polaczony": bool((stan.get("zepp") or {}).get("app_token"))}
    except (BladWagi, dysk_google.BladDysku) as e:
        raise mcp_serwer.BladNarzedzia(str(e)) from e


mcp_serwer.NARZEDZIA["waga"] = {
    "fn": t_waga, "ro": True,
    "opis": "Łącznik wagi Xiaomi (Zepp Life -> Garmin + arkusz Pomiary/Waga). widok=raport (domyślnie): raport "
            "z trybu historia (liczba pomiarów, zakres dat, min/max masy, skoki > 2 kg), stan łącznika, użycia "
            "awaryjnego logowania hasłem. widok=pomiary: pomiary z zaszyfrowanego backupu (od/do, domyślnie 30 dni; "
            "odrzucone=true dołącza odrzucone filtrem).",
    "schema": {"type": "object", "properties": {
        "widok": {"type": "string", "enum": ["raport", "pomiary"]},
        "od": {"type": "string", "description": "RRRR-MM-DD"}, "do": {"type": "string", "description": "RRRR-MM-DD"},
        "odrzucone": {"type": "boolean"}}},
}


# ----------------------------------------------------------------- HTTP (wywołuje GitHub Actions)

bp = Blueprint("waga", __name__)

KODY = {"konfiguracja": 500, "kolejnosc": 409, "zajety": 409, "stan": 500, "arkusz": 502}


def _autoryzacja():
    oczekiwany = os.environ.get("WAGA_TOKEN", "").strip()
    if not oczekiwany:
        return jsonify(ok=False, kategoria="konfiguracja", blad="Łącznik wyłączony: brak WAGA_TOKEN."), 503
    naglowek = request.headers.get("Authorization", "")
    podany = naglowek[7:].strip() if naglowek[:7].lower() == "bearer " else ""
    if not podany or not hmac.compare_digest(podany.encode(), oczekiwany.encode()):
        return jsonify(ok=False, kategoria="autoryzacja", blad="Nieprawidłowy token."), 401
    return None


@bp.route("/waga/<tryb>", methods=["POST"])
def waga_post(tryb):
    if (odp := _autoryzacja()) is not None:
        return odp
    try:
        return jsonify(przebieg(tryb)), 200
    except zepp.BladZepp as e:
        log.warning("waga %s: zepp %s", tryb, e.kategoria)
        return jsonify(ok=False, tryb=tryb, kategoria=f"zepp_{e.kategoria}", blad=e.opis), 502
    except BladWagi as e:
        log.warning("waga %s: %s", tryb, e.kategoria)
        return jsonify(ok=False, tryb=tryb, kategoria=e.kategoria, blad=str(e)), KODY.get(e.kategoria, 500)
    except mcp_serwer.BladNarzedzia as e:
        log.warning("waga %s: garmin", tryb)
        return jsonify(ok=False, tryb=tryb, kategoria="garmin", blad=str(e)), 502
    except dysk_google.BladDysku as e:
        log.warning("waga %s: google", tryb)
        return jsonify(ok=False, tryb=tryb, kategoria="google", blad=str(e)[:300]), 502
    except Exception as e:  # noqa: BLE001
        log.error("waga %s: nieoczekiwany błąd %s", tryb, type(e).__name__)
        return jsonify(ok=False, tryb=tryb, kategoria="serwer", blad=type(e).__name__), 500


def zarejestruj(app) -> None:
    app.register_blueprint(bp)
