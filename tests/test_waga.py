# -*- coding: utf-8 -*-
"""Testy łącznika wagi (Zepp -> Garmin + arkusz) na atrapach: bez sieci, Dysku i prawdziwych kont."""
import datetime as dt
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import dysk_google  # noqa: E402
import mcp_serwer  # noqa: E402
import waga  # noqa: E402
import web  # noqa: E402
import zepp  # noqa: E402

HASLO = "Tajne-Haslo-123"
APP_TOKEN = "APPTOKEN-sekret"
LOGIN_TOKEN = "LOGINTOKEN-sekret"


def rekord(ts, masa, fat=20.5, imp=480, member="-1", typ=0, **inne):
    s = {"weight": masa, "bmi": 23.1, "fatRate": fat, "muscleRate": 55.2, "bodyWaterRate": 56.0,
         "boneMass": 2.9, "metabolism": 1650, "visceralFat": 8, "muscleAge": 30, "bodyStyle": 5, **inne}
    if imp is not None:
        s["impedance"] = imp
    return {"generatedTime": ts, "weightType": typ, "memberId": member, "summary": s}


T0 = int(dt.datetime(2026, 11, 20, 7, 15, tzinfo=waga.STREFA).timestamp())


class AtrapaGarminWagi:
    def __init__(self):
        self.pomiary = []          # (ts_s, kg)
        self.wyslane = []

    def get_body_composition(self, od, do):
        return {"dateWeightList": [{"timestampGMT": t * 1000, "weight": kg * 1000} for t, kg in self.pomiary]}

    def add_body_composition(self, timestamp, weight, **kw):
        t = int(dt.datetime.fromisoformat(timestamp).timestamp())
        self.pomiary.append((t, weight))
        self.wyslane.append({"timestamp": timestamp, "weight": weight, **kw})
        return {}


class AtrapaDysku:
    """Pliki tekstowe na Dysku + arkusz (Sheets API) w pamięci."""

    def __init__(self, naglowki=None):
        self.pliki = {}
        self.arkusz = [list(naglowki or waga.NAGLOWKI)]

    def odczytaj_tekst(self, refresh, nazwa, rodzic="root"):
        return self.pliki.get(nazwa)

    def zapisz_tekst(self, refresh, nazwa, tekst, rodzic="root"):
        self.pliki[nazwa] = tekst
        return "id"

    def zapytanie(self, metoda, url, refresh, **kw):
        assert "sheets.googleapis.com" in url
        if metoda == "GET":
            return {"values": self.arkusz}
        assert kw["params"]["valueInputOption"] == "RAW"
        self.arkusz.extend(kw["json"]["values"])
        return {}


class Baza(unittest.TestCase):
    def setUp(self):
        self.dysk = AtrapaDysku()
        self.garmin = AtrapaGarminWagi()
        self.zepp_rekordy = []
        self.zepp_wywolania = []
        self.env = mock.patch.dict(os.environ, {"WAGA_MIN": "65", "WAGA_MAX": "85", "WAGA_TOKEN": "wt",
                                                "ZEPP_EMAIL": "ja@example.com", "ZEPP_PASSWORD": HASLO})
        self.env.start()
        os.environ.pop("WAGA_PROFIL", None)

        def rekordy_z_sesja(stan, email, haslo, od_czasu=0, pozwol_haslo=True):
            self.zepp_wywolania.append(od_czasu)
            kroki = ["token"] if stan else ["haslo"]
            nowy = dict(stan or {"user_id": "u1", "app_token": APP_TOKEN, "login_token": LOGIN_TOKEN})
            return [r for r in self.zepp_rekordy if r["generatedTime"] >= od_czasu], nowy, kroki

        self.latki = [
            mock.patch.object(dysk_google, "odczytaj_tekst", self.dysk.odczytaj_tekst),
            mock.patch.object(dysk_google, "zapisz_tekst", self.dysk.zapisz_tekst),
            mock.patch.object(dysk_google, "_zapytanie", self.dysk.zapytanie),
            mock.patch.object(mcp_serwer, "_refresh_google", lambda: "refresh"),
            mock.patch.object(mcp_serwer, "_z_garminem", lambda fn: fn(self.garmin)),
            mock.patch.object(zepp, "rekordy_z_sesja", rekordy_z_sesja),
            mock.patch.object(waga, "_spij", lambda s: None),
        ]
        for l in self.latki:
            l.start()
        self.c = web.app.test_client()

    def tearDown(self):
        for l in self.latki:
            l.stop()
        self.env.stop()

    def post(self, tryb, token="wt"):
        r = self.c.post(f"/waga/{tryb}", headers={"Authorization": f"Bearer {token}"})
        return r.status_code, r.get_json()

    def stan(self):
        return waga.wczytaj_stan("refresh")


