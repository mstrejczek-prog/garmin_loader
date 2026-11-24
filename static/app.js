// Garmin Plan Loader - strona w przeglądarce (komputer i telefon).
// Strefy, szkic planu i zaszyfrowany token zostają w tej przeglądarce; serwer niczego nie przechowuje.
"use strict";

const $ = (id) => document.getElementById(id);
const KLUCZ = { ust: "gpl.ust", token: "gpl.token", konto: "gpl.konto", plan: "gpl.plan", kod: "gpl.kod" };
const KOLOR_STREFY = ["--s0", "--s1", "--s2", "--s3", "--s4", "--s5"];

const stan = {
  start: null,           // /api/start: przykład, nazwy stref
  ust: { strefy: {}, moc: {}, ftp: null },
  token: null, konto: null,
  mfa: null,             // zaszyfrowany stan logowania czekającego na kod
  treningi: null, bladPlanu: null, wyslano: false, zajety: false,
  zapytanie: 0, odroczone: null,
};

// ------------------------------------------------------------------ pamięć przeglądarki (może być niedostępna)
function czytaj(klucz, gdzie = localStorage) {
  try { return gdzie.getItem(klucz); } catch { return null; }
}
function pisz(klucz, wartosc, gdzie = localStorage) {
  try {
    if (wartosc === null || wartosc === undefined) gdzie.removeItem(klucz);
    else gdzie.setItem(klucz, wartosc);
  } catch { /* tryb prywatny itp. - działa, tylko bez zapamiętywania */ }
}

// ------------------------------------------------------------------ komunikacja z serwerem
async function api(sciezka, dane, opcje = {}) {
  const naglowki = { "Content-Type": "application/json" };
  const kod = czytaj(KLUCZ.kod);
  if (kod) naglowki["X-Kod-Dostepu"] = kod;
  let odp;
  try {
    odp = await fetch(sciezka, {
      method: dane === undefined ? "GET" : "POST", headers: naglowki,
      body: dane === undefined ? undefined : JSON.stringify(dane), signal: opcje.signal,
    });
  } catch (e) {
    if (e.name === "AbortError") throw e;
    throw Object.assign(new Error("Brak połączenia z serwerem. Sprawdź internet i spróbuj ponownie."), { siec: true });
  }
  if (opcje.surowa) return odp;
  let json = {};
  try { json = await odp.json(); } catch { /* np. strona błędu proxy */ }
  if (odp.status === 403 && json.kod_dostepu) {
    await zapytajOKod(json.blad);
    return api(sciezka, dane, opcje);
  }
  if (!odp.ok) {
    const blad = new Error(json.blad || `Błąd serwera (${odp.status}). Spróbuj ponownie za chwilę.`);
    Object.assign(blad, json, { status: odp.status });
    throw blad;
  }
  return json;
}

function zapytajOKod(komunikat) {
  return new Promise((ok) => {
    const dlg = $("dlg-kod");
    $("kod-blad").textContent = czytaj(KLUCZ.kod) ? (komunikat || "") : "";
    $("kod-dostepu").value = "";
    dlg.onclose = () => { pisz(KLUCZ.kod, $("kod-dostepu").value.trim() || null); ok(); };
    dlg.showModal();
  });
}

// ------------------------------------------------------------------ drobne elementy interfejsu
function el(tag, klasa, tekst) {
  const e = document.createElement(tag);
  if (klasa) e.className = klasa;
  if (tekst !== undefined) e.textContent = tekst;
  return e;
}

let toastTimer;
function toast(tekst) {
  const t = $("toast");
  t.textContent = tekst;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 3500);
}

function komunikat(tytul, opis, tekst) {
  $("tekst-tytul").textContent = tytul;
  $("tekst-opis").textContent = opis || "";
  const pole = $("tekst-pole");
  pole.hidden = !tekst;
  pole.value = tekst || "";
  $("dlg-tekst").showModal();
  if (tekst) { pole.focus(); pole.select(); }
}

function potwierdz(tytul, opis, tak = "Tak") {
  return new Promise((ok) => {
    const dlg = $("dlg-potwierdz");
    $("potwierdz-tytul").textContent = tytul;
    $("potwierdz-opis").textContent = opis;
    $("potwierdz-tak").textContent = tak;
    dlg.returnValue = "";
    dlg.onclose = () => ok(dlg.returnValue === "tak");
    dlg.showModal();
  });
}

