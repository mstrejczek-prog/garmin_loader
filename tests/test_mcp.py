# -*- coding: utf-8 -*-
"""Testy connectora MCP na atrapie biblioteki Garmina (bez sieci i bez prawdziwego logowania)."""
import datetime
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import garmin_dane  # noqa: E402
import plan  # noqa: E402
import garmin_io  # noqa: E402
import mcp_serwer  # noqa: E402
import web  # noqa: E402
from tests.test_web import TOKEN, AtrapaGarmin  # noqa: E402

DZIS = datetime.date.today()
DZIEN = (DZIS + datetime.timedelta(days=3)).isoformat()
PLAN = {"strefy": {"bieg": {"S2": [125, 148]}}, "treningi": [
    {"data": DZIEN, "nazwa": "T1", "kroki": [{"typ": "bieg", "czas": "30:00", "cel": "S2"}]}]}


class AtrapaDane(AtrapaGarmin):
    """Atrapa klienta z metodami odczytu (kształt odpowiedzi jak w Garmin Connect)."""

    def get_activities_by_date(self, od, do, typ=None):
        self.wywolania.append(("aktywnosci", od, do, typ))
        return [{"activityId": 111, "activityName": "Rower", "activityType": {"typeKey": "cycling"},
                 "startTimeLocal": f"{od} 08:00:00", "duration": 3600, "distance": 30000,
                 "averageHR": 140, "normPower": 220, "trainingStressScore": 70.4, "averageSpeed": 8.33}]

    def get_activity(self, aid):
        return {"activityId": int(aid), "activityName": "Rower", "activityTypeDTO": {"typeKey": "cycling"},
                "summaryDTO": {"duration": 3600, "distance": 30000, "normalizedPower": 221.6, "averageHR": 141.2}}

    def get_activity_splits(self, aid):
        return {"lapDTOs": [{"lapIndex": 1, "duration": 600, "distance": 5000, "averagePower": 250}]}

    def get_activity_hr_in_timezones(self, aid):
        return [{"zoneNumber": 2, "zoneLowBoundary": 120, "secsInZone": 1800}]

    def get_user_summary(self, d):
        return {"restingHeartRate": 48, "bodyBatteryHighestValue": 90, "totalSteps": 8000}

    def get_sleep_data(self, d):
        return {"dailySleepDTO": {"sleepTimeSeconds": 27000, "sleepScores": {"overall": {"value": 82}}}}

    def get_hrv_data(self, d):
        raise RuntimeError("brak HRV")

    def get_training_readiness(self, d):
        return [{"score": 71, "level": "HIGH"}]

    def get_training_status(self, d):
        return {"mostRecentTrainingStatus": {"latestTrainingStatusData": {"123": {
            "trainingStatusFeedbackPhrase": "PRODUCTIVE",
            "acuteTrainingLoadDTO": {"dailyTrainingLoadAcute": 500, "dailyTrainingLoadChronic": 450,
                                     "dailyAcuteChronicWorkloadRatio": 1.111}}}}}


class BazaMcp(unittest.TestCase):
    def setUp(self):
        self.stary = garmin_io._nowy_klient
        garmin_io._nowy_klient = AtrapaDane
        AtrapaGarmin.mfa = AtrapaGarmin.zly_token = False
        mcp_serwer._klient = None
        self.env = {k: os.environ.get(k) for k in ("MCP_TOKEN", "GARMIN_TOKEN", "USTAWIENIA")}
        os.environ["MCP_TOKEN"] = "tajne"
        os.environ["GARMIN_TOKEN"] = web.szyfruj("token", TOKEN)
        os.environ.pop("USTAWIENIA", None)
        self.c = web.app.test_client()
        self.n = 0

    def tearDown(self):
        garmin_io._nowy_klient = self.stary
        mcp_serwer._klient = None
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def rpc(self, metoda, params=None, token="tajne"):
        self.n += 1
        h = {"Authorization": f"Bearer {token}"} if token else {}
        return self.c.post("/mcp", json={"jsonrpc": "2.0", "id": self.n, "method": metoda, "params": params or {}},
                           headers=h)

    def narzedzie(self, nazwa, **arg):
        r = self.rpc("tools/call", {"name": nazwa, "arguments": arg})
        self.assertEqual(r.status_code, 200)
        wynik = r.get_json()["result"]
        return wynik, wynik["content"][0]["text"]


