# -*- coding: utf-8 -*-
"""Testy parsera planu. Uruchom: python -m unittest discover -s tests"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import plan  # noqa: E402

STREFY = {"bieg": {"S1": [80, 124], "S2": [125, 148]}}


def p(treningi, strefy=STREFY):
    return json.dumps({"strefy": strefy, "treningi": treningi}, ensure_ascii=False)


def kroki(w):
    return w["w"]["workoutSegments"][0]["workoutSteps"]


class TestPlan(unittest.TestCase):
    def test_przyklad_sie_wczytuje(self):
        wyniki = plan.wczytaj(plan.PRZYKLAD_PLANU)
        self.assertEqual(sum(1 for x in wyniki if not x["wolne"]), 2)

    def test_tempo_i_dystans(self):
        w = plan.wczytaj(p([{"nazwa": "A", "kroki": [{"typ": "interwal", "dystans": "6km", "cel": "4:20-4:25"}]}]))[0]
        k = kroki(w)[0]
        self.assertEqual(k["endCondition"]["conditionTypeKey"], "distance")
        self.assertEqual(k["endConditionValue"], 6000.0)
        self.assertEqual(k["targetType"]["workoutTargetTypeKey"], "pace.zone")
        # pierwsza wartość = wolniejsze tempo (4:25 = 3.7736 m/s), druga = szybsze (4:20 = 3.8462 m/s)
        self.assertAlmostEqual(k["targetValueOne"], 1000 / 265, places=3)
        self.assertAlmostEqual(k["targetValueTwo"], 1000 / 260, places=3)

    def test_zapis_trenera_z_apostrofami(self):
        w = plan.wczytaj(p([{"nazwa": "A", "kroki": [{"typ": "interwal", "dystans": "3 km", "cel": "4'10''-4'15''"}]}]))[0]
        self.assertEqual(kroki(w)[0]["targetType"]["workoutTargetTypeKey"], "pace.zone")

    def test_strefa_na_tetno(self):
        w = plan.wczytaj(p([{"nazwa": "A", "kroki": [{"typ": "bieg", "czas": "40:00", "cel": "S2"}]}]))[0]
        k = kroki(w)[0]
        self.assertEqual((k["targetValueOne"], k["targetValueTwo"]), (125, 148))
        self.assertEqual(k["endConditionValue"], 2400.0)

    def test_powtorzenia_rozwijaja_sie(self):
        w = plan.wczytaj(p([{"nazwa": "A", "kroki": [
            {"typ": "rozgrzewka", "czas": "10:00"},
            {"powtorz": 3, "kroki": [{"typ": "interwal", "czas": "1:00", "cel": "HR 150-160"},
                                      {"typ": "przerwa", "czas": "1:00"}]}]}]))[0]
        self.assertEqual(len(kroki(w)), 7)
        self.assertEqual([k["stepOrder"] for k in kroki(w)], list(range(1, 8)))

    def test_moc_dla_roweru(self):
        w = plan.wczytaj(p([{"nazwa": "R", "sport": "rower", "kroki": [{"typ": "interwal", "czas": "5:00", "cel": "200-220W"}]}]))[0]
        self.assertEqual(kroki(w)[0]["targetType"]["workoutTargetTypeKey"], "power.zone")

    def test_wolne_i_sortowanie(self):
        wyniki = plan.wczytaj(p([
            {"data": "2026-10-04", "nazwa": "B", "kroki": [{"typ": "bieg", "czas": "30:00"}]},
            {"data": "2026-10-03", "wolne": True},
            {"data": "2026-10-02", "nazwa": "A", "kroki": [{"typ": "bieg", "czas": "30:00"}]},
        ]))
        self.assertEqual([x["data"].day for x in wyniki], [2, 3, 4])
        self.assertTrue(wyniki[1]["wolne"])

    def test_json_w_bloku_kodu_z_tekstem(self):
        tekst = "Oto plan:\n```json\n" + p([{"nazwa": "A", "kroki": [{"typ": "bieg", "czas": "30:00"}]}]) + "\n```\nPozdrawiam"
        self.assertEqual(len(plan.wczytaj(tekst)), 1)

    def test_bledy_maja_polski_komunikat(self):
        przypadki = [
            ([{"nazwa": "A", "kroki": [{"typ": "bieg", "czas": "30:00", "cel": "S9"}]}], "nie rozumiem celu"),
            ([{"nazwa": "A", "kroki": [{"typ": "sprint", "czas": "30:00"}]}], "nieznany typ"),
            ([{"nazwa": "A", "kroki": [{"typ": "bieg", "czas": "30:00", "dystans": "5km"}]}], "dokładnie jedno"),
            ([{"nazwa": "A", "kroki": [{"typ": "bieg"}]}], "dokładnie jedno"),
            ([{"nazwa": "A", "sport": "plywanie", "kroki": [{"typ": "bieg", "czas": "30:00"}]}], "nie jest obsługiwany"),
            ([{"nazwa": "A", "kroki": [{"typ": "bieg", "czas": "30:00", "cel": "200-220W"}]}], "rower"),
            ([{"nazwa": "A", "kroki": [{"typ": "bieg", "czas": "30:00", "cel": "1:00-1:05"}]}], "nierealnie"),
            ([{"data": "02-10-2026", "nazwa": "A", "kroki": [{"typ": "bieg", "czas": "30:00"}]}], "zła data"),
            ([{"nazwa": "A", "kroki": [{"typ": "bieg", "czas": "30:00"}]},
              {"nazwa": "A", "kroki": [{"typ": "bieg", "czas": "30:00"}]}], "tę samą nazwę"),
            ([{"kroki": [{"typ": "bieg", "czas": "30:00"}]}], "bez nazwy"),
            ([{"nazwa": "A", "kroki": []}], "żadnych kroków"),
        ]
        for treningi, fragment in przypadki:
            with self.subTest(fragment=fragment):
                with self.assertRaises(plan.BladPlanu) as cm:
                    plan.wczytaj(p(treningi))
                self.assertIn(fragment, str(cm.exception))

    def test_rower_bez_stref_nie_zgaduje(self):
        with self.assertRaises(plan.BladPlanu) as cm:
            plan.wczytaj(p([{"nazwa": "R", "sport": "rower", "kroki": [{"typ": "interwal", "czas": "5:00", "cel": "S2"}]}]))
        self.assertIn("brak strefy", str(cm.exception))

    def test_smieci_zamiast_planu(self):
        for tekst in ("", "tralala", "{ zepsuty json", "[1,2,3]", "{}"):
            with self.subTest(tekst=tekst):
                with self.assertRaises(plan.BladPlanu):
                    plan.wczytaj(tekst)

    def test_prompt_zawiera_format_i_znaczniki(self):
        for fragment in ('"treningi"', "powtorz", "ROZPISKA OD TRENERA", "ZAPYTAJ mnie"):
            self.assertIn(fragment, plan.PROMPT_AI)


if __name__ == "__main__":
    unittest.main()
