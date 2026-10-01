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
SPORTY = {
    "bieg": {"sportTypeId": 1, "sportTypeKey": "running", "displayOrder": 1},
    "rower": {"sportTypeId": 2, "sportTypeKey": "cycling", "displayOrder": 2},
}
SPORT_ALIASY = {
    "bieg": "bieg", "bieganie": "bieg", "run": "bieg", "running": "bieg",
    "rower": "rower", "kolarstwo": "rower", "bike": "rower", "cycling": "rower", "ride": "rower",
}
STEP = {
    "warmup": {"stepTypeId": 1, "stepTypeKey": "warmup", "displayOrder": 1},
    "cooldown": {"stepTypeId": 2, "stepTypeKey": "cooldown", "displayOrder": 2},
    "interval": {"stepTypeId": 3, "stepTypeKey": "interval", "displayOrder": 3},
    "recovery": {"stepTypeId": 4, "stepTypeKey": "recovery", "displayOrder": 4},
}
TYP_KROKU = {
    "rozgrzewka": "warmup", "warmup": "warmup", "wu": "warmup",
    "schlodzenie": "cooldown", "chlodzenie": "cooldown", "cooldown": "cooldown", "cd": "cooldown",
    "interwal": "interval", "interval": "interval", "praca": "interval", "work": "interval",
    "bieg": "interval", "jazda": "interval", "inny": "interval", "steady": "interval",
    "przerwa": "recovery", "recovery": "recovery", "rest": "recovery", "odpoczynek": "recovery",
    "trucht": "recovery", "marsz": "recovery",
}
ETYKIETA = {"warmup": "Rozgrzewka", "cooldown": "Schłodzenie", "interval": "Praca", "recovery": "Przerwa"}

END_TIME = {"conditionTypeId": 2, "conditionTypeKey": "time", "displayOrder": 2, "displayable": True}
END_DIST = {"conditionTypeId": 3, "conditionTypeKey": "distance", "displayOrder": 3, "displayable": True}
TGT_NONE = {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target", "displayOrder": 1}
TGT_POWER = {"workoutTargetTypeId": 2, "workoutTargetTypeKey": "power.zone", "displayOrder": 2}
TGT_HR = {"workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.zone", "displayOrder": 4}
TGT_PACE = {"workoutTargetTypeId": 6, "workoutTargetTypeKey": "pace.zone", "displayOrder": 6}

MAX_KROKOW = 50
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


def sek_na_tempo(mps: float) -> str:
    x = round(1000.0 / mps)
    return f"{x // 60}:{x % 60:02d}"


def _cel(cel, sport, strefy):
    """-> (typ celu, wartość1, wartość2, opis tekstowy, średnia prędkość m/s albo None)"""
    if cel is None or _norm(cel) in ("", "brak", "-", "bez celu"):
        return TGT_NONE, None, None, "bez celu", None
    c = _norm(cel)

    # strefa z sekcji "strefy", np. S2
    m = re.fullmatch(r"s([1-5])", c)
    if m:
        z = (strefy.get(sport) or {}).get("S" + m.group(1))
        if not z:
            raise BladPlanu(f"brak strefy S{m.group(1)} dla sportu „{sport}” w sekcji „strefy” planu")
        return TGT_HR, z[0], z[1], f"tętno {z[0]}-{z[1]} (S{m.group(1)})", None

    # tempo m:ss-m:ss (także zapis 4'20''-4'25'' i końcówka /km)
    ct = c.replace("''", "").replace('"', "").replace("'", ":").replace("/km", "").replace(" ", "")
    m = re.fullmatch(r"(\d+):(\d\d)-(\d+):(\d\d)", ct)
    if m:
        if sport != "bieg":
            raise BladPlanu(f"cel tempa „{cel}” ma sens tylko dla biegu")
        a, b = _tempo_sek(m.group(1), m.group(2)), _tempo_sek(m.group(3), m.group(4))
        if not (90 <= a <= 900 and 90 <= b <= 900):
            raise BladPlanu(f"tempo „{cel}” wygląda nierealnie (oczekuję min/km, np. 4:20-4:25)")
        v1, v2 = sorted([tempo_na_mps(a), tempo_na_mps(b)])
        # Garmin: wartość pierwsza = wolniejsze tempo (mniej m/s), druga = szybsze
        return TGT_PACE, round(v1, 4), round(v2, 4), f"tempo {sek_na_tempo(v2)}-{sek_na_tempo(v1)} /km", (v1 + v2) / 2

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
        f"nie rozumiem celu „{cel}” (użyj np. S2, HR 125-148, 4:20-4:25, 200-220W albo pomiń cel)")


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
        "endConditionValue": float(end_val),
        "targetType": target,
        "targetValueOne": v1,
        "targetValueTwo": v2,
        "zoneNumber": None,
    }


