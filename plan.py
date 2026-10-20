# -*- coding: utf-8 -*-
"""
Plan treningowy (JSON) -> kroki treningu w formacie Garmin Connect.

Ten moduł nie zna okna ani sieci: tylko czyta tekst planu, sprawdza go i buduje
dane treningów, które potem wysyła garmin_io.py.
"""
from __future__ import annotations

import datetime
import json
import re

DNI = ["pon", "wt", "śr", "czw", "pt", "sob", "niedz"]

# Identyfikatory zgodne z aktualnym API Garmin Connect (zweryfikowane w kodzie biblioteki python-garminconnect).
def _sp(i, klucz, kolejnosc=None):
    return {"sportTypeId": i, "sportTypeKey": klucz, "displayOrder": kolejnosc or i}


SPORTY = {
    "bieg": _sp(1, "running"),
    "rower": _sp(2, "cycling"),
    "plywanie": _sp(4, "swimming", 3),
    "silowy": _sp(5, "strength_training"),
    "cardio": _sp(6, "cardio_training"),
    "joga": _sp(7, "yoga"),
    "pilates": _sp(8, "pilates"),
    "hiit": _sp(9, "hiit"),
    "mobilnosc": _sp(11, "mobility"),
    "marsz": _sp(17, "walking"),
    "turystyka": _sp(18, "hiking"),
    "inny": _sp(3, "other"),
}
ETYKIETA_SPORTU = {
    "bieg": "bieg", "rower": "rower", "plywanie": "pływanie", "silowy": "siłowy", "cardio": "cardio",
    "joga": "joga", "pilates": "pilates", "hiit": "HIIT", "mobilnosc": "mobilność", "marsz": "marsz",
    "turystyka": "turystyka", "inny": "inny",
}
SPORT_ALIASY = {
    "bieg": "bieg", "bieganie": "bieg", "run": "bieg", "running": "bieg",
    "rower": "rower", "kolarstwo": "rower", "bike": "rower", "cycling": "rower", "ride": "rower",
    "plywanie": "plywanie", "plywac": "plywanie", "basen": "plywanie", "swim": "plywanie", "swimming": "plywanie",
    "silowy": "silowy", "silowe": "silowy", "sila": "silowy", "trening silowy": "silowy", "silownia": "silowy",
    "strength": "silowy", "strength_training": "silowy", "strength training": "silowy",
    "cardio": "cardio", "cardio_training": "cardio",
    "joga": "joga", "yoga": "joga",
    "pilates": "pilates",
    "hiit": "hiit",
    "mobilnosc": "mobilnosc", "mobility": "mobilnosc", "rozciaganie": "mobilnosc", "stretching": "mobilnosc",
    "marsz": "marsz", "spacer": "marsz", "walking": "marsz", "walk": "marsz", "chodzenie": "marsz",
    "turystyka": "turystyka", "hiking": "turystyka", "hike": "turystyka", "gory": "turystyka",
    "inny": "inny", "inne": "inny", "other": "inny",
}
SPORTY_TEMPO_KM = ("bieg", "marsz", "turystyka")          # tempo w min/km
SPORTY_NEUTRALNE = ("silowy", "cardio", "joga", "pilates", "hiit", "mobilnosc", "inny")   # bez stref: wykres szary
# domyślna prędkość do oszacowania czasu kroków z dystansem (m/s)
PREDKOSC_DOMYSLNA = {"bieg": 1000 / 300.0, "rower": 30 / 3.6, "plywanie": 100 / 120.0, "marsz": 5 / 3.6,
                      "turystyka": 4 / 3.6}
STEP = {
    "warmup": {"stepTypeId": 1, "stepTypeKey": "warmup", "displayOrder": 1},
    "cooldown": {"stepTypeId": 2, "stepTypeKey": "cooldown", "displayOrder": 2},
    "interval": {"stepTypeId": 3, "stepTypeKey": "interval", "displayOrder": 3},
    "recovery": {"stepTypeId": 4, "stepTypeKey": "recovery", "displayOrder": 4},
    "rest": {"stepTypeId": 5, "stepTypeKey": "rest", "displayOrder": 5},
    "repeat": {"stepTypeId": 6, "stepTypeKey": "repeat", "displayOrder": 6},
}
TYP_KROKU = {
    "rozgrzewka": "warmup", "warmup": "warmup", "wu": "warmup",
    "schlodzenie": "cooldown", "chlodzenie": "cooldown", "cooldown": "cooldown", "cd": "cooldown",
    "interwal": "interval", "interval": "interval", "praca": "interval", "work": "interval",
    "bieg": "interval", "jazda": "interval", "inny": "interval", "steady": "interval",
    "przerwa": "recovery", "recovery": "recovery", "rest": "recovery", "odpoczynek": "recovery",
    "trucht": "recovery", "marsz": "recovery",
}
ETYKIETA = {"warmup": "Rozgrzewka", "cooldown": "Schłodzenie", "interval": "Praca", "recovery": "Przerwa",
            "rest": "Przerwa"}