class TestMcp(BazaMcp):
    # --- protokół i autoryzacja
    def test_autoryzacja(self):
        self.assertEqual(self.rpc("ping", token=None).status_code, 401)
        self.assertEqual(self.rpc("ping", token="zle").status_code, 401)
        self.assertEqual(self.rpc("ping").status_code, 200)
        os.environ.pop("MCP_TOKEN")
        self.assertEqual(self.rpc("ping").status_code, 503)

    def test_initialize_lista_i_powiadomienie(self):
        r = self.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                    "clientInfo": {"name": "t", "version": "1"}}).get_json()["result"]
        self.assertEqual(r["protocolVersion"], "2025-06-18")
        self.assertIn("tools", r["capabilities"])
        r = self.c.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                        headers={"Authorization": "Bearer tajne"})
        self.assertEqual(r.status_code, 202)
        nazwy = {t["name"] for t in self.rpc("tools/list").get_json()["result"]["tools"]}
        self.assertTrue({"format_planu", "sprawdz_plan", "wyslij_plan", "kalendarz", "aktywnosci",
                         "trening", "wellness", "gotowosc", "usun_z_kalendarza", "moje_ustawienia",
                         "serie", "plan_treningu", "plan_vs_wykonanie", "pobierz_fit", "surowe_dane"} <= nazwy)
        self.assertEqual(self.c.get("/mcp", headers={"Authorization": "Bearer tajne"}).status_code, 405)
        self.assertEqual(self.rpc("nie/ma").get_json()["error"]["code"], -32601)

    # --- plany
    def test_sprawdz_i_wyslij_plan(self):
        w, tekst = self.narzedzie("sprawdz_plan", plan=PLAN)
        self.assertNotIn("isError", w)
        self.assertIn("T1", tekst)
        self.assertIn("125-148", tekst)
        w, tekst = self.narzedzie("wyslij_plan", plan=json.dumps(PLAN))
        self.assertEqual(json.loads(tekst)["wynik"], {"wyslane": 1, "pominiete": 0, "bledy": 0})
        self.assertEqual(mcp_serwer._klient.wywolania[-1], ("schedule", "501", DZIEN))

    def test_bledny_plan_to_blad_narzedzia(self):
        w, tekst = self.narzedzie("sprawdz_plan", plan={"treningi": []})
        self.assertTrue(w["isError"])
        self.assertIn("treningi", tekst)

    def test_ustawienia_z_serwera(self):
        os.environ["USTAWIENIA"] = json.dumps({"strefy": {"bieg": {"S2": [130, 150]}}})
        plan_bez_stref = {"treningi": [PLAN["treningi"][0]]}
        _, tekst = self.narzedzie("sprawdz_plan", plan=plan_bez_stref)
        self.assertIn("130-150", tekst)
        _, tekst = self.narzedzie("format_planu")
        self.assertIn("S2 130-150", tekst)
        self.assertNotIn("ROZPISKA OD TRENERA", tekst)

    # --- token Garmina
    def test_brak_i_zly_token_garmina(self):
        os.environ.pop("GARMIN_TOKEN")
        w, tekst = self.narzedzie("kalendarz")
        self.assertTrue(w["isError"])
        self.assertIn("GARMIN_TOKEN", tekst)
        os.environ["GARMIN_TOKEN"] = "podrobka"
        w, tekst = self.narzedzie("kalendarz")
        self.assertIn("nieprawidłowy", tekst)
        os.environ["GARMIN_TOKEN"] = web.szyfruj("token", TOKEN)
        AtrapaGarmin.zly_token = True
        w, tekst = self.narzedzie("kalendarz")
        self.assertIn("wygasło", tekst)

    # --- dane
    def test_aktywnosci_i_trening(self):
        _, tekst = self.narzedzie("aktywnosci", od="2026-11-01", do="2026-11-10", sport="cycling")
        a = json.loads(tekst)[0]
        self.assertEqual((a["id"], a["dystans_km"], a["moc_np_w"], a["tss"], a["predkosc_sr_kmh"]),
                         (111, 30.0, 220, 70, 30.0))
        self.assertIn(("aktywnosci", "2026-11-01", "2026-11-10", "cycling"), mcp_serwer._klient.wywolania)
        _, tekst = self.narzedzie("trening", activity_id=111)
        t = json.loads(tekst)
        self.assertEqual(t["podsumowanie"]["moc_np_w"], 222)
        self.assertEqual(t["okrazenia"][0]["moc_sr_w"], 250)
        self.assertEqual(t["strefy_hr"][0]["min"], 30.0)

    def test_zakresy_dat(self):
        w, tekst = self.narzedzie("wellness", od="2026-01-01", do="2026-03-01")
        self.assertTrue(w["isError"])
        w, tekst = self.narzedzie("aktywnosci", od="zla")
        self.assertIn("RRRR-MM-DD", tekst)

    def test_wellness_i_gotowosc(self):
        _, tekst = self.narzedzie("wellness", od="2026-11-01", do="2026-11-02")
        dni = json.loads(tekst)
        self.assertEqual(len(dni), 2)
        self.assertEqual((dni[0]["hr_spoczynkowe"], dni[0]["sen_h"], dni[0]["sleep_score"]), (48, 7.5, 82))
        self.assertIn("hrv", dni[0]["bledy"])          # błąd jednego źródła nie psuje reszty
        _, tekst = self.narzedzie("gotowosc", data="2026-11-02")
        g = json.loads(tekst)
        self.assertEqual(g["training_readiness"]["wynik"], 71)
        self.assertEqual(g["training_status"]["status"], "PRODUCTIVE")
        self.assertEqual(g["training_status"]["acwr"], 1.11)

    def test_kalendarz(self):
        k = AtrapaDane()
        d = DZIS + datetime.timedelta(days=2)
        k.kalendarz = {(d.year, d.month): [{"id": 9, "workoutId": 11, "title": "T1", "date": d.isoformat(),
                                            "itemType": "workout"},
                                           {"id": 10, "title": "Bieg", "date": d.isoformat(), "itemType": "activity"}]}
        wynik = garmin_dane.kalendarz(k, DZIS.isoformat(), (DZIS + datetime.timedelta(days=5)).isoformat())
        self.assertEqual(wynik, [{"data": d.isoformat(), "nazwa": "T1", "workout_id": 11, "wpis_id": 9}])

    def test_usun_z_kalendarza(self):
        w, _ = self.narzedzie("usun_z_kalendarza", wpis_id="abc")
        self.assertTrue(w["isError"])
        self.narzedzie("usun_z_kalendarza", wpis_id=9)
        self.assertEqual(mcp_serwer._klient.wywolania[-1], ("unschedule", "9"))

    def test_start_pokazuje_stan_connectora(self):
        r = self.c.get("/api/start").get_json()
        self.assertEqual(r["mcp"], {"wlaczony": True, "token_garmina": True})


