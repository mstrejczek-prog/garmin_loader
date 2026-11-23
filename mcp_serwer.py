# -*- coding: utf-8 -*-
"""
Serwer MCP (Model Context Protocol) - connector dla Claude (claude.ai, aplikacja na telefon i desktop).

Dzięki niemu Claude w zwykłym czacie może sprawdzać i wysyłać plany do Garmin Connect oraz czytać
Twoje dane (aktywności, sen, HRV, gotowość). Endpoint: POST /mcp (transport "Streamable HTTP",
tryb bezstanowy: każda odpowiedź to zwykły JSON).

Konfiguracja (zmienne środowiskowe serwera):
  MCP_TOKEN    - wymagany; tajny tekst, który claude.ai wysyła w nagłówku Authorization: Bearer <MCP_TOKEN>.
                 Bez niego endpoint jest wyłączony.
  GARMIN_TOKEN - zaszyfrowany token Garmina (strona: „Connector dla Claude” po zalogowaniu).
  USTAWIENIA   - opcjonalnie: strefy tętna/mocy i FTP w formacie strony (ten sam przycisk je pokazuje).

To serwer jednego użytkownika: działa na koncie Garmin, którego token jest w GARMIN_TOKEN.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import threading
from typing import Any, Callable

from html import escape

from flask import Blueprint, Response, jsonify, redirect, request, url_for

import dysk_google
import garmin_dane
import garmin_io
import plan
import ustawienia

log = logging.getLogger("garmin_plan_loader.mcp")

MAX_ODPOWIEDZ = 60000                               # znaków na odpowiedź narzędzia (trafia do kontekstu modelu)
WERSJE_PROTOKOLU = ("2025-06-18", "2025-03-26", "2024-11-05")

INSTRUKCJE = """Connector do Garmin Connect użytkownika (triathlon: bieg, rower, pływanie, siła).

Plany na zegarek: najpierw `format_planu` (format JSON + strefy użytkownika), potem `sprawdz_plan`
i pokaż użytkownikowi podgląd, a `wyslij_plan` wywołaj dopiero po jego akceptacji. Wysyłka jest
powtarzalna: trening o tej samej nazwie nie jest dublowany, a zmiana daty przestawia wpis w kalendarzu.
Przed planowaniem warto sprawdzić `kalendarz`, `aktywnosci`, `wellness` i `gotowosc`.
Ocena wykonania: `plan_vs_wykonanie` (plan z kalendarza vs aktywność), `serie` (siła), `plan_treningu`.