END_LAP = {"conditionTypeId": 1, "conditionTypeKey": "lap.button", "displayOrder": 1, "displayable": True}
END_TIME = {"conditionTypeId": 2, "conditionTypeKey": "time", "displayOrder": 2, "displayable": True}
END_DIST = {"conditionTypeId": 3, "conditionTypeKey": "distance", "displayOrder": 3, "displayable": True}
END_ITER = {"conditionTypeId": 7, "conditionTypeKey": "iterations", "displayOrder": 7, "displayable": False}
END_REPS = {"conditionTypeId": 10, "conditionTypeKey": "reps", "displayOrder": 10, "displayable": True}
TGT_NONE = {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target", "displayOrder": 1}
TGT_POWER = {"workoutTargetTypeId": 2, "workoutTargetTypeKey": "power.zone", "displayOrder": 2}
TGT_CADENCE = {"workoutTargetTypeId": 3, "workoutTargetTypeKey": "cadence", "displayOrder": 3}
TGT_HR = {"workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.zone", "displayOrder": 4}
TGT_PACE = {"workoutTargetTypeId": 6, "workoutTargetTypeKey": "pace.zone", "displayOrder": 6}
WAGA_KG = {"unitId": 8, "unitKey": "kilogram", "factor": 1000.0}

MAX_KROKOW = 50
MAX_WEZLOW_SILOWY = 100
MAX_TRENINGOW = 60


class BladPlanu(Exception):
    """Błąd w planie - komunikat jest po polsku i przeznaczony dla użytkownika."""


def _norm(t) -> str:
    """Małe litery bez polskich znaków: 'Schłodzenie' i 'schlodzenie' znaczą to samo."""
    return str(t).strip().lower().translate(str.maketrans("ąćęłńóśźż", "acelnoszz"))


# ----------------------------------------------------------------- parsowanie wartości

def sek(tekst) -> int:
    t = _norm(tekst)
    m = re.fullmatch(r"(\d+)\s*(?:min|m|')?", t)
    if m:
        return int(m.group(1)) * 60
    p = t.split(":")
    if len(p) in (2, 3) and all(x.isdigit() for x in p):
        p = [int(x) for x in p]
        return p[0] * 60 + p[1] if len(p) == 2 else p[0] * 3600 + p[1] * 60 + p[2]
    raise BladPlanu(f"nie rozumiem czasu „{tekst}” (użyj np. 40:00, 1:30:00 albo 40min)")


def metry(tekst) -> float:
    t = _norm(tekst).replace(" ", "").replace(",", ".")
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(km|m)", t)
    if not m:
        raise BladPlanu(f"nie rozumiem dystansu „{tekst}” (użyj np. 3km albo 800m)")
    v = float(m.group(1))
    return v * 1000 if m.group(2) == "km" else v


def _tempo_sek(m, s) -> int:
    return int(m) * 60 + int(s)


def tempo_na_mps(sekundy: int) -> float:
    """Garmin trzyma tempo biegu jako prędkość w m/s."""
    return 1000.0 / sekundy


def sek_na_tempo(mps: float, odcinek: float = 1000.0) -> str:
    x = round(odcinek / mps)
    return f"{x // 60}:{x % 60:02d}"


def _tabela_hr(strefy: dict, sport: str) -> dict:
    """Strefy tętna sportu. Rower i pływanie mają własne tabele (tętno jest tam niższe); pozostałe sporty, które
    nie mają własnej tabeli (siła, cardio, marsz...), korzystają z tętna biegowego."""
    z = strefy.get(sport)
    if not z and sport not in ("rower", "plywanie"):
        z = strefy.get("bieg")
    return z or {}


def _cel(cel, sport, strefy, moc=None, ftp=None):
    """-> (typ celu, wartość1, wartość2, opis tekstowy, średnia prędkość m/s albo None)"""
    if cel is None or _norm(cel) in ("", "brak", "-", "bez celu"):
        return TGT_NONE, None, None, "bez celu", None
    c = _norm(cel)

    # strefa z sekcji "strefy", np. S2
    m = re.fullmatch(r"s([1-5])", c)
    if m:
        z = _tabela_hr(strefy, sport).get("S" + m.group(1))
        if not z:
            raise BladPlanu(f"brak strefy S{m.group(1)} dla sportu „{sport}”. Ustaw swoje strefy w programie "
                            f"(przycisk „Moje strefy”) albo dodaj sekcję „strefy” do planu")
        return TGT_HR, z[0], z[1], f"tętno {z[0]}-{z[1]} (S{m.group(1)})", None

    # tempo m:ss-m:ss (także zapis 4'20''-4'25'' i końcówka /km albo /100m)
    ct = c.replace("''", "").replace('"', "").replace("'", ":").replace("/km", "").replace("/100m", "").replace(" ", "")
    m = re.fullmatch(r"(\d+):(\d\d)-(\d+):(\d\d)", ct)
    if m:
        a, b = _tempo_sek(m.group(1), m.group(2)), _tempo_sek(m.group(3), m.group(4))
        if sport == "plywanie":                                    # tempo na 100 m
            if not (40 <= a <= 400 and 40 <= b <= 400):
                raise BladPlanu(f"tempo „{cel}” wygląda nierealnie (oczekuję min na 100 m, np. 1:50-2:00)")
            v1, v2 = sorted([100.0 / a, 100.0 / b])
            return TGT_PACE, round(v1, 4), round(v2, 4), \
                f"tempo {sek_na_tempo(v2, 100)}-{sek_na_tempo(v1, 100)} /100 m", (v1 + v2) / 2
        if sport not in SPORTY_TEMPO_KM:
            raise BladPlanu(f"cel tempa „{cel}” ma sens tylko dla biegu, marszu, turystyki i pływania")
        if not (90 <= a <= 1800 and 90 <= b <= 1800):
            raise BladPlanu(f"tempo „{cel}” wygląda nierealnie (oczekuję min/km, np. 4:20-4:25)")
        v1, v2 = sorted([tempo_na_mps(a), tempo_na_mps(b)])
        # Garmin: wartość pierwsza = wolniejsze tempo (mniej m/s), druga = szybsze
        return TGT_PACE, round(v1, 4), round(v2, 4), f"tempo {sek_na_tempo(v2)}-{sek_na_tempo(v1)} /km", (v1 + v2) / 2

    # kadencja: "90-100rpm", "kadencja 85-95", "170-180 spm"
    m = re.fullmatch(r"(?:kadencja|cad)\s*(\d+)\s*-\s*(\d+)\s*(?:rpm|spm)?", c) or \
        re.fullmatch(r"(\d+)\s*-\s*(\d+)\s*(?:rpm|spm)", c)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        a, b = sorted((a, b))
        if not (10 <= a and b <= 300):
            raise BladPlanu(f"kadencja „{cel}” wygląda nierealnie (10-300)")
        return TGT_CADENCE, a, b, f"kadencja {a}-{b}", None

    # strefa mocy z "Moje strefy": P1-P5 (tylko rower)
    m = re.fullmatch(r"p([1-5])", c)
    if m:
        if sport != "rower":
            raise BladPlanu(f"cel „{cel}” (strefa mocy) ma sens tylko dla roweru")
        z = (moc or {}).get("rower", {}).get("P" + m.group(1))
        if not z:
            raise BladPlanu(f"brak strefy mocy P{m.group(1)}. Ustaw waty stref w programie (przycisk „Moje strefy”) "
                            f"albo podaj moc w watach (np. 200-220W)")
        return TGT_POWER, z[0], z[1], f"moc {z[0]:g}-{z[1]:g} W (P{m.group(1)})", None

    # moc w procentach FTP: "90-95% FTP", "95%FTP", "FTP 90-95%"
    m = re.fullmatch(r"(\d+(?:[.,]\d+)?)(?:-(\d+(?:[.,]\d+)?))?%", c.replace(" ", "").replace("ftp", ""))
    if m and "%" in c:
        if sport != "rower":
            raise BladPlanu(f"cel „{cel}” (procent FTP) ma sens tylko dla roweru")
        p1 = float(m.group(1).replace(",", "."))
        p2 = float(m.group(2).replace(",", ".")) if m.group(2) else None
        if p2 is None:                                        # pojedyncza wartość: pasmo +/- 2 punkty procentowe
            p1, p2 = p1 - 2, p1 + 2
            opis_proc = f"{p1 + 2:g}% FTP, pasmo +/-2 pp"
        else:
            p1, p2 = sorted((p1, p2))
            opis_proc = f"{p1:g}-{p2:g}% FTP"
        if not (20 <= p1 and p2 <= 300):
            raise BladPlanu(f"moc „{cel}” wygląda nierealnie (oczekuję procentu FTP, np. 90-95% FTP)")
        if not ftp:
            raise BladPlanu(f"cel „{cel}” wymaga FTP. Wpisz FTP w programie (przycisk „Moje strefy”) "
                            f"albo dodaj do planu pole \"ftp\": 220")
        a, b = round(ftp * p1 / 100), round(ftp * p2 / 100)
        return TGT_POWER, a, b, f"moc {a}-{b} W ({opis_proc}, FTP {ftp:g} W)", None

    # moc 200-220W
    m = re.fullmatch(r"(\d+)-(\d+)w", c.replace(" ", ""))
    if m:
        if sport != "rower":
            raise BladPlanu(f"cel mocy „{cel}” ma sens tylko dla roweru")
        a, b = sorted((int(m.group(1)), int(m.group(2))))
        if not (1 <= a and b <= 2500):
            raise BladPlanu(f"moc „{cel}” wygląda nierealnie")
        return TGT_POWER, a, b, f"moc {a}-{b} W", None

    # tętno: "HR 125-148", "125-148 bpm", "125-148"
    m = re.fullmatch(r"(?:hr)?\s*(\d+)\s*-\s*(\d+)\s*(?:bpm)?", c)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        if a > b:
            raise BladPlanu(f"tętno „{cel}”: dolna granica jest większa od górnej")
        if not (30 <= a and b <= 250):
            raise BladPlanu(f"tętno „{cel}” wygląda nierealnie (30-250)")
        return TGT_HR, a, b, f"tętno {a}-{b}", None

    raise BladPlanu(
        f"nie rozumiem celu „{cel}” (użyj np. S2, HR 125-148, 4:20-4:25, 200-220W, 90-95% FTP, P2, "
        f"kadencja 90-100 albo pomiń cel)")


def _splaszcz(kroki, glebokosc=0):
    """Powtórzenia rozwijają się na kroki jeden po drugim (prościej i pewniej niż grupy powtórzeń)."""
    if glebokosc > 3:
        raise BladPlanu("powtórzenia zagnieżdżone zbyt głęboko")
    wynik = []
    for k in kroki:
        if not isinstance(k, dict):
            raise BladPlanu("krok musi być obiektem { ... }")
        if "powtorz" in k:
            try:
                n = int(k["powtorz"])
            except (TypeError, ValueError):
                raise BladPlanu("„powtorz” musi być liczbą")
            if not 1 <= n <= 50 or not isinstance(k.get("kroki"), list):
                raise BladPlanu("„powtorz” wymaga liczby 1-50 i listy „kroki”")
            for _ in range(n):
                wynik.extend(_splaszcz(k["kroki"], glebokosc + 1))
        else:
            wynik.append(k)
    return wynik


# ----------------------------------------------------------------- budowa treningu

def _krok(nr, typ, end, end_val, target, v1, v2):
    return {
        "type": "ExecutableStepDTO",
        "stepOrder": nr,
        "stepType": STEP[typ],
        "endCondition": end,
        "endConditionValue": None if end_val is None else float(end_val),
        "targetType": target,
        "targetValueOne": v1,
        "targetValueTwo": v2,
        "zoneNumber": None,
    }


def _dl_czas(val: int) -> str:
    return f"{val // 3600}:{val % 3600 // 60:02d}:{val % 60:02d}" if val >= 3600 else f"{val // 60}:{val % 60:02d}"


def _sport(t: dict) -> str:
    sport = SPORT_ALIASY.get(_norm(t.get("sport", "bieg")))
    if not sport:
        raise BladPlanu(f"sport „{t.get('sport')}” nie jest obsługiwany (dostępne: bieg, rower, plywanie, silowy, "
                        f"cardio, joga, pilates, hiit, mobilnosc, marsz, turystyka, inny)")
    return sport


def _opis_i_nazwa(t: dict) -> str:
    nazwa = t.get("nazwa")
    if not nazwa or not str(nazwa).strip():
        raise BladPlanu("trening bez nazwy (pole „nazwa”)")
    return str(nazwa).strip()


def zbuduj(t: dict, strefy: dict, moc: dict | None = None, ftp: float | None = None):
    """Jeden trening z planu -> (dane treningu dla Garmina, wiersze podglądu)."""
    nazwa = _opis_i_nazwa(t)
    sport = _sport(t)
    if sport == "silowy":
        return _zbuduj_silowy(t, nazwa, strefy)
    surowe = _splaszcz(t.get("kroki") or [])
    if not surowe:
        raise BladPlanu("trening nie ma żadnych kroków")
    if len(surowe) > MAX_KROKOW:
        raise BladPlanu(f"{len(surowe)} kroków to za dużo (limit {MAX_KROKOW})")

    kroki, wiersze, szac = [], [], 0.0
    for nr, k in enumerate(surowe, 1):
        typ = TYP_KROKU.get(_norm(k.get("typ", "")))
        if not typ:
            raise BladPlanu(f"krok {nr}: nieznany typ „{k.get('typ')}” "
                            f"(rozgrzewka, interwal, przerwa, schlodzenie, bieg)")
        ile = sum(1 for p in ("czas", "dystans", "przycisk") if k.get(p) not in (None, False))
        if ile != 1:
            raise BladPlanu(f"krok {nr}: podaj dokładnie jedno z trzech - „czas”, „dystans” albo "
                            f"„przycisk”: true (krok kończony przyciskiem okrążenia)")
        try:
            tgt, v1, v2, opis_celu, sr = _cel(k.get("cel"), sport, strefy, moc, ftp)
            if k.get("czas") is not None:
                end, val = END_TIME, sek(k["czas"])
                if not 1 <= val <= 24 * 3600:
                    raise BladPlanu(f"czas „{k['czas']}” poza zakresem")
                dl = _dl_czas(val)
                szac += val
            elif k.get("dystans") is not None:
                end, val = END_DIST, metry(k["dystans"])
                if not 1 <= val <= 500_000:
                    raise BladPlanu(f"dystans „{k['dystans']}” poza zakresem")
                dl = f"{val / 1000:g} km" if val >= 1000 else f"{val:g} m"
                szac += val / (sr or PREDKOSC_DOMYSLNA.get(sport, 5 / 3.6))
            else:
                end, val, dl = END_LAP, None, "przycisk"
        except BladPlanu as e:
            raise BladPlanu(f"krok {nr}: {e}")
        kroki.append(_krok(nr, typ, end, val, tgt, v1, v2))
        wiersze.append((nr, ETYKIETA[typ], dl, opis_celu))

    w = {
        "workoutName": nazwa,
        "description": str(t.get("opis") or ""),
        "sportType": SPORTY[sport],
        "estimatedDurationInSecs": int(szac),
        "workoutSegments": [{"segmentOrder": 1, "sportType": SPORTY[sport], "workoutSteps": kroki}],
    }
    return w, wiersze


# ----------------------------------------------------------------- trening siłowy

def ciezar_kg(wartosc):
    """'60', '60kg', '62,5 kg', 60 -> 60.0; 'masa ciala' / brak -> None."""
    if wartosc in (None, "", False):
        return None
    t = _norm(wartosc).replace(",", ".").replace(" ", "")
    if t in ("masaciala", "bodyweight", "bw", "brak", "0"):
        return None
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(?:kg)?", t)
    if not m:
        raise BladPlanu(f"nie rozumiem ciężaru „{wartosc}” (użyj np. 60 albo 62,5kg)")
    v = float(m.group(1))
    if not 0 < v <= 1000:
        raise BladPlanu(f"ciężar „{wartosc}” wygląda nierealnie (0-1000 kg)")
    return v


def _zbuduj_silowy(t: dict, nazwa: str, strefy: dict):
    """Trening siłowy: ćwiczenia z katalogu Garmina, serie, powtórzenia, ciężar, przerwy, serie łączone."""
    import cwiczenia

    licznik = [0]          # numer kroku w treningu (stepOrder), rosnący w głąb grup
    wiersze, szac = [], [0.0]
    elementy = []

    def nast():
        licznik[0] += 1
        if licznik[0] > MAX_WEZLOW_SILOWY:
            raise BladPlanu(f"trening ma za dużo kroków (limit {MAX_WEZLOW_SILOWY})")
        return licznik[0]

    def przerwa_krok(nr, sekundy):
        return _krok(nr, "rest", END_TIME, sekundy, TGT_NONE, None, None)

    def grupa(nr, n, dzieci):
        return {"type": "RepeatGroupDTO", "stepOrder": nr, "stepType": STEP["repeat"], "numberOfIterations": n,
                "workoutSteps": dzieci, "endCondition": END_ITER, "endConditionValue": float(n),
                "smartRepeat": False}

    def wiersz(et, dl, cel):
        wiersze.append((len(wiersze) + 1, et, dl, cel))

    def cwiczenie(k, glebokosc):
        nr_cw_ref = k.get("cwiczenie")
        try:
            c = cwiczenia.znajdz(nr_cw_ref, k.get("kategoria"))
        except ValueError as e:
            raise BladPlanu(str(e))
        serie = k.get("serie", 1)
        try:
            serie = int(serie)
        except (TypeError, ValueError):
            raise BladPlanu(f"ćwiczenie „{nr_cw_ref}”: „serie” musi być liczbą")
        if not 1 <= serie <= 50:
            raise BladPlanu(f"ćwiczenie „{nr_cw_ref}”: liczba serii 1-50")
        ma_p, ma_c = k.get("powtorzenia") not in (None, ""), k.get("czas") not in (None, "")
        if ma_p == ma_c:
            raise BladPlanu(f"ćwiczenie „{nr_cw_ref}”: podaj dokładnie jedno z dwóch - „powtorzenia” albo „czas”")
        kg = ciezar_kg(k.get("ciezar"))
        if ma_p:
            try:
                reps = int(k["powtorzenia"])
            except (TypeError, ValueError):
                raise BladPlanu(f"ćwiczenie „{nr_cw_ref}”: „powtorzenia” musi być liczbą (np. 8)")
            if not 1 <= reps <= 1000:
                raise BladPlanu(f"ćwiczenie „{nr_cw_ref}”: powtórzenia 1-1000")
            end, val, dl_jedna, czas_serii = END_REPS, reps, str(reps), reps * 3.5
        else:
            val = sek(k["czas"])
            if not 1 <= val <= 3600:
                raise BladPlanu(f"ćwiczenie „{nr_cw_ref}”: czas serii poza zakresem")
            end, dl_jedna, czas_serii = END_TIME, _dl_czas(val), float(val)
        odp = sek(k["przerwa"]) if k.get("przerwa") not in (None, "", False) else 0
        if odp > 3600:
            raise BladPlanu(f"ćwiczenie „{nr_cw_ref}”: przerwa dłuższa niż godzina")
        opis = []
        if kg:
            opis.append(f"{kg:g} kg")
        if odp:
            opis.append(f"przerwa {_dl_czas(odp)}")
        if c["uwaga"]:
            opis.append(c["uwaga"])
        wiersz(c["name"], f"{serie} x {dl_jedna}", ", ".join(opis))
        szac[0] += serie * (czas_serii + odp)

        def krok_cw(nr):
            st = _krok(nr, "interval", end, val, TGT_NONE, None, None)
            st["category"] = c["category"]
            st["exerciseName"] = c["exercise"]
            if kg:
                st["weightValue"] = kg * 1000.0
                st["weightUnit"] = dict(WAGA_KG)
            return st

        if serie > 1:
            g = nast()
            dzieci = [krok_cw(nast())]
            if odp:
                dzieci.append(przerwa_krok(nast(), odp))
            return grupa(g, serie, dzieci)
        # jedna seria: samo ćwiczenie i ewentualna przerwa (bez grupy powtórzeń)
        return [krok_cw(nast())] + ([przerwa_krok(nast(), odp)] if odp else [])

    def lista(surowe, glebokosc):
        wynik = []
        for k in surowe:
            if not isinstance(k, dict):
                raise BladPlanu("krok musi być obiektem { ... }")
            if "powtorz" in k:
                if glebokosc >= 2:
                    raise BladPlanu("powtórzenia zagnieżdżone zbyt głęboko")
                try:
                    n = int(k["powtorz"])
                except (TypeError, ValueError):
                    raise BladPlanu("„powtorz” musi być liczbą")
                if not 1 <= n <= 50 or not isinstance(k.get("kroki"), list) or not k["kroki"]:
                    raise BladPlanu("„powtorz” wymaga liczby 1-50 i niepustej listy „kroki”")
                g = nast()
                wiersz("Seria łączona", f"{n} x", "ćwiczenia poniżej w rundach")
                szac_przed = szac[0]
                dzieci = lista(k["kroki"], glebokosc + 1)
                szac[0] = szac_przed + (szac[0] - szac_przed) * n
                wynik.append(grupa(g, n, dzieci))
            elif "cwiczenie" in k:
                r = cwiczenie(k, glebokosc)
                wynik.extend(r if isinstance(r, list) else [r])
            else:
                typ = TYP_KROKU.get(_norm(k.get("typ", "")))
                if typ not in ("warmup", "cooldown", "recovery"):
                    raise BladPlanu("krok siłowy to ćwiczenie (pole „cwiczenie”), przerwa/rozgrzewka/schłodzenie "
                                    "(pole „typ” + „czas”) albo blok „powtorz”")
                if typ == "recovery":
                    typ = "rest"
                if k.get("czas") is not None:
                    val = sek(k["czas"])
                    if not 1 <= val <= 24 * 3600:
                        raise BladPlanu(f"czas „{k['czas']}” poza zakresem")
                    end, dl = END_TIME, _dl_czas(val)
                    szac[0] += val
                elif k.get("przycisk"):
                    end, val, dl = END_LAP, None, "przycisk"
                else:
                    raise BladPlanu("krok „typ” w treningu siłowym wymaga „czas” albo „przycisk”: true")
                wiersz(ETYKIETA[typ], dl, "bez celu")
                wynik.append(_krok(nast(), typ, end, val, TGT_NONE, None, None))
        return wynik

    elementy = lista(t.get("kroki") or [], 0)
    if not elementy:
        raise BladPlanu("trening nie ma żadnych kroków")
    w = {
        "workoutName": nazwa,
        "description": str(t.get("opis") or ""),
        "sportType": SPORTY["silowy"],
        "estimatedDurationInSecs": int(szac[0]),
        "workoutSegments": [{"segmentOrder": 1, "sportType": SPORTY["silowy"], "workoutSteps": elementy}],
    }
    return w, wiersze


# ----------------------------------------------------------------- cały plan

def _wyciagnij_json(tekst: str) -> str:
    t = (tekst or "").strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S | re.I)
    if m:
        t = m.group(1).strip()
    a, b = t.find("{"), t.rfind("}")
    if a == -1 or b <= a:
        raise BladPlanu("Nie widzę w tekście planu w formacie JSON (brakuje nawiasów { }). "
                        "Wklej cały wynik od AI albo użyj przycisku z przykładem.")
    return t[a:b + 1]


