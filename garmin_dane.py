# -*- coding: utf-8 -*-
"""
Odczyt danych z Garmin Connect (ta sama nieoficjalna biblioteka i ten sam token, co wysyłka planów).

Odpowiedzi Garmina są duże i nieudokumentowane, więc każda funkcja zwraca zwięzłe podsumowanie
wyciągnięte "defensywnie" (brakujące pole = None, a nie błąd). Gdy podsumowanie czegoś nie
zawiera, narzędzia MCP mogą zwrócić surową odpowiedź (przyciętą) - patrz przytnij().
"""
from __future__ import annotations

import datetime
import json
from typing import Any

MAX_DNI_AKTYWNOSCI = 92
MAX_DNI_WELLNESS = 14
MAX_SUROWE_ZNAKI = 20000


class BladDanych(Exception):
    pass


# ----------------------------------------------------------------- pomocnicze

def g(d: Any, sciezka: str, domyslne=None):
    """Bezpieczne d["a"]["b"][0]["c"] dla sciezki "a.b.0.c"."""
    for k in sciezka.split("."):
        if isinstance(d, dict):
            d = d.get(k)
        elif isinstance(d, list) and k.isdigit() and int(k) < len(d):
            d = d[int(k)]
        else:
            return domyslne
        if d is None:
            return domyslne
    return d


def _r(x, n=1):
    return round(x, n) if isinstance(x, (int, float)) else x


def _min(sek):
    return round(sek / 60, 1) if isinstance(sek, (int, float)) else None


def _h(sek):
    return round(sek / 3600, 2) if isinstance(sek, (int, float)) else None


def _bez_none(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None and v != {} and v != []}


def data_lub_dzis(tekst) -> datetime.date:
    if not tekst:
        return datetime.date.today()
    try:
        return datetime.date.fromisoformat(str(tekst))
    except ValueError:
        raise BladDanych(f"Zła data „{tekst}” (format RRRR-MM-DD)")


def zakres(od, do, domyslnie_dni: int, max_dni: int) -> tuple[datetime.date, datetime.date]:
    koniec = data_lub_dzis(do)
    poczatek = data_lub_dzis(od) if od else koniec - datetime.timedelta(days=domyslnie_dni - 1)
    if poczatek > koniec:
        raise BladDanych("Data „od” jest późniejsza niż „do”")
    if (koniec - poczatek).days + 1 > max_dni:
        raise BladDanych(f"Za długi zakres - najwyżej {max_dni} dni naraz (Garmin limituje zapytania)")
    return poczatek, koniec


def przytnij(dane: Any, limit: int = MAX_SUROWE_ZNAKI) -> Any:
    """Surowa odpowiedź Garmina, przycięta do rozsądnej wielkości."""
    tekst = json.dumps(dane, ensure_ascii=False, default=str)
    if len(tekst) <= limit:
        return dane
    return {"przycieto": True, "rozmiar_znakow": len(tekst), "poczatek": tekst[:limit]}


def _dni(poczatek, koniec):
    d = poczatek
    while d <= koniec:
        yield d
        d += datetime.timedelta(days=1)


# ----------------------------------------------------------------- aktywności

def _aktywnosc(a: dict) -> dict:
    return _bez_none({
        "id": a.get("activityId"),
        "nazwa": a.get("activityName"),
        "sport": g(a, "activityType.typeKey"),
        "start": a.get("startTimeLocal"),
        "czas_min": _min(a.get("duration")),
        "czas_ruchu_min": _min(a.get("movingDuration")),
        "dystans_km": _r((a.get("distance") or 0) / 1000, 2) if a.get("distance") else None,
        "przewyzszenie_m": _r(a.get("elevationGain"), 0),
        "hr_sr": a.get("averageHR"),
        "hr_max": a.get("maxHR"),
        "moc_sr_w": _r(a.get("avgPower"), 0),
        "moc_np_w": _r(a.get("normPower"), 0),
        "tss": _r(a.get("trainingStressScore"), 0),
        "if": _r(a.get("intensityFactor"), 2),
        "kadencja_sr": _r(a.get("averageRunningCadenceInStepsPerMinute") or a.get("averageBikingCadenceInRevPerMinute"), 0),
        "predkosc_sr_kmh": _r(a["averageSpeed"] * 3.6, 1) if a.get("averageSpeed") else None,
        "te_tlenowy": _r(a.get("aerobicTrainingEffect"), 1),
        "te_beztlenowy": _r(a.get("anaerobicTrainingEffect"), 1),
        "obciazenie": _r(a.get("activityTrainingLoad"), 0),
        "kcal": _r(a.get("calories"), 0),
        "tempo_basen_100m_s": _r(100 / a["averageSpeed"], 0) if a.get("averageSpeed") and g(a, "activityType.typeKey", "").endswith("swimming") else None,
    })