Dane: wartości pochodzą z Garmin Connect; własne wyliczenia i szacunki zaznaczaj jako takie.
Daty w formacie RRRR-MM-DD, domyślnie liczone od dzisiaj."""


class BladNarzedzia(Exception):
    """Błąd do pokazania modelowi jako wynik narzędzia (isError)."""


# ----------------------------------------------------------------- klient Garmina (token z serwera)

_klient = None
_klient_lock = threading.RLock()
_odszyfruj: Callable[..., Any] | None = None
_szyfruj: Callable[[str, Any], str] | None = None
CZAS_LINKU_FIT = 3600                               # link do pobrania FIT ważny godzinę
MAX_FIT_NARAZ = 20


def _ust() -> dict:
    surowe = os.environ.get("USTAWIENIA", "").strip()
    if not surowe:
        return ustawienia.puste()
    try:
        return ustawienia.czyste(json.loads(surowe))
    except (ValueError, plan.BladPlanu) as e:
        log.warning("USTAWIENIA są niepoprawne (%s) - pomijam", e)
        return ustawienia.puste()


def _token_garmina() -> str:
    surowy = os.environ.get("GARMIN_TOKEN", "").strip()
    if not surowy:
        raise BladNarzedzia("Serwer nie ma tokenu Garmina: zaloguj się na stronie Plan Loadera, kliknij "
                            "„Connector dla Claude” i wpisz GARMIN_TOKEN w zmiennych serwera.")
    tok = _odszyfruj("token", surowy) if _odszyfruj else None
    if tok is None:
        raise BladNarzedzia("GARMIN_TOKEN jest nieprawidłowy (inny SECRET_KEY albo uszkodzony). "
                            "Skopiuj go ponownie ze strony („Connector dla Claude”).")
    return tok


def _klient_garmina():
    global _klient
    with _klient_lock:
        if _klient is None:
            try:
                _klient = garmin_io.zaloguj_tokenem(_token_garmina())
            except garmin_io.TokenWygasl as e:
                raise BladNarzedzia("Logowanie Garmina wygasło. Zaloguj się na stronie Plan Loadera, kliknij "
                                    "„Connector dla Claude” i zaktualizuj GARMIN_TOKEN na serwerze.") from e
            except garmin_io.BladGarmin as e:
                raise BladNarzedzia(str(e)) from e
        return _klient


def _z_garminem(fn):
    """Wywołanie na kliencie Garmina; przy wygasłym tokenie jedna próba ponownego logowania."""
    global _klient
    with _klient_lock:
        for proba in range(2):
            k = _klient_garmina()
            try:
                return fn(k)
            except (BladNarzedzia, garmin_dane.BladDanych, plan.BladPlanu):
                raise
            except Exception as e:  # noqa: BLE001
                auth, _conn, _many = garmin_io._bledy_biblioteki()
                if isinstance(e, auth) and proba == 0:
                    _klient = None
                    continue
                raise BladNarzedzia(garmin_io._opis_bledu(e)) from e
    raise BladNarzedzia("Nie udało się połączyć z Garmin Connect")


# ----------------------------------------------------------------- narzędzia

def _plan_tekst(a: dict) -> str:
    p = a.get("plan")
    if isinstance(p, (dict, list)):
        return json.dumps(p, ensure_ascii=False)
    if isinstance(p, str) and p.strip():
        return p
    raise BladNarzedzia("Podaj plan (obiekt JSON z listą „treningi”)")


def t_format_planu(a: dict) -> str:
    tekst = plan.prompt_ai(_ust())
    tekst = tekst.split("ROZPISKA OD TRENERA")[0].rstrip()
    return ("Format planu dla narzędzi sprawdz_plan / wyslij_plan (argument „plan”). "
            "Pomiń pola, o które prompt każe pytać, jeśli znasz odpowiedź z rozmowy.\n\n" + tekst)


def t_moje_ustawienia(a: dict) -> dict:
    ust = _ust()
    wynik = {"zapisane_w_programie": ust,
             "uwaga": ("Strefy S1-S5 (tętno) i P1-P5 (moc) w planie są przeliczane z tych ustawień. "
                       "Gdy ich brak, dodaj do planu sekcję „strefy” (i „ftp”).")}
    if a.get("z_garmina", True):
        wynik["z_garmin_connect"] = _z_garminem(garmin_dane.strefy_garmin)
    return wynik


def t_sprawdz_plan(a: dict) -> str:
    try:
        wyniki = plan.wczytaj(_plan_tekst(a), _ust())
    except plan.BladPlanu as e:
        raise BladNarzedzia(f"Plan ma błędy: {e}") from e
    return plan.pokaz(wyniki)


def t_wyslij_plan(a: dict) -> dict:
    try:
        wyniki = plan.wczytaj(_plan_tekst(a), _ust())
    except plan.BladPlanu as e:
        raise BladNarzedzia(f"Plan ma błędy: {e}") from e
    if not any(not x["wolne"] for x in wyniki):
        raise BladNarzedzia("W planie nie ma treningów do wysłania (same dni wolne)")
    dziennik: list[str] = []
    st = _z_garminem(lambda k: garmin_io.wyslij(k, wyniki, bool(a.get("nadpisz")), dziennik.append))
    return {"wynik": st, "dziennik": dziennik}


def t_kalendarz(a: dict) -> list:
    return _z_garminem(lambda k: garmin_dane.kalendarz(k, a.get("od"), a.get("do")))


def t_usun_z_kalendarza(a: dict) -> str:
    wpis = str(a.get("wpis_id") or "").strip()
    if not wpis.isdigit():
        raise BladNarzedzia("Podaj wpis_id z narzędzia kalendarz")
    _z_garminem(lambda k: k.unschedule_workout(wpis))
    return f"Usunięto wpis {wpis} z kalendarza (sam trening zostaje na liście Treningi w Garmin Connect)."


def t_aktywnosci(a: dict) -> list:
    return _z_garminem(lambda k: garmin_dane.aktywnosci(k, a.get("od"), a.get("do"), a.get("sport")))


def t_trening(a: dict) -> Any:
    if a.get("surowe"):
        return _z_garminem(lambda k: garmin_dane.przytnij(k.get_activity(str(a.get("activity_id")))))
    return _z_garminem(lambda k: garmin_dane.trening(k, a.get("activity_id"), bool(a.get("dlugosci"))))


def t_wellness(a: dict) -> Any:
    return _z_garminem(lambda k: garmin_dane.wellness(k, a.get("od"), a.get("do"), a.get("pola")))


def t_strumienie(a: dict) -> dict:
    return _z_garminem(lambda k: garmin_dane.strumienie(k, a.get("activity_id"), a.get("metryki"), a.get("krok_s", 10)))


def t_sklad_ciala(a: dict) -> dict:
    return _z_garminem(lambda k: garmin_dane.sklad_ciala(k, a.get("od"), a.get("do"), bool(a.get("nawodnienie"))))


def t_historia_metryk(a: dict) -> dict:
    return _z_garminem(lambda k: garmin_dane.historia_metryk(k, a.get("od"), a.get("do"), a.get("agregacja")))


def t_gotowosc(a: dict) -> dict:
    return _z_garminem(lambda k: garmin_dane.gotowosc(k, a.get("data")))


def t_serie(a: dict) -> dict:
    return _z_garminem(lambda k: garmin_dane.serie(k, a.get("activity_id")))


def t_plan_treningu(a: dict) -> Any:
    if a.get("surowe"):
        return _z_garminem(lambda k: garmin_dane.surowe(k, "trening_planowany", a.get("workout_id")))
    return _z_garminem(lambda k: garmin_dane.plan_treningu(k, a.get("workout_id")))


def t_plan_vs_wykonanie(a: dict) -> dict:
    return _z_garminem(lambda k: garmin_dane.plan_vs_wykonanie(k, a.get("activity_id")))


def t_surowe_dane(a: dict) -> Any:
    return _z_garminem(lambda k: garmin_dane.surowe(k, str(a.get("zrodlo") or ""), a.get("id"),
                                                    a.get("od"), a.get("do")))


def _refresh_google() -> str | None:
    surowy = os.environ.get("GOOGLE_TOKEN", "").strip()
    if not surowy or not dysk_google.ma_klienta():
        return None
    return _odszyfruj("google", surowy) if _odszyfruj else None


def _pobierz_fit_bajty(aid: str) -> bytes:
    from garminconnect import Garmin
    dane = _z_garminem(lambda k: k.download_activity(aid, dl_fmt=Garmin.ActivityDownloadFormat.ORIGINAL))
    if not isinstance(dane, (bytes, bytearray)) or not dane:
        raise BladNarzedzia(f"Garmin nie zwrócił pliku aktywności {aid}")
    return bytes(dane)


def _nazwa_fit(aid: str, dane: bytes) -> str:
    # jak eksport z Garmin Connect w folderze użytkownika: <activity_id>.zip (w środku <id>_ACTIVITY.fit)
    return f"{aid}.zip" if dane[:2] == b"PK" else f"{aid}_ACTIVITY.fit"


def _wgraj_na_dysk(refresh: str, ids: list[str], opisy: dict, istniejace: list[dict]) -> list[dict]:
    wyniki = []
    for i in ids:
        try:
            juz = next((f for f in istniejace if f.get("activity_id") == i), None)
            if juz:
                w = {"nazwa": juz["nazwa"], "status": "juz_byl", "id": juz["id"], "link": juz.get("link")}
            else:
                dane = _pobierz_fit_bajty(i)
                w = dysk_google.wyslij(refresh, _nazwa_fit(i, dane), dane, istniejace=istniejace)
                if w["status"] == "wgrany":
                    istniejace.append({"nazwa": w["nazwa"], "id": w["id"], "activity_id": i, "link": w.get("link")})
        except (BladNarzedzia, dysk_google.BladDysku) as e:
            w = {"nazwa": f"{i}.zip", "status": "blad", "blad": str(e)}
        wyniki.append({"activity_id": int(i), "opis": opisy.get(i), **w})
    return wyniki


def t_pobierz_fit(a: dict) -> dict:
    """Oryginalne pliki FIT: na Dysk Google (gdy połączony) albo jako linki do pobrania."""
    if _szyfruj is None:
        raise BladNarzedzia("Serwer nie obsługuje pobierania plików")
    if a.get("activity_id"):
        ids = [str(a["activity_id"]).strip()]
        if not ids[0].isdigit():
            raise BladNarzedzia("activity_id musi być liczbą")
        opisy = {ids[0]: None}
    else:
        lista = _z_garminem(lambda k: garmin_dane.aktywnosci(k, a.get("od"), a.get("do"), a.get("sport")))
        if len(lista) > MAX_FIT_NARAZ:
            raise BladNarzedzia(f"W tym okresie jest {len(lista)} aktywności - najwyżej {MAX_FIT_NARAZ} naraz, "
                                "zawęź zakres dat albo sport")
        ids = [str(x["id"]) for x in lista]
        opisy = {str(x["id"]): f"{x.get('start', '')[:10]} {x.get('nazwa') or ''}".strip() for x in lista}

    refresh = _refresh_google()
    cel = a.get("cel") or ("dysk" if refresh else "link")
    if cel == "dysk" and not refresh:
        raise BladNarzedzia("Dysk Google nie jest połączony - użyj narzędzia polacz_dysk_google "
                            "albo wywołaj pobierz_fit z cel=\"link\".")

    if cel == "dysk":
        try:
            istniejace = dysk_google.lista_plikow(refresh)
            folder = dysk_google.opis_folderu(refresh)
        except dysk_google.BladDysku as e:
            raise BladNarzedzia(str(e)) from e
        pliki = _wgraj_na_dysk(refresh, ids, opisy, istniejace)
        wynik = {"cel": "dysk", "folder": folder, "pliki": pliki,
                 "jak_uzyc": ("Pliki są na Dysku Google w podanym folderze (pliki o tej samej nazwie nie są "
                              "dublowane). Jeśli Dysk jest zsynchronizowany z komputerem (Dysk Google na komputer), "
                              "uruchom lokalnie: python ingest_fit.py treningi.duckdb \"<folder na dysku>\"")}
    else:
        baza = request.url_root.rstrip("/")
        pliki = [{"activity_id": int(i), "opis": opisy.get(i), "plik": f"{i}_ACTIVITY.zip",
                  "link": baza + url_for("mcp.pobierz_plik_fit", token=_szyfruj("fit", i))} for i in ids]
        wynik = {
            "cel": "link", "pliki": pliki, "wazne_minut": CZAS_LINKU_FIT // 60,
            "jak_uzyc": ("Pokaż użytkownikowi linki - otwarte w przeglądarce pobierają zip z oryginalnym plikiem FIT "
                         "(nazwa <activity_id>_ACTIVITY.fit, zgodna z ingest_fit.py). Zapisz je w folderze "
                         "„Dane treningowe/FIT (surowe pliki z Garmina)” i uruchom lokalnie: "
                         "python ingest_fit.py treningi.duckdb <pobrane pliki .zip>"),
        }
    if a.get("zasil_baze"):
        wynik["zasil_baze"] = ("Niewykonane: serwer konektora działa zdalnie (Render) i nie ma dostępu do "
                               "treningi.duckdb. Uruchom ingest_fit.py lokalnie na plikach z Dysku/linków.")
    return wynik


def t_stan_dysku(a: dict) -> dict:
    """Pliki FIT w folderze na Dysku: duplikaty, braki względem aktywności z Garmina, opcjonalne uzupełnienie."""
    refresh = _refresh_google()
    if not refresh:
        raise BladNarzedzia("Dysk Google nie jest połączony - użyj narzędzia polacz_dysk_google.")
    try:
        pliki = dysk_google.lista_plikow(refresh)
        folder = dysk_google.opis_folderu(refresh)
    except dysk_google.BladDysku as e:
        raise BladNarzedzia(str(e)) from e

    po_id: dict[str, list] = {}
    for f in pliki:
        if f["activity_id"]:
            po_id.setdefault(f["activity_id"], []).append(f)
    duplikaty = [{"activity_id": int(k), "pliki": [{x: f[x] for x in ("nazwa", "rozmiar_b", "utworzony", "id")}
                                                   for f in v],
                  "identyczne": len({f["md5"] for f in v}) == 1 and v[0]["md5"] is not None,
                  # zip pobrany z Garmina dwa razy ma inną sumę kontrolną (inne znaczniki czasu w archiwum),
                  # ale ten sam rozmiar - to zwykle ta sama treść
                  "ten_sam_rozmiar": len({f["rozmiar_b"] for f in v}) == 1}
                 for k, v in sorted(po_id.items()) if len(v) > 1]
    po_md5: dict[str, list] = {}
    for f in pliki:
        if f["md5"]:
            po_md5.setdefault(f["md5"], []).append(f["nazwa"])
    # ta sama treść pod różnymi numerami (albo bez numeru) - duplikaty z tym samym numerem są już wyżej
    ta_sama_tresc = [n for n in po_md5.values() if len({dysk_google.id_aktywnosci(x) for x in n}) > 1]
    inne = [f["nazwa"] for f in pliki if not f["activity_id"]]

    wynik: dict[str, Any] = {
        "folder": folder,
        "podsumowanie": {"pliki": len(pliki), "aktywnosci_z_plikiem": len(po_id),
                         "duplikaty": len(duplikaty), "pliki_bez_numeru_aktywnosci": len(inne)},
        "duplikaty": duplikaty,
        "ta_sama_tresc_rozne_nazwy": ta_sama_tresc or None,
        "pliki_bez_numeru_aktywnosci": inne[:50] or None,
        "najstarszy_i_najnowszy_numer": [min(po_id), max(po_id)] if po_id else None,
    }

    if a.get("od") or a.get("do") or a.get("uzupelnij"):
        akt = _z_garminem(lambda k: garmin_dane.aktywnosci(k, a.get("od"), a.get("do"), a.get("sport")))
        brak = [x for x in akt if str(x["id"]) not in po_id]
        wynik["zakres_garmin"] = {"od": a.get("od"), "do": a.get("do"), "aktywnosci": len(akt),
                                  "z_plikiem": len(akt) - len(brak), "bez_pliku": len(brak)}
        wynik["brakujace"] = [{"activity_id": x["id"], "start": x.get("start"), "nazwa": x.get("nazwa"),
                               "sport": x.get("sport")} for x in brak]
        if a.get("uzupelnij") and brak:
            do_wgrania = brak[:MAX_FIT_NARAZ]
            opisy = {str(x["id"]): f"{(x.get('start') or '')[:10]} {x.get('nazwa') or ''}".strip() for x in do_wgrania}
            wynik["uzupelniono"] = _wgraj_na_dysk(refresh, [str(x["id"]) for x in do_wgrania], opisy, pliki)
            if len(brak) > MAX_FIT_NARAZ:
                wynik["uwaga"] = f"Wgrano {MAX_FIT_NARAZ} z {len(brak)} brakujących - wywołaj ponownie, żeby dograć resztę."
    return wynik


def t_polacz_dysk_google(a: dict) -> dict:
    if not dysk_google.ma_klienta():
        raise BladNarzedzia(
            "Brak klienta OAuth Google na serwerze. Właściciel musi raz: (1) w Google Cloud Console utworzyć "
            "projekt i włączyć Google Drive API, (2) skonfigurować ekran zgody OAuth (typ zewnętrzny, zakres "
            "drive, opublikować aplikację), (3) utworzyć identyfikator klienta OAuth typu „Aplikacja "
            f"internetowa” z URI przekierowania {request.url_root.rstrip('/')}/google/powrot, (4) wpisać "
            "GOOGLE_CLIENT_ID i GOOGLE_CLIENT_SECRET w zmiennych serwera na Render. Szczegóły: README, „Dysk Google”.")
    link = request.url_root.rstrip("/") + url_for("mcp.google_polacz", token=_szyfruj("google_start", "1"))
    return {
        "polaczony": bool(_refresh_google()),
        "link": link, "wazny_minut": 30,
        "jak_uzyc": ("Pokaż użytkownikowi link. Po zalogowaniu do Google i zgodzie strona pokaże wartość "
                     "GOOGLE_TOKEN - użytkownik wpisuje ją w zmiennych serwera na Render (Environment). "
                     f"Pliki FIT trafią do folderu „{dysk_google.folder()}” na jego Dysku."),
    }


_DATA = {"type": "string", "description": "RRRR-MM-DD"}
_PLAN = {"description": "Plan w formacie z narzędzia format_planu: obiekt {\"treningi\": [...]} "
                        "(może też zawierać \"strefy\", \"moc\", \"ftp\").",
         "anyOf": [{"type": "object"}, {"type": "string"}]}

NARZEDZIA: dict[str, dict] = {
    "format_planu": {
        "fn": t_format_planu, "ro": True,
        "opis": "Zwraca format JSON planu treningowego i zasady (wraz ze strefami użytkownika). "
                "Użyj przed ułożeniem planu do wysłania.",
        "schema": {"type": "object", "properties": {}},
    },
    "moje_ustawienia": {
        "fn": t_moje_ustawienia, "ro": True,
        "opis": "Strefy tętna, strefy mocy i FTP: zapisane w Plan Loaderze oraz z profilu Garmin Connect.",
        "schema": {"type": "object", "properties": {
            "z_garmina": {"type": "boolean", "description": "Czy dociągnąć strefy z Garmin Connect (domyślnie tak)"}}},
    },
    "sprawdz_plan": {
        "fn": t_sprawdz_plan, "ro": True,
        "opis": "Sprawdza plan bez wysyłania i zwraca czytelny podgląd (kroki, czasy, cele w bpm/W/tempie). "
                "Pokaż go użytkownikowi przed wysyłką.",
        "schema": {"type": "object", "properties": {"plan": _PLAN}, "required": ["plan"]},
    },
    "wyslij_plan": {
        "fn": t_wyslij_plan, "ro": False, "destrukcyjne": False,
        "opis": "Wysyła treningi do Garmin Connect i wstawia je do kalendarza (zegarek pobierze je przy "
                "synchronizacji). Treningi o tej samej nazwie nie są dublowane; zmieniona data przestawia wpis. "
                "nadpisz=true kasuje i wgrywa ponownie treningi o tych samych nazwach (gdy zmieniły się kroki). "
                "Wywołuj dopiero po akceptacji podglądu przez użytkownika.",
        "schema": {"type": "object", "properties": {
            "plan": _PLAN,
            "nadpisz": {"type": "boolean", "description": "Nadpisz istniejące treningi o tych samych nazwach"}},
            "required": ["plan"]},
    },
    "kalendarz": {
        "fn": t_kalendarz, "ro": True,
        "opis": "Treningi zaplanowane w kalendarzu Garmin Connect (domyślnie 14 dni od dzisiaj, najwyżej 3 miesiące).",
        "schema": {"type": "object", "properties": {"od": _DATA, "do": _DATA}},
    },
    "usun_z_kalendarza": {
        "fn": t_usun_z_kalendarza, "ro": False, "destrukcyjne": True,
        "opis": "Zdejmuje trening z kalendarza Garmin Connect (wpis_id z narzędzia kalendarz). "
                "Sam trening zostaje na liście Treningi.",
        "schema": {"type": "object", "properties": {"wpis_id": {"type": ["string", "integer"]}},
                   "required": ["wpis_id"]},
    },
    "aktywnosci": {
        "fn": t_aktywnosci, "ro": True,
        "opis": "Wykonane aktywności z okresu (domyślnie 14 dni, najwyżej 92): sport, czas, dystans, HR, moc, "
                "NP, TSS, Training Effect, obciążenie.",
        "schema": {"type": "object", "properties": {
            "od": _DATA, "do": _DATA,
            "sport": {"type": "string", "description": "running, cycling, swimming, multi_sport, "
                                                       "fitness_equipment, hiking, walking, other"}}},
    },
    "trening": {
        "fn": t_trening, "ro": True,
        "opis": "Szczegóły jednej aktywności: podsumowanie, RPE i samopoczucie wpisane po treningu, zgodność z planem, "
                "okrążenia (czas, dystans, HR, moc śr/max/NP, tempo, kadencja; w pływaniu styl, długości, SWOLF, "
                "tempo /100 m), czas w strefach HR i mocy, pływanie (tempo_100m_mediana jest odporne na odpoczynek przy "
                "ścianie bez naciśnięcia przycisku), dynamika biegu, pogoda (°C, km/h), sprzęt. "
                "Sekcje, które nie dotyczą sportu, są null. dlugosci=true dodaje każdą długość basenu. "
                "surowe=true zwraca pełną odpowiedź Garmina (przyciętą).",
        "schema": {"type": "object", "properties": {
            "activity_id": {"type": ["string", "integer"], "description": "id z narzędzia aktywnosci"},
            "dlugosci": {"type": "boolean"}, "surowe": {"type": "boolean"}}, "required": ["activity_id"]},
    },
    "wellness": {
        "fn": t_wellness, "ro": True,
        "opis": "Regeneracja dzień po dniu (domyślnie 7 dni, najwyżej 92): sen i jego fazy, sleep score, HRV "
                "(noc, średnia 7 dni, status, norma), tętno spoczynkowe, Body Battery, stres, kroki. pola ogranicza "
                "źródła (podsumowanie, sen, hrv) - przy długich zakresach szybciej. Gdy Garmin odpowiada wolno, "
                "zwraca część dni i kontynuuj_od. Trendy HRV/tętna na rok: historia_metryk.",
        "schema": {"type": "object", "properties": {
            "od": _DATA, "do": _DATA,
            "pola": {"type": "array", "items": {"type": "string", "enum": list(garmin_dane.POLA_WELLNESS)}}}},
    },
    "strumienie": {
        "fn": t_strumienie, "ro": True,
        "opis": "Przebieg aktywności w czasie, próbkowany co krok_s sekund (domyślnie 10, najwyżej 1500 punktów): "
                "tabela kolumny + wiersze. Metryki: " + ", ".join(garmin_dane.METRYKI_STRUMIENI)
                + " (domyślnie hr, moc_w, kadencja, predkosc_kmh, wysokosc_m). Do analiz 1 Hz - plik FIT.",
        "schema": {"type": "object", "properties": {
            "activity_id": {"type": ["string", "integer"]},
            "metryki": {"type": "array", "items": {"type": "string", "enum": list(garmin_dane.METRYKI_STRUMIENI)}},
            "krok_s": {"type": "integer", "minimum": 1}}, "required": ["activity_id"]},
    },
    "sklad_ciala": {
        "fn": t_sklad_ciala, "ro": True,
        "opis": "Masa i skład ciała dzień po dniu (do 1 roku): masa, tkanka tłuszczowa, masa mięśniowa i kostna, "
                "woda, BMI, źródło pomiaru. nawodnienie=true dodaje wypitą wodę (do 31 dni).",
        "schema": {"type": "object", "properties": {"od": _DATA, "do": _DATA, "nawodnienie": {"type": "boolean"}}},
    },
    "historia_metryk": {
        "fn": t_historia_metryk, "ro": True,
        "opis": "Trend formy do 1 roku jako tabela (kolumny + wiersze), dziennie albo tygodniowo (domyślnie tygodniowo "
                "powyżej 92 dni): VO2max bieg/rower, Training Status, obciążenie ostre/przewlekłe, ACWR, Training "
                "Readiness (poranny), HRV noc/7 dni/status/norma, tętno spoczynkowe, Endurance i Hill Score, FTP, "
                "próg mleczanowy. Kolumny bez danych są pomijane (lista w puste_zrodla).",
        "schema": {"type": "object", "properties": {
            "od": _DATA, "do": _DATA, "agregacja": {"type": "string", "enum": ["dzien", "tydzien"]}}},
    },
    "gotowosc": {
        "fn": t_gotowosc, "ro": True,
        "opis": "Training Readiness, Training Status (obciążenie ostre/przewlekłe, ACWR, bilans 4 tygodni) "
                "i VO2max na dany dzień (domyślnie dziś).",
        "schema": {"type": "object", "properties": {"data": _DATA}},
    },
    "serie": {
        "fn": t_serie, "ro": True,
        "opis": "Serie treningu siłowego w kolejności wykonania (ćwiczenie, powtórzenia, ciężar w kg, czas, "
                "krok planu) + podsumowanie per ćwiczenie (serie, powtórzenia, ciężar max/średni, tonaż).",
        "schema": {"type": "object", "properties": {
            "activity_id": {"type": ["string", "integer"], "description": "id z narzędzia aktywnosci"}},
            "required": ["activity_id"]},
    },
    "plan_treningu": {
        "fn": t_plan_treningu, "ro": True,
        "opis": "Kroki zaplanowanego treningu z biblioteki Garmin Connect (workout_id z narzędzia kalendarz): "
                "typ, ćwiczenie, powtórzenia, ciężar, czas/dystans, cel (strefa HR, W, tempo), bloki powtórzeń. "
                "surowe=true zwraca pełny JSON Garmina.",
        "schema": {"type": "object", "properties": {
            "workout_id": {"type": ["string", "integer"]}, "surowe": {"type": "boolean"}},
            "required": ["workout_id"]},
    },
    "plan_vs_wykonanie": {
        "fn": t_plan_vs_wykonanie, "ro": True,
        "opis": "Zestawia wykonaną aktywność z treningiem, z którego była zrobiona (associatedWorkoutId): "
                "siła - serie/powtórzenia/ciężar per krok planu; bieg/rower/pływanie - kroki planu vs okrążenia "
                "(czas, dystans, HR, moc, tempo względem celu). Każdy wiersz ma flagę odchylenia.",
        "schema": {"type": "object", "properties": {
            "activity_id": {"type": ["string", "integer"]}}, "required": ["activity_id"]},
    },
    "pobierz_fit": {
        "fn": t_pobierz_fit, "ro": False, "destrukcyjne": False,
        "opis": "Oryginalne pliki FIT aktywności - jednej (activity_id) albo z okresu (od, do, opcjonalnie sport; "
                "najwyżej 20). cel=\"dysk\" (domyślnie, gdy Dysk Google połączony): wgrywa do folderu na Dysku "
                "Google (bez dublowania); cel=\"link\": linki do pobrania ważne 1 h. Serwer działa zdalnie, "
                "więc nie zasila treningi.duckdb.",
        "schema": {"type": "object", "properties": {
            "activity_id": {"type": ["string", "integer"]}, "od": _DATA, "do": _DATA,
            "sport": {"type": "string"}, "cel": {"type": "string", "enum": ["dysk", "link"]},
            "zasil_baze": {"type": "boolean"}}},
    },
    "stan_dysku": {
        "fn": t_stan_dysku, "ro": False, "destrukcyjne": False,
        "opis": "Stan folderu z plikami FIT na Dysku Google: liczba plików, duplikaty (ten sam numer aktywności "
                "w kilku plikach, np. 123.zip i 123_ACTIVITY.zip; flaga identyczne = ta sama treść), pliki bez numeru. "
                "Z od/do (do 92 dni) porównuje z aktywnościami w Garmin Connect i wypisuje brakujące; "
                "uzupelnij=true dogrywa brakujące (do 20 na wywołanie). Niczego nie kasuje.",
        "schema": {"type": "object", "properties": {
            "od": _DATA, "do": _DATA, "sport": {"type": "string"}, "uzupelnij": {"type": "boolean"}}},
    },
    "polacz_dysk_google": {
        "fn": t_polacz_dysk_google, "ro": True,
        "opis": "Link do jednorazowego połączenia Dysku Google (zapis plików FIT przez pobierz_fit) i stan połączenia.",
        "schema": {"type": "object", "properties": {}},
    },
    "surowe_dane": {
        "fn": t_surowe_dane, "ro": True,
        "opis": "Diagnostyka: surowa odpowiedź Garmina (przycięta do ~30 kB). Źródła z id (activity_id/workout_id): "
                + ", ".join(list(garmin_dane.ZRODLA_SUROWE) + ["szczegoly_strumienie"]) + "; źródła z zakresem dat "
                "(od, do, do 366 dni): " + ", ".join(k for k in garmin_dane.ZRODLA_SUROWE_ZAKRES
                                                     if k != "szczegoly_strumienie")
                + ". Używaj, gdy zwięzłe narzędzia czegoś nie pokazują.",
        "schema": {"type": "object", "properties": {
            "zrodlo": {"type": "string", "enum": list(garmin_dane.ZRODLA_SUROWE) + list(garmin_dane.ZRODLA_SUROWE_ZAKRES)},
            "id": {"type": ["string", "integer"], "description": "activity_id albo workout_id"},
            "od": _DATA, "do": _DATA},
            "required": ["zrodlo"]},
    },
}


def _lista_narzedzi() -> list[dict]:
    out = []
    for nazwa, n in NARZEDZIA.items():
        adn = {"readOnlyHint": n["ro"], "openWorldHint": True}
        if not n["ro"]:
            adn["destructiveHint"] = n.get("destrukcyjne", False)
            adn["idempotentHint"] = nazwa == "wyslij_plan"
        out.append({"name": nazwa, "description": n["opis"], "inputSchema": n["schema"], "annotations": adn})
    return out


def _wywolaj(nazwa: str, argumenty: dict) -> dict:
    n = NARZEDZIA.get(nazwa)
    if not n:
        return {"content": [{"type": "text", "text": f"Nieznane narzędzie: {nazwa}"}], "isError": True}
    try:
        wynik = n["fn"](argumenty or {})
    except (BladNarzedzia, garmin_dane.BladDanych, plan.BladPlanu) as e:
        return {"content": [{"type": "text", "text": str(e)}], "isError": True}
    except Exception as e:  # noqa: BLE001
        log.exception("narzędzie %s", nazwa)
        return {"content": [{"type": "text", "text": f"Błąd serwera: {e}"}], "isError": True}
    tekst = wynik if isinstance(wynik, str) else json.dumps(wynik, ensure_ascii=False, default=str)
    if len(tekst) > MAX_ODPOWIEDZ:
        tekst = tekst[:MAX_ODPOWIEDZ] + f"\n…[przycięto: odpowiedź miała {len(tekst)} znaków - zawęź zapytanie]"
    return {"content": [{"type": "text", "text": tekst}]}


# ----------------------------------------------------------------- JSON-RPC / HTTP

def _odpowiedz(id_, wynik=None, blad=None):
    d = {"jsonrpc": "2.0", "id": id_}
    if blad is not None:
        d["error"] = blad
    else:
        d["result"] = wynik
    return d


def obsluz(wiadomosc: Any) -> dict | None:
    """Jedna wiadomość JSON-RPC -> odpowiedź (albo None dla powiadomień)."""
    if not isinstance(wiadomosc, dict) or wiadomosc.get("jsonrpc") != "2.0":
        return _odpowiedz(None, blad={"code": -32600, "message": "Invalid Request"})
    metoda, id_, par = wiadomosc.get("method"), wiadomosc.get("id"), wiadomosc.get("params") or {}
    if "id" not in wiadomosc:                      # powiadomienie (np. notifications/initialized)
        return None
    if metoda == "initialize":
        wersja = par.get("protocolVersion")
        return _odpowiedz(id_, {
            "protocolVersion": wersja if wersja in WERSJE_PROTOKOLU else WERSJE_PROTOKOLU[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "garmin-plan-loader", "title": "Garmin Plan Loader", "version": "0.3.0"},
            "instructions": INSTRUKCJE,
        })
    if metoda == "ping":
        return _odpowiedz(id_, {})
    if metoda == "tools/list":
        return _odpowiedz(id_, {"tools": _lista_narzedzi()})
    if metoda == "tools/call":
        if not isinstance(par.get("name"), str):
            return _odpowiedz(id_, blad={"code": -32602, "message": "Brak nazwy narzędzia"})
        argumenty = par.get("arguments")
        return _odpowiedz(id_, _wywolaj(par["name"], argumenty if isinstance(argumenty, dict) else {}))
    if metoda in ("resources/list", "prompts/list"):
        return _odpowiedz(id_, {metoda.split("/")[0]: []})
    return _odpowiedz(id_, blad={"code": -32601, "message": f"Nieznana metoda: {metoda}"})


bp = Blueprint("mcp", __name__)


def _autoryzacja() -> Response | None:
    oczekiwany = os.environ.get("MCP_TOKEN", "").strip()
    if not oczekiwany:
        return jsonify(blad="Connector wyłączony: ustaw zmienną MCP_TOKEN na serwerze."), 503
    naglowek = request.headers.get("Authorization", "")
    podany = naglowek[7:].strip() if naglowek[:7].lower() == "bearer " else request.headers.get("X-MCP-Token", "")
    if not podany or not hmac.compare_digest(podany.encode(), oczekiwany.encode()):
        return jsonify(blad="Nieprawidłowy token MCP."), 401
    return None


@bp.route("/mcp", methods=["POST"])
def mcp_post():
    if (odp := _autoryzacja()) is not None:
        return odp
    dane = request.get_json(silent=True)
    if dane is None:
        return jsonify(_odpowiedz(None, blad={"code": -32700, "message": "Parse error"})), 400
    if isinstance(dane, list):                     # paczka wiadomości (starsze wersje protokołu)
        wyniki = [w for w in (obsluz(x) for x in dane) if w is not None]
        return (jsonify(wyniki), 200) if wyniki else ("", 202)
    wynik = obsluz(dane)
    return (jsonify(wynik), 200) if wynik is not None else ("", 202)


@bp.route("/mcp", methods=["GET", "DELETE"])
def mcp_inne():
    if (odp := _autoryzacja()) is not None:
        return odp
    return Response(status=405, headers={"Allow": "POST"})


@bp.route("/fit/<token>", methods=["GET"])
def pobierz_plik_fit(token):
    """Pobranie oryginalnego FIT z linku z narzędzia pobierz_fit (link podpisany, ważny godzinę)."""
    aid = _odszyfruj("fit", token, ttl=CZAS_LINKU_FIT) if _odszyfruj else None
    if not aid or not str(aid).isdigit():
        return jsonify(blad="Link wygasł albo jest nieprawidłowy - poproś Claude o nowy."), 403
    try:
        from garminconnect import Garmin
        dane = _z_garminem(lambda k: k.download_activity(
            str(aid), dl_fmt=Garmin.ActivityDownloadFormat.ORIGINAL))
    except BladNarzedzia as e:
        return jsonify(blad=str(e)), 502
    zip_ = isinstance(dane, (bytes, bytearray)) and dane[:2] == b"PK"
    nazwa = f"{aid}_ACTIVITY.zip" if zip_ else f"{aid}_ACTIVITY.fit"
    return Response(bytes(dane), mimetype="application/zip" if zip_ else "application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{nazwa}"', "Cache-Control": "no-store"})


def _strona(tytul: str, tresc_html: str, status: int = 200) -> Response:
    html = (f"<!doctype html><html lang=\"pl\"><head><meta charset=\"utf-8\">"
            f"<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><title>{escape(tytul)}</title>"
            f"</head><body><h1>{escape(tytul)}</h1>{tresc_html}</body></html>")
    return Response(html, status=status, mimetype="text/html", headers={"Cache-Control": "no-store"})


def _powrot_google() -> str:
    return request.url_root.rstrip("/") + url_for("mcp.google_powrot")


@bp.route("/google/polacz/<token>", methods=["GET"])
def google_polacz(token):
    if not (_odszyfruj and _odszyfruj("google_start", token, ttl=30 * 60)):
        return _strona("Link wygasł", "<p>Poproś Claude o nowy link (narzędzie polacz_dysk_google).</p>", 403)
    if not dysk_google.ma_klienta():
        return _strona("Brak konfiguracji", "<p>Na serwerze brakuje GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET.</p>", 503)
    return redirect(dysk_google.adres_logowania(_powrot_google(), _szyfruj("google_state", "1")))


@bp.route("/google/powrot", methods=["GET"])
def google_powrot():
    if not (_odszyfruj and _odszyfruj("google_state", request.args.get("state", ""), ttl=15 * 60)):
        return _strona("Logowanie nieważne", "<p>Zacznij od nowa linkiem od Claude.</p>", 403)
    if request.args.get("error"):
        return _strona("Nie połączono", f"<p>Google: {escape(request.args['error'])}</p>", 400)
    try:
        refresh = dysk_google.wymien_kod(request.args.get("code", ""), _powrot_google())
    except dysk_google.BladDysku as e:
        return _strona("Nie połączono", f"<p>{escape(str(e))}</p>", 502)
    wartosc = _szyfruj("google", refresh)
    return _strona("Dysk Google połączony", (
        "<p>Ostatni krok: w Render → garmin-plan-loader → <b>Environment</b> dodaj zmienną "
        "<code>GOOGLE_TOKEN</code> o wartości poniżej (zaszyfrowana kluczem serwera; nie pokazuj jej nikomu) "
        "i zapisz z ponownym wdrożeniem.</p>"
        f"<p><textarea readonly rows=\"6\" cols=\"80\">{escape(wartosc)}</textarea></p>"
        f"<p>Pliki FIT trafią do folderu „{escape(dysk_google.folder())}” na Twoim Dysku.</p>"))


def zarejestruj(app, odszyfruj, szyfruj=None) -> None:
    global _odszyfruj, _szyfruj
    _odszyfruj, _szyfruj = odszyfruj, szyfruj
    app.register_blueprint(bp)
