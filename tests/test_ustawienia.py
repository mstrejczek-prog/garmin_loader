# -*- coding: utf-8 -*-
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import plan  # noqa: E402
import ustawienia  # noqa: E402

STREFY = {"bieg": {"S1": [80, 124], "S2": [125, 148], "S3": [149, 170], "S4": [171, 182], "S5": [183, 196]}}
UST = {"strefy": STREFY, "moc": {}, "ftp": None}
UST_ROWER = {"strefy": STREFY, "ftp": 200,
             "moc": {"rower": {"P1": [0, 110], "P2": [112, 150], "P3": [152, 180], "P4": [182, 210], "P5": [212, 240]}}}


def plan_json(sport="bieg", cel="S2", extra=None):
    d = {"treningi": [{"nazwa": "A", "sport": sport, "kroki": [{"typ": "bieg", "czas": "30:00", "cel": cel}]}]}
    d.update(extra or {})
    return json.dumps(d)


def krok(wyniki):
    return wyniki[0]["w"]["workoutSegments"][0]["workoutSteps"][0]


class TestZapis(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.stary = ustawienia.PLIK
        ustawienia.PLIK = Path(self.tmp.name) / "u.json"

    def tearDown(self):
        ustawienia.PLIK = self.stary
        self.tmp.cleanup()

    def test_zapis_i_odczyt(self):
        self.assertFalse(ustawienia.ma_cos(ustawienia.wczytaj()))
        ustawienia.zapisz(UST_ROWER)
        self.assertEqual(ustawienia.wczytaj(), UST_ROWER)

    def test_pusty_zapis_kasuje(self):
        ustawienia.zapisz(UST)
        ustawienia.zapisz(ustawienia.puste())
        self.assertFalse(ustawienia.PLIK.exists())
        ustawienia.zapisz(ustawienia.puste())               # drugi raz nie rzuca błędu

    def test_sam_ftp_wystarczy(self):
        ustawienia.zapisz({"ftp": 215})
        self.assertEqual(ustawienia.wczytaj()["ftp"], 215)

    def test_uszkodzony_plik_to_puste_ustawienia(self):
        ustawienia.PLIK.write_text("{nie json", encoding="utf-8")
        self.assertEqual(ustawienia.wczytaj(), ustawienia.puste())

    def test_moc_z_ftp(self):
        z = ustawienia.moc_z_ftp(200)
        self.assertEqual(z["P1"], [0, 110])
        self.assertEqual(z["P3"], [152, 180])
        self.assertEqual(z["P5"], [212, 240])


class TestZPol(unittest.TestCase):
    def pola(self):
        return {"bieg": {n: (str(v[0]), str(v[1])) for n, v in STREFY["bieg"].items()}, "rower": {}, "moc": {}, "ftp": ""}

    def test_poprawne(self):
        self.assertEqual(ustawienia.z_pol(self.pola()), {"strefy": STREFY, "moc": {}, "ftp": None})

    def test_ftp_i_moc(self):
        p = self.pola()
        p["ftp"] = "215,5"
        p["moc"] = {n: (str(v[0]), str(v[1])) for n, v in ustawienia.moc_z_ftp(215).items()}
        w = ustawienia.z_pol(p)
        self.assertEqual(w["ftp"], 215.5)
        self.assertEqual(w["moc"]["rower"]["P2"], [120, 161])

    def test_bledy(self):
        p = self.pola()
        p["bieg"]["S3"] = ("", "")
        with self.assertRaisesRegex(plan.BladPlanu, "brakuje: S3"):
            ustawienia.z_pol(p)
        p["bieg"]["S3"] = ("170", "149")
        with self.assertRaisesRegex(plan.BladPlanu, "większa"):
            ustawienia.z_pol(p)
        p["bieg"]["S3"] = ("abc", "170")
        with self.assertRaisesRegex(plan.BladPlanu, "liczb"):
            ustawienia.z_pol(p)
        p["bieg"]["S3"] = ("5", "170")
        with self.assertRaisesRegex(plan.BladPlanu, "30-250"):
            ustawienia.z_pol(p)
        p = self.pola()
        p["ftp"] = "9999"
        with self.assertRaisesRegex(plan.BladPlanu, "FTP"):
            ustawienia.z_pol(p)
        p["ftp"] = ""
        p["moc"] = {"P1": ("0", "100")}
        with self.assertRaisesRegex(plan.BladPlanu, "Rower \\(moc\\)"):
            ustawienia.z_pol(p)


class TestPlanZUstawieniami(unittest.TestCase):
    def test_plan_bez_stref_korzysta_z_zapisanych(self):
        k = krok(plan.wczytaj(plan_json(), UST))
        self.assertEqual((k["targetValueOne"], k["targetValueTwo"]), (125, 148))

    def test_brak_stref_daje_czytelny_blad(self):
        with self.assertRaisesRegex(plan.BladPlanu, "Moje strefy"):
            plan.wczytaj(plan_json())

    def test_strefy_z_planu_maja_pierwszenstwo(self):
        tekst = plan_json(extra={"strefy": {"bieg": {"S2": [130, 150]}}})
        k = krok(plan.wczytaj(tekst, UST))
        self.assertEqual((k["targetValueOne"], k["targetValueTwo"]), (130, 150))
        k = krok(plan.wczytaj(plan_json(cel="S3", extra={"strefy": {"bieg": {"S2": [130, 150]}}}), UST))
        self.assertEqual((k["targetValueOne"], k["targetValueTwo"]), (149, 170))

    def test_procent_ftp_zakres(self):
        k = krok(plan.wczytaj(plan_json("rower", "90-95% FTP"), UST_ROWER))
        self.assertEqual((k["targetValueOne"], k["targetValueTwo"]), (180, 190))
        self.assertEqual(k["targetType"]["workoutTargetTypeKey"], "power.zone")

    def test_procent_ftp_formaty_zapisu(self):
        for cel in ("90-95%FTP", "FTP 90-95%", "90-95 % ftp", "90,0-95%"):
            k = krok(plan.wczytaj(plan_json("rower", cel), UST_ROWER))
            self.assertEqual((k["targetValueOne"], k["targetValueTwo"]), (180, 190), cel)

    def test_procent_ftp_pojedyncza_wartosc_to_pasmo_2pp(self):
        k = krok(plan.wczytaj(plan_json("rower", "95% FTP"), UST_ROWER))
        self.assertEqual((k["targetValueOne"], k["targetValueTwo"]), (186, 194))

    def test_ftp_z_planu_ma_pierwszenstwo(self):
        k = krok(plan.wczytaj(plan_json("rower", "100% FTP", {"ftp": 250}), UST_ROWER))
        self.assertEqual(k["targetValueOne"], 245)

    def test_procent_ftp_bez_ftp_i_dla_biegu(self):
        with self.assertRaisesRegex(plan.BladPlanu, "wymaga FTP"):
            plan.wczytaj(plan_json("rower", "90% FTP"), UST)
        with self.assertRaisesRegex(plan.BladPlanu, "tylko dla roweru"):
            plan.wczytaj(plan_json("bieg", "90% FTP"), UST_ROWER)
        with self.assertRaisesRegex(plan.BladPlanu, "nierealnie"):
            plan.wczytaj(plan_json("rower", "900% FTP"), UST_ROWER)

    def test_strefa_mocy(self):
        k = krok(plan.wczytaj(plan_json("rower", "P3"), UST_ROWER))
        self.assertEqual((k["targetValueOne"], k["targetValueTwo"]), (152, 180))
        with self.assertRaisesRegex(plan.BladPlanu, "brak strefy mocy P3"):
            plan.wczytaj(plan_json("rower", "P3"), UST)
        with self.assertRaisesRegex(plan.BladPlanu, "tylko dla roweru"):
            plan.wczytaj(plan_json("bieg", "P3"), UST_ROWER)

    def test_s2_dla_roweru_to_nadal_tetno(self):
        tekst = plan_json("rower", "S2", {"strefy": {"rower": {"S2": [120, 140]}}})
        k = krok(plan.wczytaj(tekst, UST_ROWER))
        self.assertEqual(k["targetType"]["workoutTargetTypeKey"], "heart.rate.zone")

    def test_wykres_poziom_z_mocy(self):
        w = plan.wczytaj(plan_json("rower", "95% FTP"), UST_ROWER)[0]
        self.assertEqual(w["profil"][0][1], 4)               # 95% FTP = strefa 4 (91-105 %)
        w = plan.wczytaj(plan_json("rower", "P2"), UST_ROWER)[0]
        self.assertEqual(w["profil"][0][1], 2)
        w = plan.wczytaj(plan_json("rower", "60-70% FTP"), {"ftp": 200})[0]
        self.assertEqual(w["profil"][0][1], 2)


class TestPrompt(unittest.TestCase):
    def test_bez_ustawien_pyta_o_wszystko(self):
        t = plan.prompt_ai({})
        self.assertIn("MOJE STREFY TĘTNA - bieg: [WPISZ", t)
        self.assertIn("MOJE FTP (rower, w watach): [WPISZ", t)
        self.assertIn('"strefy"', t)
        self.assertIn("% FTP", t)

    def test_zapisane_tetno_nie_pytamy_o_tetno(self):
        t = plan.prompt_ai(UST)
        self.assertNotIn("[WPISZ, np. S1", t)
        self.assertNotIn('"strefy": { "bieg"', t)
        self.assertIn("S2 125-148", t)
        self.assertIn("NIE dodawaj sekcji", t)
        self.assertIn("MOJE FTP (rower, w watach): [WPISZ", t)      # FTP nadal do uzupełnienia
        self.assertTrue(t.startswith("Zamień rozpiskę"))

    def test_zapisane_ftp_i_moc(self):
        t = plan.prompt_ai(UST_ROWER)
        self.assertNotIn("[WPISZ", t.split("ROK I DATY")[0].split("ZASADY")[1])
        self.assertIn("FTP: 200 W", t)
        self.assertIn("P2 112-150 W", t)
        self.assertIn("NIE przeliczaj", t)
        self.assertIn("ROK I DATY", t)
        self.assertIn("ROZPISKA OD TRENERA", t)

    def test_sam_ftp(self):
        t = plan.prompt_ai({"ftp": 215})
        self.assertIn("FTP: 215 W", t)
        self.assertIn("MOJE STREFY TĘTNA - bieg: [WPISZ", t)


if __name__ == "__main__":
    unittest.main()