PLAN_SILA = json.dumps({"treningi": [{"nazwa": "Siła A", "sport": "silowy", "kroki": [
    {"typ": "rozgrzewka", "czas": "8:00"},
    {"cwiczenie": "Lat Pull-down", "serie": 2, "powtorzenia": 10, "ciezar": "50kg", "przerwa": "1:30"},
    {"cwiczenie": "Plank", "serie": 1, "czas": "0:45"}]}]})


def _workout_sila():
    w = plan.wczytaj(PLAN_SILA)[0]["w"]
    return {**w, "workoutId": 1769460084}


def _fit_indeksy(w):
    return garmin_dane._indeksy_fit(w["workoutSegments"][0]["workoutSteps"])


class AtrapaSila(AtrapaDane):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.w = _workout_sila()

    def get_activity(self, aid):
        if str(aid) == "25145798508":
            return {"activityId": 25145798508, "activityTypeDTO": {"typeKey": "strength_training"}, "summaryDTO": {}}
        return {"activityId": int(aid), "activityName": "Siła A", "activityTypeDTO": {"typeKey": "strength_training"},
                "summaryDTO": {"associatedWorkoutId": 1769460084, "startTimeLocal": "2026-11-22T12:31:11"}}

    def get_workout_by_id(self, wid):
        return self.w

    def get_activity_exercise_sets(self, aid):
        idx = _fit_indeksy(self.w)
        kroki = {}
        def zbierz(lista):
            for st in lista:
                if st.get("type") == "RepeatGroupDTO":
                    zbierz(st["workoutSteps"])
                elif st.get("category"):
                    kroki[st["category"]] = idx.index(st["stepOrder"])
        zbierz(self.w["workoutSegments"][0]["workoutSteps"])
        lat = [k for k in kroki if k != "PLANK"][0]
        return {"exerciseSets": [
            {"setType": "ACTIVE", "repetitionCount": 10, "weight": 50000.0, "duration": 40.0,
             "exercises": [{"category": lat, "name": "LAT_PULLDOWN", "probability": 90}], "wktStepIndex": kroki[lat]},
            {"setType": "REST", "duration": 90.0, "exercises": []},
            {"setType": "ACTIVE", "repetitionCount": 8, "weight": 50000.0, "duration": 35.0,
             "exercises": [{"category": lat, "name": "LAT_PULLDOWN", "probability": 90}], "wktStepIndex": kroki[lat]},
            {"setType": "ACTIVE", "repetitionCount": None, "weight": None, "duration": 45.0,
             "exercises": [{"category": "PLANK", "name": "PLANK", "probability": 99}], "wktStepIndex": kroki["PLANK"]},
        ]}

    def download_activity(self, aid, dl_fmt=None):
        return b"PK\x03\x04zip"