def aktywnosci(klient, od=None, do=None, sport: str | None = None) -> list[dict]:
    poczatek, koniec = zakres(od, do, 14, MAX_DNI_AKTYWNOSCI)
    lista = klient.get_activities_by_date(poczatek.isoformat(), koniec.isoformat(), sport or None) or []
    return [_aktywnosc(a) for a in lista]


def _okrazenie(l: dict) -> dict:
    return _bez_none({
        "nr": l.get("lapIndex"),
        "czas_min": _min(l.get("duration")),
        "dystans_km": _r((l.get("distance") or 0) / 1000, 2) if l.get("distance") else None,
        "hr_sr": _r(l.get("averageHR"), 0),
        "hr_max": _r(l.get("maxHR"), 0),
        "moc_sr_w": _r(l.get("averagePower"), 0),
        "moc_np_w": _r(l.get("normalizedPower"), 0),
        "predkosc_sr_kmh": _r(l["averageSpeed"] * 3.6, 1) if l.get("averageSpeed") else None,
        "kadencja_sr": _r(l.get("averageRunCadence") or l.get("averageBikeCadence"), 0),
        "przewyzszenie_m": _r(l.get("elevationGain"), 0),
        "typ": l.get("intensityType"),
    })


def trening(klient, activity_id) -> dict:
    """Szczegóły jednej aktywności: podsumowanie, okrążenia, czas w strefach tętna."""
    aid = str(activity_id).strip()
    if not aid.isdigit():
        raise BladDanych("activity_id musi być liczbą (z narzędzia aktywnosci)")
    a = klient.get_activity(aid) or {}
    s = a.get("summaryDTO") or {}
    wynik = _bez_none({
        "id": a.get("activityId"),
        "nazwa": a.get("activityName"),
        "sport": g(a, "activityTypeDTO.typeKey"),
        "start": s.get("startTimeLocal"),
        "opis": a.get("description"),
        "podsumowanie": _bez_none({
            "czas_min": _min(s.get("duration")),
            "czas_ruchu_min": _min(s.get("movingDuration")),
            "dystans_km": _r((s.get("distance") or 0) / 1000, 2) if s.get("distance") else None,
            "przewyzszenie_m": _r(s.get("elevationGain"), 0),
            "hr_sr": _r(s.get("averageHR"), 0),
            "hr_max": _r(s.get("maxHR"), 0),
            "moc_sr_w": _r(s.get("averagePower"), 0),
            "moc_np_w": _r(s.get("normalizedPower"), 0),
            "moc_max_w": _r(s.get("maxPower"), 0),
            "tss": _r(s.get("trainingStressScore"), 0),
            "if": _r(s.get("intensityFactor"), 2),
            "praca_kj": _r(s.get("totalWork"), 0),
            "predkosc_sr_kmh": _r(s["averageSpeed"] * 3.6, 1) if s.get("averageSpeed") else None,
            "kadencja_sr": _r(s.get("averageRunCadence") or s.get("averageBikeCadence"), 0),
            "te_tlenowy": _r(s.get("trainingEffect"), 1),
            "te_beztlenowy": _r(s.get("anaerobicTrainingEffect"), 1),
            "obciazenie": _r(s.get("activityTrainingLoad"), 0),
            "temp_sr_c": _r(s.get("averageTemperature"), 0),
            "kcal": _r(s.get("calories"), 0),
        }),
    })
    try:
        laps = (klient.get_activity_splits(aid) or {}).get("lapDTOs") or []
        wynik["okrazenia"] = [_okrazenie(l) for l in laps[:60]]
    except Exception as e:  # noqa: BLE001 - brak okrążeń nie przekreśla reszty
        wynik["okrazenia_blad"] = str(e)[:200]
    try:
        strefy = klient.get_activity_hr_in_timezones(aid) or []
        wynik["strefy_hr"] = [_bez_none({"strefa": z.get("zoneNumber"), "od_bpm": z.get("zoneLowBoundary"),
                                         "min": _min(z.get("secsInZone"))}) for z in strefy]
    except Exception as e:  # noqa: BLE001
        wynik["strefy_hr_blad"] = str(e)[:200]
    return wynik


