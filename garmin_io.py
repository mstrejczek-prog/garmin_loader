# -*- coding: utf-8 -*-
"""
Rozmowa z Garmin Connect: logowanie (token / hasło + MFA) i wysyłka treningów do kalendarza.

Hasło nie jest nigdzie zapisywane ani wypisywane. Na dysku zostaje tylko token logowania
(plik w katalogu TOKEN_DIR), dzięki któremu kolejne uruchomienia nie wymagają hasła.
Dostęp odbywa się przez nieoficjalną bibliotekę python-garminconnect.
"""
from __future__ import annotations

import datetime
import shutil
from pathlib import Path

TOKEN_DIR = Path.home() / ".garmin-plan-loader"


class BladGarmin(Exception):
    """Błąd po stronie Garmin Connect - komunikat po polsku dla użytkownika."""


def _bledy_biblioteki():
    from garminconnect import (GarminConnectAuthenticationError, GarminConnectConnectionError,
                               GarminConnectTooManyRequestsError)
    return GarminConnectAuthenticationError, GarminConnectConnectionError, GarminConnectTooManyRequestsError


def _opis_bledu(e: Exception) -> str:
    try:
        auth, conn, many = _bledy_biblioteki()
    except Exception:
        return str(e)
    if isinstance(e, many):
        return "Garmin ogranicza liczbę prób (zbyt wiele żądań). Odczekaj kilkanaście minut i spróbuj ponownie."
    if isinstance(e, auth):
        return ("Nie udało się zalogować. Sprawdź e-mail, hasło i kod weryfikacyjny. "
                "Garmin potrafi też czasowo blokować logowanie spoza przeglądarki - wtedy odczekaj i spróbuj ponownie. "
                f"(szczegóły: {e})")
    if isinstance(e, conn):
        return f"Problem z połączeniem z Garmin Connect. Sprawdź internet i spróbuj ponownie. (szczegóły: {e})"
    return str(e)


def zapisane_logowanie() -> bool:
    return TOKEN_DIR.is_dir() and any(TOKEN_DIR.iterdir())


def _nazwa_konta(klient) -> str:
    return (getattr(klient, "full_name", None) or getattr(klient, "display_name", None) or "konto Garmin")


def zaloguj_tokenem():
    """Logowanie zapisanym tokenem. Zwraca klienta albo None, jeśli tokenu nie ma / wygasł."""
    if not zapisane_logowanie():
        return None
    from garminconnect import Garmin
    try:
        klient = Garmin()
        klient.login(str(TOKEN_DIR))
        return klient
    except Exception:
        return None


def zaloguj_haslem(email: str, haslo: str, prompt_mfa, zapamietaj: bool = True):
    """Logowanie e-mailem i hasłem. prompt_mfa() ma zwrócić kod z e-maila/SMS/aplikacji."""
    from garminconnect import Garmin
    if not email or not haslo:
        raise BladGarmin("Wpisz e-mail i hasło do Garmin Connect.")
    try:
        klient = Garmin(email.strip(), haslo, prompt_mfa=prompt_mfa)
        if zapamietaj:
            TOKEN_DIR.mkdir(parents=True, exist_ok=True)
            klient.login(str(TOKEN_DIR))
        else:
            klient.login()
        return klient
    except Exception as e:
        raise BladGarmin(_opis_bledu(e)) from e


def wyloguj() -> None:
    """Usuwa zapisany token z tego komputera."""
    if TOKEN_DIR.exists():
        shutil.rmtree(TOKEN_DIR, ignore_errors=True)


nazwa_konta = _nazwa_konta


# ----------------------------------------------------------------- kalendarz i wysyłka

def _elementy(dane):
    if isinstance(dane, list):
        return dane
    if isinstance(dane, dict):
        for k in ("calendarItems", "workouts", "items", "scheduledWorkouts"):
            if isinstance(dane.get(k), list):
                return dane[k]
    return []


