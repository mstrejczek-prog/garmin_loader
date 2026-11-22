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
