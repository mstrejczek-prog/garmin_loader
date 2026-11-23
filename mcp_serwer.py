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

from flask import Blueprint, Response, jsonify, request, url_for

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
    return _z_garminem(lambda k: garmin_dane.trening(k, a.get("activity_id")))


def t_wellness(a: dict) -> list:
    return _z_garminem(lambda k: garmin_dane.wellness(k, a.get("od"), a.get("do")))


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
    return _z_garminem(lambda k: garmin_dane.surowe(k, str(a.get("zrodlo") or ""), a.get("id")))


def t_pobierz_fit(a: dict) -> dict:
    """Linki do oryginalnych plików FIT (serwer działa zdalnie - nie ma dostępu do dysku użytkownika)."""
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
    baza = request.url_root.rstrip("/")
    pliki = [{"activity_id": int(i), "opis": opisy.get(i), "plik": f"{i}_ACTIVITY.zip",
              "link": baza + url_for("mcp.pobierz_plik_fit", token=_szyfruj("fit", i))} for i in ids]
    wynik = {
        "pliki": pliki,
        "wazne_minut": CZAS_LINKU_FIT // 60,
        "jak_uzyc": ("Pokaż użytkownikowi linki - otwarte w przeglądarce pobierają zip z oryginalnym plikiem FIT "
                     "(nazwa <activity_id>_ACTIVITY.fit, zgodna z ingest_fit.py). Zapisz je w folderze "
                     "„Dane treningowe/FIT (surowe pliki z Garmina)” i uruchom lokalnie: "
                     "python ingest_fit.py treningi.duckdb <pobrane pliki .zip>"),
    }
    if a.get("zasil_baze"):
        wynik["zasil_baze"] = ("Niewykonane: serwer konektora działa zdalnie (Render) i nie ma dostępu do dysku "
                               "ani do treningi.duckdb. Uruchom ingest_fit.py lokalnie na pobranych plikach.")
    return wynik


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
        "opis": "Szczegóły jednej aktywności: podsumowanie, okrążenia (interwały) i czas w strefach tętna. "
                "surowe=true zwraca pełną odpowiedź Garmina (przyciętą).",
        "schema": {"type": "object", "properties": {
            "activity_id": {"type": ["string", "integer"], "description": "id z narzędzia aktywnosci"},
            "surowe": {"type": "boolean"}}, "required": ["activity_id"]},
    },
    "wellness": {
        "fn": t_wellness, "ro": True,
        "opis": "Regeneracja dzień po dniu (domyślnie 7 dni, najwyżej 14): sen i jego fazy, sleep score, HRV "
                "(noc, średnia 7 dni, status, norma), tętno spoczynkowe, Body Battery, stres, kroki.",
        "schema": {"type": "object", "properties": {"od": _DATA, "do": _DATA}},
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
        "fn": t_pobierz_fit, "ro": True,
        "opis": "Linki (ważne 1 h) do oryginalnych plików FIT aktywności - jednej (activity_id) albo z okresu "
                "(od, do, opcjonalnie sport; najwyżej 20). Serwer działa zdalnie, więc nie zapisuje na dysk "
                "użytkownika ani nie zasila treningi.duckdb - pokaż linki użytkownikowi.",
        "schema": {"type": "object", "properties": {
            "activity_id": {"type": ["string", "integer"]}, "od": _DATA, "do": _DATA,
            "sport": {"type": "string"}, "zasil_baze": {"type": "boolean"}}},
    },
    "surowe_dane": {
        "fn": t_surowe_dane, "ro": True,
        "opis": "Diagnostyka: surowa odpowiedź Garmina (przycięta do ~30 kB) dla jednego źródła: "
                + ", ".join(garmin_dane.ZRODLA_SUROWE) + ". Używaj, gdy zwięzłe narzędzia czegoś nie pokazują.",
        "schema": {"type": "object", "properties": {
            "zrodlo": {"type": "string", "enum": list(garmin_dane.ZRODLA_SUROWE)},
            "id": {"type": ["string", "integer"], "description": "activity_id albo workout_id"}},
            "required": ["zrodlo", "id"]},
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


def zarejestruj(app, odszyfruj, szyfruj=None) -> None:
    global _odszyfruj, _szyfruj
    _odszyfruj, _szyfruj = odszyfruj, szyfruj
    app.register_blueprint(bp)