class TestPomiary(unittest.TestCase):
    def test_normalizacja_bez_przeliczen_i_czas_warszawski(self):
        p = waga.normalizuj(rekord(T0, 75.35))
        self.assertEqual((p["data"], p["godzina"], p["masa"], p["tluszcz"], p["miesnie"]),
                         ("2026-11-20", "07:15", 75.35, 20.5, 55.2))

    def test_bez_impedancji_tylko_masa(self):
        p = waga.normalizuj(rekord(T0, 75.0, imp=0))
        self.assertEqual(p["masa"], 75.0)
        self.assertFalse(p["impedancja"])
        self.assertTrue(all(p[k] is None for k in ("tluszcz", "miesnie", "woda", "kosci")))

    def test_zera_to_brak_a_uszkodzone_rekordy_pomijane(self):
        p = waga.normalizuj(rekord(T0, 75.0, boneMass=0))
        self.assertIsNone(p["kosci"])
        self.assertIsNone(waga.normalizuj(rekord(T0, 75.0, typ=3)))
        self.assertIsNone(waga.normalizuj({"generatedTime": T0, "summary": {}}))

    def test_filtr_zakresu_i_profilu(self):
        pom = [waga.normalizuj(rekord(T0, 75)), waga.normalizuj(rekord(T0 + 1, 50)),
               waga.normalizuj(rekord(T0 + 2, 76, member="123456"))]
        ok, zle = waga.filtruj(pom, 65, 85, "u1")
        self.assertEqual([p["masa"] for p in ok], [75])
        self.assertEqual(sorted(p["status"] for p in zle), ["odrzucony:inny_profil", "odrzucony:zakres_masy"])
        with mock.patch.dict(os.environ, {"WAGA_PROFIL": "123456"}):
            ok, _ = waga.filtruj(pom, 65, 85, "u1")
        self.assertEqual(len(ok), 2)

    def test_raport_skoki(self):
        pom = [waga.normalizuj(rekord(T0, 75)), waga.normalizuj(rekord(T0 + 86400, 77.5)),
               waga.normalizuj(rekord(T0 + 2 * 86400, 77.0))]
        r = waga.raport(pom, [])
        self.assertEqual((r["pomiarow"], r["od"], r["do"], r["min"]["masa_kg"], r["max"]["masa_kg"]),
                         (3, "2026-11-20", "2026-11-22", 75, 77.5))
        self.assertEqual(r["skoki_ponad_2kg_dzien_do_dnia"][0]["roznica_kg"], 2.5)

    def test_csv_tam_i_z_powrotem(self):
        p = {**waga.normalizuj(rekord(T0, 75.35, imp=0)), "status": "przyjety"}
        z = waga.pomiary_z_csv(waga.csv_z([p]))[0]
        self.assertEqual(z, p)

    def test_wiersz_arkusza_puste_komorki_zamiast_zera(self):
        w = waga.wiersz(waga.normalizuj(rekord(T0, 75.0, imp=0)))
        self.assertEqual(w[:4], ["2026-11-20", "07:15", 75.0, ""])
        self.assertEqual(w[-1], "Zepp → łącznik")


class TestGarminDeduplikacja(unittest.TestCase):
    def test_tolerancje(self):
        p = {"ts": 1000, "masa": 75.0}
        self.assertTrue(waga._jest_w_garminie(p, [(1059, 75.05)]))
        self.assertFalse(waga._jest_w_garminie(p, [(1061, 75.0)]))
        self.assertFalse(waga._jest_w_garminie(p, [(1000, 75.06)]))