class TestMcpPlanVsWykonanie(BazaMcp):
    def setUp(self):
        super().setUp()
        garmin_io._nowy_klient = AtrapaSila

    def test_serie(self):
        _, tekst = self.narzedzie("serie", activity_id=25162650914)
        d = json.loads(tekst)
        self.assertEqual(d["podsumowanie"], {"serie_aktywne": 3, "powtorzenia": 18, "tonaz_kg": 900.0})
        lat = [c for c in d["cwiczenia"] if c["cwiczenie"].endswith("LAT_PULLDOWN")][0]
        self.assertEqual((lat["serie"], lat["ciezar_max_kg"], lat["tonaz_kg"]), (2, 50.0, 900.0))
        self.assertEqual(d["serie"][1]["typ"], "przerwa")
        self.assertIsNone(d["serie"][1]["powtorzenia"])          # brak danych = null, klucz jest

    def test_plan_treningu(self):
        _, tekst = self.narzedzie("plan_treningu", workout_id=1769460084)
        d = json.loads(tekst)
        self.assertEqual(d["nazwa"], "Siła A")
        blok = [k for k in d["kroki"] if k["typ"] == "powtorz"][0]
        self.assertEqual(blok["powtorzenia_bloku"], 2)
        cw = blok["kroki"][0]
        self.assertEqual((cw["powtorzenia"], cw["ciezar_kg"]), (10, 50.0))
        self.assertNotIn("_kategoria", cw)

    def test_plan_vs_wykonanie_sila(self):
        _, tekst = self.narzedzie("plan_vs_wykonanie", activity_id=25162650914)
        d = json.loads(tekst)
        self.assertEqual(d["rodzaj"], "silowy")
        self.assertEqual(d["dopasowanie_krokow"], "fit")
        lat = [k for k in d["kroki"] if k["cwiczenie_plan"] and "PLANK" not in k["cwiczenie_plan"]][0]
        self.assertEqual((lat["serie_plan"], lat["serie_wykonane"], lat["powtorzenia_wykonane"]), (2, 2, [10, 8]))
        self.assertEqual(lat["odchylenie"], ["powtórzenia"])      # 18 zamiast 20
        plank = [k for k in d["kroki"] if k["cwiczenie_plan"] and "PLANK" in k["cwiczenie_plan"]][0]
        self.assertEqual(plank["serie_wykonane"], 1)

    def test_plan_vs_wykonanie_bez_planu(self):
        w, tekst = self.narzedzie("plan_vs_wykonanie", activity_id=25145798508)
        self.assertNotIn("isError", w)
        self.assertIsNone(json.loads(tekst)["workout_id"])

    def test_pobierz_fit_link(self):
        _, tekst = self.narzedzie("pobierz_fit", activity_id=25162650914, zasil_baze=True)
        d = json.loads(tekst)
        link = d["pliki"][0]["link"]
        self.assertIn("/fit/", link)
        self.assertIn("Niewykonane", d["zasil_baze"])
        r = self.c.get(link.replace("http://localhost", ""))
        self.assertEqual(r.status_code, 200)
        self.assertIn("25162650914_ACTIVITY.zip", r.headers["Content-Disposition"])
        self.assertEqual(self.c.get("/fit/podrobka").status_code, 403)

    def test_surowe_dane(self):
        _, tekst = self.narzedzie("surowe_dane", zrodlo="trening_planowany", id=1769460084)
        self.assertEqual(json.loads(tekst)["workoutName"], "Siła A")
        w, tekst = self.narzedzie("surowe_dane", zrodlo="cos", id=1)
        self.assertTrue(w["isError"])