function liczbaTreningow(n) {
  if (n === 1) return "1 trening";
  if (n % 10 >= 2 && n % 10 <= 4 && !(n % 100 >= 12 && n % 100 <= 14)) return `${n} treningi`;
  return `${n} treningów`;
}

function status(id, tekst, rodzaj = "") {
  const s = $(id);
  s.textContent = tekst;
  s.className = "status " + rodzaj;
}

// ------------------------------------------------------------------ stan widoku
function odswiez() {
  const zalogowany = !!stan.token;
  $("form-logowanie").hidden = zalogowany || !!stan.mfa;
  $("form-mfa").hidden = zalogowany || !stan.mfa;
  $("zalogowany").hidden = !zalogowany;
  if (zalogowany) {
    $("konto-nazwa").textContent = stan.konto || "konto Garmin";
    $("konto-pod").textContent = czytaj(KLUCZ.token)
      ? "Logowanie zapamiętane w tej przeglądarce." : "Zalogowano do zamknięcia karty.";
  }
  for (const b of ["btn-zaloguj", "btn-mfa", "btn-wyloguj"]) $(b).disabled = stan.zajety;

  const n = (stan.treningi || []).filter((x) => !x.wolne).length;
  const wyslij = $("btn-wyslij");
  wyslij.disabled = !(zalogowany && n > 0 && !stan.zajety && !stan.bladPlanu);
  wyslij.textContent = n ? `Wyślij ${liczbaTreningow(n)}` : "Wyślij do Garmin Connect";
  let wskaz = "";
  if (stan.zajety) wskaz = "";
  else if (!zalogowany) wskaz = "Najpierw zaloguj się do konta Garmin.";
  else if (stan.bladPlanu) wskaz = "Popraw błędy w planie.";
  else if (!n) wskaz = "Wklej plan, żeby go wysłać.";
  else wskaz = "Trafią do kalendarza Garmin Connect.";
  $("wskazowka-wysylki").textContent = wskaz;

  const gotowe = [zalogowany, n > 0 && !stan.bladPlanu, stan.wyslano];
  const aktualny = gotowe.indexOf(false);
  document.querySelectorAll(".kroki li").forEach((li, i) => {
    li.classList.toggle("gotowe", gotowe[i]);
    li.classList.toggle("aktualny", !gotowe[i] && i === aktualny);
  });
}

function odswiezStrefy() {
  const u = stan.ust, czesci = [];
  const sporty = ["bieg", "rower", "plywanie"].filter((sp) => u.strefy && u.strefy[sp]);
  if (sporty.length) czesci.push(`tętno (${sporty.join(", ")})`);
  if (u.moc && u.moc.rower) czesci.push("moc");
  if (u.ftp) czesci.push(`FTP ${u.ftp} W`);
  const s = $("strefy-stan");
  s.textContent = czesci.length ? "✓ " + czesci.join(", ") : "Strefy i FTP: nie ustawiono";
  s.style.color = czesci.length ? "var(--ok)" : "";
}

// ------------------------------------------------------------------ logowanie
function ustawToken(token, konto, zapamietaj) {
  stan.token = token;
  stan.konto = konto;
  pisz(KLUCZ.token, null); pisz(KLUCZ.token, null, sessionStorage);
  if (token) {
    pisz(KLUCZ.token, token, zapamietaj ? localStorage : sessionStorage);
    pisz(KLUCZ.konto, konto);
  } else {
    pisz(KLUCZ.konto, null);
  }
}

function odnowToken(token) {
  // biblioteka Garmina odświeża token przy użyciu - zapisz nową wersję tam, gdzie leżała stara
  if (!token) return;
  stan.token = token;
  if (czytaj(KLUCZ.token)) pisz(KLUCZ.token, token);
  else pisz(KLUCZ.token, token, sessionStorage);
}