def _wczytaj_json(tekst: str):
    surowy = _wyciagnij_json(tekst)
    try:
        return json.loads(surowy)
    except json.JSONDecodeError as e:
        proba = surowy.translate({0x201C: '"', 0x201D: '"', 0x201E: '"'})   # cudzysłowy ze Worda/AI
        try:
            return json.loads(proba)
        except json.JSONDecodeError:
            raise BladPlanu(f"To nie jest poprawny JSON: {e.msg} (wiersz {e.lineno}, kolumna {e.colno}). "
                            f"Poproś AI o poprawienie.")


def sprawdz_ftp(wartosc):
    """FTP w watach (liczba 50-600) albo None, gdy nie podano. Rzuca BladPlanu przy nonsensie."""
    if wartosc in (None, ""):
        return None
    try:
        v = float(wartosc)
    except (TypeError, ValueError):
        raise BladPlanu(f"FTP „{wartosc}” musi być liczbą watów, np. 220")
    if not 50 <= v <= 600:
        raise BladPlanu(f"FTP {v:g} W wygląda nierealnie (oczekuję 50-600 W)")
    return v


def normalizuj_strefy(surowe) -> dict:
    """{"bieg": {"s2": [125, 148]}} -> {"bieg": {"S2": [125, 148]}}; rzuca BladPlanu przy błędnym zapisie."""
    wynik = {}
    for sp, z in (surowe or {}).items():
        if not isinstance(z, dict):
            raise BladPlanu(f"strefy dla „{sp}” muszą być obiektem, np. {{\"S2\": [125, 148]}}")
        klucz = SPORT_ALIASY.get(_norm(sp), _norm(sp))
        wynik[klucz] = {k.upper(): v for k, v in z.items()}
        for k, v in wynik[klucz].items():
            if not (isinstance(v, list) and len(v) == 2 and all(isinstance(x, (int, float)) for x in v) and v[0] <= v[1]):
                raise BladPlanu(f"strefa {k} dla „{sp}” musi mieć postać [dół, góra], np. [125, 148]")
    return wynik