class TestPlanVsWykonanieBieg(unittest.TestCase):
    def test_okrazenia(self):
        w = plan.wczytaj(json.dumps({"treningi": [{"nazwa": "B", "sport": "bieg", "kroki": [
            {"typ": "rozgrzewka", "czas": "10:00"},
            {"powtorz": 2, "kroki": [{"typ": "interwal", "dystans": "1km", "cel": "HR 160-170"},
                                    {"typ": "przerwa", "czas": "2:00"}]}]}]}))[0]["w"]

        class K:
            def get_activity(self, aid):
                return {"activityTypeDTO": {"typeKey": "running"}, "associatedWorkoutId": 5, "summaryDTO": {}}

            def get_workout_by_id(self, wid):
                return w

            def get_activity_splits(self, aid):
                return {"lapDTOs": [{"duration": 600, "distance": 1800, "averageHR": 130},
                                    {"duration": 240, "distance": 1000, "averageHR": 165, "averageSpeed": 4.17},
                                    {"duration": 120, "distance": 300, "averageHR": 140},
                                    {"duration": 250, "distance": 1000, "averageHR": 178},
                                    {"duration": 120, "distance": 300, "averageHR": 140}]}
        d = garmin_dane.plan_vs_wykonanie(K(), 1)
        self.assertEqual(d["rodzaj"], "wytrzymalosc")
        self.assertEqual(len(d["kroki"]), 5)
        self.assertIsNone(d["uwaga"])
        self.assertIsNone(d["kroki"][1]["odchylenie"])
        self.assertEqual(d["kroki"][1]["tempo_min_km"], "4:00")
        self.assertEqual(d["kroki"][3]["odchylenie"], ["poza celem"])


if __name__ == "__main__":
    unittest.main()
