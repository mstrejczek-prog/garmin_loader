# -*- coding: utf-8 -*-
"""
Garmin Plan Loader - okno do wysyłania planu treningowego do Garmin Connect.

Uruchomienie: dwuklik na pobranym programie (bez instalacji). Z kodu źródłowego: python app.py
Test bez okna i bez sieci: python app.py --selftest
"""
from __future__ import annotations

import queue
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, simpledialog, ttk

import garmin_io
import plan

NAZWA = "Garmin Plan Loader"
WERSJA = "0.1.0"

# paleta: ciemny granat jak pasek zegarka sportowego, jeden akcent, kolory stref jak na wykresie treningu
INK, BG, KARTA, LINIA = "#17212B", "#EEF1F4", "#FFFFFF", "#D5DCE3"
TEKST, PRZYGASZONY = "#17212B", "#5E6B78"
AKCENT, AKCENT_JASNY = "#0F7B6C", "#0C6256"
BLAD, BLAD_TLO, OK = "#B3261E", "#FCEBEA", "#1E7D3A"
STREFY_KOLOR = {1: "#9DB4C8", 2: "#4FA67A", 3: "#E3B83B", 4: "#E8803A", 5: "#C9362E"}
KOLORY = {"ok": OK, "err": BLAD, "info": PRZYGASZONY}
KROKI_PASEK = ("Konto", "Plan", "Wysyłka")