def wczytaj(tekst: str, ust: dict | None = None) -> list:
    """Tekst planu -> lista wpisów: {"data", "wolne", "w", "wiersze", "profil"}. Rzuca BladPlanu przy błędzie.

    ust: ustawienia zapisane w programie ({"strefy": ..., "moc": ..., "ftp": ...}); to, co plan podaje sam
    (sekcje "strefy", "moc", pole "ftp"), ma nad nimi pierwszeństwo.
    """
    ust = ust or {}
    plan = _wczytaj_json(tekst)
    if not isinstance(plan, dict):
        raise BladPlanu("Plan musi być obiektem { ... } z listą „treningi”")
    treningi = plan.get("treningi")
    if not isinstance(treningi, list) or not treningi:
        raise BladPlanu("W planie brakuje listy „treningi”")
    if len(treningi) > MAX_TRENINGOW:
        raise BladPlanu(f"Za dużo treningów naraz ({len(treningi)}, limit {MAX_TRENINGOW})")

    strefy = {sp: dict(z) for sp, z in normalizuj_strefy(ust.get("strefy")).items()}   # zapisane w programie
    for sp, z in normalizuj_strefy(plan.get("strefy")).items():                        # strefy z planu mają pierwszeństwo
        strefy.setdefault(sp, {}).update(z)
    moc = {sp: dict(z) for sp, z in normalizuj_strefy(ust.get("moc")).items()}
    for sp, z in normalizuj_strefy(plan.get("moc")).items():
        moc.setdefault(sp, {}).update(z)
    ftp = sprawdz_ftp(plan.get("ftp") if plan.get("ftp") is not None else ust.get("ftp"))

    wyniki, nazwy = [], set()
    for i, t in enumerate(treningi, 1):
        if not isinstance(t, dict):
            raise BladPlanu(f"Trening {i}: musi być obiektem { ... }")
        data = None
        if t.get("data"):
            try:
                data = datetime.date.fromisoformat(str(t["data"]))
            except ValueError:
                raise BladPlanu(f"Trening {i}: zła data „{t['data']}” (format RRRR-MM-DD)")
        if t.get("wolne"):
            wyniki.append({"data": data, "wolne": True})
            continue
        try:
            w, wiersze = zbuduj(t, strefy, moc, ftp)
        except BladPlanu as e:
            raise BladPlanu(f"Trening {i} ({t.get('nazwa') or t.get('data') or '?'}): {e}")
        if w["workoutName"] in nazwy:
            raise BladPlanu(f"Dwa treningi mają tę samą nazwę „{w['workoutName']}” - nazwy muszą być "
                            f"unikalne (dopisz do nich datę)")
        nazwy.add(w["workoutName"])
        sport = _sport(t)
        pr = profil(w, _tabela_hr(strefy, sport), moc.get(sport), ftp)
        poziomy = [0] * len(wiersze) if sport == "silowy" else [p[1] for p in pr]
        wyniki.append({"data": data, "wolne": False, "w": w, "wiersze": wiersze, "profil": pr,
                       "poziomy": poziomy, "sport": sport})
    wyniki.sort(key=lambda x: (x["data"] is None, x["data"] or datetime.date.max))
    return wyniki