class TestPrzebiegi(Baza):
    def test_autoryzacja(self):
        self.assertEqual(self.post("test", token="zly")[0], 401)
        self.assertEqual(self.c.post("/waga/test").status_code, 401)

    def test_test_tylko_czyta(self):
        self.zepp_rekordy = [rekord(int(dt.datetime.now().timestamp()) - 3600, 75)]
        kod, d = self.post("test")
        self.assertEqual(kod, 200, d)
        self.assertEqual(d["zepp"]["logowanie_haslem"], "pierwsze")
        self.assertEqual(d["zepp"]["pomiarow_30_dni"], 1)
        self.assertEqual(self.garmin.wyslane, [])
        self.assertEqual(len(self.dysk.arkusz), 1)
        self.assertNotIn("75", json.dumps(d))          # bez masy w odpowiedzi (publiczny log)

    def test_cala_sciezka_bez_duplikatow_i_poza_zakresem_tylko_backup(self):
        self.zepp_rekordy = [rekord(T0, 75.0), rekord(T0 + 86400, 50.0), rekord(T0 + 2 * 86400, 75.4, imp=0)]
        self.assertEqual(self.post("test")[0], 200)
        self.assertEqual(self.post("sync")[1].get("pominieto"), True)      # sync czeka na historię
        kod, d = self.post("historia")
        self.assertEqual((kod, d["pomiarow"], d["odrzuconych"]), (200, 2, 1))
        self.assertNotIn("50.0", json.dumps(d))
        self.assertEqual(self.garmin.wyslane, [])                           # historia: bez wysyłki
        kod, d = self.post("wyslij_historie")
        self.assertEqual((kod, d["pozostalo"], d["garmin"]["wyslane"], d["arkusz"]["wyslane"]), (200, 0, 2, 2))
        self.assertEqual([w["weight"] for w in self.garmin.wyslane], [75.0, 75.4])
        self.assertIsNone(self.garmin.wyslane[1]["percent_fat"])            # bez impedancji
        self.assertEqual([w[2] for w in self.dysk.arkusz[1:]], [75.0, 75.4])

        # nowe ważenie -> sync; drugi sync bez ważenia nie tworzy duplikatów
        self.zepp_rekordy.append(rekord(T0 + 3 * 86400, 74.9))
        self.zepp_rekordy.append(rekord(T0 + 3 * 86400 + 60, 49.0))       # poza zakresem
        kod, d = self.post("sync")
        self.assertEqual((kod, d["garmin"]["wyslane"], d["arkusz"]["wyslane"], d["odrzuconych"]), (200, 1, 1, 2))   # okno -24 h łapie też stary 50 kg
        self.assertEqual(self.zepp_wywolania[-1], T0 + 2 * 86400 - waga.OKNO_SYNC_S)
        kod, d = self.post("sync")
        self.assertEqual((d["garmin"]["wyslane"], d["arkusz"]["wyslane"]), (0, 0))
        self.assertEqual((d["garmin"]["pominiete_duplikaty"], d["arkusz"]["pominiete_duplikaty"]), (2, 2))
        self.assertEqual(len(self.garmin.wyslane), 3)
        self.assertEqual(len(self.dysk.arkusz), 4)
        masy = [w[2] for w in self.dysk.arkusz[1:]]
        self.assertNotIn(49.0, masy)
        self.assertNotIn(50.0, masy)
        backup = waga.wczytaj_backup("refresh")
        self.assertEqual(sorted(p["masa"] for p in backup if p["status"] != "przyjety"), [49.0, 50.0])

    def test_dane_zaszyfrowane_na_dysku(self):
        self.zepp_rekordy = [rekord(T0, 75.123)]
        self.post("test")
        self.post("historia")
        calosc = json.dumps(self.dysk.pliki)
        for jawne in (APP_TOKEN, LOGIN_TOKEN, "75.123", "2026-11-20"):
            self.assertNotIn(jawne, calosc)

    def test_partie_historii(self):
        self.zepp_rekordy = [rekord(T0 + i * 86400, 75 + i / 10) for i in range(5)]
        self.post("test")
        self.post("historia")
        with mock.patch.dict(os.environ, {"WAGA_PARTIA": "2"}):
            wyniki = [self.post("wyslij_historie")[1]["pozostalo"] for _ in range(3)]
        self.assertEqual(wyniki, [3, 1, 0])
        self.assertEqual([w[2] for w in self.dysk.arkusz[1:]], [75 + i / 10 for i in range(5)])
        self.assertEqual(self.post("wyslij_historie")[1]["pozostalo"], 0)
        self.assertEqual(len(self.garmin.wyslane), 5)

    def test_arkusz_istniejace_wiersze(self):
        self.zepp_rekordy = [rekord(T0, 75.0)]
        self.dysk.arkusz.append([46346, 0.3020833333, 75, "", "", "", "", "", "ręcznie"])   # 2026-11-20 07:15
        self.post("test")
        self.post("historia")
        d = self.post("wyslij_historie")[1]
        self.assertEqual((d["arkusz"]["wyslane"], d["arkusz"]["pominiete_duplikaty"]), (0, 1))

    def test_arkusz_zle_naglowki_nic_nie_zapisuje(self):
        self.dysk.arkusz = [["Data", "Inne"]]
        self.zepp_rekordy = [rekord(T0, 75.0)]
        self.post("test")
        self.post("historia")
        kod, d = self.post("wyslij_historie")
        self.assertEqual((kod, d["kategoria"]), (502, "arkusz"))
        self.assertEqual(self.dysk.arkusz, [["Data", "Inne"]])

    def test_blad_zepp_jeden_fail_bez_danych_logowania(self):
        def zle(*a, **kw):
            raise zepp.BladZepp("zle_haslo", "Xiaomi odrzucił e-mail albo hasło")
        with mock.patch.object(zepp, "rekordy_z_sesja", zle), self.assertLogs("garmin_plan_loader.waga") as logi:
            kod, d = self.post("test")
        self.assertEqual((kod, d["ok"], d["kategoria"]), (502, False, "zepp_zle_haslo"))
        tekst = json.dumps(d) + " ".join(logi.output)
        self.assertNotIn(HASLO, tekst)
        self.assertNotIn("ja@example.com", tekst)

    def test_brak_konfiguracji_zakresu(self):
        self.post("test")
        os.environ.pop("WAGA_MIN")
        kod, d = self.post("historia")
        self.assertEqual((kod, d["kategoria"]), (500, "konfiguracja"))

    def test_historia_ze_starego_konta(self):
        self.zepp_rekordy = [rekord(T0 + 10 * 86400, 74.0)]
        self.post("test")
        stare = {"items": [rekord(T0, 76.0, member="999"), rekord(T0 + 86400, 75.5)], "next": 0}
        with mock.patch.dict(os.environ, {"ZEPP_STARE_APPTOKEN": "STARY", "ZEPP_STARE_USER_ID": "999"}), \
                mock.patch.object(zepp, "_strona_rekordow", lambda st, do: stare if st["app_token"] == "STARY" else {}):
            kod, d = self.post("historia")
        self.assertEqual((kod, d["pomiarow"], d["zepp"]["stare_konto_rekordow"]), (200, 3, 2))
        with mock.patch.dict(os.environ, {"ZEPP_STARE_APPTOKEN": "STARY"}):
            self.assertEqual(self.post("historia")[1]["kategoria"], "konfiguracja")

    def test_narzedzie_mcp(self):
        self.zepp_rekordy = [rekord(T0, 75.0)]
        self.post("test")
        self.post("historia")
        r = waga.t_waga({})
        self.assertEqual(r["historia"]["raport"]["min"]["masa_kg"], 75.0)
        self.assertIn("waga", mcp_serwer.NARZEDZIA)
        p = waga.t_waga({"widok": "pomiary", "od": "2026-11-01", "do": "2026-11-30"})
        self.assertEqual(len(p["pomiary"]), 1)