def _pasuje(it, wid, nazwa) -> bool:
    """Wpis kalendarza dotyczy treningu: po workoutId albo (awaryjnie) po nazwie."""
    if str(it.get("workoutId")) == str(wid):
        return True
    return nazwa in (it.get("title"), it.get("workoutName"), it.get("name"))


def _wszystkie_treningi(klient):
    wynik, start = [], 0
    while True:
        strona = klient.get_workouts(start, 100) or []
        wynik.extend(strona)
        if len(strona) < 100:
            return wynik
        start += 100


def wyslij(klient, wyniki: list, nadpisz: bool, log) -> dict:
    """Wysyła treningi i wstawia je do kalendarza. log(tekst) dostaje komunikaty postępu.

    Powtarzalne: trening o tej samej nazwie, który już jest w Connect, nie jest dublowany;
    jeśli stoi w kalendarzu na innym dniu - wpis jest przestawiany (najpierw nowy, potem kasowanie starego).
    """
    po_nazwie = {}
    for x in _wszystkie_treningi(klient):
        po_nazwie.setdefault(x.get("workoutName"), []).append(str(x.get("workoutId")))

    kal = {}

    def wpisy_miesiaca(r, m):
        if (r, m) not in kal:
            kal[(r, m)] = _elementy(klient.get_scheduled_workouts(r, m))
        return kal[(r, m)]

    dzis = datetime.date.today()
    wyslane = pominiete = bledy = 0
    for x in wyniki:
        if x["wolne"]:
            continue
        w, d = x["w"], x["data"]
        nazwa = w["workoutName"]
        try:
            ids = po_nazwie.get(nazwa, [])
            if ids and nadpisz:
                for i in ids:
                    klient.delete_workout(i)
                log(f"{nazwa}: skasowano starą wersję (nadpisywanie)")
                ids = []
            if ids:
                wid, nowy = ids[0], False
                pominiete += 1
                log(f"{nazwa}: już jest w Garmin Connect, nie wysyłam drugi raz")
            else:
                wynik = klient.upload_workout(w)
                wid = str(wynik.get("workoutId")) if isinstance(wynik, dict) else None
                if not wid or wid == "None":
                    raise BladGarmin(f"Garmin nie potwierdził zapisu treningu (odpowiedź: {wynik})")
                nowy = True
                wyslane += 1
                log(f"{nazwa}: wysłano")

            if not d:
                continue
            miesiace = {(d.year, d.month)}
            if not nowy:                                  # stary wpis może stać w najbliższych miesiącach
                for k in range(5):
                    mm = dzis.month - 1 + k
                    miesiace.add((dzis.year + mm // 12, mm % 12 + 1))
            znalezione, przeskanowano = [], 0
            for r, m in sorted(miesiace):
                el = wpisy_miesiaca(r, m)
                przeskanowano += len(el)
                for it in el:
                    if _pasuje(it, wid, nazwa):
                        znalezione.append({"id": it.get("id") or it.get("scheduledWorkoutId"),
                                           "data": str(it.get("date") or "")[:10]})
            if not nowy and not znalezione:
                log(f"   kalendarz: nie znalazłem istniejących wpisów tego treningu "
                    f"(przejrzałem {przeskanowano} pozycji); jeśli stoi gdzieś w kalendarzu, usuń go ręcznie")
            if any(z["data"] == d.isoformat() for z in znalezione):
                log(f"   kalendarz: już na {d.isoformat()}")
            else:
                klient.schedule_workout(wid, d.isoformat())    # najpierw nowy wpis, potem kasowanie starych
                log(f"   kalendarz: wstawiono na {d.isoformat()}")
            for z in znalezione:
                if z["data"] != d.isoformat():
                    if z["id"]:
                        klient.unschedule_workout(z["id"])
                        log(f"   kalendarz: usunięto stary wpis z {z['data']}")
                    else:
                        log(f"   kalendarz: stary wpis z {z['data']} zostaje (brak id) - usuń go ręcznie")
        except Exception as e:
            bledy += 1
            log(f"{nazwa}: BŁĄD - {_opis_bledu(e)}")
    return {"wyslane": wyslane, "pominiete": pominiete, "bledy": bledy}