def _rozwin(kroki, mnoznik=1):
    """Kroki z grupami powtórzeń -> płaska lista (krok, ile razy)."""
    for k in kroki:
        if k.get("type") == "RepeatGroupDTO":
            yield from _rozwin(k["workoutSteps"], mnoznik * int(k["numberOfIterations"]))
        else:
            yield k, mnoznik


def profil(w: dict, strefy_sportu: dict | None, moc_sportu: dict | None = None, ftp: float | None = None) -> list:
    """Lista (czas w sekundach, poziom 0-5, id typu kroku) - do narysowania wykresu intensywności w oknie.

    Poziom 1-5 wynika ze strefy tętna lub mocy (jeśli plan ją opisuje) albo z typu kroku; poziom 0 oznacza
    krok bez intensywności (np. ćwiczenie siłowe, joga) - rysowany na szaro. Grupy powtórzeń są rozwijane.
    """
    klucz = w["sportType"]["sportTypeKey"]
    neutralny = klucz in ("strength_training", "cardio_training", "yoga", "pilates", "hiit", "mobility", "other")
    domysl = {"running": PREDKOSC_DOMYSLNA["bieg"], "cycling": PREDKOSC_DOMYSLNA["rower"],
              "swimming": PREDKOSC_DOMYSLNA["plywanie"], "walking": PREDKOSC_DOMYSLNA["marsz"],
              "hiking": PREDKOSC_DOMYSLNA["turystyka"]}.get(klucz, 5 / 3.6)
    bazowy = {1: 2, 2: 1, 3: 4, 4: 1, 5: 1}               # rozgrzewka, schłodzenie, interwał, przerwa, odpoczynek
    out = []
    for k, ile in _rozwin(w["workoutSegments"][0]["workoutSteps"]):
        v1, v2 = k.get("targetValueOne"), k.get("targetValueTwo")
        warunek = k["endCondition"]["conditionTypeKey"]
        tgt = k["targetType"]["workoutTargetTypeKey"]
        if warunek == "time":
            sekundy = k["endConditionValue"]
        elif warunek == "reps":
            sekundy = k["endConditionValue"] * 3.5
        elif warunek == "distance":
            sr = (v1 + v2) / 2 if tgt == "pace.zone" and v1 and v2 else domysl
            sekundy = k["endConditionValue"] / sr
        else:
            sekundy = 300.0                                  # krok kończony przyciskiem: umowna długość na wykresie
        poziom = 0 if (neutralny and tgt == "no.target") else bazowy.get(k["stepType"]["stepTypeId"], 2)
        if tgt == "heart.rate.zone" and v1 is not None and strefy_sportu:
            sr_hr = (v1 + v2) / 2
            for nr in range(1, 6):
                z = strefy_sportu.get(f"S{nr}")
                if z and z[0] <= sr_hr <= z[1]:
                    poziom = nr
                    break
        if tgt == "power.zone" and v1 is not None:
            sr_w = (v1 + v2) / 2
            znaleziono = False
            for nr in range(1, 6):
                z = (moc_sportu or {}).get(f"P{nr}")
                if z and z[0] <= sr_w <= z[1]:
                    poziom, znaleziono = nr, True
                    break
            if not znaleziono and ftp:                       # progi Coggana: <56, 56-75, 76-90, 91-105, >105 % FTP
                pr = sr_w / ftp * 100
                poziom = 1 if pr < 56 else 2 if pr <= 75 else 3 if pr <= 90 else 4 if pr <= 105 else 5
        out.append((float(sekundy) * ile, poziom, k["stepType"]["stepTypeId"]))
    return out