# ----------------------------------------------------------------- kalendarz

def _elementy(dane):
    if isinstance(dane, list):
        return dane
    if isinstance(dane, dict):
        for k in ("calendarItems", "workouts", "items", "scheduledWorkouts"):
            if isinstance(dane.get(k), list):
                return dane[k]
    return []


def kalendarz(klient, od=None, do=None) -> list[dict]:
    """Treningi zaplanowane w kalendarzu Garmin Connect (bez wykonanych aktywności)."""
    poczatek = data_lub_dzis(od)
    koniec = data_lub_dzis(do) if do else poczatek + datetime.timedelta(days=13)
    if koniec < poczatek:
        raise BladDanych("Data „od” jest późniejsza niż „do”")
    if (koniec - poczatek).days > 92:
        raise BladDanych("Za długi zakres - najwyżej 3 miesiące naraz")
    miesiace, d = [], poczatek.replace(day=1)
    while d <= koniec:
        miesiace.append((d.year, d.month))
        d = (d + datetime.timedelta(days=32)).replace(day=1)
    wynik = []
    for r, m in miesiace:
        for it in _elementy(klient.get_scheduled_workouts(r, m)):
            dzien = str(it.get("date") or "")[:10]
            if not dzien or not (poczatek.isoformat() <= dzien <= koniec.isoformat()):
                continue
            if it.get("itemType") not in (None, "workout"):
                continue
            wynik.append(_bez_none({
                "data": dzien,
                "nazwa": it.get("title") or it.get("workoutName") or it.get("name"),
                "sport": it.get("sportTypeKey"),
                "workout_id": it.get("workoutId"),
                "wpis_id": it.get("id") or it.get("scheduledWorkoutId"),
                "czas_min": _min(it.get("duration") or it.get("estimatedDurationInSecs")),
            }))
    wynik.sort(key=lambda x: x["data"])
    return wynik


# ----------------------------------------------------------------- wellness i gotowość

def _dzien_wellness(klient, dzien: datetime.date) -> dict:
    ds = dzien.isoformat()
    wynik: dict[str, Any] = {"data": ds}
    bledy = {}

    try:
        u = klient.get_user_summary(ds) or {}
        wynik.update({
            "hr_spoczynkowe": u.get("restingHeartRate"),
            "hr_spoczynkowe_7d": u.get("lastSevenDaysAvgRestingHeartRate"),
            "stres_sr": u.get("averageStressLevel"),
            "bb_max": u.get("bodyBatteryHighestValue"),
            "bb_min": u.get("bodyBatteryLowestValue"),
            "bb_po_nocy": u.get("bodyBatteryAtWakeTime") or u.get("bodyBatteryMostRecentValue"),
            "kroki": u.get("totalSteps"),
            "kcal_aktywne": u.get("activeKilocalories"),
            "minuty_intensywne": (u.get("moderateIntensityMinutes") or 0) + 2 * (u.get("vigorousIntensityMinutes") or 0)
            if u.get("moderateIntensityMinutes") is not None or u.get("vigorousIntensityMinutes") is not None else None,
            "oddech_sr": u.get("avgWakingRespirationValue"),
            "spo2_sr": u.get("averageSpo2"),
        })
    except Exception as e:  # noqa: BLE001
        bledy["podsumowanie"] = str(e)[:200]

    try:
        s = klient.get_sleep_data(ds) or {}
        dto = s.get("dailySleepDTO") or {}
        wynik.update({
            "sen_h": _h(dto.get("sleepTimeSeconds")),
            "sen_gleboki_h": _h(dto.get("deepSleepSeconds")),
            "sen_rem_h": _h(dto.get("remSleepSeconds")),
            "sen_czuwanie_h": _h(dto.get("awakeSleepSeconds")),
            "sleep_score": g(dto, "sleepScores.overall.value"),
            "sen_hr_sr": _r(dto.get("avgHeartRate"), 0),
            "sen_stres_sr": _r(dto.get("avgSleepStress"), 0),
            "hrv_noc": s.get("avgOvernightHrv"),
            "bb_zmiana_w_nocy": s.get("bodyBatteryChange"),
        })
    except Exception as e:  # noqa: BLE001
        bledy["sen"] = str(e)[:200]

    try:
        h = klient.get_hrv_data(ds) or {}
        hs = h.get("hrvSummary") or {}
        wynik.update({
            "hrv_noc": hs.get("lastNightAvg") or wynik.get("hrv_noc"),
            "hrv_7d": hs.get("weeklyAvg"),
            "hrv_status": hs.get("status"),
            "hrv_norma": [g(hs, "baseline.balancedLow"), g(hs, "baseline.balancedUpper")]
            if g(hs, "baseline.balancedLow") else None,
        })
    except Exception as e:  # noqa: BLE001
        bledy["hrv"] = str(e)[:200]

    if bledy:
        wynik["bledy"] = bledy
    return _bez_none(wynik)