async function zaloguj(ev) {
  ev.preventDefault();
  const email = $("email").value.trim(), haslo = $("haslo").value;
  if (!email || !haslo) { status("status-logowania", "Wpisz e-mail i hasło.", "err"); return; }
  stan.zajety = true; odswiez();
  status("status-logowania", "Logowanie... (to może potrwać kilkanaście sekund)");
  try {
    const odp = await api("/api/logowanie", { email, haslo });
    $("haslo").value = "";
    if (odp.stan === "mfa") {
      stan.mfa = odp.mfa;
      status("status-logowania", "");
      status("status-mfa", "");
      $("kod-mfa").value = "";
      odswiez();
      $("kod-mfa").focus();
    } else {
      ustawToken(odp.token, odp.konto, $("zapamietaj").checked);
      status("status-logowania", "");
      infoConnector(odp.connector);
    }
  } catch (e) {
    status("status-logowania", e.message, "err");
  } finally {
    stan.zajety = false; odswiez();
  }
}

function infoConnector(c) {
  if (!c) return;
  if (c.zapisano) toast("Logowanie zapisane też dla Claude (connector) - nie trzeba nic zmieniać na serwerze.");
  else if (c.powod && c.powod.indexOf("nie jest właścicielem") < 0)
    toast("Connector dla Claude: " + c.powod + (c.profil ? " (profil " + c.profil + ")" : ""));
}

async function potwierdzMfa(ev) {
  ev.preventDefault();
  const kod = $("kod-mfa").value.trim();
  if (!kod) { status("status-mfa", "Wpisz kod.", "err"); return; }
  stan.zajety = true; odswiez();
  status("status-mfa", "Sprawdzam kod...");
  try {
    const odp = await api("/api/mfa", { mfa: stan.mfa, kod });
    stan.mfa = null;
    ustawToken(odp.token, odp.konto, $("zapamietaj").checked);
    infoConnector(odp.connector);
  } catch (e) {
    if (e.od_nowa) { stan.mfa = null; status("status-logowania", e.message, "err"); }
    else status("status-mfa", e.message, "err");
  } finally {
    stan.zajety = false; odswiez();
  }
}

function anulujMfa() {
  stan.mfa = null;
  status("status-logowania", "");
  odswiez();
}

async function wyloguj() {
  if (!await potwierdz("Wylogowanie", "Usunąć zapisane logowanie z tej przeglądarki? Przy następnym użyciu trzeba będzie zalogować się ponownie.", "Wyloguj")) return;
  ustawToken(null, null);
  stan.wyslano = false;
  odswiez();
}

// ------------------------------------------------------------------ plan i podgląd
function tekstPlanu() { return $("tekst-planu").value; }

function ustawPlan(tekst) {
  $("tekst-planu").value = tekst;
  pisz(KLUCZ.plan, tekst || null);
  sprawdzPlan();
}

function poZmianie() {
  pisz(KLUCZ.plan, tekstPlanu() || null);
  clearTimeout(stan.odroczone);
  stan.odroczone = setTimeout(sprawdzPlan, 500);
}

async function sprawdzPlan() {
  clearTimeout(stan.odroczone);
  const nr = ++stan.zapytanie, tekst = tekstPlanu();
  stan.wyslano = false;
  if (!tekst.trim()) {
    stan.treningi = null; stan.bladPlanu = null;
    rysujPodglad(); odswiez();
    return;
  }
  try {
    const odp = await api("/api/podglad", { plan: tekst, ust: stan.ust });
    if (nr !== stan.zapytanie) return;                  // przyszła odpowiedź na starszą wersję tekstu
    stan.bladPlanu = odp.blad || null;
    stan.treningi = odp.blad ? null : odp.treningi;
  } catch (e) {
    if (nr !== stan.zapytanie) return;
    stan.bladPlanu = e.message; stan.treningi = null;
  }
  rysujPodglad(); odswiez();
}

function rysujPodglad() {
  const lista = $("lista-treningow");
  lista.replaceChildren();
  if (stan.bladPlanu) {
    const r = el("div", "blad-ramka");
    r.append(el("strong", "", "Nie mogę wczytać planu"), el("p", "", stan.bladPlanu));
    if (/brak strefy|wymaga FTP/.test(stan.bladPlanu)) {
      const b = el("button", "drugi", "Ustaw strefy i FTP");
      b.type = "button";
      b.addEventListener("click", otworzStrefy);
      r.append(b);
    }
    lista.append(r);
    return;
  }
  if (!stan.treningi || !stan.treningi.length) {
    const p = el("div", "pusto");
    p.append(el("h3", "", "Tu zobaczysz swoje treningi"),
      el("p", "pod", "Wklej plan - podgląd pojawi się od razu."));
    const b = el("button", "drugi", "Zobacz przykład");
    b.type = "button";
    b.addEventListener("click", wstawPrzyklad);
    p.append(b);
    lista.append(p);
    return;
  }
  for (const x of stan.treningi) lista.append(kartaTreningu(x));
}

