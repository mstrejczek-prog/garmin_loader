# -*- coding: utf-8 -*-
"""Testy serwera webowego na atrapie biblioteki Garmina (bez sieci i bez prawdziwego logowania)."""
import datetime
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import garmin_io  # noqa: E402
import plan  # noqa: E402
import web  # noqa: E402
from tests.test_garmin_io import Atrapa  # noqa: E402

DZIEN = (datetime.date.today() + datetime.timedelta(days=3)).isoformat()
PLAN = json.dumps({"strefy": {"bieg": {"S2": [125, 148]}}, "treningi": [
    {"data": DZIEN, "nazwa": "T1", "kroki": [{"typ": "bieg", "czas": "30:00", "cel": "S2"}]},
    {"data": DZIEN, "wolne": True}]})
TOKEN = '{"di_token": "abc", "di_refresh_token": "def", "di_client_id": "x"}'


class _Wewn:
    def dumps(self):
        return TOKEN


class AtrapaGarmin(Atrapa):
    """Udaje garminconnect.Garmin: logowanie hasłem (z MFA lub bez), tokenem i resume_login."""
    mfa = False
    zly_token = False

    def __init__(self, email=None, haslo=None, return_on_mfa=False):
        super().__init__()
        self.email, self.haslo = email, haslo
        self.client = _Wewn()
        self.full_name = "Jan Biegacz"

    def login(self, tokenstore=None):
        from garminconnect import GarminConnectAuthenticationError
        if tokenstore:
            if AtrapaGarmin.zly_token or tokenstore != TOKEN:
                raise GarminConnectAuthenticationError("token odrzucony")
            return None, None
        if self.haslo != "dobre":
            raise GarminConnectAuthenticationError("złe hasło")
        if AtrapaGarmin.mfa:
            return "needs_mfa", {"sesja": "s1"}
        return None, None

    def resume_login(self, stan, kod):
        from garminconnect import GarminConnectAuthenticationError
        if stan != {"sesja": "s1"} or kod != "123456":
            raise GarminConnectAuthenticationError("zły kod")
        return None, None