def wellness(klient, od=None, do=None) -> list[dict]:
    """Dzień po dniu: sen, HRV, tętno spoczynkowe, Body Battery, stres, kroki."""
    poczatek, koniec = zakres(od, do, 7, MAX_DNI_WELLNESS)
    return [_dzien_wellness(klient, d) for d in _dni(poczatek, koniec)]


def gotowosc(klient, dzien=None) -> dict:
    """Training Readiness, Training Status (obciążenie ostre/przewlekłe) i VO2max na dany dzień."""
    ds = data_lub_dzis(dzien).isoformat()
    wynik: dict[str, Any] = {"data": ds}

    try:
        tr = klient.get_training_readiness(ds) or []
        tr = tr[0] if isinstance(tr, list) and tr else (tr if isinstance(tr, dict) else {})
        wynik["training_readiness"] = _bez_none({
            "wynik": tr.get("score"),
            "poziom": tr.get("level"),
            "opis": tr.get("feedbackShort") or tr.get("feedbackLong"),
            "sen_wynik": tr.get("sleepScore"),
            "regeneracja_h": _h((tr.get("recoveryTime") or 0) * 60) if tr.get("recoveryTime") else None,
            "hrv_czynnik_proc": tr.get("hrvFactorPercent"),
            "obciazenie_ostre": tr.get("acuteLoad"),
            "obciazenie_czynnik_proc": tr.get("acwrFactorPercent"),
            "stres_historia_proc": tr.get("stressHistoryFactorPercent"),
        })
    except Exception as e:  # noqa: BLE001
        wynik["training_readiness_blad"] = str(e)[:200]

    try:
        ts = klient.get_training_status(ds) or {}
        dane_urz = g(ts, "mostRecentTrainingStatus.latestTrainingStatusData") or {}
        st = next(iter(dane_urz.values()), {}) if isinstance(dane_urz, dict) else {}
        acwr = st.get("acuteTrainingLoadDTO") or {}
        bilans = g(ts, "mostRecentTrainingLoadBalance.metricsTrainingLoadBalanceDTOMap") or {}
        bil = next(iter(bilans.values()), {}) if isinstance(bilans, dict) else {}
        wynik["training_status"] = _bez_none({
            "status": st.get("trainingStatusFeedbackPhrase"),
            "obciazenie_ostre": acwr.get("dailyTrainingLoadAcute"),
            "obciazenie_przewlekle": acwr.get("dailyTrainingLoadChronic"),
            "acwr": _r(acwr.get("dailyAcuteChronicWorkloadRatio"), 2),
            "acwr_status": acwr.get("acwrStatus"),
            "obc_4tyg_tlenowe_niskie": _r(bil.get("monthlyLoadAerobicLow"), 0),
            "obc_4tyg_tlenowe_wysokie": _r(bil.get("monthlyLoadAerobicHigh"), 0),
            "obc_4tyg_beztlenowe": _r(bil.get("monthlyLoadAnaerobic"), 0),
            "bilans_opis": bil.get("trainingBalanceFeedbackPhrase"),
            "vo2max_bieg": g(ts, "mostRecentVO2Max.generic.vo2MaxPreciseValue"),
            "vo2max_rower": g(ts, "mostRecentVO2Max.cycling.vo2MaxPreciseValue"),
        })
    except Exception as e:  # noqa: BLE001
        wynik["training_status_blad"] = str(e)[:200]

    return wynik


def strefy_garmin(klient) -> dict:
    """Strefy tętna/mocy i FTP zapisane w profilu Garmin Connect (jeśli konto je udostępnia)."""
    wynik = {}
    for nazwa, metoda in (("strefy_tetna", "get_heart_rate_zones"), ("strefy_mocy", "get_power_zones"),
                          ("ftp", "get_cycling_ftp"), ("prog_mleczanowy", "get_lactate_threshold")):
        try:
            wynik[nazwa] = przytnij(getattr(klient, metoda)(), 6000)
        except Exception as e:  # noqa: BLE001
            wynik[nazwa + "_blad"] = str(e)[:200]
    return wynik


