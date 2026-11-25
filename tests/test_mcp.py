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
                         "serie", "plan_treningu", "plan_vs_wykonanie", "pobierz_fit", "surowe_dane",
                         "strumienie", "sklad_ciala", "historia_metryk", "sprzet", "rekordy", "prognozy"} <= nazwy)
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
        w, tekst = self.narzedzie("wellness", od="2026-01-01", do="2026-06-01")
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
                                           {"id": 9, "workoutId": 11, "title": "T1", "date": d.isoformat(),
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

    def test_serie_uzupelnia_nazwe_z_planu(self):
        class Niepelna(AtrapaSila):
            def get_activity_exercise_sets(self, aid):
                d = super().get_activity_exercise_sets(aid)
                d["exerciseSets"][0]["exercises"] = [{"category": "UNKNOWN", "name": None, "probability": 50}]
                return d
        garmin_io._nowy_klient = Niepelna
        _, tekst = self.narzedzie("serie", activity_id=25162650914)
        d = json.loads(tekst)
        self.assertTrue(d["nazwy_uzupelnione_z_planu"])
        self.assertTrue(d["serie"][0]["nazwa_z_planu"])
        self.assertEqual(d["serie"][0]["cwiczenie"], d["serie"][2]["cwiczenie"])   # ta sama nazwa co 2. seria
        self.assertNotIn("_kat", d["serie"][0])
        lat = [c for c in d["cwiczenia"] if "UNKNOWN" not in c["cwiczenie"]]
        self.assertEqual(len(lat), len(d["cwiczenia"]))

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


class AtrapaP2(AtrapaDane):
    """Odpowiedzi w kształcie żywego API (wartości wymyślone)."""

    def get_activity(self, aid):
        if str(aid) == "2":
            return {"activityId": 2, "activityTypeDTO": {"typeKey": "lap_swimming"}, "summaryDTO": {
                "duration": 1800, "distance": 450, "averageSpeed": 0.5, "poolLength": 25.0, "numberOfActiveLengths": 18,
                "averageSWOLF": 84.0, "averageStrokes": 12.1, "totalNumberOfStrokes": 217, "averageSwimCadence": 9.0,
                "directWorkoutRpe": 50, "directWorkoutFeel": 50}}
        return {"activityId": 1, "activityTypeDTO": {"typeKey": "running"}, "summaryDTO": {
            "duration": 3600, "elapsedDuration": 3600, "distance": 12000, "averageSpeed": 3.333, "averageMovingSpeed": 3.333,
            "avgGradeAdjustedSpeed": 3.4, "averagePower": 344.0, "averageRunCadence": 180.0, "groundContactTime": 252.7,
            "groundContactBalanceLeft": 50.58, "verticalOscillation": 7.82, "verticalRatio": 6.81, "strideLength": 113.76,
            "directWorkoutRpe": 80, "directWorkoutFeel": 25, "directWorkoutComplianceScore": 53}}

    def get_activity_splits(self, aid):
        if str(aid) == "2":
            return {"lapDTOs": [{"lapIndex": 1, "duration": 300, "distance": 100, "averageSpeed": 0.4,
                                 "averageMovingSpeed": 0.5, "swimStroke": "FREESTYLE", "numberOfActiveLengths": 4,
                                 "averageSWOLF": 60, "averageStrokes": 10,
                                 "lengthDTOs": [{"lengthIndex": 1, "swimStroke": "FREESTYLE", "duration": 49.6,
                                                 "distance": 25.0, "averageSpeed": 0.504, "totalNumberOfStrokes": 10, "averageSWOLF": 60}]},
                                {"lapIndex": 2, "duration": 120, "distance": 0, "numberOfActiveLengths": 0,
                                 "averageSWOLF": 0, "averageStrokes": 0}]}
        return {"lapDTOs": [{"lapIndex": 1, "duration": 300, "distance": 1000, "averageSpeed": 3.333, "averagePower": 340,
                             "maxPower": 420}]}

    def get_activity_power_in_timezones(self, aid):
        return [{"zoneNumber": 1, "secsInZone": 600.0, "zoneLowBoundary": 256}]

    def get_activity_weather(self, aid):
        return {"temp": 61, "apparentTemp": 59, "relativeHumidity": 65, "windSpeed": 10,
                "windDirectionCompassPoint": "ne", "weatherTypeDTO": {"desc": "Partly Cloudy"}}

    def get_activity_gear(self, aid):
        return [{"displayName": "Buty", "customMakeModel": "Model X", "gearTypeName": "Shoes",
                 "gearStatusName": "active", "maximumMeters": 644000.0}]

    def get_activity_details(self, aid, maxchart=2000, maxpoly=0):
        self.wywolania.append(("details", maxchart))
        return {"metricDescriptors": [{"metricsIndex": 0, "key": "sumElapsedDuration"},
                                      {"metricsIndex": 1, "key": "directHeartRate"},
                                      {"metricsIndex": 2, "key": "directSpeed"}],
                "activityDetailMetrics": [{"metrics": [0.0, 120.0, 3.0]}, {"metrics": [10.0, 130.0, None]}]}

    def get_body_composition(self, od, do):
        return {"dateWeightList": [{"calendarDate": "2026-11-01", "weight": 75300.0, "bodyFat": 14.2,
                                    "muscleMass": 35100.0, "bmi": 23.1, "sourceType": "INDEX_SCALE"}],
                "totalAverage": {"weight": 75300.0, "bodyFat": 14.2}}

    def get_max_metrics_range(self, o, k):
        return [{"generic": {"calendarDate": "2026-11-02", "vo2MaxPreciseValue": 52.1, "sport": "RUNNING"},
                 "cycling": {"calendarDate": "2026-11-02", "vo2MaxPreciseValue": 52.8, "sport": "CYCLING"}}]

    def get_weekly_training_status(self, o, k):
        return {"reportData": {"1": [{"calendarDate": "2026-11-02", "trainingStatusFeedbackPhrase": "RECOVERY_1",
                                      "primaryTrainingDevice": True, "acuteTrainingLoadDTO": {
                                          "dailyTrainingLoadAcute": 92, "dailyTrainingLoadChronic": 223,
                                          "dailyAcuteChronicWorkloadRatio": 0.41}}]}}

    def get_training_readiness_range(self, o, k):
        return [{"calendarDate": "2026-11-02", "score": 40, "level": "LOW", "inputContext": "UPDATE_REALTIME_VARIABLES",
                 "timestamp": "2026-11-02T18:00"},
                {"calendarDate": "2026-11-02", "score": 69, "level": "MODERATE", "inputContext": "AFTER_WAKEUP_RESET",
                 "timestamp": "2026-11-02T07:00"}]

    def get_hrv_data_range(self, o, k):
        return {"hrvSummaries": [{"calendarDate": "2026-11-02", "lastNightAvg": 40, "weeklyAvg": 42,
                                  "status": "UNBALANCED", "baseline": {"balancedLow": 47, "balancedUpper": 74}}]}

    def get_rhr_daily(self, o, k):
        return [{"calendarDate": "2026-11-02", "value": 53.0}, {"calendarDate": "2026-11-03", "value": 55.0}]

    def get_endurance_score(self, o, k, aggregation="weekly"):
        return {"groupMap": {"2026-11-02": {"groupAverage": 7370}}}

    def get_hill_score(self, o, k):
        return {"hillScoreDTOList": [{"calendarDate": "2026-11-02", "overallScore": 37, "primaryTrainingDevice": True}]}

    def get_functional_threshold_power_range(self, o, k, sport="RUNNING"):
        return []

    def get_lactate_threshold(self, latest=True, start_date=None, end_date=None):
        return {"heart_rate": [{"from": "2026-11-02", "value": 175}],
                "speed": [{"from": "2026-11-02", "value": 0.3833}], "power": []}


class TestMcpP2(BazaMcp):
    def setUp(self):
        super().setUp()
        garmin_io._nowy_klient = AtrapaP2

    def test_trening_bieg(self):
        _, tekst = self.narzedzie("trening", activity_id=1)
        d = json.loads(tekst)
        self.assertEqual((d["rpe"], d["samopoczucie"], d["zgodnosc_z_planem_proc"]), (8.0, "słabo", 53))
        self.assertEqual(d["dynamika_biegu"]["dlugosc_kroku_m"], 1.14)
        self.assertEqual(d["podsumowanie"]["tempo_min_km"], "5:00")
        self.assertIsNone(d["plywanie"])
        self.assertEqual(d["strefy_mocy"][0], {"strefa": 1, "od_w": 256, "min": 10.0})
        self.assertEqual((d["pogoda"]["temp_c"], d["pogoda"]["wiatr_kmh"], d["pogoda"]["wiatr_kierunek"]), (16.1, 16, "NE"))
        self.assertEqual(d["sprzet"][0]["limit_km"], 644)
        self.assertEqual(d["okrazenia"][0]["moc_max_w"], 420)

    def test_trening_plywanie(self):
        _, tekst = self.narzedzie("trening", activity_id=2, dlugosci=True)
        d = json.loads(tekst)
        self.assertIsNone(d["dynamika_biegu"])
        self.assertEqual((d["plywanie"]["basen_m"], d["plywanie"]["swolf_sr"]), (25, 84))
        self.assertEqual((d["plywanie"]["tempo_100m"], d["plywanie"]["tempo_100m_z_przerwami"]), ("3:18", "3:20"))
        self.assertEqual(d["plywanie"]["tempo_100m_mediana"], "3:18")
        self.assertEqual(d["okrazenia"][1], {"nr": 2, "typ": "przerwa", "czas_min": 2.0})
        o = d["okrazenia"][0]
        self.assertEqual((o["styl"], o["dlugosci"], o["tempo_100m"], o["swolf"]), ("freestyle", 4, "3:20", 60))
        self.assertEqual(d["dlugosci"][0]["tempo_100m"], "3:18")

    def test_strumienie(self):
        _, tekst = self.narzedzie("strumienie", activity_id=1, metryki=["hr", "predkosc_kmh", "moc_w"], krok_s=10)
        d = json.loads(tekst)
        self.assertEqual(d["kolumny"], ["czas_s", "hr", "predkosc_kmh"])
        self.assertEqual(d["wiersze"], [[0, 120, 10.8], [10, 130, None]])
        self.assertEqual(d["brak_metryk"], ["moc_w"])
        self.assertIn(("details", 360), mcp_serwer._klient.wywolania)
        w, _ = self.narzedzie("strumienie", activity_id=1, metryki=["xyz"])
        self.assertTrue(w["isError"])

    def test_sklad_ciala(self):
        _, tekst = self.narzedzie("sklad_ciala", od="2026-10-01", do="2026-11-22")
        d = json.loads(tekst)
        self.assertEqual(d["pomiary"][0]["masa_kg"], 75.3)
        self.assertEqual(d["pomiary"][0]["masa_miesniowa_kg"], 35.1)
        w, _ = self.narzedzie("sklad_ciala", od="2026-01-01", do="2026-11-22", nawodnienie=True)
        self.assertTrue(w["isError"])

    def test_historia_metryk(self):
        _, tekst = self.narzedzie("historia_metryk", od="2026-11-01", do="2026-11-22", agregacja="dzien")
        d = json.loads(tekst)
        wiersz = dict(zip(d["kolumny"], d["wiersze"][0]))
        self.assertEqual(wiersz["data"], "2026-11-02")
        self.assertEqual((wiersz["vo2max_bieg"], wiersz["vo2max_rower"], wiersz["gotowosc"], wiersz["acwr"]),
                         (52.1, 52.8, 69, 0.41))
        self.assertEqual((wiersz["hrv_noc"], wiersz["hr_spoczynkowe"], wiersz["endurance_score"]), (40, 53, 7370))
        self.assertIn("ftp_rower_w", d["puste_zrodla"])
        self.assertEqual((wiersz["prog_hr"], wiersz["prog_tempo_min_km"]), (175, "4:21"))
        _, tekst = self.narzedzie("historia_metryk", od="2026-01-01", do="2026-11-22")
        d = json.loads(tekst)
        self.assertEqual(d["agregacja"], "tydzien")
        tydzien = dict(zip(d["kolumny"], d["wiersze"][0]))
        self.assertEqual((tydzien["data"], tydzien["hr_spoczynkowe"]), ("2026-11-02", 54.0))

    def test_wellness_pola(self):
        _, tekst = self.narzedzie("wellness", od="2026-11-01", do="2026-11-01", pola=["sen"])
        d = json.loads(tekst)[0]
        self.assertEqual(d["sen_h"], 7.5)
        self.assertNotIn("hr_spoczynkowe", d)
        w, _ = self.narzedzie("wellness", pola=["xyz"])
        self.assertTrue(w["isError"])


class AtrapaP3(AtrapaDane):
    def get_gear_list(self, start=0, limit=20, gear_statuses=None):
        return [{"uuid": "u-1", "gearType": "SHOES", "status": "ACTIVE", "name": "Buty", "brand": "Model X",
                 "maxUsageDistanceMeters": 644000.0, "numActivitiesLinked": 134, "durationUsedSeconds": 386855,
                 "distanceUsedMeters": 1141115.3, "firstUseDate": "2025-07-31", "daysUsed": 121}]

    def get_personal_record(self):
        return [{"typeId": 3, "activityType": "running", "value": 1199.75, "activityId": 5, "activityName": "Bieg",
                 "actStartDateTimeInGMTFormatted": "2026-10-11T07:59:29.0"},
                {"typeId": 10, "activityType": "road_biking", "value": 207.71},
                {"typeId": 20, "activityType": "lap_swimming", "value": 597.07},
                {"typeId": 7, "activityType": "running", "value": 21355.76},
                {"typeId": 9, "activityType": "road_biking", "value": 1056.0},
                {"typeId": 99, "activityType": None, "value": 1}]

    def get_race_predictions(self, startdate=None, enddate=None, _type=None):
        if _type == "monthly":
            return [{"calendarDate": "2026-10-31", "time5K": 1200, "time10K": 2500, "timeHalfMarathon": 5400,
                     "timeMarathon": 12300}]
        return {"calendarDate": "2026-11-23", "time5K": 1184, "time10K": 2450, "timeHalfMarathon": 5301,
                "timeMarathon": 12307}


class TestMcpP3(BazaMcp):
    def setUp(self):
        super().setUp()
        garmin_io._nowy_klient = AtrapaP3

    def test_sprzet(self):
        _, tekst = self.narzedzie("sprzet")
        b = json.loads(tekst)[0]
        self.assertEqual((b["typ"], b["status"], b["przebieg_km"], b["limit_km"], b["zuzycie_proc"], b["czas_h"]),
                         ("buty", "aktywny", 1141, 644, 177, 107.5))

    def test_rekordy(self):
        _, tekst = self.narzedzie("rekordy")
        r = json.loads(tekst)
        piec = [x for x in r if x["rekord"] == "5 km"][0]
        self.assertEqual((piec["czas"], piec["tempo_min_km"], piec["data"]), ("20:00", "4:00", "2026-10-11"))
        self.assertEqual([x for x in r if x["rekord"] == "najwyższa moc 20 min"][0]["moc_w"], 208)
        self.assertEqual([x for x in r if x["rekord"] == "400 m"][0]["tempo_100m"], "2:29")
        self.assertEqual([x for x in r if x["rekord"] == "najdłuższy bieg"][0]["dystans_km"], 21.36)
        self.assertEqual([x for x in r if x["rekord"] == "największe przewyższenie"][0]["przewyzszenie_m"], 1056)
        _, tekst = self.narzedzie("rekordy", sport="plywanie")
        self.assertEqual([x["rekord"] for x in json.loads(tekst)], ["400 m"])

    def test_prognozy(self):
        _, tekst = self.narzedzie("prognozy", od="2026-06-01", do="2026-11-22")
        d = json.loads(tekst)
        self.assertEqual(d["aktualne"]["półmaraton"], {"czas": "1:28:21", "tempo_min_km": "4:11"})
        self.assertEqual(d["aktualne"]["5 km"]["czas"], "19:44")
        self.assertEqual(d["miesiecznie"][0]["maraton"]["czas"], "3:25:00")


if __name__ == "__main__":
    unittest.main()
