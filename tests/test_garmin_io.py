# -*- coding: utf-8 -*-
"""Testy wysyłki na atrapie klienta Garmina (bez sieci i bez logowania)."""
import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import garmin_io  # noqa: E402
import plan  # noqa: E402


class Atrapa:
    def __init__(self, istniejace=None, kalendarz=None):
        self.istniejace = istniejace or []
        self.kalendarz = kalendarz or {}
        self.wywolania = []
        self.n = 500

    def get_workouts(self, start, limit):
        return self.istniejace[start:start + limit]

    def get_scheduled_workouts(self, r, m):
        return {"calendarItems": self.kalendarz.get((r, m), [])}

    def upload_workout(self, w):
        self.n += 1
        self.wywolania.append(("upload", w["workoutName"]))
        return {"workoutId": self.n}

    def schedule_workout(self, wid, data):
        self.wywolania.append(("schedule", str(wid), data))

    def unschedule_workout(self, i):
        self.wywolania.append(("unschedule", i))

    def delete_workout(self, i):
        self.wywolania.append(("delete", str(i)))


PLAN = """{"strefy":{"bieg":{"S2":[125,148]}},"treningi":[
 {"data":"%s","nazwa":"T1","kroki":[{"typ":"bieg","czas":"30:00","cel":"S2"}]},
 {"nazwa":"T2 bez daty","kroki":[{"typ":"bieg","czas":"30:00"}]}]}"""


class TestWysylka(unittest.TestCase):
    def setUp(self):
        self.data = datetime.date.today() + datetime.timedelta(days=3)
        self.wyniki = plan.wczytaj(PLAN % self.data.isoformat())
        self.logi = []

    def uruchom(self, klient, nadpisz=False):
        return garmin_io.wyslij(klient, self.wyniki, nadpisz, self.logi.append)

    def test_nowe_treningi(self):
        k = Atrapa()
        st = self.uruchom(k)
        self.assertEqual(st, {"wyslane": 2, "pominiete": 0, "bledy": 0})
        self.assertEqual(k.wywolania, [("upload", "T1"), ("schedule", "501", self.data.isoformat()), ("upload", "T2 bez daty")])

    def test_ponowne_uruchomienie_nie_dubluje(self):
        k = Atrapa(istniejace=[{"workoutId": 11, "workoutName": "T1"}, {"workoutId": 12, "workoutName": "T2 bez daty"}],
                   kalendarz={(self.data.year, self.data.month): [{"id": 9, "workoutId": 11, "date": self.data.isoformat()}]})
        st = self.uruchom(k)
        self.assertEqual(st, {"wyslane": 0, "pominiete": 2, "bledy": 0})
        self.assertEqual(k.wywolania, [])

    def test_przestawienie_wpisu_najpierw_nowy_potem_kasowanie(self):
        stary = self.data + datetime.timedelta(days=1)
        k = Atrapa(istniejace=[{"workoutId": 11, "workoutName": "T1"}],
                   kalendarz={(stary.year, stary.month): [{"id": 9, "workoutId": 11, "date": stary.isoformat()}]})
        self.uruchom(k)
        akcje = [w for w in k.wywolania if w[0] in ("schedule", "unschedule")]
        self.assertEqual(akcje, [("schedule", "11", self.data.isoformat()), ("unschedule", 9)])

    def test_dopasowanie_po_tytule_gdy_brak_workoutid(self):
        stary = self.data + datetime.timedelta(days=1)
        k = Atrapa(istniejace=[{"workoutId": 11, "workoutName": "T1"}],
                   kalendarz={(stary.year, stary.month): [{"id": 9, "title": "T1", "date": stary.isoformat()}]})
        self.uruchom(k)
        self.assertIn(("unschedule", 9), k.wywolania)

    def test_nadpisywanie(self):
        k = Atrapa(istniejace=[{"workoutId": 11, "workoutName": "T1"}])
        st = self.uruchom(k, nadpisz=True)
        self.assertIn(("delete", "11"), k.wywolania)
        self.assertEqual(st["wyslane"], 2)

    def test_blad_jednego_treningu_nie_zatrzymuje_reszty(self):
        k = Atrapa()
        oryginal = k.upload_workout

        def upload(w):
            if w["workoutName"] == "T1":
                raise RuntimeError("awaria")
            return oryginal(w)
        k.upload_workout = upload
        st = self.uruchom(k)
        self.assertEqual((st["wyslane"], st["bledy"]), (1, 1))
        self.assertTrue(any("BŁĄD" in l for l in self.logi))

    def test_brak_workoutid_w_odpowiedzi_to_blad(self):
        k = Atrapa()
        k.upload_workout = lambda w: {}
        self.assertEqual(self.uruchom(k)["bledy"], 2)

    def test_paginacja_listy_treningow(self):
        k = Atrapa(istniejace=[{"workoutId": i, "workoutName": f"X{i}"} for i in range(250)] + [{"workoutId": 999, "workoutName": "T1"}])
        self.uruchom(k)
        self.assertNotIn(("upload", "T1"), k.wywolania)     # T1 był na 251. pozycji, a mimo to został znaleziony


if __name__ == "__main__":
    unittest.main()
