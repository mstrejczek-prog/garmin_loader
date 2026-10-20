# -*- coding: utf-8 -*-
"""
Wyszukiwanie ćwiczeń siłowych w katalogu Garmin Connect (ok. 1500 ćwiczeń, nazwy angielskie).

Plan może podawać nazwę z Garmin Connect ("Barbell Back Squat"), popularną polską nazwę ("przysiad ze sztangą")
albo samą kategorię Garmina ("kategoria": "ROW"), gdy dokładnego ćwiczenia w katalogu nie ma.
"""
from __future__ import annotations

import difflib

# polskie nazwy (bez ogonków, małymi literami) -> nazwa w katalogu Garmina
ALIASY_PL = {
    "przysiad": "Barbell Back Squat",
    "przysiad ze sztanga": "Barbell Back Squat",
    "przysiad ze sztanga na plecach": "Barbell Back Squat",
    "przysiad przedni": "Barbell Front Squat",
    "przysiad goblet": "Goblet Squat",
    "przysiad bulgarski": "Dumbbell Bulgarian Split Squat",
    "przysiad z wyskokiem": "Jump Squat",
    "martwy ciag": "Barbell Deadlift",
    "martwy ciag rumunski": "Romanian Deadlift",
    "rdl": "Romanian Deadlift",
    "martwy ciag sumo": "Sumo Deadlift",
    "wyciskanie lezac": "Barbell Bench Press",
    "wyciskanie sztangi lezac": "Barbell Bench Press",
    "wyciskanie sztangi na lawce skosnej": "Incline Barbell Bench Press",
    "wyciskanie hantli lezac": "Dumbbell Bench Press",
    "wyciskanie hantli na lawce skosnej": "Incline Dumbbell Bench Press",
    "wyciskanie zolnierskie": "Barbell Overhead Press",
    "wyciskanie nad glowe": "Barbell Overhead Press",
    "ohp": "Barbell Overhead Press",
    "push press": "Barbell Push Press",
    "arnoldki": "Arnold Press",
    "podciaganie": "Pull-up",
    "podciaganie nachwytem": "Pull-up",
    "podciaganie podchwytem": "Chin-up",
    "sciaganie drazka": "Lat Pull-down",
    "sciaganie drazka wyciagu": "Lat Pull-down",
    "wioslowanie sztanga": "Barbell Row",
    "wioslowanie hantla": "Dumbbell Row",
    "wioslowanie hantlem": "Dumbbell Row",
    "wioslowanie na wyciagu": "Seated Cable Row",
    "face pull": "Face Pull",
    "uginanie ramion ze sztanga": "Barbell Biceps Curl",
    "uginanie ramion z hantlami": "Dumbbell Biceps Curl",
    "prostowanie ramion nad glowa": "Overhead Dumbbell Triceps Extension",
    "pompki": "Push-up",
    "dipy": "Body-weight Dip",
    "wznosy bokiem": "Dumbbell Lateral Raise",
    "rozpietki": "Dumbbell Fly",
    "hip thrust": "Barbell Hip Thrust with Bench",
    "wykroki chodzone": "Walking Lunge",
    "wejscia na skrzynie": "Dumbbell Step-up",
    "step up": "Step-up",
    "suwnica": "Leg Press",
    "wyciskanie na suwnicy": "Leg Press",
    "uginanie nog": "Leg Curl",
    "prostowanie nog": "Leg Extensions",
    "wspiecia na palce": "Standing Calf Raise",
    "wspiecia na palce jednonoz": "Single-leg Standing Calf Raise",
    "plank": "Plank",
    "deska": "Plank",
    "deska boczna": "Side Plank",
    "dead bug": "Dead Bug",
    "russian twist": "Russian Twist",
    "unoszenie nog w zwisie": "Hanging Leg Raise",
    "spacer farmera": "Farmer's Walk",
    "skok na skrzynie": "Box Jump",
    "swing kettlebell": "Kettlebell Swing",
    "szrugsy": "Barbell Shrug",
    "wyprosty grzbietu": "Hyperextension",
    "burpees": "Burpee",
    "wspinaczka": "Mountain Climber",
}


def _norm(t) -> str:
    return " ".join(str(t).strip().lower().translate(str.maketrans("ąćęłńóśźż", "acelnoszz")).replace("-", " ").split())


def _katalog():
    from garminconnect import exercises
    return exercises


def znajdz(nazwa: str, kategoria: str | None = None) -> dict:
    """-> {"name", "category", "exercise", "uwaga"}; rzuca ValueError z komunikatem po polsku."""
    k = _katalog()
    nazwa = str(nazwa or "").strip()
    if not nazwa:
        raise ValueError("ćwiczenie bez nazwy (pole „cwiczenie”)")
    wolna = {e["name"].lower(): e for e in k.EXERCISES}
    n = _norm(nazwa)

    wynik = wolna.get(nazwa.lower())                                    # 1. dokładna nazwa z katalogu
    if not wynik and n in ALIASY_PL:                                    # 2. polska nazwa
        wynik = k.BY_NAME.get(ALIASY_PL[n])
    if not wynik:                                                       # 3. nazwa angielska z inną pisownią (myślniki)
        trafione = [e for e in k.EXERCISES if _norm(e["name"]) == n]
        wynik = trafione[0] if len(trafione) == 1 else None
    if not wynik:                                                       # 4. nazwa techniczna, np. BARBELL_BACK_SQUAT
        techn = nazwa.upper().replace(" ", "_")
        trafione = [e for e in k.EXERCISES if e["exercise"] == techn]
        if len(trafione) == 1:
            wynik = trafione[0]
    kandydaci = []
    if not wynik:                                                       # 5. fragment nazwy
        kandydaci = [e for e in k.EXERCISES if n in _norm(e["name"])]
        if len(kandydaci) == 1:
            wynik, kandydaci = kandydaci[0], []
    if wynik:
        return {"name": wynik["name"], "category": wynik["category"], "exercise": wynik["exercise"], "uwaga": None}

    if kategoria:
        kat = str(kategoria).strip().upper().replace(" ", "_")
        if kat not in k.CATEGORIES:
            raise ValueError(f"nieznana kategoria „{kategoria}” (przykłady: SQUAT, DEADLIFT, BENCH_PRESS, ROW, "
                             f"PULL_UP, SHOULDER_PRESS, CURL, PLANK, CORE, LUNGE)")
        return {"name": f"{nazwa} (kategoria {kat})", "category": kat, "exercise": "",
                "uwaga": f"Ćwiczenie: {nazwa} (w Garmin tylko kategoria {kat})"}

    podobne = [e["name"] for e in kandydaci[:6]]
    if not podobne:
        podobne = difflib.get_close_matches(nazwa, [e["name"] for e in k.EXERCISES], n=4, cutoff=0.6)
    wskazowka = (f" Podobne: {', '.join(podobne)}." if podobne else "")
    powod = "pasuje do wielu ćwiczeń" if kandydaci else "nie ma go w katalogu Garmin"
    raise ValueError(
        f"ćwiczenie „{nazwa}” {powod}.{wskazowka} Wpisz nazwę po angielsku dokładnie jak w Garmin Connect "
        f"(np. Barbell Back Squat) albo dodaj do ćwiczenia pole \"kategoria\", np. \"kategoria\": \"ROW\".")
