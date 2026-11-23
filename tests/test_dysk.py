# -*- coding: utf-8 -*-
"""Testy zapisu FIT na Dysk Google na atrapie API Google (bez sieci)."""
import json
import os
import sys
import unittest
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import dysk_google  # noqa: E402
import garmin_io  # noqa: E402
import mcp_serwer  # noqa: E402
import web  # noqa: E402
from tests.test_mcp import BazaMcp, AtrapaSila  # noqa: E402


class Odp:
    def __init__(self, status=200, dane=None):
        self.status_code, self._d = status, dane if dane is not None else {}
        self.content = json.dumps(self._d).encode()
        self.text = self.content.decode()

    def json(self):
        return self._d


class AtrapaGoogle:
    """Udaje token endpoint i Drive v3: foldery, wyszukiwanie po nazwie, upload multipart."""
    def __init__(self):
        self.pliki = {}          # id -> {name, parents, mimeType}
        self.n = 0
        self.uploady = 0
        self.zly_refresh = False

    def post(self, url, data=None, timeout=None):
        assert url == dysk_google.URL_TOKEN
        if data["grant_type"] == "authorization_code":
            return Odp(200, {"access_token": "a1", "refresh_token": "r1", "expires_in": 3600})
        if self.zly_refresh or data["refresh_token"] != "r1":
            return Odp(400, {"error": "invalid_grant"})
        return Odp(200, {"access_token": "a1", "expires_in": 3600})

    def request(self, metoda, url, headers=None, params=None, json=None, data=None, timeout=None):
        assert headers["Authorization"] == "Bearer a1"
        if metoda == "GET":
            q = params["q"]
            nazwa = q.split("name = '")[1].split("' and")[0].replace("\\'", "'")
            rodzic = q.split("and '")[1].split("' in parents")[0]
            pasuje = [{"id": i, "name": f["name"], "webViewLink": f"https://drive/{i}"} for i, f in self.pliki.items()
                      if f["name"] == nazwa and rodzic in f["parents"]]
            return Odp(200, {"files": pasuje})
        self.n += 1
        fid = f"f{self.n}"
        if url == dysk_google.URL_UPLOAD:
            self.uploady += 1
            meta = __import__("json").loads(data.split(b"\r\n\r\n", 1)[1].split(b"\r\n--", 1)[0])
            self.pliki[fid] = {**meta, "mimeType": "application/zip"}
            return Odp(200, {"id": fid, "name": meta["name"], "webViewLink": f"https://drive/{fid}"})
        self.pliki[fid] = json
        return Odp(200, {"id": fid})


class TestDysk(BazaMcp):
    def setUp(self):
        super().setUp()
        garmin_io._nowy_klient = AtrapaSila
        self.g = AtrapaGoogle()
        self.stary_http = dysk_google._http
        dysk_google._http = self.g
        dysk_google.resetuj_cache()
        for k in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_TOKEN", "DYSK_FOLDER"):
            self.env.setdefault(k, os.environ.get(k))
        os.environ["GOOGLE_CLIENT_ID"], os.environ["GOOGLE_CLIENT_SECRET"] = "cid", "sec"
        os.environ.pop("GOOGLE_TOKEN", None)
        os.environ.pop("DYSK_FOLDER", None)

    def tearDown(self):
        dysk_google._http = self.stary_http
        dysk_google.resetuj_cache()
        super().tearDown()

    def test_bez_polaczenia_domyslnie_link(self):
        _, tekst = self.narzedzie("pobierz_fit", activity_id=25162650914)
        self.assertEqual(json.loads(tekst)["cel"], "link")
        w, tekst = self.narzedzie("pobierz_fit", activity_id=25162650914, cel="dysk")
        self.assertTrue(w["isError"])
        self.assertIn("polacz_dysk_google", tekst)

    def test_wgrywanie_bez_dublowania(self):
        os.environ["GOOGLE_TOKEN"] = web.szyfruj("google", "r1")
        _, tekst = self.narzedzie("pobierz_fit", activity_id=25162650914)
        d = json.loads(tekst)
        self.assertEqual(d["cel"], "dysk")
        self.assertEqual(d["folder"], "Dane treningowe/FIT (surowe pliki z Garmina)")
        self.assertEqual((d["pliki"][0]["status"], d["pliki"][0]["nazwa"]), ("wgrany", "25162650914_ACTIVITY.zip"))
        foldery = [f for f in self.g.pliki.values() if f.get("mimeType") == dysk_google.MIME_FOLDER]
        self.assertEqual([f["name"] for f in foldery], ["Dane treningowe", "FIT (surowe pliki z Garmina)"])
        dysk_google.resetuj_cache()                      # drugi raz: foldery i plik już są
        _, tekst = self.narzedzie("pobierz_fit", activity_id=25162650914)
        self.assertEqual(json.loads(tekst)["pliki"][0]["status"], "juz_byl")
        self.assertEqual(self.g.uploady, 1)
        self.assertEqual(len([f for f in self.g.pliki.values() if f.get("mimeType") == dysk_google.MIME_FOLDER]), 2)

    def test_cofniety_token(self):
        os.environ["GOOGLE_TOKEN"] = web.szyfruj("google", "r1")
        self.g.zly_refresh = True
        _, tekst = self.narzedzie("pobierz_fit", activity_id=25162650914)
        p = json.loads(tekst)["pliki"][0]
        self.assertEqual(p["status"], "blad")
        self.assertIn("polacz_dysk_google", p["blad"])

    def test_polaczenie_oauth(self):
        _, tekst = self.narzedzie("polacz_dysk_google")
        d = json.loads(tekst)
        self.assertFalse(d["polaczony"])
        r = self.c.get(d["link"].replace("http://localhost", ""))
        self.assertEqual(r.status_code, 302)
        u = urlparse(r.headers["Location"])
        q = parse_qs(u.query)
        self.assertEqual(u.netloc, "accounts.google.com")
        self.assertEqual(q["scope"], [dysk_google.ZAKRES])
        self.assertEqual(q["redirect_uri"], ["http://localhost/google/powrot"])
        self.assertEqual(q["access_type"], ["offline"])
        r = self.c.get("/google/powrot", query_string={"code": "kod", "state": q["state"][0]})
        self.assertEqual(r.status_code, 200)
        wartosc = r.data.decode().split("readonly rows=\"6\" cols=\"80\">")[1].split("</textarea>")[0]
        self.assertEqual(web.odszyfruj("google", wartosc), "r1")
        self.assertEqual(self.c.get("/google/powrot", query_string={"code": "k", "state": "podrobka"}).status_code, 403)
        self.assertEqual(self.c.get("/google/polacz/podrobka").status_code, 403)

    def test_brak_klienta(self):
        os.environ.pop("GOOGLE_CLIENT_ID")
        w, tekst = self.narzedzie("polacz_dysk_google")
        self.assertTrue(w["isError"])
        self.assertIn("/google/powrot", tekst)


if __name__ == "__main__":
    unittest.main()
