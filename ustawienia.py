# -*- coding: utf-8 -*-
"""
Ustawienia użytkownika: strefy tętna, FTP i strefy mocy roweru.

W wersji webowej trzyma je przeglądarka (localStorage na danym urządzeniu) i dołącza do żądań; serwer tylko
je sprawdza i normalizuje (czyste()), niczego nie zapisuje.
Format: {"strefy": {"bieg": {"S1": [80, 124], ...}, "rower": {...}},
         "moc": {"rower": {"P1": [0, 118], ...}}, "ftp": 215}
"""
from __future__ import annotations

import plan

NAZWY_STREF = ("S1", "S2", "S3", "S4", "S5")
NAZWY_MOCY = ("P1", "P2", "P3", "P4", "P5")
# domyślne progi stref mocy w % FTP (pięć pierwszych stref wg Coggana); użytkownik może je poprawić
PROCENTY_MOCY = ((0, 55), (56, 75), (76, 90), (91, 105), (106, 120))


def puste() -> dict:
    return {"strefy": {}, "moc": {}, "ftp": None}


def czyste(ust) -> dict:
    """Waliduje i normalizuje ustawienia; rzuca plan.BladPlanu."""
    if ust is not None and not isinstance(ust, dict):
        raise plan.BladPlanu("ustawienia muszą być obiektem")
    ust = ust or {}
    return {
        "strefy": {sp: z for sp, z in plan.normalizuj_strefy(ust.get("strefy")).items() if z},
        "moc": {sp: z for sp, z in plan.normalizuj_strefy(ust.get("moc")).items() if z},
        "ftp": plan.sprawdz_ftp(ust.get("ftp")),
    }


def ma_cos(ust: dict) -> bool:
    return bool(ust and (ust.get("strefy") or ust.get("moc") or ust.get("ftp")))


def moc_z_ftp(ftp: float) -> dict:
    """Strefy mocy P1-P5 w watach policzone z FTP wg domyślnych procentów."""
    return {n: [round(ftp * a / 100), round(ftp * b / 100)] for n, (a, b) in zip(NAZWY_MOCY, PROCENTY_MOCY)}


def _liczba(tekst, opis, dolna, gorna) -> int:
    try:
        v = int(str(tekst).strip())
    except ValueError:
        raise plan.BladPlanu(f"{opis}: wpisz obie granice jako liczby")
    if not dolna <= v <= gorna:
        raise plan.BladPlanu(f"{opis}: wartość musi być w zakresie {dolna}-{gorna}")
    return v


def _z_tabeli(wiersze: dict, nazwy, sport: str, dolna: int, gorna: int, jednostka: str) -> dict:
    z = {}
    for nazwa in nazwy:
        od, do = (wiersze.get(nazwa) or ("", ""))
        if not str(od).strip() and not str(do).strip():
            continue
        opis = f"{sport}, {nazwa}"
        a = _liczba(od, opis, dolna, gorna)
        b = _liczba(do, opis, dolna, gorna)
        if a > b:
            raise plan.BladPlanu(f"{opis}: dolna granica jest większa od górnej")
        z[nazwa] = [a, b]
    if z and len(z) != len(nazwy):
        brak = ", ".join(n for n in nazwy if n not in z)
        raise plan.BladPlanu(f"{sport}: wypełnij wszystkie strefy ({nazwy[0]}-{nazwy[-1]}) albo zostaw puste "
                             f"(brakuje: {brak})")
    return z


def z_pol(pola: dict) -> dict:
    """Pola formularza stref -> ustawienia. pola = {"bieg": {"S1": ("80", "124"), ...}, "rower": {...}, "moc": {"P1": ("0", "118")},
    "ftp": "215"}. Puste wiersze są pomijane; dla każdej tabeli trzeba wypełnić wszystkie 5 stref albo żadnej.
    Rzuca plan.BladPlanu z komunikatem po polsku.
    """
    strefy = {}
    for sport in ("bieg", "rower", "plywanie"):
        opis = "Pływanie" if sport == "plywanie" else sport.capitalize()
        z = _z_tabeli(pola.get(sport) or {}, NAZWY_STREF, f"{opis} (tętno)", 30, 250, "bpm")
        if z:
            strefy[sport] = z
    moc = _z_tabeli(pola.get("moc") or {}, NAZWY_MOCY, "Rower (moc)", 0, 2500, "W")
    ftp_txt = str(pola.get("ftp") or "").strip()
    ftp = None
    if ftp_txt:
        try:
            ftp = plan.sprawdz_ftp(ftp_txt.replace(",", "."))
        except plan.BladPlanu as e:
            raise plan.BladPlanu(f"FTP: {e}")
    return {"strefy": strefy, "moc": {"rower": moc} if moc else {}, "ftp": ftp}