class Odp:
    def __init__(self, status=200, dane=None, tekst=None, naglowki=None):
        self.status_code, self._dane, self.headers = status, dane, naglowki or {}
        self.text = tekst if tekst is not None else json.dumps(dane)

    def json(self):
        if self._dane is None:
            raise ValueError
        return self._dane


class AtrapaHttp:
    def __init__(self, odpowiedzi):
        self.odpowiedzi, self.wywolania = odpowiedzi, []
        self.cookies = mock.MagicMock()

    def request(self, metoda, url, **kw):
        self.wywolania.append((metoda, url, kw))
        for wzor, odp in self.odpowiedzi:
            if wzor in url:
                return odp(kw) if callable(odp) else odp
        raise AssertionError(url)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestZepp(unittest.TestCase):
    def stan(self, **kw):
        return {"user_id": "u1", "app_token": "A", "login_token": "L", "device_id": "d", **kw}

    def test_wazny_token_bez_logowania(self):
        h = AtrapaHttp([("weightRecords", Odp(dane={"items": [rekord(T0, 75)], "next": 0}))])
        with mock.patch.object(zepp, "_http", h):
            rek, stan, kroki = zepp.rekordy_z_sesja(self.stan(), "e", "p")
        self.assertEqual((len(rek), kroki), (1, ["token"]))
        self.assertTrue(all("login" not in u for _, u, _ in h.wywolania))

    def test_401_odswiezenie_bez_hasla(self):
        licznik = {"n": 0}

        def rekordy_odp(kw):
            licznik["n"] += 1
            return Odp(401, {}) if kw["headers"]["apptoken"] == "A" else Odp(dane={"items": [], "next": 0})
        h = AtrapaHttp([("weightRecords", rekordy_odp),
                        ("app_tokens", Odp(dane={"token_info": {"app_token": "B", "app_ttl": 7776000}}))])
        with mock.patch.object(zepp, "_http", h), \
                mock.patch.object(zepp, "zaloguj_haslem", side_effect=AssertionError("bez hasła!")):
            _, stan, kroki = zepp.rekordy_z_sesja(self.stan(), "e", "p")
        self.assertEqual((kroki, stan["app_token"], stan["login_token"]), (["token", "odswiezenie"], "B", "L"))

    def test_awaryjne_haslo_tylko_raz(self):
        h = AtrapaHttp([("weightRecords", Odp(401, {})), ("app_tokens", Odp(401, {}))])
        with mock.patch.object(zepp, "_http", h), \
                mock.patch.object(zepp, "zaloguj_haslem", return_value=self.stan(app_token="A")) as zh:
            with self.assertRaises(zepp.BladZepp) as e:
                zepp.rekordy_z_sesja(self.stan(), "e", "p")
        self.assertEqual(zh.call_count, 1)
        self.assertEqual(e.exception.kategoria, "token")

    def test_logowanie_email_zepp(self):
        przekier = Odp(303, {}, naglowki={"Location": "https://s3/x?access=ACC&refresh=REF&country_code=PL"})
        login = Odp(dane={"token_info": {"login_token": "L3", "app_token": "A3", "user_id": "u3", "app_ttl": 100},
                          "domains": [{"host": "api-mifit.zepp.com", "cnames": ["api-mifit-de2.zepp.com"]}]})
        h = AtrapaHttp([("registrations/tokens", przekier), ("v2/client/login", login)])
        with mock.patch.object(zepp, "_http", h):
            stan = zepp.zaloguj_haslem("e@x", HASLO)                       # domyślnie konto e-mail
        self.assertEqual((stan["konto"], stan["app_token"], stan["login_token"], stan["host_api"]),
                         ("email", "A3", "L3", "api-mifit.zepp.com"))
        wyslane = h.wywolania[0][2]["data"]
        self.assertIsInstance(wyslane, bytes)
        self.assertNotIn(HASLO.encode(), wyslane)                           # formularz zaszyfrowany
        self.assertEqual(h.wywolania[1][2]["data"]["code"], "ACC")

    def test_logowanie_email_zle_haslo(self):
        h = AtrapaHttp([("registrations/tokens", Odp(303, {}, naglowki={"Location": "https://s3/x?error=0106"}))])
        with mock.patch.object(zepp, "_http", h):
            with self.assertRaises(zepp.BladZepp) as e:
                zepp.zaloguj_haslem("e@x", HASLO)
        self.assertEqual(e.exception.kategoria, "zle_haslo")
        self.assertNotIn(HASLO, str(e.exception))

    def test_host_regionalny_i_odswiezenie_email(self):
        self.assertEqual(zepp._host_api({"domains": [{"host": "api-user.zepp.com"},
                                                     {"host": "x", "cnames": ["api-mifit-de2.huami.com"]}]}),
                         "api-mifit-de2.huami.com")
        h = AtrapaHttp([("api-mifit-us2.zepp.com/v1/client/app_tokens", Odp(dane={"token_info": {"app_token": "N"}})),
                        ("api-mifit-de2.huami.com/users/u1", Odp(dane={"items": [rekord(T0, 75)], "next": 0}))])
        with mock.patch.object(zepp, "_http", h):
            st = zepp.odswiez(self.stan(konto="email", host_api="api-mifit-de2.huami.com"))
            self.assertEqual(st["app_token"], "N")
            self.assertEqual(len(zepp.rekordy(st)), 1)

    def test_blokada_ip(self):
        h = AtrapaHttp([("weightRecords", Odp(429, {}))])
        with mock.patch.object(zepp, "_http", h):
            with self.assertRaises(zepp.BladZepp) as e:
                zepp.rekordy_z_sesja(self.stan(), "e", "p")
        self.assertEqual(e.exception.kategoria, "blokada")

    def test_logowanie_xiaomi_i_kody_bledow(self):
        pre = zepp.PREFIKS

        def sesja(auth2):
            return AtrapaHttp([
                ("oauth2/authorize", Odp(tekst=pre + json.dumps({"data": {"oauthLoginUrl": "https://account.xiaomi.com/x"}}))),
                ("serviceLoginAuth2", Odp(tekst=pre + json.dumps(auth2))),
                ("account.xiaomi.com/x", Odp(tekst=pre + json.dumps({"_sign": "s", "qs": "q", "callback": "c", "sid": "i"}))),
                ("sts", Odp(302, {}, naglowki={"Location": zepp.REDIRECT_URI + "?code=KOD"})),
            ])
        logowanie = Odp(dane={"token_info": {"login_token": "L2", "app_token": "A2", "user_id": "u9", "app_ttl": 100}})
        s = sesja({"code": 0, "location": "https://sts.example/x"})
        with mock.patch.object(zepp, "_nowa_sesja", lambda: s), \
                mock.patch.object(zepp, "_http", AtrapaHttp([("v2/client/login", logowanie)])) as h:
            stan = zepp.zaloguj_haslem("e@x", HASLO, konto="xiaomi")
        self.assertEqual((stan["app_token"], stan["login_token"], stan["user_id"]), ("A2", "L2", "u9"))
        self.assertNotIn(HASLO, json.dumps([w[2] for w in s.wywolania]))   # wysyłany tylko hash MD5
        for odp, kat in (({"code": 70016}, "zle_haslo"), ({"captchaUrl": "/c"}, "captcha"),
                         ({"code": 0, "location": "x", "notificationUrl": "/n"}, "weryfikacja")):
            with mock.patch.object(zepp, "_nowa_sesja", lambda o=odp: sesja(o)):
                with self.assertRaises(zepp.BladZepp) as e:
                    zepp.zaloguj_haslem("e@x", HASLO, konto="xiaomi")
            self.assertEqual(e.exception.kategoria, kat)
            self.assertNotIn(HASLO, str(e.exception))

    def test_brak_sieci(self):
        import requests

        class Zerwane(AtrapaHttp):
            def request(self, *a, **kw):
                raise requests.ConnectionError("nie ma sieci " + HASLO)
        with mock.patch.object(zepp, "_http", Zerwane([])):
            with self.assertRaises(zepp.BladZepp) as e:
                zepp.rekordy_z_sesja(self.stan(), "e", "p")
        self.assertEqual(e.exception.kategoria, "siec")
        self.assertNotIn(HASLO, str(e.exception))


if __name__ == "__main__":
    unittest.main()