def pokaz(wyniki: list) -> str:
    """Czytelny podgląd planu - to, co użytkownik sprawdza przed wysłaniem."""
    out = []
    for x in wyniki:
        d = x["data"]
        kiedy = f"{DNI[d.weekday()]} {d.strftime('%d.%m.%Y')}" if d else "bez daty (tylko lista Treningi)"
        if x["wolne"]:
            out.append(f"=== {kiedy} - wolne ===\n")
            continue
        w = x["w"]
        m = w["estimatedDurationInSecs"] // 60
        out.append(f"=== {kiedy} - {w['workoutName']} ({w['sportType']['sportTypeKey']}, "
                   f"ok. {m // 60} h {m % 60:02d} min) ===")
        szer = max(13, max((len(r[1]) for r in x["wiersze"]), default=0) + 2)
        out.append(f"  {'#':<3}{'Krok':<{szer}}{'Długość':<10}Cel")
        for nr, et, dl, cel in x["wiersze"]:
            out.append(f"  {nr:<3}{et:<{szer}}{dl:<10}{cel}")
        out.append("")
    n = sum(1 for x in wyniki if not x["wolne"])
    out.append(f"Treningów do wysłania: {n}")
    return "\n".join(out)


# ----------------------------------------------------------------- prompt dla AI i przykład