class TestWeb(unittest.TestCase):
    def setUp(self):
        self.stary = garmin_io._nowy_klient
        garmin_io._nowy_klient = AtrapaGarmin
        AtrapaGarmin.mfa = AtrapaGarmin.zly_token = False
        web._proby.clear()
        web.KOD_DOSTEPU = ""
        self.c = web.app.test_client()

    def tearDown(self):
        garmin_io._nowy_klient = self.stary

    def post(self, sciezka, dane, **kw):
        return self.c.post(sciezka, json=dane, **kw)

    # --- strona i plan
    def test_strona_i_naglowki(self):
        r = self.c.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"Garmin Plan Loader", r.data)
        self.assertIn("default-src 'self'", r.headers["Content-Security-Policy"])
        self.assertIn("drive.file", self.c.get("/prywatnosc").get_data(as_text=True))
        for plik in ("/static/app.js", "/static/style.css", "/manifest.webmanifest", "/static/ikona-192.png"):
            self.assertEqual(self.c.get(plik).status_code, 200, plik)

    def test_start(self):
        d = self.c.get("/api/start").get_json()
        self.assertIn("treningi", d["przyklad"])
        self.assertFalse(d["kod_dostepu"])

    def test_podglad(self):
        d = self.post("/api/podglad", {"plan": PLAN}).get_json()
        t = d["treningi"]
        self.assertEqual([x["wolne"] for x in t], [False, True])
        self.assertEqual(t[0]["nazwa"], "T1")
        self.assertEqual(t[0]["minuty"], 30)
        self.assertEqual(t[0]["poziomy"], [2])
        self.assertEqual(t[0]["data"], DZIEN)

    def test_podglad_przyklad(self):
        d = self.post("/api/podglad", {"plan": plan.PRZYKLAD_PLANU}).get_json()
        self.assertEqual(sum(1 for x in d["treningi"] if not x["wolne"]), 3)

    def test_podglad_bledu_i_pusty(self):
        self.assertIn("JSON", self.post("/api/podglad", {"plan": "bez nawiasów"}).get_json()["blad"])
        self.assertEqual(self.post("/api/podglad", {"plan": "  "}).get_json(), {"treningi": []})

    def test_podglad_uzywa_stref_z_przegladarki(self):
        tekst = json.dumps({"treningi": [{"nazwa": "A", "kroki": [{"typ": "bieg", "czas": "30:00", "cel": "S2"}]}]})
        self.assertIn("blad", self.post("/api/podglad", {"plan": tekst}).get_json())
        ust = {"strefy": {"bieg": {"S2": [125, 148]}}}
        self.assertNotIn("blad", self.post("/api/podglad", {"plan": tekst, "ust": ust}).get_json())

    def test_uszkodzone_ustawienia_ignorowane(self):
        self.assertIn("treningi", self.post("/api/podglad", {"plan": PLAN, "ust": "śmieci"}).get_json())

    def test_prompt_zna_strefy(self):
        ust = {"strefy": {"bieg": {f"S{i}": [100 + 10 * i, 109 + 10 * i] for i in range(1, 6)}}}
        p = self.post("/api/prompt", {"ust": ust}).get_json()["prompt"]
        self.assertIn("S1 110-119", p)

    def test_strefy_z_formularza(self):
        pola = {"bieg": {f"S{i}": [str(100 + 10 * i), str(109 + 10 * i)] for i in range(1, 6)}, "ftp": "215"}
        d = self.post("/api/strefy", {"pola": pola}).get_json()
        self.assertEqual(d["ust"]["ftp"], 215)
        self.assertEqual(d["ust"]["strefy"]["bieg"]["S1"], [110, 119])
        r = self.post("/api/strefy", {"pola": {"bieg": {"S1": ["80", "120"]}}})
        self.assertEqual(r.status_code, 400)
        self.assertIn("wypełnij wszystkie", r.get_json()["blad"])

    def test_moc_z_ftp(self):
        self.assertEqual(self.post("/api/moc-z-ftp", {"ftp": "200"}).get_json()["moc"]["P1"], [0, 110])
        self.assertEqual(self.post("/api/moc-z-ftp", {"ftp": ""}).status_code, 400)

    # --- logowanie
    def test_logowanie_bez_mfa(self):
        d = self.post("/api/logowanie", {"email": "a@b.pl", "haslo": "dobre"}).get_json()
        self.assertEqual(d["stan"], "ok")
        self.assertEqual(d["konto"], "Jan Biegacz")
        self.assertNotIn("abc", d["token"])                       # token jest zaszyfrowany
        self.assertEqual(web.odszyfruj("token", d["token"]), TOKEN)

    def test_zle_haslo(self):
        r = self.post("/api/logowanie", {"email": "a@b.pl", "haslo": "złe"})
        self.assertEqual(r.status_code, 401)
        self.assertIn("Nie udało się zalogować", r.get_json()["blad"])

    def test_logowanie_z_mfa(self):
        AtrapaGarmin.mfa = True
        d = self.post("/api/logowanie", {"email": "a@b.pl", "haslo": "dobre"}).get_json()
        self.assertEqual(d["stan"], "mfa")
        self.assertNotIn("s1", d["mfa"])
        self.assertEqual(self.post("/api/mfa", {"mfa": d["mfa"], "kod": "000"}).status_code, 401)
        ok = self.post("/api/mfa", {"mfa": d["mfa"], "kod": "123456"}).get_json()
        self.assertEqual(ok["stan"], "ok")

    def test_mfa_podrobiony_stan(self):
        r = self.post("/api/mfa", {"mfa": "podrobka", "kod": "123456"})
        self.assertTrue(r.get_json()["od_nowa"])

    def test_stan_mfa_nie_jest_tokenem(self):
        mfa = web.szyfruj("mfa", {"sesja": "s1"})
        r = self.post("/api/wyslij", {"plan": PLAN, "token": mfa})
        self.assertEqual(r.status_code, 401)

    def test_limit_prob_logowania(self):
        for _ in range(web.LIMIT_LOGOWAN[0]):
            self.post("/api/logowanie", {"email": "a@b.pl", "haslo": "złe"})
        self.assertEqual(self.post("/api/logowanie", {"email": "a@b.pl", "haslo": "dobre"}).status_code, 429)

    # --- wysyłka
    def linie(self, r):
        return [json.loads(l) for l in r.get_data(as_text=True).splitlines() if l.strip()]

    def test_wysylka_strumien(self):
        tok = web.szyfruj("token", TOKEN)
        r = self.post("/api/wyslij", {"plan": PLAN, "token": tok})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.mimetype, "application/x-ndjson")
        linie = self.linie(r)
        logi = [x["log"] for x in linie if "log" in x]
        self.assertIn("T1: wysłano", logi)
        self.assertTrue(any("kalendarz: wstawiono" in l for l in logi))
        koniec = linie[-1]
        self.assertEqual(koniec["koniec"], {"wyslane": 1, "pominiete": 0, "bledy": 0})
        self.assertEqual(web.odszyfruj("token", koniec["token"]), TOKEN)

    def test_wysylka_wygasly_token(self):
        AtrapaGarmin.zly_token = True
        r = self.post("/api/wyslij", {"plan": PLAN, "token": web.szyfruj("token", TOKEN)})
        self.assertEqual(r.status_code, 401)
        self.assertTrue(r.get_json()["wygaslo"])

    def test_wysylka_bez_tokenu_i_z_bledem_planu(self):
        self.assertEqual(self.post("/api/wyslij", {"plan": PLAN}).status_code, 401)
        r = self.post("/api/wyslij", {"plan": "{}", "token": web.szyfruj("token", TOKEN)})
        self.assertEqual(r.status_code, 400)
        wolne = json.dumps({"treningi": [{"data": DZIEN, "wolne": True}]})
        self.assertIn("same dni wolne", self.post("/api/wyslij", {"plan": wolne, "token": "x"}).get_json()["blad"])

    # --- kod dostępu
    def test_kod_dostepu(self):
        web.KOD_DOSTEPU = "tajne"
        self.assertTrue(self.c.get("/api/start").get_json()["kod_dostepu"])     # start działa bez kodu
        self.assertEqual(self.post("/api/podglad", {"plan": PLAN}).status_code, 403)
        r = self.post("/api/podglad", {"plan": PLAN}, headers={"X-Kod-Dostepu": "tajne"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.c.get("/").status_code, 200)                    # sama strona też


if __name__ == "__main__":
    unittest.main()
