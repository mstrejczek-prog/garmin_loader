# -*- coding: utf-8 -*-
"""Testy treningu siłowego i pozostałych typów treningów Garmina."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import cwiczenia  # noqa: E402
import plan  # noqa: E402


def wczytaj(trening, **extra):
    d = {"treningi": [trening]}
    d.update(extra)
    return plan.wczytaj(json.dumps(d, ensure_ascii=False))[0]


def silowy(kroki):
    return wczytaj({"nazwa": "S", "sport": "silowy", "kroki": kroki})


class TestCwiczenia(unittest.TestCase):
    def test_nazwa_z_katalogu(self):
        c = cwiczenia.znajdz("Barbell Back Squat")
        self.assertEqual((c["category"], c["exercise"]), ("SQUAT", "BARBELL_BACK_SQUAT"))

    def test_polska_nazwa_i_wielkosc_liter(self):
        self.assertEqual(cwiczenia.znajdz("Przysiad ze sztangą")["exercise"], "BARBELL_BACK_SQUAT")
        self.assertEqual(cwiczenia.znajdz("martwy ciąg rumuński")["exercise"], "ROMANIAN_DEADLIFT")
        self.assertEqual(cwiczenia.znajdz("pull up")["category"], "PULL_UP")

    def test_wszystkie_aliasy_istnieja_w_katalogu(self):
        from garminconnect import exercises
        for pl, ang in cwiczenia.ALIASY_PL.items():
            self.assertIn(ang, exercises.BY_NAME, pl)

    def test_nieznane_cwiczenie_podpowiada(self):
        with self.assertRaises(ValueError) as cm:
            cwiczenia.znajdz("Nordic hamstring curl")
        self.assertIn("kategoria", str(cm.exception))

    def test_kategoria_zamiast_cwiczenia(self):
        c = cwiczenia.znajdz("Nordic hamstring curl", "LEG_CURL")
        self.assertEqual((c["category"], c["exercise"]), ("LEG_CURL", ""))

    def test_zla_kategoria(self):
        with self.assertRaises(ValueError):
            cwiczenia.znajdz("Cokolwiek", "NIE_MA_TAKIEJ")


class TestSilowy(unittest.TestCase):
    def test_serie_to_grupa_powtorzen_jak_w_bibliotece(self):
        x = silowy([{"cwiczenie": "Barbell Back Squat", "serie": 4, "powtorzenia": 6, "ciezar": "70kg", "przerwa": "2:00"}])
        g = x["w"]["workoutSegments"][0]["workoutSteps"][0]
        self.assertEqual((g["type"], g["numberOfIterations"], g["stepOrder"]), ("RepeatGroupDTO", 4, 1))
        cw, odp = g["workoutSteps"]
        self.assertEqual((cw["stepOrder"], odp["stepOrder"]), (2, 3))
        self.assertEqual(cw["endCondition"]["conditionTypeKey"], "reps")
        self.assertEqual(cw["endConditionValue"], 6.0)
        self.assertEqual((cw["category"], cw["exerciseName"]), ("SQUAT", "BARBELL_BACK_SQUAT"))
        self.assertEqual(cw["weightValue"], 70000.0)
        self.assertEqual(cw["weightUnit"]["unitKey"], "kilogram")
        self.assertEqual(odp["stepType"]["stepTypeKey"], "rest")
        self.assertEqual(odp["endConditionValue"], 120.0)

    def test_zgodnosc_z_budowniczym_biblioteki(self):
        try:
            from garminconnect.workout import create_strength_set
            lib = create_strength_set("SQUAT", 1, 4, 6, 120, exercise_name="BARBELL_BACK_SQUAT", weight_kg=70)
        except Exception:                                   # np. brak pydantic
            self.skipTest("biblioteka nie buduje kroków bez pydantic")

        def czysc(d):
            if isinstance(d, dict):
                return {k: czysc(v) for k, v in d.items() if v is not None}
            if isinstance(d, list):
                return [czysc(v) for v in d]
            return d
        x = silowy([{"cwiczenie": "Barbell Back Squat", "serie": 4, "powtorzenia": 6, "ciezar": 70, "przerwa": "2:00"}])
        self.assertEqual(czysc(x["w"]["workoutSegments"][0]["workoutSteps"][0]), czysc(lib.model_dump()))

    def test_pojedyncza_seria_bez_grupy(self):
        x = silowy([{"cwiczenie": "Pull-up", "powtorzenia": 8}])
        k = x["w"]["workoutSegments"][0]["workoutSteps"]
        self.assertEqual([s["type"] for s in k], ["ExecutableStepDTO"])
        self.assertNotIn("weightValue", k[0])

    def test_cwiczenie_na_czas(self):
        x = silowy([{"cwiczenie": "Plank", "serie": 3, "czas": "0:45", "przerwa": "1:00"}])
        cw = x["w"]["workoutSegments"][0]["workoutSteps"][0]["workoutSteps"][0]
        self.assertEqual(cw["endCondition"]["conditionTypeKey"], "time")
        self.assertEqual(cw["endConditionValue"], 45.0)

    def test_seria_laczona_i_unikalne_stepOrder(self):
        x = silowy([
            {"typ": "rozgrzewka", "czas": "5:00"},
            {"powtorz": 3, "kroki": [{"cwiczenie": "Pull-up", "powtorzenia": 6},
                                      {"cwiczenie": "Plank", "czas": "0:30", "przerwa": "1:00"}]},
            {"cwiczenie": "Goblet Squat", "serie": 2, "powtorzenia": 10, "przerwa": "1:00"},
        ])
        numery = []

        def zbierz(kroki):
            for k in kroki:
                numery.append(k["stepOrder"])
                zbierz(k.get("workoutSteps", []))
        zbierz(x["w"]["workoutSegments"][0]["workoutSteps"])
        self.assertEqual(numery, sorted(numery))
        self.assertEqual(len(numery), len(set(numery)))
        self.assertEqual(len(x["poziomy"]), len(x["wiersze"]))
        self.assertTrue(all(p == 0 for p in x["poziomy"]))
        self.assertGreater(x["w"]["estimatedDurationInSecs"], 600)

    def test_profil_rozwija_serie(self):
        x = silowy([{"cwiczenie": "Barbell Back Squat", "serie": 4, "powtorzenia": 10, "przerwa": "2:00"}])
        # 4 x (10 powtórzeń po 3,5 s + 120 s przerwy)
        self.assertAlmostEqual(sum(p[0] for p in x["profil"]), 4 * (35 + 120))

    def test_bledy_po_polsku(self):
        przypadki = [
            ([{"cwiczenie": "Barbell Back Squat", "serie": 3}], "powtorzenia"),
            ([{"cwiczenie": "Barbell Back Squat", "powtorzenia": 5, "czas": "1:00"}], "powtorzenia"),
            ([{"cwiczenie": "Barbell Back Squat", "serie": 0, "powtorzenia": 5}], "liczba serii"),
            ([{"cwiczenie": "Barbell Back Squat", "powtorzenia": 5, "ciezar": "dużo"}], "ciężaru"),
            ([{"cwiczenie": "Wymyślone ćwiczenie xyz", "powtorzenia": 5}], "katalogu Garmin"),
            ([{"typ": "interwal", "czas": "5:00"}], "krok siłowy"),
            ([{"typ": "rozgrzewka"}], "wymaga"),
        ]
        for kroki, fragment in przypadki:
            with self.subTest(fragment=fragment, kroki=kroki):
                with self.assertRaises(plan.BladPlanu) as cm:
                    silowy(kroki)
                self.assertIn(fragment, str(cm.exception))


class TestInneSporty(unittest.TestCase):
    def test_wszystkie_sporty_maja_poprawne_id(self):
        oczekiwane = {"bieg": 1, "rower": 2, "plywanie": 4, "silowy": 5, "cardio": 6, "joga": 7, "pilates": 8,
                      "hiit": 9, "mobilnosc": 11, "marsz": 17, "turystyka": 18, "inny": 3}
        self.assertEqual({k: v["sportTypeId"] for k, v in plan.SPORTY.items()}, oczekiwane)

    def test_kazdy_sport_buduje_trening(self):
        for sport in plan.SPORTY:
            if sport == "silowy":
                continue
            with self.subTest(sport=sport):
                x = wczytaj({"nazwa": "T", "sport": sport, "kroki": [{"typ": "interwal", "czas": "10:00"}]})
                self.assertEqual(x["w"]["sportType"]["sportTypeKey"], plan.SPORTY[sport]["sportTypeKey"])
                self.assertEqual(x["w"]["workoutSegments"][0]["sportType"], plan.SPORTY[sport])

    def test_aliasy_sportow(self):
        for alias, sport in (("Pływanie", "plywanie"), ("trening siłowy", "silowy"), ("yoga", "joga"),
                              ("Stretching", "mobilnosc"), ("hiking", "turystyka"), ("spacer", "marsz")):
            self.assertEqual(plan.SPORT_ALIASY[plan._norm(alias)], sport)

    def test_plywanie_tempo_na_100m(self):
        x = wczytaj({"nazwa": "P", "sport": "plywanie", "kroki": [{"typ": "interwal", "dystans": "400m", "cel": "1:50-2:00"}]})
        k = x["w"]["workoutSegments"][0]["workoutSteps"][0]
        self.assertEqual(k["targetType"]["workoutTargetTypeKey"], "pace.zone")
        self.assertAlmostEqual(k["targetValueOne"], 100 / 120, places=3)       # wolniej
        self.assertAlmostEqual(k["targetValueTwo"], 100 / 110, places=3)       # szybciej
        self.assertIn("/100 m", x["wiersze"][0][3])
        self.assertAlmostEqual(x["w"]["estimatedDurationInSecs"], 460, delta=3)

    def test_tempo_dla_marszu_i_odrzucone_dla_roweru(self):
        wczytaj({"nazwa": "M", "sport": "marsz", "kroki": [{"typ": "interwal", "dystans": "5km", "cel": "9:00-10:00"}]})
        with self.assertRaises(plan.BladPlanu):
            wczytaj({"nazwa": "R", "sport": "rower", "kroki": [{"typ": "interwal", "dystans": "5km", "cel": "4:00-4:30"}]})

    def test_kadencja(self):
        for zapis, a, b in (("90-100rpm", 90, 100), ("kadencja 85-95", 85, 95), ("170-180 spm", 170, 180)):
            x = wczytaj({"nazwa": "K", "sport": "rower", "kroki": [{"typ": "interwal", "czas": "5:00", "cel": zapis}]})
            k = x["w"]["workoutSegments"][0]["workoutSteps"][0]
            self.assertEqual(k["targetType"]["workoutTargetTypeKey"], "cadence", zapis)
            self.assertEqual((k["targetValueOne"], k["targetValueTwo"]), (a, b))

    def test_krok_konczony_przyciskiem(self):
        x = wczytaj({"nazwa": "J", "sport": "joga", "kroki": [{"typ": "interwal", "przycisk": True}]})
        k = x["w"]["workoutSegments"][0]["workoutSteps"][0]
        self.assertEqual(k["endCondition"]["conditionTypeKey"], "lap.button")
        self.assertIsNone(k["endConditionValue"])
        self.assertEqual(x["wiersze"][0][2], "przycisk")

    def test_cwiczenie_bez_intensywnosci_jest_szare(self):
        x = wczytaj({"nazwa": "H", "sport": "hiit", "kroki": [
            {"typ": "interwal", "czas": "0:40"}, {"typ": "przerwa", "czas": "0:20"}]})
        self.assertEqual(x["poziomy"], [0, 0])

    def test_tetno_z_biegu_dla_cardio_ale_nie_dla_roweru(self):
        strefy = {"bieg": {f"S{i}": [100 + 20 * i, 119 + 20 * i] for i in range(1, 6)}}
        x = wczytaj({"nazwa": "C", "sport": "cardio", "kroki": [{"typ": "interwal", "czas": "10:00", "cel": "S2"}]},
                    strefy=strefy)
        self.assertEqual(x["w"]["workoutSegments"][0]["workoutSteps"][0]["targetValueOne"], 140)
        with self.assertRaises(plan.BladPlanu):
            wczytaj({"nazwa": "R", "sport": "rower", "kroki": [{"typ": "interwal", "czas": "10:00", "cel": "S2"}]},
                    strefy=strefy)

    def test_wlasne_strefy_plywania(self):
        strefy = {"plywanie": {f"S{i}": [100 + 10 * i, 109 + 10 * i] for i in range(1, 6)}}
        x = wczytaj({"nazwa": "P", "sport": "pływanie", "kroki": [{"typ": "interwal", "dystans": "200m", "cel": "S3"}]},
                    strefy=strefy)
        self.assertEqual(x["w"]["workoutSegments"][0]["workoutSteps"][0]["targetValueOne"], 130)
        self.assertEqual(x["poziomy"], [3])

    def test_prompt_zna_nowe_sporty_i_silowy(self):
        t = plan.prompt_ai()
        for fragment in ("silowy", "plywanie", "cwiczenie", "kategoria", "przycisk"):
            self.assertIn(fragment, t)


if __name__ == "__main__":
    unittest.main()