PROMPT_AI = """Zamień rozpiskę treningową od trenera (na końcu tej wiadomości) na plik JSON dla programu "Garmin Plan Loader".

ZANIM ZACZNIESZ
Jeśli brakuje Ci informacji potrzebnych do poprawnego planu (moich stref tętna, roku, dat treningów, sportu), ZAPYTAJ mnie. Niczego nie zgaduj i nie wymyślaj stref.

CO ZWRÓCIĆ
Jeden blok kodu JSON w poniższym formacie. Poza blokiem możesz dodać najwyżej 1-2 zdania uwag (np. co pominąłeś).

FORMAT
{
  "strefy": { "bieg": { "S1": [80, 124], "S2": [125, 148], "S3": [149, 170], "S4": [171, 182], "S5": [183, 196] } },
  "treningi": [
    {
      "data": "RRRR-MM-DD",
      "nazwa": "02.10 Bieg rozluźniający",
      "sport": "bieg",
      "opis": "krótki opis (opcjonalnie)",
      "kroki": [
        {"typ": "rozgrzewka", "czas": "15:00", "cel": "S2"},
        {"powtorz": 3, "kroki": [
          {"typ": "interwal", "dystans": "3km", "cel": "4:10-4:15"},
          {"typ": "przerwa", "czas": "3:00"}
        ]},
        {"typ": "schlodzenie", "czas": "10:00", "cel": "S2"}
      ]
    },
    {"data": "RRRR-MM-DD", "wolne": true}
  ]
}

ZASADY
- "sport": bieg, rower, plywanie, silowy, cardio, joga, pilates, hiit, mobilnosc, marsz, turystyka albo inny. Wielosportu (triathlon w jednym pliku) program nie obsługuje - rozbij go na osobne treningi.
- Każdy krok (poza treningiem siłowym) ma dokładnie jedno z trzech: "czas" ("40:00", "1:30:00" albo "40min") ALBO "dystans" ("3km", "800m") ALBO "przycisk": true (krok trwa do naciśnięcia przycisku okrążenia, np. w jodze).
- "typ": rozgrzewka, interwal, przerwa, schlodzenie, bieg (zwykły krok).
- "cel" (opcjonalny): strefa ("S2", z sekcji "strefy"), tętno ("HR 125-148"), tempo w min/km ("4:20-4:25", bieg, marsz, turystyka) albo w min/100 m ("1:50-2:00", pływanie), kadencja ("90-100rpm"), moc, tylko rower ("200-220W" albo "90-95% FTP", gdy trener podaje procent FTP - program przeliczy go na waty; "P2" to strefa mocy z moich stref). Bez pola "cel" krok nie ma celu.
- "powtorz": liczba powtórzeń bloku kroków. Przerwę między powtórzeniami dodawaj tylko tam, gdzie trener ją przewidział (nie po ostatnim powtórzeniu, jeśli dalej jest schłodzenie).
TRENING SIŁOWY ("sport": "silowy") - kroki to ćwiczenia, a nie czas/dystans:
  {"cwiczenie": "Barbell Back Squat", "serie": 4, "powtorzenia": 8, "ciezar": "60kg", "przerwa": "2:00"}
  - zamiast "powtorzenia" można podać "czas" (np. plank: {"cwiczenie": "Plank", "serie": 3, "czas": "0:45", "przerwa": "1:00"}),
  - "ciezar" jest opcjonalny (pomiń przy ćwiczeniach z masą ciała), "przerwa" to odpoczynek po każdej serii,
  - serie łączone / obwód: {"powtorz": 3, "kroki": [ćwiczenie, ćwiczenie, ...]} - kolejne ćwiczenia w rundach,
  - rozgrzewka i schłodzenie: {"typ": "rozgrzewka", "czas": "8:00"}.
  - Nazwy ćwiczeń podawaj po angielsku dokładnie tak, jak w katalogu Garmin Connect (np. Barbell Back Squat, Romanian Deadlift, Barbell Bench Press, Pull-up, Lat Pull-down, Dumbbell Row, Plank, Goblet Squat). Program zna też popularne polskie nazwy (przysiad, martwy ciąg, podciąganie). Jeśli nie jesteś pewien nazwy, wpisz ją i dodaj pole "kategoria" (np. SQUAT, DEADLIFT, BENCH_PRESS, ROW, PULL_UP, SHOULDER_PRESS, CURL, PLANK, LUNGE, CORE), a program użyje samej kategorii.
- Nazwa każdego treningu musi być unikalna - dopisz do niej datę (np. "04.10 Long 90 min").
- Dzień wolny: {"data": "RRRR-MM-DD", "wolne": true}.
- Jeśli rozpiska podaje zakres czasu (np. 35-40 min), wybierz górną granicę i dopisz zakres w "opis".
- Jeśli rozpiska podaje łączny czas ("long 90 min, w środku 6 km w tempie ..., reszta w S2"), policz czasy kroków tak, by suma dała ten czas przy środku podanego zakresu tempa.
- Jeśli strefa ma tylko górną granicę, jako dolną wpisz 80 (bieg) albo 70 (rower).
- Nie zmieniaj założeń trenera (tempa, moc, tętno, dystanse, czasy).

MOJE STREFY TĘTNA - bieg: [WPISZ, np. S1 do 124, S2 125-148, S3 149-170, S4 171-182, S5 183-196]
MOJE STREFY TĘTNA - rower: [WPISZ albo usuń tę linię, jeśli nie dotyczy]
MOJE STREFY TĘTNA - pływanie: [WPISZ albo usuń tę linię, jeśli nie dotyczy]
MOJE FTP (rower, w watach): [WPISZ albo usuń tę linię, jeśli nie dotyczy]
ROK I DATY: [WPISZ rok albo pełne daty treningów]

ROZPISKA OD TRENERA:
[WKLEJ TUTAJ]
"""