function kartaTreningu(x) {
  const k = el("article", "trening");
  const gora = el("div", "trening-gora");
  gora.append(el("span", x.dzien ? "dzien" : "dzien brak", x.dzien || "bez daty"));
  if (x.wolne) {
    gora.append(el("span", "pod", "Dzień wolny"));
    k.append(gora);
    return k;
  }
  const m = x.minuty;
  const czas = m >= 60 ? `ok. ${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")} min` : `ok. ${m} min`;
  gora.append(el("span", "trening-nazwa", x.nazwa), el("span", "trening-info", `${czas} · ${x.sport}`));
  k.append(gora, profil(x.profil));

  const tab = el("table", "kroki-tabela");
  const tb = el("tbody");
  x.wiersze.forEach(([nr, et, dl, cel], i) => {
    const tr = el("tr");
    const tdCel = el("td", "cel");
    const kropka = el("span", "kropka");
    kropka.style.background = `var(${KOLOR_STREFY[x.poziomy[i]] || "--s0"})`;
    tdCel.append(kropka, document.createTextNode(cel));
    tr.append(el("td", "nr", String(nr)), el("td", "", et), el("td", "dl", dl), tdCel);
    tb.append(tr);
  });
  tab.append(tb);
  k.append(tab);
  return k;
}

function profil(kroki) {
  // wykres kroków: szerokość = czas trwania, wysokość i kolor = intensywność (strefa)
  const NS = "http://www.w3.org/2000/svg", SZER = 1000, WYS = 56, ODSTEP = 4;
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("class", "profil");
  svg.setAttribute("viewBox", `0 0 ${SZER} ${WYS}`);
  svg.setAttribute("preserveAspectRatio", "none");
  svg.setAttribute("aria-hidden", "true");
  if (!kroki || !kroki.length) return svg;
  const suma = kroki.reduce((s, p) => s + p[0], 0) || 1;
  const dostepne = SZER - ODSTEP * (kroki.length - 1);
  let x = 0;
  for (const [sekundy, poziom, typ] of kroki) {
    const dl = Math.max(4, dostepne * sekundy / suma);
    const h = poziom === 0 ? (typ === 4 || typ === 5 ? 14 : 30) : 10 + poziom * 9;
    const r = document.createElementNS(NS, "rect");
    r.setAttribute("x", x.toFixed(1)); r.setAttribute("y", WYS - h);
    r.setAttribute("width", dl.toFixed(1)); r.setAttribute("height", h);
    r.setAttribute("rx", "1.5");
    r.style.fill = `var(${KOLOR_STREFY[poziom] || "--s0"})`;
    svg.append(r);
    x += dl + ODSTEP;
  }
  return svg;
}

function wstawPrzyklad() { if (stan.start) ustawPlan(stan.start.przyklad); }

async function czytajPlik(ev) {
  const plik = ev.target.files && ev.target.files[0];
  ev.target.value = "";
  if (!plik) return;
  if (plik.size > 512 * 1024) { komunikat("Plik", "Ten plik jest za duży jak na plan treningowy."); return; }
  try {
    ustawPlan((await plik.text()).replace(/^﻿/, ""));
  } catch (e) {
    komunikat("Plik", `Nie mogę odczytać pliku: ${e.message}`);
  }
}

// ------------------------------------------------------------------ prompt dla AI
async function pobierzPrompt() {
  return (await api("/api/prompt", { ust: stan.ust })).prompt;
}

function opisPromptu() {
  const maStrefy = (stan.ust.strefy && Object.keys(stan.ust.strefy).length) || stan.ust.ftp;
  return "1. Wklej go do swojego AI (ChatGPT, Claude, Gemini...).\n" +
    (maStrefy
      ? "2. Wpisz rok lub daty treningów i wklej rozpiskę od trenera. Zapisane strefy i FTP prompt już zawiera.\n"
      : "2. W oznaczonych miejscach wpisz swoje strefy tętna, FTP, rok i wklej rozpiskę od trenera. (Strefy możesz zapisać raz przyciskiem „Moje strefy i FTP”.)\n") +
    "3. Skopiuj odpowiedź AI (blok JSON) i wklej ją tutaj, w pole planu.";
}