def zbuduj(t: dict, strefy: dict):
    """Jeden trening z planu -> (dane treningu dla Garmina, wiersze podglądu)."""
    nazwa = t.get("nazwa")
    if not nazwa or not str(nazwa).strip():
        raise BladPlanu("trening bez nazwy (pole „nazwa”)")
    sport = SPORT_ALIASY.get(_norm(t.get("sport", "bieg")))
    if not sport:
        raise BladPlanu(f"sport „{t.get('sport')}” nie jest obsługiwany (na razie: bieg, rower)")
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
        ma_czas, ma_dyst = "czas" in k, "dystans" in k
        if ma_czas == ma_dyst:
            raise BladPlanu(f"krok {nr}: podaj dokładnie jedno z dwóch - „czas” albo „dystans”")
        try:
            tgt, v1, v2, opis_celu, sr = _cel(k.get("cel"), sport, strefy)
            if ma_czas:
                end, val = END_TIME, sek(k["czas"])
                if not 1 <= val <= 24 * 3600:
                    raise BladPlanu(f"czas „{k['czas']}” poza zakresem")
                dl = f"{val // 3600}:{val % 3600 // 60:02d}:{val % 60:02d}" if val >= 3600 else f"{val // 60}:{val % 60:02d}"
                szac += val
            else:
                end, val = END_DIST, metry(k["dystans"])
                if not 1 <= val <= 500_000:
                    raise BladPlanu(f"dystans „{k['dystans']}” poza zakresem")
                dl = f"{val / 1000:g} km" if val >= 1000 else f"{val:g} m"
                v_sr = sr or (1000 / 300.0 if sport == "bieg" else 30 / 3.6)  # bez tempa: 5:00/km albo 30 km/h
                szac += val / v_sr
        except BladPlanu as e:
            raise BladPlanu(f"krok {nr}: {e}")
        kroki.append(_krok(nr, typ, end, val, tgt, v1, v2))
        wiersze.append((nr, ETYKIETA[typ], dl, opis_celu))

    w = {
        "workoutName": str(nazwa).strip(),
        "description": str(t.get("opis") or ""),
        "sportType": SPORTY[sport],
        "estimatedDurationInSecs": int(szac),
        "workoutSegments": [{"segmentOrder": 1, "sportType": SPORTY[sport], "workoutSteps": kroki}],
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


def wczytaj(tekst: str) -> list:
    """Tekst planu -> lista wpisów: {"data", "wolne", "w", "wiersze"}. Rzuca BladPlanu przy błędzie."""
    plan = _wczytaj_json(tekst)
    if not isinstance(plan, dict):
        raise BladPlanu("Plan musi być obiektem { ... } z listą „treningi”")
    treningi = plan.get("treningi")
    if not isinstance(treningi, list) or not treningi:
        raise BladPlanu("W planie brakuje listy „treningi”")
    if len(treningi) > MAX_TRENINGOW:
        raise BladPlanu(f"Za dużo treningów naraz ({len(treningi)}, limit {MAX_TRENINGOW})")

    strefy = {}
    for s, z in (plan.get("strefy") or {}).items():
        if not isinstance(z, dict):
            raise BladPlanu(f"strefy dla „{s}” muszą być obiektem, np. {{\"S2\": [125, 148]}}")
        strefy[SPORT_ALIASY.get(_norm(s), _norm(s))] = {k.upper(): v for k, v in z.items()}
        for k, v in strefy[SPORT_ALIASY.get(_norm(s), _norm(s))].items():
            if not (isinstance(v, list) and len(v) == 2 and all(isinstance(x, (int, float)) for x in v) and v[0] <= v[1]):
                raise BladPlanu(f"strefa {k} dla „{s}” musi mieć postać [dół, góra], np. [125, 148]")

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
            w, wiersze = zbuduj(t, strefy)
        except BladPlanu as e:
            raise BladPlanu(f"Trening {i} ({t.get('nazwa') or t.get('data') or '?'}): {e}")
        if w["workoutName"] in nazwy:
            raise BladPlanu(f"Dwa treningi mają tę samą nazwę „{w['workoutName']}” - nazwy muszą być "
                            f"unikalne (dopisz do nich datę)")
        nazwy.add(w["workoutName"])
        sport = SPORT_ALIASY.get(_norm(t.get("sport", "bieg")))
        wyniki.append({"data": data, "wolne": False, "w": w, "wiersze": wiersze,
                       "profil": profil(w, strefy.get(sport))})
    wyniki.sort(key=lambda x: (x["data"] is None, x["data"] or datetime.date.max))
    return wyniki


def profil(w: dict, strefy_sportu: dict | None) -> list:
    """Lista (czas w sekundach, poziom 1-5, etykieta kroku) - do narysowania wykresu intensywności w oknie.

    Poziom wynika ze strefy tętna (jeśli plan ją opisuje), a dla tempa i mocy z typu kroku.
    """
    bazowy = {1: 2, 2: 1, 3: 4, 4: 1}                      # rozgrzewka, schłodzenie, interwał, przerwa
    out = []
    for k in w["workoutSegments"][0]["workoutSteps"]:
        v1, v2 = k.get("targetValueOne"), k.get("targetValueTwo")
        if k["endCondition"]["conditionTypeKey"] == "time":
            sekundy = k["endConditionValue"]
        else:
            sr = (v1 + v2) / 2 if k["targetType"]["workoutTargetTypeKey"] == "pace.zone" and v1 and v2 else 1000 / 300.0
            sekundy = k["endConditionValue"] / sr
        poziom = bazowy.get(k["stepType"]["stepTypeId"], 2)
        if k["targetType"]["workoutTargetTypeKey"] == "heart.rate.zone" and v1 is not None and strefy_sportu:
            sr_hr = (v1 + v2) / 2
            for nr in range(1, 6):
                z = strefy_sportu.get(f"S{nr}")
                if z and z[0] <= sr_hr <= z[1]:
                    poziom = nr
                    break
        out.append((float(sekundy), poziom, k["stepType"]["stepTypeId"]))
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
        out.append(f"  {'#':<3}{'Krok':<13}{'Długość':<10}Cel")
        for nr, et, dl, cel in x["wiersze"]:
            out.append(f"  {nr:<3}{et:<13}{dl:<10}{cel}")
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
- "sport": "bieg" albo "rower". Pływania i innych sportów program nie obsługuje - pomiń je i napisz mi o tym.
- Każdy krok ma dokładnie jedno z dwóch: "czas" ("40:00", "1:30:00" albo "40min") ALBO "dystans" ("3km", "800m").
- "typ": rozgrzewka, interwal, przerwa, schlodzenie, bieg (zwykły krok).
- "cel" (opcjonalny): strefa ("S2", z sekcji "strefy"), tętno ("HR 125-148"), tempo w min/km ("4:20-4:25", tylko bieg), moc ("200-220W", tylko rower). Bez pola "cel" krok nie ma celu.
- "powtorz": liczba powtórzeń bloku kroków. Przerwę między powtórzeniami dodawaj tylko tam, gdzie trener ją przewidział (nie po ostatnim powtórzeniu, jeśli dalej jest schłodzenie).
- Nazwa każdego treningu musi być unikalna - dopisz do niej datę (np. "04.10 Long 90 min").
- Dzień wolny: {"data": "RRRR-MM-DD", "wolne": true}.
- Jeśli rozpiska podaje zakres czasu (np. 35-40 min), wybierz górną granicę i dopisz zakres w "opis".
- Jeśli rozpiska podaje łączny czas ("long 90 min, w środku 6 km w tempie ..., reszta w S2"), policz czasy kroków tak, by suma dała ten czas przy środku podanego zakresu tempa.
- Jeśli strefa ma tylko górną granicę, jako dolną wpisz 80 (bieg) albo 70 (rower).
- Nie zmieniaj założeń trenera (tempa, moc, tętno, dystanse, czasy).

MOJE STREFY TĘTNA - bieg: [WPISZ, np. S1 do 124, S2 125-148, S3 149-170, S4 171-182, S5 183-196]
MOJE STREFY TĘTNA - rower: [WPISZ albo usuń tę linię, jeśli nie dotyczy]
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
    }
  ]
}
"""