PRZYKLAD_PLANU = """{
  "strefy": {
    "bieg": { "S1": [80, 124], "S2": [125, 148], "S3": [149, 170], "S4": [171, 182], "S5": [183, 196] }
  },
  "treningi": [
    {
      "nazwa": "Przykład - bieg 40 min w S2",
      "sport": "bieg",
      "opis": "Przykładowy trening: nie ma daty, więc trafi tylko na listę Treningi.",
      "kroki": [
        { "typ": "bieg", "czas": "40:00", "cel": "S2" }
      ]
    },
    {
      "nazwa": "Przykład - 3 x 3 km w tempie",
      "sport": "bieg",
      "kroki": [
        { "typ": "rozgrzewka", "czas": "15:00", "cel": "S2" },
        { "powtorz": 2, "kroki": [
          { "typ": "interwal", "dystans": "3km", "cel": "4:10-4:15" },
          { "typ": "przerwa", "czas": "3:00" }
        ] },
        { "typ": "interwal", "dystans": "3km", "cel": "4:10-4:15" },
        { "typ": "schlodzenie", "czas": "12:00", "cel": "S2" }
      ]
    },
    {
      "nazwa": "Przykład - siła: nogi i plecy",
      "sport": "silowy",
      "kroki": [
        { "typ": "rozgrzewka", "czas": "8:00" },
        { "cwiczenie": "Barbell Back Squat", "serie": 4, "powtorzenia": 6, "ciezar": "70kg", "przerwa": "2:00" },
        { "cwiczenie": "Romanian Deadlift", "serie": 3, "powtorzenia": 8, "ciezar": "60kg", "przerwa": "1:30" },
        { "powtorz": 3, "kroki": [
          { "cwiczenie": "Pull-up", "powtorzenia": 6 },
          { "cwiczenie": "Plank", "czas": "0:45", "przerwa": "1:00" }
        ] }
      ]
    }
  ]
}
"""


def prompt_ai(ust: dict | None = None) -> str:
    """Prompt dla AI. Co program ma zapisane (strefy tętna, FTP, strefy mocy), o to AI nie pyta."""
    ust = ust or {}
    hr = {sp: z for sp, z in normalizuj_strefy(ust.get("strefy")).items() if z}
    moc = {sp: z for sp, z in normalizuj_strefy(ust.get("moc")).items() if z}
    ftp = sprawdz_ftp(ust.get("ftp"))
    t = PROMPT_AI

    def zamien(stary, nowy):
        nonlocal t
        assert stary in t, stary
        t = t.replace(stary, nowy)

    if hr:
        zamien("(moich stref tętna, roku, dat treningów, sportu)", "(roku, dat treningów, sportu)")
        zamien('  "strefy": { "bieg": { "S1": [80, 124], "S2": [125, 148], "S3": [149, 170], "S4": [171, 182], "S5": [183, 196] } },\n', "")
        zamien('strefa ("S2", z sekcji "strefy")', 'strefa ("S2" - program zna moje strefy, wystarczy numer)')
        zamien("- Jeśli strefa ma tylko górną granicę, jako dolną wpisz 80 (bieg) albo 70 (rower).\n", "")
        a = t.index("MOJE STREFY TĘTNA - bieg")
        b = t.index("MOJE FTP")
        linie = [f"- {sp}: " + ", ".join(f"{k} {v[0]:g}-{v[1]:g}" for k, v in sorted(hr[sp].items()))
                 for sp in ("bieg", "rower", "plywanie") if sp in hr]
        blok = ("MOJE STREFY TĘTNA (program ma je zapisane i sam je doda - NIE dodawaj sekcji \"strefy\" do JSON):\n"
                + "\n".join(linie)
                + "\nJeśli trener nazywa strefy inaczej (np. Z2, strefa 2), przypisz je wg numeru do S1-S5 "
                  "i w polu \"cel\" pisz samo \"S2\" itd.\n")
        t = t[:a] + blok + t[b:]
    if ftp or moc:
        a = t.index("MOJE FTP")
        b = t.index("ROK I DATY")
        linie = []
        if ftp:
            linie.append(f"- FTP: {ftp:g} W (program sam przeliczy procenty FTP na waty)")
        if "rower" in moc:
            linie.append("- strefy mocy (rower): " + ", ".join(
                f"{k} {v[0]:g}-{v[1]:g} W" for k, v in sorted(moc["rower"].items())))
        blok = ("MOJE FTP I STREFY MOCY (program ma je zapisane - nie pytaj o nie):\n" + "\n".join(linie) + "\n"
                "Gdy trener podaje moc w procentach FTP, wpisz je wprost w polu \"cel\" (np. \"90-95% FTP\"), "
                "NIE przeliczaj ich na waty. Strefy mocy zapisuj jako \"P1\"-\"P5\".\n")
        t = t[:a] + blok + t[b:]
    return t