async function kopiujPrompt() {
  let prompt;
  try { prompt = await pobierzPrompt(); } catch (e) { komunikat("Prompt", e.message); return; }
  try {
    await navigator.clipboard.writeText(prompt);
    komunikat("Prompt skopiowany", opisPromptu());
  } catch {
    komunikat("Skopiuj prompt", "Przeglądarka nie pozwoliła na automatyczne kopiowanie - zaznacz tekst poniżej i skopiuj go.\n\n" + opisPromptu(), prompt);
  }
}

async function udostepnijPrompt() {
  let prompt;
  try { prompt = await pobierzPrompt(); } catch (e) { komunikat("Prompt", e.message); return; }
  try {
    await navigator.share({ title: "Prompt - Garmin Plan Loader", text: prompt });
  } catch (e) {
    if (e.name !== "AbortError") kopiujPrompt();
  }
}

// ------------------------------------------------------------------ strefy i FTP
let tabelaStref = "bieg";

function otworzStrefy() {
  const u = stan.ust;
  const nazwyS = (stan.start && stan.start.nazwy_stref) || ["S1", "S2", "S3", "S4", "S5"];
  const nazwyP = (stan.start && stan.start.nazwy_mocy) || ["P1", "P2", "P3", "P4", "P5"];
  $("ftp").value = u.ftp ? String(u.ftp) : "";
  const zrodla = {
    bieg: [(u.strefy || {}).bieg, nazwyS],
    rower: [(u.strefy || {}).rower, nazwyS],
    moc: [(u.moc || {}).rower, nazwyP],
    plywanie: [(u.strefy || {}).plywanie, nazwyS],
  };
  const kontener = $("tabele-stref");
  kontener.replaceChildren();
  for (const [klucz, [zapisane, nazwy]] of Object.entries(zrodla)) {
    const tab = el("table", "tabela-stref");
    tab.dataset.tabela = klucz;
    tab.setAttribute("role", "tabpanel");
    const jedn = klucz === "moc" ? "W" : "bpm";
    const th = el("tr");
    th.append(el("th", "", ""), el("th", "", `od (${jedn})`), el("th", "", `do (${jedn})`));
    const thead = el("thead"); thead.append(th);
    const tb = el("tbody");
    nazwy.forEach((n, i) => {
      const tr = el("tr");
      const td0 = el("td");
      td0.append(el("span", `znacznik s${i + 1}`, n));
      tr.append(td0);
      const z = (zapisane && zapisane[n]) || ["", ""];
      [0, 1].forEach((j) => {
        const td = el("td"), inp = el("input");
        inp.type = "text"; inp.inputMode = "numeric";
        inp.dataset.strefa = n; inp.dataset.granica = String(j);
        inp.value = z[j] === "" || z[j] === undefined ? "" : String(z[j]);
        inp.setAttribute("aria-label", `${n} ${j ? "do" : "od"}`);
        td.append(inp); tr.append(td);
      });
      tb.append(tr);
    });
    tab.append(thead, tb);
    kontener.append(tab);
  }
  pokazTabele(tabelaStref);
  $("strefy-blad").textContent = "";
  $("strefy-info").textContent = "";
  $("dlg-strefy").showModal();
}