# ----------------------------------------------------------------- serie siłowe, plan treningu, plan vs wykonanie

TYPY_KROKU = {"warmup": "rozgrzewka", "cooldown": "schlodzenie", "interval": "interwal", "recovery": "przerwa",
              "rest": "odpoczynek", "repeat": "powtorz", "other": "inny", "main": "glowny"}


def _kg(v):
    """Ciężar z Garmina -> kg. Garmin podaje go raz w gramach (20000.0), raz w kg - rozróżniamy po wielkości."""
    if not isinstance(v, (int, float)) or v < 0:
        return None
    return round(v / 1000.0, 2) if v > 400 else round(float(v), 2)


def _cwiczenie(kat, nazwa) -> str | None:
    if not kat and not nazwa:
        return None
    if kat and nazwa and nazwa != kat:
        return f"{kat} / {nazwa}"
    return kat or nazwa


def _id(x, opis: str) -> str:
    s = str(x if x is not None else "").strip()
    if not s.isdigit():
        raise BladDanych(f"{opis} musi być liczbą")
    return s


def serie(klient, activity_id) -> dict:
    """Serie siłowe aktywności (ćwiczenie, powtórzenia, ciężar, czas) + podsumowanie per ćwiczenie i tonaż."""
    aid = _id(activity_id, "activity_id")
    dane = klient.get_activity_exercise_sets(aid) or {}
    surowe = dane.get("exerciseSets") if isinstance(dane, dict) else dane
    lista, per = [], {}
    for i, s in enumerate(surowe or [], 1):
        ex = (s.get("exercises") or [{}])
        ex = max(ex, key=lambda e: e.get("probability") or 0) if ex else {}
        aktywna = str(s.get("setType") or "").upper() == "ACTIVE"
        wpis = {
            "nr": i,
            "typ": "aktywna" if aktywna else "przerwa",
            "cwiczenie": _cwiczenie(ex.get("category"), ex.get("name")) if aktywna else None,
            "powtorzenia": s.get("repetitionCount") if aktywna else None,
            "ciezar_kg": _kg(s.get("weight")) if aktywna else None,
            "czas_s": _r(s.get("duration"), 0),
            "start_utc": s.get("startTime"),
            "krok_planu": s.get("wktStepIndex"),
        }
        lista.append(wpis)
        if aktywna:
            p = per.setdefault(wpis["cwiczenie"] or "nieznane", {"serie": 0, "powtorzenia": 0, "ciezary": [], "tonaz_kg": 0.0})
            p["serie"] += 1
            p["powtorzenia"] += wpis["powtorzenia"] or 0
            if wpis["ciezar_kg"]:
                p["ciezary"].append(wpis["ciezar_kg"])
                p["tonaz_kg"] += (wpis["powtorzenia"] or 0) * wpis["ciezar_kg"]
    cwiczenia = []
    for nazwa, p in per.items():
        c = p.pop("ciezary")
        cwiczenia.append({"cwiczenie": nazwa, **p, "tonaz_kg": round(p["tonaz_kg"], 1),
                          "ciezar_max_kg": max(c) if c else None,
                          "ciezar_sr_kg": round(sum(c) / len(c), 1) if c else None})
    aktywne = [s for s in lista if s["typ"] == "aktywna"]
    return {
        "activity_id": int(aid),
        "podsumowanie": {"serie_aktywne": len(aktywne),
                         "powtorzenia": sum(s["powtorzenia"] or 0 for s in aktywne),
                         "tonaz_kg": round(sum(c["tonaz_kg"] for c in cwiczenia), 1)},
        "cwiczenia": cwiczenia,
        "serie": lista,
    }


def _mps_na_tempo(v, na=1000.0):
    if not isinstance(v, (int, float)) or v <= 0:
        return None
    s = round(na / v)
    return f"{s // 60}:{s % 60:02d}"