def _liczba_treningow(n: int) -> str:
    if n == 1:
        return "1 trening"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return f"{n} treningi"
    return f"{n} treningów"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{NAZWA} {WERSJA}")
        szer = max(980, min(1180, self.winfo_screenwidth() - 80))
        wys = max(620, min(760, self.winfo_screenheight() - 110))   # mieści się też na laptopie 1366x768
        self.geometry(f"{szer}x{wys}")
        self.minsize(960, 600)
        self.configure(bg=BG)

        self.q: queue.Queue = queue.Queue()
        self.klient = None
        self.zajety = False
        self.wyniki = None            # ostatni poprawnie wczytany plan
        self.blad_planu = None
        self.wyslano = False
        self._odroczone = None

        self.var_email = tk.StringVar()
        self.var_haslo = tk.StringVar()
        self.var_zapamietaj = tk.BooleanVar(value=True)
        self.var_nadpisz = tk.BooleanVar(value=False)

        self._style()
        self._buduj()
        self.after(100, self._pump)
        self._start_token()
        self._pokaz_podglad()

    # ------------------------------------------------------------------ wygląd
    def _style(self):
        bazowa = tkfont.nametofont("TkDefaultFont")
        rodzina = bazowa.actual("family")
        self.f_norm = tkfont.Font(family=rodzina, size=10)
        self.f_maly = tkfont.Font(family=rodzina, size=9)
        self.f_pogr = tkfont.Font(family=rodzina, size=10, weight="bold")
        self.f_tytul = tkfont.Font(family=rodzina, size=15, weight="bold")
        self.f_nagl = tkfont.Font(family=rodzina, size=11, weight="bold")
        self.f_mono = tkfont.nametofont("TkFixedFont").copy()
        self.f_mono.configure(size=10)
        for f in (bazowa,):
            f.configure(size=10)

        st = ttk.Style(self)
        st.theme_use("clam")                       # jedyny motyw, w którym kolory przycisków działają na każdym systemie
        st.configure(".", font=self.f_norm, background=KARTA, foreground=TEKST)
        st.configure("TFrame", background=KARTA)
        st.configure("TLabel", background=KARTA, foreground=TEKST)
        st.configure("TEntry", fieldbackground="#FFFFFF", bordercolor=LINIA, lightcolor=LINIA, darkcolor=LINIA,
                     padding=6)
        st.map("TEntry", bordercolor=[("focus", AKCENT)], lightcolor=[("focus", AKCENT)], darkcolor=[("focus", AKCENT)])
        st.configure("TCheckbutton", background=KARTA, focuscolor=KARTA)
        st.map("TCheckbutton", background=[("active", KARTA)])

        st.configure("Primary.TButton", background=AKCENT, foreground="#FFFFFF", bordercolor=AKCENT,
                     lightcolor=AKCENT, darkcolor=AKCENT, padding=(16, 9), font=self.f_pogr, focuscolor=AKCENT)
        st.map("Primary.TButton",
               background=[("disabled", "#B9C4CC"), ("pressed", AKCENT_JASNY), ("active", AKCENT_JASNY)],
               bordercolor=[("disabled", "#B9C4CC")], lightcolor=[("disabled", "#B9C4CC")],
               darkcolor=[("disabled", "#B9C4CC")], foreground=[("disabled", "#F4F6F8")])
        st.configure("Ghost.TButton", background="#FFFFFF", foreground=TEKST, bordercolor=LINIA, lightcolor=LINIA,
                     darkcolor=LINIA, padding=(10, 6), focuscolor="#FFFFFF")
        st.map("Ghost.TButton", background=[("active", "#F1F4F7"), ("pressed", "#E6EBF0")],
               foreground=[("disabled", "#9AA6B1")])
        st.configure("Link.TButton", background=KARTA, foreground=PRZYGASZONY, borderwidth=0, padding=(6, 6),
                     focuscolor=KARTA)
        st.map("Link.TButton", foreground=[("active", AKCENT), ("disabled", "#B9C4CC")],
               background=[("active", KARTA)])
        st.configure("Vertical.TScrollbar", background="#C9D2DA", troughcolor=BG, bordercolor=BG,
                     arrowcolor=BG, lightcolor="#C9D2DA", darkcolor="#C9D2DA", gripcount=0)

    def _karta(self, rodzic, **kw):
        return tk.Frame(rodzic, bg=KARTA, highlightbackground=LINIA, highlightthickness=1, **kw)

    def _naglowek_karty(self, rodzic, tekst, pod=None):
        tk.Label(rodzic, text=tekst, font=self.f_nagl, bg=KARTA, fg=TEKST, anchor="w").pack(anchor="w")
        if pod:
            tk.Label(rodzic, text=pod, font=self.f_maly, bg=KARTA, fg=PRZYGASZONY, anchor="w", justify="left",
                     wraplength=340).pack(anchor="w", pady=(2, 0))

    # ------------------------------------------------------------------ budowa okna
    def _buduj(self):
        # --- pasek tytułowy z postępem
        pasek = tk.Frame(self, bg=INK)
        pasek.pack(fill="x")
        wew = tk.Frame(pasek, bg=INK)
        wew.pack(fill="x", padx=20, pady=12)
        tk.Label(wew, text=NAZWA, font=self.f_tytul, bg=INK, fg="#FFFFFF").pack(side="left")
        self.pille = []
        for nazwa in reversed(KROKI_PASEK):
            l = tk.Label(wew, text=nazwa, font=self.f_pogr, padx=12, pady=4)
            l.pack(side="right", padx=(6, 0))
            self.pille.append(l)
        self.pille.reverse()

        # --- korpus: lewa kolumna (konto + plan), prawa (podgląd + wysyłka)
        korpus = tk.Frame(self, bg=BG)
        korpus.pack(fill="both", expand=True, padx=16, pady=(14, 6))
        korpus.rowconfigure(0, weight=1)
        korpus.columnconfigure(0, weight=0, minsize=420)
        korpus.columnconfigure(1, weight=1)

        lewa = tk.Frame(korpus, bg=BG)
        lewa.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        lewa.columnconfigure(0, weight=1)
        lewa.rowconfigure(1, weight=1)
        self._buduj_konto(lewa)
        self._buduj_plan(lewa)

        prawa = tk.Frame(korpus, bg=BG)
        prawa.grid(row=0, column=1, sticky="nsew")
        prawa.columnconfigure(0, weight=1)
        prawa.rowconfigure(0, weight=1)
        self._buduj_podglad(prawa)
        self._buduj_wysylke(prawa)

        tk.Label(self, bg=BG, fg=PRZYGASZONY, font=self.f_maly, justify="left", wraplength=1080, anchor="w",
                 text="Program nieoficjalny, niezwiązany z firmą Garmin. Hasło służy tylko do jednorazowego logowania i "
                      "nie jest zapisywane; na dysku zostaje wyłącznie token, który możesz usunąć. Używasz na własną "
                      "odpowiedzialność.").pack(fill="x", padx=18, pady=(0, 10))
        self._odswiez_przyciski()

    def _buduj_konto(self, rodzic):
        karta = self._karta(rodzic)
        karta.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        wew = tk.Frame(karta, bg=KARTA)
        wew.pack(fill="x", padx=16, pady=14)

        # widok: niezalogowany
        self.fr_form = tk.Frame(wew, bg=KARTA)
        self._naglowek_karty(self.fr_form, "Konto Garmin Connect")
        ttk.Label(self.fr_form, text="E-mail").pack(anchor="w", pady=(10, 2))
        self.ent_email = ttk.Entry(self.fr_form, textvariable=self.var_email)
        self.ent_email.pack(fill="x")
        ttk.Label(self.fr_form, text="Hasło").pack(anchor="w", pady=(8, 2))
        self.ent_haslo = ttk.Entry(self.fr_form, textvariable=self.var_haslo, show="*")
        self.ent_haslo.pack(fill="x")
        self.ent_haslo.bind("<Return>", lambda _e: self.on_login())
        wiersz = tk.Frame(self.fr_form, bg=KARTA)
        wiersz.pack(fill="x", pady=(10, 0))
        self.btn_login = ttk.Button(wiersz, text="Zaloguj", style="Primary.TButton", command=self.on_login)
        self.btn_login.pack(side="left")
        self.lbl_status = tk.Label(wiersz, text="", bg=KARTA, fg=PRZYGASZONY, font=self.f_maly, anchor="w",
                                   justify="left", wraplength=240)
        self.lbl_status.pack(side="left", padx=(12, 0))
        ttk.Checkbutton(self.fr_form, variable=self.var_zapamietaj,
                        text="Zapamiętaj logowanie (zapisuję token, nie hasło)").pack(anchor="w", pady=(10, 0))

        # widok: zalogowany
        self.fr_konto = tk.Frame(wew, bg=KARTA)
        self._naglowek_karty(self.fr_konto, "Konto Garmin Connect")
        self.lbl_konto = tk.Label(self.fr_konto, text="", bg=KARTA, fg=OK, font=self.f_pogr, anchor="w")
        self.lbl_konto.pack(anchor="w", pady=(8, 0))
        self.lbl_konto_pod = tk.Label(self.fr_konto, text="", bg=KARTA, fg=PRZYGASZONY, font=self.f_maly, anchor="w")
        self.lbl_konto_pod.pack(anchor="w")
        self.btn_logout = ttk.Button(self.fr_konto, text="Wyloguj i usuń token", style="Ghost.TButton",
                                     command=self.on_logout)
        self.btn_logout.pack(anchor="w", pady=(10, 0))
        self.fr_form.pack(fill="x")

    def _buduj_plan(self, rodzic):
        karta = self._karta(rodzic)
        karta.grid(row=1, column=0, sticky="nsew")
        wew = tk.Frame(karta, bg=KARTA)
        wew.pack(fill="both", expand=True, padx=16, pady=14)
        self._naglowek_karty(wew, "Plan treningowy")
        pasek = tk.Frame(wew, bg=KARTA)
        pasek.pack(fill="x", pady=(10, 8))
        ttk.Button(pasek, text="Skopiuj prompt dla AI", style="Ghost.TButton", command=self.on_prompt).pack(side="left")
        ttk.Button(pasek, text="Z pliku", style="Link.TButton", width=0, command=self.on_plik).pack(side="left", padx=(6, 0))
        ttk.Button(pasek, text="Przykład", style="Link.TButton", width=0, command=self.on_przyklad).pack(side="left")
        ttk.Button(pasek, text="Wyczyść", style="Link.TButton", width=0, command=self.on_czysc).pack(side="left")

        ramka = tk.Frame(wew, bg=KARTA, highlightbackground=LINIA, highlightcolor=AKCENT, highlightthickness=1)
        ramka.pack(fill="both", expand=True)
        ramka.columnconfigure(0, weight=1)
        ramka.rowconfigure(0, weight=1)
        self.txt_plan = tk.Text(ramka, wrap="word", font=self.f_mono, undo=True, relief="flat", bd=0, padx=10,
                                pady=8, bg="#FFFFFF", fg=TEKST, insertbackground=TEKST, highlightthickness=0,
                                height=6, width=10)
        sb = ttk.Scrollbar(ramka, command=self.txt_plan.yview)
        self.txt_plan.configure(yscrollcommand=sb.set)
        self.txt_plan.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        self.lbl_wskazowka = tk.Label(
            ramka, bg="#FFFFFF", fg="#8793A0", font=self.f_norm, justify="left", anchor="nw",
            text="Wklej tutaj plan w formacie JSON.\n\nNie masz go jeszcze? Kliknij „Skopiuj prompt dla AI”,\n"
                 "wklej go swojemu asystentowi razem z rozpiską\ntrenera i skopiuj jego odpowiedź tutaj.")
        self.lbl_wskazowka.place(x=12, y=10)
        self.lbl_wskazowka.bind("<Button-1>", lambda _e: self.txt_plan.focus_set())
        for zd in ("<KeyRelease>", "<<Paste>>", "<<Cut>>", "<<Undo>>", "<<Redo>>"):
            self.txt_plan.bind(zd, lambda _e: self._po_zmianie())

    def _buduj_podglad(self, rodzic):
        karta = self._karta(rodzic)
        karta.grid(row=0, column=0, sticky="nsew", pady=(0, 12))
        karta.rowconfigure(1, weight=1)
        karta.columnconfigure(0, weight=1)
        glowa = tk.Frame(karta, bg=KARTA)
        glowa.grid(row=0, column=0, columnspan=2, sticky="ew", padx=16, pady=(14, 8))
        tk.Label(glowa, text="Podgląd planu", font=self.f_nagl, bg=KARTA, fg=TEKST).pack(side="left")
        leg = tk.Frame(glowa, bg=KARTA)
        leg.pack(side="right")
        tk.Label(leg, text="intensywność", font=self.f_maly, bg=KARTA, fg=PRZYGASZONY).pack(side="left", padx=(0, 6))
        for n in range(1, 6):
            tk.Label(leg, text=f"S{n}", font=self.f_maly, bg=STREFY_KOLOR[n],
                     fg="#FFFFFF" if n in (1, 2, 4, 5) else INK, width=3).pack(side="left", padx=1)

        self.cv = tk.Canvas(karta, bg=KARTA, highlightthickness=0, bd=0)
        self.sb_podglad = ttk.Scrollbar(karta, command=self.cv.yview)
        self.cv.configure(yscrollcommand=self.sb_podglad.set)
        self.cv.grid(row=1, column=0, sticky="nsew", padx=(16, 0), pady=(0, 14))
        self.sb_podglad.grid(row=1, column=1, sticky="ns", padx=(0, 4), pady=(0, 14))
        self.fr_podglad = tk.Frame(self.cv, bg=KARTA)
        self._okno_podgladu = self.cv.create_window(0, 0, window=self.fr_podglad, anchor="nw")
        self.fr_podglad.bind("<Configure>", lambda _e: self.cv.configure(scrollregion=self.cv.bbox("all")))
        self.cv.bind("<Configure>", lambda e: self.cv.itemconfigure(self._okno_podgladu, width=e.width))
        for zd in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.bind_all(zd, self._kolko, add="+")

    def _buduj_wysylke(self, rodzic):
        karta = self._karta(rodzic)
        karta.grid(row=1, column=0, sticky="ew")
        wew = tk.Frame(karta, bg=KARTA)
        wew.pack(fill="x", padx=16, pady=12)
        wiersz = tk.Frame(wew, bg=KARTA)
        wiersz.pack(fill="x")
        self.btn_wyslij = ttk.Button(wiersz, text="Wyślij do Garmin Connect", style="Primary.TButton",
                                     command=self.on_wyslij)
        self.btn_wyslij.pack(side="left")
        self.lbl_wskaz_wysylki = tk.Label(wiersz, text="", bg=KARTA, fg=PRZYGASZONY, font=self.f_maly, anchor="w")
        self.lbl_wskaz_wysylki.pack(side="left", padx=(12, 0))
        ttk.Checkbutton(wew, variable=self.var_nadpisz, text="Nadpisz treningi o tych samych nazwach "
                        "(gdy zmieniły się kroki)").pack(anchor="w", pady=(8, 0))
        self.txt_log = tk.Text(wew, height=3, wrap="word", font=self.f_maly, relief="flat", bd=0, bg=KARTA,
                               fg=PRZYGASZONY, state="disabled", highlightthickness=0, padx=0, pady=0)
        self.txt_log.pack(fill="x", pady=(8, 0))
        self.txt_log.pack_forget()                # log pojawia się dopiero po pierwszej wysyłce

    def _kolo_ok(self, widget) -> bool:
        w = widget
        while w is not None:
            if w is self.cv:
                return True
            w = getattr(w, "master", None)
        return False

    def _kolko(self, e):
        if not self._kolo_ok(e.widget):
            return
        if e.num == 4:
            self.cv.yview_scroll(-3, "units")
        elif e.num == 5:
            self.cv.yview_scroll(3, "units")
        elif e.delta:
            self.cv.yview_scroll(-1 if e.delta > 0 else 1, "units") if sys.platform == "darwin" \
                else self.cv.yview_scroll(int(-e.delta / 40), "units")

    # ------------------------------------------------------------------ podgląd
    def _wyczysc_podglad(self):
        for w in self.fr_podglad.winfo_children():
            w.destroy()
        self.cv.yview_moveto(0)

    def _pokaz_podglad(self):
        """Rysuje kartę dla każdego treningu, komunikat o błędzie albo pusty ekran z zachętą."""
        self._wyczysc_podglad()
        f = self.fr_podglad
        if self.blad_planu:
            ramka = tk.Frame(f, bg=BLAD_TLO, highlightbackground="#E8B4B0", highlightthickness=1)
            ramka.pack(fill="x", pady=(0, 10))
            tk.Label(ramka, text="Nie mogę wczytać planu", font=self.f_pogr, bg=BLAD_TLO, fg=BLAD,
                     anchor="w").pack(fill="x", padx=14, pady=(12, 2))
            tk.Label(ramka, text=self.blad_planu, font=self.f_norm, bg=BLAD_TLO, fg=TEKST, anchor="w",
                     justify="left", wraplength=560).pack(fill="x", padx=14, pady=(0, 12))
            return
        if not self.wyniki:
            pusto = tk.Frame(f, bg=KARTA)
            pusto.pack(fill="x", pady=40)
            tk.Label(pusto, text="Tu zobaczysz swoje treningi", font=self.f_nagl, bg=KARTA, fg=TEKST).pack()
            tk.Label(pusto, text="Wklej plan po lewej - podgląd pojawi się od razu.", font=self.f_norm, bg=KARTA,
                     fg=PRZYGASZONY).pack(pady=(4, 10))
            ttk.Button(pusto, text="Zobacz przykład", style="Ghost.TButton", command=self.on_przyklad).pack()
            return
        for x in self.wyniki:
            self._karta_treningu(f, x)

    def _karta_treningu(self, rodzic, x):
        d = x["data"]
        if d:
            kiedy = f"{plan.DNI[d.weekday()]} {d.strftime('%d.%m')}"
        else:
            kiedy = "bez daty"
        ramka = tk.Frame(rodzic, bg=KARTA, highlightbackground=LINIA, highlightthickness=1)
        ramka.pack(fill="x", pady=(0, 10))
        wew = tk.Frame(ramka, bg=KARTA)
        wew.pack(fill="x", padx=14, pady=10)
        gora = tk.Frame(wew, bg=KARTA)
        gora.pack(fill="x")
        tk.Label(gora, text=kiedy, font=self.f_pogr, bg=INK if d else "#E3E8ED", fg="#FFFFFF" if d else PRZYGASZONY,
                 padx=8, pady=2).pack(side="left")
        if x["wolne"]:
            tk.Label(gora, text="Dzień wolny", font=self.f_norm, bg=KARTA, fg=PRZYGASZONY).pack(side="left", padx=10)
            return
        w = x["w"]
        minuty = w["estimatedDurationInSecs"] // 60
        czas = f"ok. {minuty // 60} h {minuty % 60:02d} min" if minuty >= 60 else f"ok. {minuty} min"
        sport = "bieg" if w["sportType"]["sportTypeKey"] == "running" else "rower"
        tk.Label(gora, text=f"{czas}  |  {sport}", font=self.f_maly, bg=KARTA, fg=PRZYGASZONY).pack(side="right")
        tk.Label(gora, text=w["workoutName"], font=self.f_nagl, bg=KARTA, fg=TEKST, anchor="w",
                 justify="left", wraplength=300).pack(side="left", padx=10)

        cv = tk.Canvas(wew, height=56, bg=KARTA, highlightthickness=0, bd=0)
        cv.pack(fill="x", pady=(10, 6))
        cv.bind("<Configure>", lambda e, c=cv, p=x["profil"]: self._rysuj_profil(c, p))

        tab = tk.Frame(wew, bg=KARTA)
        tab.pack(fill="x")
        tab.columnconfigure(3, weight=1)
        for i, (nr, et, dl, cel) in enumerate(x["wiersze"]):
            poziom = x["profil"][i][1]
            tk.Label(tab, text=str(nr), font=self.f_maly, bg=KARTA, fg=PRZYGASZONY, width=3, anchor="e").grid(
                row=i, column=0, sticky="e")
            tk.Label(tab, text=et, font=self.f_norm, bg=KARTA, fg=TEKST, width=12, anchor="w").grid(
                row=i, column=1, sticky="w", padx=(6, 0))
            tk.Label(tab, text=dl, font=self.f_pogr, bg=KARTA, fg=TEKST, width=9, anchor="w").grid(row=i, column=2, sticky="w")
            tk.Frame(tab, bg=STREFY_KOLOR[poziom], width=10, height=10).grid(row=i, column=3, sticky="w", pady=4)
            tk.Label(tab, text=cel, font=self.f_norm, bg=KARTA, fg=TEKST, anchor="w").grid(
                row=i, column=3, sticky="w", padx=(18, 0))

    @staticmethod
    def _rysuj_profil(cv, profil):
        """Wykres kroków: szerokość = czas trwania, wysokość i kolor = intensywność (strefa)."""
        cv.delete("all")
        szer, wys = cv.winfo_width(), cv.winfo_height()
        if szer < 20 or not profil:
            return
        suma = sum(p[0] for p in profil) or 1
        odstep = 2
        dostepne = szer - odstep * (len(profil) - 1)
        x = 0.0
        for sekundy, poziom, _typ in profil:
            dl = max(3.0, dostepne * sekundy / suma)
            h = 10 + poziom * 9
            cv.create_rectangle(x, wys - h, x + dl, wys, fill=STREFY_KOLOR[poziom], outline="")
            x += dl + odstep

    # ------------------------------------------------------------------ pomocnicze
    def _wpisz(self, txt, tekst, tag=None, wyczysc=True, od_poczatku=False):
        txt.configure(state="normal")
        if wyczysc:
            txt.delete("1.0", "end")
        txt.insert("end", tekst, tag or ())
        txt.configure(state="disabled")
        txt.see("1.0" if od_poczatku else "end")

    def log(self, tekst):
        if not self.txt_log.winfo_ismapped():
            self.txt_log.pack(fill="x", pady=(8, 0))
        self._wpisz(self.txt_log, tekst + "\n", wyczysc=False)

    def status(self, tekst, rodzaj="info"):
        self.lbl_status.configure(text=tekst, fg=KOLORY[rodzaj])

    def _pille(self):
        gotowe = [self.klient is not None, bool(self.wyniki), self.wyslano]
        aktualny = next((i for i, g in enumerate(gotowe) if not g), None)
        for i, (l, nazwa) in enumerate(zip(self.pille, KROKI_PASEK)):
            if gotowe[i]:
                l.configure(text=f"\u2713 {nazwa}", bg=AKCENT, fg="#FFFFFF")
            elif i == aktualny:
                l.configure(text=nazwa, bg="#FFFFFF", fg=INK)
            else:
                l.configure(text=nazwa, bg="#2C3A49", fg="#9FB0BF")

    def _odswiez_przyciski(self):
        stan = "disabled" if self.zajety else "normal"
        self.btn_login.configure(state=stan)
        self.btn_logout.configure(state=stan)
        if self.klient:
            self.lbl_konto.configure(text=f"\u2713 {garmin_io.nazwa_konta(self.klient)}")
            self.fr_form.pack_forget()
            if not self.fr_konto.winfo_ismapped():
                self.fr_konto.pack(fill="x")
        else:
            self.fr_konto.pack_forget()
            if not self.fr_form.winfo_ismapped():
                self.fr_form.pack(fill="x")
        n = sum(1 for x in (self.wyniki or []) if not x["wolne"])
        mozna = bool(self.klient) and n > 0 and not self.zajety
        self.btn_wyslij.configure(state="normal" if mozna else "disabled",
                                  text=f"Wyślij {_liczba_treningow(n)}" if n else "Wyślij do Garmin Connect")
        if self.zajety:
            wskaz = ""
        elif not self.klient:
            wskaz = "Najpierw zaloguj się do konta Garmin."
        elif self.blad_planu:
            wskaz = "Popraw błędy w planie."
        elif n == 0:
            wskaz = "Wklej plan, żeby go wysłać."
        else:
            wskaz = "Trafią do kalendarza Garmin Connect."
        self.lbl_wskaz_wysylki.configure(text=wskaz)
        self._pille()

    def _zajety(self, tak):
        self.zajety = tak
        self._odswiez_przyciski()

    # praca w tle: okno nie zamarza podczas rozmowy z Garminem
    def _run(self, praca, ok, blad):
        def cel():
            try:
                wynik = praca()
            except Exception as e:  # noqa: BLE001
                self.q.put(("call", lambda e=e: blad(e)))
            else:
                self.q.put(("call", lambda: ok(wynik)))
        threading.Thread(target=cel, daemon=True).start()

    def _pump(self):
        try:
            while True:
                rodzaj, *dane = self.q.get_nowait()
                if rodzaj == "log":
                    self.log(dane[0])
                elif rodzaj == "call":
                    dane[0]()
                elif rodzaj == "mfa":
                    zdarzenie, pudelko = dane
                    kod = simpledialog.askstring(
                        "Kod weryfikacyjny",
                        "Garmin wymaga kodu weryfikacyjnego (e-mail, SMS lub aplikacja).\nWpisz go tutaj:",
                        parent=self)
                    pudelko.append(kod or "")
                    zdarzenie.set()
        except queue.Empty:
            pass
        self.after(100, self._pump)

    def _prompt_mfa(self):
        """Wywoływane z wątku roboczego przez bibliotekę Garmina - pytanie o kod idzie do głównego wątku."""
        zdarzenie, pudelko = threading.Event(), []
        self.q.put(("mfa", zdarzenie, pudelko))
        zdarzenie.wait()
        kod = (pudelko[0] if pudelko else "").strip()
        if not kod:
            raise garmin_io.BladGarmin("Anulowano wpisywanie kodu weryfikacyjnego.")
        return kod

    # ------------------------------------------------------------------ logowanie
    def _start_token(self):
        if not garmin_io.zapisane_logowanie():
            return
        self.status("Sprawdzam zapisane logowanie...")
        self._odswiez_przyciski()
        self._zajety(True)

        def ok(klient):
            self._zajety(False)
            if klient:
                self.klient = klient
                self.lbl_konto_pod.configure(text="Logowanie zapamiętane na tym komputerze")
            else:
                self.status("Zapisane logowanie wygasło - zaloguj się ponownie.")
            self._odswiez_przyciski()

        self._run(garmin_io.zaloguj_tokenem, ok, lambda e: (self._zajety(False), self.status("")))

    def on_login(self):
        email, haslo, zapamietaj = self.var_email.get(), self.var_haslo.get(), self.var_zapamietaj.get()
        if not email.strip() or not haslo:
            messagebox.showwarning("Logowanie", "Wpisz e-mail i hasło do Garmin Connect.")
            return
        self.status("Logowanie...")
        self._zajety(True)

        def ok(klient):
            self.klient = klient
            self.var_haslo.set("")
            self._zajety(False)
            self.status("")
            self.lbl_konto_pod.configure(text="Zalogowano" + (" - logowanie zapamiętane" if zapamietaj else ""))
            self._odswiez_przyciski()

        def blad(e):
            self._zajety(False)
            self.status("Nie udało się zalogować.", "err")
            messagebox.showerror("Logowanie", str(e))

        self._run(lambda: garmin_io.zaloguj_haslem(email, haslo, self._prompt_mfa, zapamietaj), ok, blad)

    def on_logout(self):
        if not messagebox.askyesno("Wylogowanie",
                                   "Usunąć zapisany token logowania z tego komputera?\n"
                                   "Przy następnym użyciu trzeba będzie zalogować się ponownie."):
            return
        garmin_io.wyloguj()
        self.klient = None
        self.status("")
        self._odswiez_przyciski()

    # ------------------------------------------------------------------ plan
    def on_prompt(self):
        self.clipboard_clear()
        self.clipboard_append(plan.PROMPT_AI)
        self.update()
        messagebox.showinfo(
            "Prompt skopiowany",
            "Prompt jest w schowku.\n\n"
            "1. Wklej go do swojego AI (ChatGPT, Claude, Gemini...).\n"
            "2. W oznaczonych miejscach wpisz swoje strefy tętna, rok i wklej rozpiskę od trenera.\n"
            "3. Skopiuj odpowiedź AI (blok JSON) i wklej ją tutaj, w pole planu.")

    def on_plik(self):
        sciezka = filedialog.askopenfilename(
            title="Wybierz plik z planem",
            filetypes=[("Plan (JSON lub tekst)", "*.json *.txt"), ("Wszystkie pliki", "*.*")])
        if not sciezka:
            return
        try:
            with open(sciezka, encoding="utf-8-sig") as f:
                tekst = f.read()
        except (OSError, UnicodeDecodeError) as e:
            messagebox.showerror("Plik", f"Nie mogę odczytać pliku:\n{e}")
            return
        self._ustaw_plan(tekst)

    def on_przyklad(self):
        self._ustaw_plan(plan.PRZYKLAD_PLANU)

    def _ustaw_plan(self, tekst):
        self.txt_plan.delete("1.0", "end")
        self.txt_plan.insert("1.0", tekst)
        self.on_sprawdz()

    def on_czysc(self):
        self._ustaw_plan("")

    def _po_zmianie(self):
        """Podgląd odświeża się sam, pół sekundy po ostatnim wpisanym znaku."""
        self._odswiez_wskazowke()
        if self._odroczone:
            self.after_cancel(self._odroczone)
        self._odroczone = self.after(500, self.on_sprawdz)

    def _odswiez_wskazowke(self):
        pusty = not self.txt_plan.get("1.0", "end").strip()
        if pusty:
            self.lbl_wskazowka.place(x=12, y=10)
        else:
            self.lbl_wskazowka.place_forget()

    def _parsuj(self):
        """Wczytuje plan z pola tekstowego i odświeża podgląd. Zwraca listę wpisów albo None."""
        self._odroczone = None
        self._odswiez_wskazowke()
        tekst = self.txt_plan.get("1.0", "end")
        self.wyniki, self.blad_planu = None, None
        if tekst.strip():
            try:
                self.wyniki = plan.wczytaj(tekst)
            except plan.BladPlanu as e:
                self.blad_planu = str(e)
        self.wyslano = False
        self._pokaz_podglad()
        self._odswiez_przyciski()
        return self.wyniki

    def on_sprawdz(self):
        self._parsuj()

    # ------------------------------------------------------------------ wysyłka
    def on_wyslij(self):
        if not self.klient:
            messagebox.showwarning("Wysyłka", "Najpierw zaloguj się do Garmin Connect.")
            return
        wyniki = self._parsuj()
        if wyniki is None:
            messagebox.showerror("Wysyłka", "W planie są błędy - popraw je (szczegóły w podglądzie).")
            return
        n = sum(1 for x in wyniki if not x["wolne"])
        if n == 0:
            messagebox.showinfo("Wysyłka", "W planie nie ma treningów do wysłania (same dni wolne).")
            return
        konto = garmin_io.nazwa_konta(self.klient)
        if not messagebox.askyesno(
                "Wysyłka",
                f"Wysłać {n} trening(ów) na konto: {konto}?\n\n"
                "Treningi z datą trafią do kalendarza Garmin Connect, a po synchronizacji pojawią się na zegarku."):
            return
        nadpisz, klient = self.var_nadpisz.get(), self.klient
        self._wpisz(self.txt_log, "")
        self.wyslano = False
        self.log("Wysyłam...")
        self._zajety(True)

        def ok(wynik):
            self._zajety(False)
            self.wyslano = wynik["bledy"] == 0
            self._pille()
            self.log(f"\nGotowe: wysłano {wynik['wyslane']}, pominięto (już były) {wynik['pominiete']}, "
                     f"błędy {wynik['bledy']}.")
            self.log("Zsynchronizuj zegarek - treningi będą w: Trening > Treningi.")
            if wynik["bledy"]:
                messagebox.showwarning("Wysyłka", "Część treningów nie została wysłana - szczegóły w logu.")

        def blad(e):
            self._zajety(False)
            self.log(f"BŁĄD: {e}")
            messagebox.showerror("Wysyłka", str(e))

        self._run(lambda: garmin_io.wyslij(klient, wyniki, nadpisz, lambda m: self.q.put(("log", m))), ok, blad)


# ---------------------------------------------------------------------- start

def selftest() -> int:
    """Szybki test bez okna i bez sieci - używany w budowie paczki, żeby wykryć brakujące moduły."""
    import tkinter  # noqa: F401  (musi się dać zaimportować w zbudowanej paczce)
    import curl_cffi  # noqa: F401
    import garminconnect

    garminconnect.Garmin()                                   # konstruktor bez logowania
    wyniki = plan.wczytaj(plan.PRZYKLAD_PLANU)
    assert sum(1 for x in wyniki if not x["wolne"]) == 2, "przykład powinien mieć 2 treningi"
    assert "Treningów do wysłania: 2" in plan.pokaz(wyniki)
    print(f"{NAZWA} {WERSJA}: selftest OK")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    if sys.platform == "win32":
        try:                                                  # ostre czcionki na ekranach HiDPI
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    App().mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