function pokazTabele(klucz) {
  tabelaStref = klucz;
  document.querySelectorAll(".zakladki button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tabela === klucz)));
  document.querySelectorAll(".tabela-stref").forEach((t) => { t.hidden = t.dataset.tabela !== klucz; });
}

function zbierzPola() {
  const pola = { ftp: $("ftp").value };
  document.querySelectorAll(".tabela-stref").forEach((t) => {
    const w = {};
    t.querySelectorAll("input").forEach((inp) => {
      (w[inp.dataset.strefa] ||= ["", ""])[Number(inp.dataset.granica)] = inp.value;
    });
    pola[t.dataset.tabela] = w;
  });
  return pola;
}

async function policzMoc() {
  $("strefy-blad").textContent = "";
  try {
    const odp = await api("/api/moc-z-ftp", { ftp: $("ftp").value });
    document.querySelectorAll('.tabela-stref[data-tabela="moc"] input').forEach((inp) => {
      inp.value = String(odp.moc[inp.dataset.strefa][Number(inp.dataset.granica)]);
    });
    $("strefy-info").textContent = odp.opis;
    pokazTabele("moc");
  } catch (e) {
    $("strefy-blad").textContent = e.message;
  }
}

function zapiszUst(ust) {
  stan.ust = ust;
  const puste = !(Object.keys(ust.strefy || {}).length || Object.keys(ust.moc || {}).length || ust.ftp);
  pisz(KLUCZ.ust, puste ? null : JSON.stringify(ust));
  odswiezStrefy();
  if (tekstPlanu().trim()) sprawdzPlan();          // cele planu mogły dostać nowe wartości
}

async function zapiszStrefy() {
  $("strefy-blad").textContent = "";
  try {
    const odp = await api("/api/strefy", { pola: zbierzPola() });
    zapiszUst(odp.ust);
    $("dlg-strefy").close();
    toast("Strefy zapisane w tej przeglądarce.");
  } catch (e) {
    $("strefy-blad").textContent = e.message;
  }
}

function usunStrefy() {
  zapiszUst({ strefy: {}, moc: {}, ftp: null });
  $("dlg-strefy").close();
}

// ------------------------------------------------------------------ wysyłka
function logDopisz(tekst) {
  const l = $("log");
  l.hidden = false;
  l.textContent += tekst + "\n";
  l.scrollTop = l.scrollHeight;
}

async function wyslij() {
  const n = (stan.treningi || []).filter((x) => !x.wolne).length;
  if (!stan.token || !n) return;
  if (!await potwierdz("Wysyłka",
    `Wysłać ${liczbaTreningow(n)} na konto: ${stan.konto}?\n\nTreningi z datą trafią do kalendarza Garmin Connect, a po synchronizacji pojawią się na zegarku.`,
    "Wyślij")) return;

  stan.zajety = true; stan.wyslano = false; odswiez();
  $("log").textContent = "";
  let koniec = null, blad = null;
  try {
    const odp = await api("/api/wyslij", { plan: tekstPlanu(), ust: stan.ust, nadpisz: $("nadpisz").checked, token: stan.token }, { surowa: true });
    if (!odp.ok) {
      let json = {};
      try { json = await odp.json(); } catch { /* brak treści */ }
      if (odp.status === 403 && json.kod_dostepu) { await zapytajOKod(json.blad); stan.zajety = false; odswiez(); return wyslij(); }
      if (json.wygaslo) { ustawToken(null, null); status("status-logowania", json.blad, "err"); }
      throw new Error(json.blad || `Błąd serwera (${odp.status}).`);
    }
    // odpowiedź przychodzi kawałkami: każda linia to osobny komunikat JSON
    const czytnik = odp.body.getReader(), dekoder = new TextDecoder();
    let bufor = "";
    for (;;) {
      const { value, done } = await czytnik.read();
      if (value) bufor += dekoder.decode(value, { stream: true });
      let i;
      while ((i = bufor.indexOf("\n")) >= 0) {
        const linia = bufor.slice(0, i).trim();
        bufor = bufor.slice(i + 1);
        if (!linia) continue;
        const m = JSON.parse(linia);
        if (m.log) logDopisz(m.log);
        if (m.koniec) { koniec = m.koniec; odnowToken(m.token); }
        if (m.blad) blad = m.blad;
      }
      if (done) break;
    }
  } catch (e) {
    blad = e.siec || e instanceof TypeError
      ? "Połączenie zostało przerwane. Wysyłka mogła się dokończyć na serwerze - sprawdź Garmin Connect albo wyślij ponownie (treningi się nie zdublują)."
      : e.message;
  }
  stan.zajety = false;
  if (koniec) {
    stan.wyslano = koniec.bledy === 0;
    logDopisz(`\nGotowe: wysłano ${koniec.wyslane}, pominięto (już były) ${koniec.pominiete}, błędy ${koniec.bledy}.`);
    logDopisz("Zsynchronizuj zegarek - treningi będą w: Trening > Treningi.");
    if (koniec.bledy) komunikat("Wysyłka", "Część treningów nie została wysłana - szczegóły w logu.");
    else toast("Wysłano do Garmin Connect.");
  } else if (blad) {
    logDopisz(`BŁĄD: ${blad}`);
    komunikat("Wysyłka", blad);
  }
  odswiez();
}

// ------------------------------------------------------------------ start
// ------------------------------------------------------------------ connector dla Claude (MCP)
function pokazConnector() {
  const mcp = (stan.start && stan.start.mcp) || {};
  const ust = stan.ust && (Object.keys(stan.ust.strefy || {}).length || Object.keys(stan.ust.moc || {}).length || stan.ust.ftp)
    ? JSON.stringify(stan.ust) : "";
  const tekst = [
    "# Zmienne do ustawienia na serwerze (Render → Environment):",
    "GARMIN_TOKEN=" + (stan.token || ""),
    ust ? "USTAWIENIA=" + ust : "# USTAWIENIA: brak zapisanych stref i FTP w tej przeglądarce",
    mcp.wlaczony ? "# MCP_TOKEN: ustawiony" : "MCP_TOKEN=<wymyśl długi losowy tekst>",
    "",
    "# W claude.ai: Ustawienia → Connectors → Dodaj własny connector:",
    "#   adres:   " + location.origin + "/mcp",
    "#   nagłówek: Authorization: Bearer <MCP_TOKEN>",
  ].join("\n");
  komunikat("Connector dla Claude",
    "Dzięki niemu Claude w czacie wysyła plany i czyta Twoje dane z Garmina. GARMIN_TOKEN to Twoje logowanie " +
    "(zaszyfrowane kluczem serwera) - nie pokazuj go nikomu. Gdy Claude zgłosi, że logowanie wygasło, zaloguj się " +
    "tu ponownie i podmień GARMIN_TOKEN." + (mcp.token_garmina ? " Serwer ma już ustawiony GARMIN_TOKEN." : ""),
    tekst);
}

async function init() {
  try {
    const u = JSON.parse(czytaj(KLUCZ.ust) || "null");
    if (u && typeof u === "object") stan.ust = { strefy: u.strefy || {}, moc: u.moc || {}, ftp: u.ftp || null };
  } catch { /* uszkodzone - zaczynamy od pustych */ }
  stan.token = czytaj(KLUCZ.token) || czytaj(KLUCZ.token, sessionStorage);
  stan.konto = stan.token ? czytaj(KLUCZ.konto) : null;
  $("tekst-planu").value = czytaj(KLUCZ.plan) || "";

  $("form-logowanie").addEventListener("submit", zaloguj);
  $("form-mfa").addEventListener("submit", potwierdzMfa);
  $("btn-mfa-anuluj").addEventListener("click", anulujMfa);
  $("btn-wyloguj").addEventListener("click", wyloguj);
  $("btn-connector").addEventListener("click", pokazConnector);
  $("btn-strefy").addEventListener("click", otworzStrefy);
  $("btn-prompt").addEventListener("click", kopiujPrompt);
  $("btn-udostepnij").addEventListener("click", udostepnijPrompt);
  $("btn-udostepnij").hidden = !(navigator.share && matchMedia("(pointer: coarse)").matches);
  $("plik").addEventListener("change", czytajPlik);
  $("btn-przyklad").addEventListener("click", wstawPrzyklad);
  $("btn-czysc").addEventListener("click", () => ustawPlan(""));
  $("tekst-planu").addEventListener("input", poZmianie);
  $("btn-wyslij").addEventListener("click", wyslij);
  $("btn-policz-moc").addEventListener("click", policzMoc);
  $("btn-strefy-zapisz").addEventListener("click", zapiszStrefy);
  $("btn-strefy-anuluj").addEventListener("click", () => $("dlg-strefy").close());
  $("btn-strefy-usun").addEventListener("click", usunStrefy);
  $("form-strefy").addEventListener("submit", (e) => { e.preventDefault(); zapiszStrefy(); });
  document.querySelectorAll(".zakladki button").forEach((b) => b.addEventListener("click", () => pokazTabele(b.dataset.tabela)));

  odswiezStrefy();
  odswiez();
  rysujPodglad();
  try {
    stan.start = await api("/api/start");
    $("wersja").textContent = `Wersja ${stan.start.wersja}.`;
    if (stan.start.kod_dostepu && !czytaj(KLUCZ.kod)) await zapytajOKod();
  } catch (e) {
    toast(e.message);
  }
  if (tekstPlanu().trim()) sprawdzPlan();
}

document.addEventListener("DOMContentLoaded", init);