def _cel(typ: dict | None, v1, v2, strefa, sport: str) -> str | None:
    klucz = (typ or {}).get("workoutTargetTypeKey")
    if not klucz or klucz == "no.target":
        return None
    if klucz == "heart.rate.zone":
        return f"HR strefa {strefa}" if strefa else f"HR {v1:g}-{v2:g} bpm" if v1 and v2 else "HR"
    if klucz == "power.zone":
        return f"moc strefa {strefa}" if strefa else f"moc {v1:g}-{v2:g} W" if v1 and v2 else "moc"
    if klucz == "pace.zone":
        na = 100.0 if sport.endswith("swimming") else 1000.0
        jedn = "/100m" if na == 100 else "/km"
        a, b = _mps_na_tempo(v1, na), _mps_na_tempo(v2, na)
        return f"tempo {a}-{b} {jedn}" if a and b else "tempo"
    if klucz == "speed.zone":
        return f"prędkość {v1 * 3.6:.1f}-{v2 * 3.6:.1f} km/h" if v1 and v2 else "prędkość"
    if klucz == "cadence":
        return f"kadencja {v1:g}-{v2:g}" if v1 and v2 else "kadencja"
    if klucz == "swim.css.offset":
        return f"CSS {v1:+g} s/100m" if v1 is not None else "CSS"
    return klucz


def _krok_planu(s: dict, sport: str) -> dict:
    end = g(s, "endCondition.conditionTypeKey")
    val = s.get("endConditionValue")
    return {
        "nr_kroku": s.get("stepOrder"),
        "typ": TYPY_KROKU.get(g(s, "stepType.stepTypeKey"), g(s, "stepType.stepTypeKey")),
        "cwiczenie": _cwiczenie(s.get("category"), s.get("exerciseName")),
        "powtorzenia": int(val) if end == "reps" and val else None,
        "ciezar_kg": _kg(s.get("weightValue")),
        "czas_s": _r(val, 0) if end == "time" else None,
        "dystans_m": _r(val, 0) if end == "distance" else None,
        "przycisk": True if end == "lap.button" else None,
        "cel": _cel(s.get("targetType"), s.get("targetValueOne"), s.get("targetValueTwo"), s.get("zoneNumber"), sport),
        "cel_dodatkowy": _cel(s.get("secondaryTargetType"), s.get("secondaryTargetValueOne"),
                              s.get("secondaryTargetValueTwo"), s.get("secondaryZoneNumber"), sport),
        "notatka": s.get("description"),
        "_kategoria": s.get("category"),
    }


def _kroki(lista, sport, mnoznik=1):
    """Kroki Garmina -> (drzewo do pokazania, płaska lista kroków wykonawczych z krotnością)."""
    drzewo, plaskie = [], []
    for s in sorted(lista or [], key=lambda x: x.get("stepOrder") or 0):
        if s.get("type") == "RepeatGroupDTO" or g(s, "stepType.stepTypeKey") == "repeat":
            n = int(s.get("numberOfIterations") or 1)
            d, p = _kroki(s.get("workoutSteps"), sport, mnoznik * n)
            drzewo.append({"nr_kroku": s.get("stepOrder"), "typ": "powtorz", "powtorzenia_bloku": n, "kroki": d})
            plaskie += p
        else:
            k = _krok_planu(s, sport)
            plaskie.append({**k, "_krotnosc": mnoznik})
            drzewo.append({x: y for x, y in k.items() if not x.startswith("_")})
    return drzewo, plaskie


def _indeksy_fit(lista, wynik=None):
    """Kolejność kroków w pliku FIT (krok powtórzenia stoi PO swoich dzieciach) -> lista stepOrder."""
    wynik = [] if wynik is None else wynik
    for s in sorted(lista or [], key=lambda x: x.get("stepOrder") or 0):
        if s.get("type") == "RepeatGroupDTO" or g(s, "stepType.stepTypeKey") == "repeat":
            _indeksy_fit(s.get("workoutSteps"), wynik)
        wynik.append(s.get("stepOrder"))
    return wynik


def _workout(klient, workout_id) -> dict:
    return klient.get_workout_by_id(_id(workout_id, "workout_id")) or {}


def plan_treningu(klient, workout_id, _w: dict | None = None) -> dict:
    """Kroki zaplanowanego treningu (biblioteka Treningi w Garmin Connect)."""
    w = _w if _w is not None else _workout(klient, workout_id)
    sport = g(w, "sportType.sportTypeKey") or ""
    kroki = []
    for seg in w.get("workoutSegments") or []:
        d, _ = _kroki(seg.get("workoutSteps"), sport)
        kroki += d
    return {
        "workout_id": w.get("workoutId"),
        "nazwa": w.get("workoutName"),
        "sport": sport or None,
        "opis": w.get("description"),
        "czas_szac_min": _min(w.get("estimatedDurationInSecs")),
        "dystans_szac_km": _r((w.get("estimatedDistanceInMeters") or 0) / 1000, 2) if w.get("estimatedDistanceInMeters") else None,
        "kroki": kroki,
    }


def _odchylenie(plan, wykon, prog=0.1):
    if plan in (None, 0) or wykon is None:
        return None
    return abs(wykon - plan) / plan >= prog


def _w_celu(cel: str | None, hr, moc) -> bool | None:
    """Czy średnie HR/moc mieszczą się w celu podanym liczbami (strefy numeryczne pomijamy)."""
    import re
    if not cel:
        return None
    m = re.match(r"(HR|moc) (\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)", cel)
    if not m:
        return None
    wart = hr if m.group(1) == "HR" else moc
    if wart is None:
        return None
    return float(m.group(2)) * 0.97 <= wart <= float(m.group(3)) * 1.03


def plan_vs_wykonanie(klient, activity_id) -> dict:
    aid = _id(activity_id, "activity_id")
    a = klient.get_activity(aid) or {}
    s = a.get("summaryDTO") or {}
    wid = a.get("associatedWorkoutId") or s.get("associatedWorkoutId") or g(a, "metadataDTO.associatedWorkoutId")
    sport = g(a, "activityTypeDTO.typeKey") or ""
    naglowek = {"activity_id": int(aid), "nazwa": a.get("activityName"), "sport": sport or None,
                "start": s.get("startTimeLocal")}
    if not wid:
        return {**naglowek, "workout_id": None,
                "komunikat": "Ta aktywność nie była wykonana z zaplanowanego treningu (brak associatedWorkoutId), "
                             "więc nie ma z czym jej porównać. Użyj narzędzi trening/serie."}
    w = _workout(klient, wid)
    plan = plan_treningu(klient, wid, w)
    plaskie = []
    for seg in w.get("workoutSegments") or []:
        plaskie += _kroki(seg.get("workoutSteps"), plan["sport"] or "")[1]
    wynik = {**naglowek, "workout_id": int(wid), "plan_nazwa": plan["nazwa"]}

    if "strength" in sport or (plan["sport"] or "").startswith("strength"):
        sr = serie(klient, aid)
        aktywne = [x for x in sr["serie"] if x["typ"] == "aktywna"]
        po_order = {k["nr_kroku"]: k for k in plaskie}
        kolejnosc_fit = []
        for seg in w.get("workoutSegments") or []:
            kolejnosc_fit += _indeksy_fit(seg.get("workoutSteps"))
        # wktStepIndex: sprawdzamy obie konwencje numeracji i wybieramy tę, w której zgadza się więcej kategorii
        mapy = {"stepOrder-1": lambda i: i + 1,
                "fit": lambda i: kolejnosc_fit[i] if 0 <= i < len(kolejnosc_fit) else None}

        def trafienia(f):
            n = 0
            for x in aktywne:
                k = po_order.get(f(x["krok_planu"])) if x["krok_planu"] is not None else None
                if k and k["_kategoria"] and x["cwiczenie"] and x["cwiczenie"].split(" / ")[0] == k["_kategoria"]:
                    n += 1
            return n
        nazwa_mapy = max(mapy, key=lambda m: trafienia(mapy[m]))
        f = mapy[nazwa_mapy]
        wykonane: dict[int, list] = {}
        bez_kroku = []
        for x in aktywne:
            nr = f(x["krok_planu"]) if x["krok_planu"] is not None else None
            if nr in po_order:
                wykonane.setdefault(nr, []).append(x)
            else:
                bez_kroku.append(x)
        tabela = []
        for k in plaskie:
            if not k["cwiczenie"] and not k["powtorzenia"]:
                continue                                     # rozgrzewka/odpoczynek bez ćwiczenia
            sx = wykonane.get(k["nr_kroku"], [])
            pow_wszystkie = [x["powtorzenia"] for x in sx]
            pow_ = [p for p in pow_wszystkie if p is not None]
            cie = [x["ciezar_kg"] for x in sx if x["ciezar_kg"]]
            plan_pow = (k["powtorzenia"] or 0) * k["_krotnosc"] if k["powtorzenia"] else None
            wiersz = {
                "nr_kroku": k["nr_kroku"], "cwiczenie_plan": k["cwiczenie"],
                "cwiczenie_wykonane": sx[0]["cwiczenie"] if sx else None,
                "serie_plan": k["_krotnosc"], "serie_wykonane": len(sx),
                "powtorzenia_plan_seria": k["powtorzenia"], "powtorzenia_wykonane": pow_wszystkie or None,
                "ciezar_plan_kg": k["ciezar_kg"], "ciezar_wykonany_kg": cie or None,
            }
            odch = []
            if len(sx) != k["_krotnosc"]:
                odch.append("liczba serii")
            if plan_pow and _odchylenie(plan_pow, sum(pow_)):
                odch.append("powtórzenia")
            if k["ciezar_kg"] and cie and _odchylenie(k["ciezar_kg"], sum(cie) / len(cie), 0.05):
                odch.append("ciężar")
            wiersz["odchylenie"] = odch or None
            tabela.append(wiersz)
        wynik.update(rodzaj="silowy", dopasowanie_krokow=nazwa_mapy, kroki=tabela,
                     serie_bez_kroku_planu=bez_kroku or None, podsumowanie=sr["podsumowanie"])
        return wynik

    # wytrzymałość: kolejne kroki planu (rozwinięte powtórzenia) vs kolejne okrążenia
    rozwiniete = [k for k in plaskie for _ in range(k["_krotnosc"])] if all(
        k["_krotnosc"] <= 50 for k in plaskie) else plaskie
    try:
        laps = (klient.get_activity_splits(aid) or {}).get("lapDTOs") or []
    except Exception as e:  # noqa: BLE001
        return {**wynik, "komunikat": f"Nie udało się pobrać okrążeń: {str(e)[:200]}", "plan": plan}
    tabela = []
    for i in range(max(len(rozwiniete), len(laps))):
        k = rozwiniete[i] if i < len(rozwiniete) else None
        l = _okrazenie(laps[i]) if i < len(laps) else None
        czas_w = round(l["czas_min"] * 60) if l and l.get("czas_min") else None
        dyst_w = round(l["dystans_km"] * 1000) if l and l.get("dystans_km") else None
        wiersz = {
            "nr": i + 1,
            "krok_plan": (f"{k['typ']}" + (f" {k['cel']}" if k and k["cel"] else "")) if k else None,
            "czas_plan_s": k["czas_s"] if k else None, "czas_wykonany_s": czas_w,
            "dystans_plan_m": k["dystans_m"] if k else None, "dystans_wykonany_m": dyst_w,
            "cel": k["cel"] if k else None,
            "hr_sr": l.get("hr_sr") if l else None, "moc_sr_w": l.get("moc_sr_w") if l else None,
            "tempo_min_km": _mps_na_tempo(l["predkosc_sr_kmh"] / 3.6) if l and l.get("predkosc_sr_kmh") else None,
        }
        odch = []
        if not k or not l:
            odch.append("brak kroku planu" if not k else "brak okrążenia")
        else:
            if _odchylenie(k["czas_s"], czas_w):
                odch.append("czas")
            if _odchylenie(k["dystans_m"], dyst_w, 0.05):
                odch.append("dystans")
            if _w_celu(k["cel"], wiersz["hr_sr"], wiersz["moc_sr_w"]) is False:
                odch.append("poza celem")
        wiersz["odchylenie"] = odch or None
        tabela.append(wiersz)
    uwaga = None
    if len(laps) != len(rozwiniete):
        uwaga = (f"Liczba okrążeń ({len(laps)}) różni się od liczby kroków planu ({len(rozwiniete)}) - "
                 "np. ręczne naciśnięcie LAP albo pominięty krok; dopasowanie po kolejności może być przesunięte.")
    wynik.update(rodzaj="wytrzymalosc", kroki=tabela, uwaga=uwaga)
    return wynik


# ----------------------------------------------------------------- surowe odpowiedzi (diagnostyka)

ZRODLA_SUROWE = {
    "aktywnosc": ("get_activity", "activity_id"),
    "serie": ("get_activity_exercise_sets", "activity_id"),
    "okrazenia": ("get_activity_splits", "activity_id"),
    "okrazenia_typowane": ("get_activity_typed_splits", "activity_id"),
    "strefy_hr": ("get_activity_hr_in_timezones", "activity_id"),
    "strefy_mocy": ("get_activity_power_in_timezones", "activity_id"),
    "pogoda": ("get_activity_weather", "activity_id"),
    "sprzet": ("get_activity_gear", "activity_id"),
    "trening_planowany": ("get_workout_by_id", "workout_id"),
}


def surowe(klient, zrodlo: str, ident) -> Any:
    if zrodlo not in ZRODLA_SUROWE:
        raise BladDanych(f"Nieznane źródło „{zrodlo}”. Dostępne: {', '.join(ZRODLA_SUROWE)}")
    metoda, opis = ZRODLA_SUROWE[zrodlo]
    return przytnij(getattr(klient, metoda)(_id(ident, opis)), 30000)
