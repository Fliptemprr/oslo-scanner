"""
Akseptansetester for Correction Radar — §41 A–M i masterspesifikasjonen.

Kjør:  python test_scanner.py

Testene bruker syntetiske kursserier med bevisst ulik volatilitet, fordi
poenget nettopp er at radaren skal behandle en rolig bank og et volatilt
flyselskap forskjellig. Ingen nettverkstilgang kreves.
"""

import copy
import numpy as np
import pandas as pd

import scanner as S


PROFILER = {
    "DNB.OL": {"sigma": 1.0, "drift": 0.030, "seed": 11},
    "NOD.OL": {"sigma": 2.4, "drift": 0.030, "seed": 22},
    "NAS.OL": {"sigma": 3.6, "drift": 0.010, "seed": 33},
}

def lag_df(n=780, sigma=2.0, drift=0.03, seed=0, start=100.0):
    rng = np.random.default_rng(seed)
    r = rng.normal(drift / 100, sigma / 100, n)
    close = start * np.exp(np.cumsum(r))
    prev = np.concatenate([[start], close[:-1]])
    open_ = prev * (1 + rng.normal(0, sigma / 400, n))
    spread = np.abs(rng.normal(sigma / 200, sigma / 400, n))
    high = np.maximum(open_, close) * (1 + spread)
    low = np.minimum(open_, close) * (1 - spread)
    vol = rng.lognormal(np.log(1_500_000), 0.35, n)
    idx = pd.bdate_range(end="2026-09-04", periods=n)
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": vol},
        index=idx,
    )


def paalegg_fall(df, pct, dager, opptrapping=25):
    """Legg på et kontrollert fall på `pct` % over `dager` barer helt til slutt."""
    d = df.copy()
    n, i0 = len(d), len(d) - dager
    topp = float(d["Close"].iloc[:i0].max())
    loft = topp * 1.01 / float(d["Close"].iloc[i0])
    skala = np.ones(n)
    skala[i0 - opptrapping:i0] = np.linspace(1.0, loft, opptrapping)
    skala[i0:] = loft * np.linspace(1.0, 1 - pct / 100, dager)
    for k in ("Open", "High", "Low", "Close"):
        d[k] = d[k].to_numpy() * skala
    return d


def endagsfall(df, pct, volum_faktor=3.0):
    """Kraftig fall på siste bar, med gap ned og volumspike."""
    d = df.copy()
    prev = float(d["Close"].iloc[-2])
    ny = prev * (1 - pct / 100)
    d.iloc[-1, d.columns.get_loc("Open")] = prev * (1 - pct / 200)
    d.iloc[-1, d.columns.get_loc("Close")] = ny
    d.iloc[-1, d.columns.get_loc("High")] = prev * (1 - pct / 250)
    d.iloc[-1, d.columns.get_loc("Low")] = ny * 0.99
    d.iloc[-1, d.columns.get_loc("Volume")] = float(d["Volume"].iloc[-1]) * volum_faktor
    return d


def bane(punkter, barer_per_ben=12, sigma=0.4, seed=5, forhistorie=600):
    """Bygg en serie som følger en gitt prisbane, med litt støy."""
    rng = np.random.default_rng(seed)
    hale = []
    for a, b in zip(punkter[:-1], punkter[1:]):
        ben = np.linspace(a, b, barer_per_ben, endpoint=False)
        hale.append(ben)
    hale = np.concatenate(hale + [np.array([punkter[-1]])])
    hale = hale * (1 + rng.normal(0, sigma / 100, len(hale)))

    pre = lag_df(n=forhistorie, sigma=1.4, drift=0.02, seed=seed, start=punkter[0] * 0.75)
    pre_close = pre["Close"].to_numpy()
    pre_close = pre_close * (punkter[0] / pre_close[-1]) * 0.995
    close = np.concatenate([pre_close, hale])

    n = len(close)
    prev = np.concatenate([[close[0]], close[:-1]])
    open_ = (prev + close) / 2
    high = np.maximum(open_, close) * 1.004
    low = np.minimum(open_, close) * 0.996
    vol = rng.lognormal(np.log(1_500_000), 0.3, n)
    idx = pd.bdate_range(end="2026-09-04", periods=n)
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": vol},
        index=idx,
    )



def trendserie(n_pre=620, sigma=1.6, drift=0.055, seed=7, start=60.0):
    """Rolig, stigende aksje – gir en historikk med små korreksjoner."""
    return lag_df(n=n_pre, sigma=sigma, drift=drift, seed=seed, start=start)


def legg_til_ben(df, mal, barer, sigma=0.5, seed=3):
    """Forleng en serie med et prisben som ender på `mal` (multiplikator)."""
    rng = np.random.default_rng(seed)
    sist = float(df["Close"].iloc[-1])
    bane = np.linspace(sist, sist * mal, barer + 1)[1:]
    bane = bane * (1 + rng.normal(0, sigma / 100, barer))
    prev = np.concatenate([[sist], bane[:-1]])
    open_ = (prev + bane) / 2
    spread = np.abs(rng.normal(0.004, 0.002, barer))
    ny = pd.DataFrame({
        "Open": open_,
        "High": np.maximum(open_, bane) * (1 + spread),
        "Low": np.minimum(open_, bane) * (1 - spread),
        "Close": bane,
        "Volume": rng.lognormal(np.log(1_600_000), 0.3, barer),
    }, index=pd.bdate_range(start=df.index[-1] + pd.Timedelta(days=1), periods=barer, freq="B"))
    return pd.concat([df, ny])


def ath_serie(n=760, seed=101):
    """
    Aksje i normal opptrend som står på sin egen topp akkurat nå.
    Siste bar tvinges til å være seriens høyeste lukk, slik at drawdown er
    null og det ikke finnes noen aktiv korreksjon å hente seg inn fra.
    """
    d = lag_df(n=n, sigma=1.2, drift=0.14, seed=seed, start=40.0)
    d = legg_til_ben(d, 1.06, 20, sigma=0.25, seed=seed + 1)
    topp = float(d["Close"].max())
    for kol, faktor in (("Close", 1.004), ("High", 1.008),
                        ("Open", 1.000), ("Low", 0.996)):
        d.iloc[-1, d.columns.get_loc(kol)] = topp * faktor
    return d


def dyp_så_recovery(fall=0.80, seed=17):
    """Klar opptrend → dypt fall → bunn → bounce → higher low → brudd opp."""
    d = trendserie(n_pre=640, sigma=1.1, drift=0.16, seed=seed, start=30.0)
    d = legg_til_ben(d, fall, 26, seed=2)
    ved_bunn = d.copy()
    d = legg_til_ben(d, 1.09, 12, seed=3)
    d = legg_til_ben(d, 0.965, 8, seed=4)
    d = legg_til_ben(d, 1.12, 16, seed=5)
    return ved_bunn, d


def stabilisering_som_feiler(seed=23):
    """Fall → stabilisering → nytt lavpunkt. Skal beholde samme correctionId."""
    d = trendserie(n_pre=630, sigma=1.2, drift=0.14, seed=seed, start=35.0)
    d = legg_til_ben(d, 0.85, 22, seed=2)
    faller = d.copy()
    d = legg_til_ben(d, 1.07, 11, seed=3)
    d = legg_til_ben(d, 0.975, 7, seed=4)
    stabiliserer = d.copy()
    d = legg_til_ben(d, 0.88, 14, seed=5)      # bryter ned til nytt lavpunkt
    return faller, stabiliserer, d


def fjern_support_serie(seed=77):
    """Kraftig, sammenhengende fall uten reaksjoner nær dagens kurs."""
    d = lag_df(n=700, sigma=1.4, drift=0.10, seed=seed, start=25.0)
    return legg_til_ben(d, 0.62, 70, sigma=0.35, seed=seed + 1)

# ══════════════════════════════════════════════════════════════
# AKSEPTANSETESTER §41 A–M
# ══════════════════════════════════════════════════════════════

RES = []


def krav(bokstav, navn, ok, detalj=""):
    RES.append((ok, f"{bokstav}. {navn}", detalj))
    print(f"{'✓' if ok else '✗'} {bokstav}. {navn}" + (f"\n    {detalj}" if detalj else ""))


def scan(ticker, df, fund=None, state=None, cfg=None):
    return S.scan_stock(ticker, df, fund or {}, state, cfg or S.SCANNER_CONFIG)


GODKJENT = {"reportChecked": True, "guidanceChecked": True,
            "newsChecked": True, "thesisIntact": True}


# ── A: DNB vs NAS ──
FALL = 7.0
res = {t: scan(t, paalegg_fall(lag_df(**p), FALL, 18))
       for t, p in PROFILER.items()}
d, n = res["DNB.OL"], res["NAS.OL"]
krav("A", "DNB og NAS gir ikke samme Correction Score ved samme prosentfall",
     abs(d["correctionScore"] - n["correctionScore"]) >= 20
     and abs(d["correctionPercentile"] - n["correctionPercentile"]) >= 20,
     f"DNB max -{d['currentCorrection'].maxDrawdownPct:.1f} % → percentil "
     f"{d['correctionPercentile']:.0f}, score {d['correctionScore']:.0f}, {d['status']}\n    "
     f"NAS max -{n['currentCorrection'].maxDrawdownPct:.1f} % → percentil "
     f"{n['correctionPercentile']:.0f}, score {n['correctionScore']:.0f}, {n['status']}")

# ── B: ulik historisk volatilitet ──
medianer = {}
for t, r in res.items():
    dd = sorted(h.drawdownPct for h in r["historiskeKorreksjoner"])
    medianer[t] = round(dd[len(dd) // 2], 1) if dd else 0
krav("B", "Ulik historisk volatilitet gir ulik correction percentile",
     len(set(medianer.values())) == 3
     and len({r["correctionPercentile"] for r in res.values()}) >= 2,
     "median historisk fall: "
     + " · ".join(f"{k.replace('.OL','')} {v} %" for k, v in medianer.items())
     + "\n    percentiler: "
     + " · ".join(f"{k.replace('.OL','')} {r['correctionPercentile']:.0f}"
                  for k, r in res.items()))

# ── C: gammel swing-low langt under kursen ──
w = scan("WAWI.OL", fjern_support_serie())
sone = w["supportSone"]
krav("C", "Gammel swing-low langt under kurs gir ikke høy support relevance",
     w["supportScore"] == 0 or (sone and sone["avstandPct"] <= w["supportVindu"]),
     f"relevansvindu {w['supportVindu']:.1f} % · support {w['supportScore']:.0f} · "
     + (f"nærmeste relevante sone {sone['avstandPct']:.1f} % under kurs"
        if sone else "ingen sone innenfor vinduet, score satt til 0")
     + f"\n    (totalt {len(w['supportSoner'])} kandidatnivåer funnet, nærmeste "
     f"{w['supportSoner'][0]['avstandPct']:.1f} % unna)" if w["supportSoner"] else "")

# ── D: PROT — stor tidligere dybde bevares under recovery ──
ved_bunn, etter = dyp_så_recovery()
p_bunn = scan("PROT.OL", ved_bunn)
state = {"corrections": {}, "statuses": {}, "alerts": [], "varslet": {}}
S.evaluer_varsler([p_bunn], state)
p_etter = scan("PROT.OL", etter, state=state["corrections"]["PROT.OL"])
cc_b, cc_e = p_bunn["currentCorrection"], p_etter["currentCorrection"]
krav("D", "Stor tidligere peak→trough bevares som max correction under recovery",
     cc_e.maxDrawdownPct >= cc_b.maxDrawdownPct - 0.01
     and cc_e.currentDrawdownPct < cc_e.maxDrawdownPct
     and p_etter["severity"] == S.SEV_STRONG
     and p_etter["phase"] in (S.PHASE_BASE_BUILDING, S.PHASE_RECOVERING),
     f"ved bunn: max -{cc_b.maxDrawdownPct:.1f} % · severity {p_bunn['severity']} · "
     f"phase {p_bunn['phase']}\n    "
     f"etter recovery: max -{cc_e.maxDrawdownPct:.1f} % (uendret), nå "
     f"-{cc_e.currentDrawdownPct:.1f} % · severity {p_etter['severity']} · "
     f"phase {p_etter['phase']} → status {p_etter['status']}")

# ── E: aksje på ATH ──
a = scan("KOG.OL", ath_serie())
aktive = [k for k, v in a["recoveryDeler"].items() if v is True]
krav("E", "Aksje ved ATH får ikke Recovery Score uten aktiv korreksjon",
     a["recoveryScore"] == 0 and a["recoveryDeler"].get("ingenAktivKorreksjon"),
     f"kurs {a['ind']['close_now']:.1f}, drawdown "
     f"-{a['currentCorrection'].currentDrawdownPct:.1f} %, severity {a['severity']} "
     f"→ recovery {a['recoveryScore']:.0f}, ingen kriterier aktive {aktive}")

# ── F: stabilisering som feiler ──
f_fall, f_stab, f_nytt = stabilisering_som_feiler()
st_f = {"corrections": {}, "statuses": {}, "alerts": [], "varslet": {}}
forlop = []
for merke, d_ in [("fall", f_fall), ("stabilisering", f_stab), ("nytt lavpunkt", f_nytt)]:
    r_ = scan("NOD.OL", d_, state=st_f["corrections"].get("NOD.OL"))
    S.evaluer_varsler([r_], st_f)
    c_ = r_["currentCorrection"]
    forlop.append((merke, c_.correctionId, r_["phase"], r_["status"],
                   c_.maxDrawdownPct, c_.currentDrawdownPct))
ider = {x[1] for x in forlop}
krav("F", "Feilet stabilisering beholder correctionId og går tilbake til FALLING",
     len(ider) == 1 and forlop[-1][2] == S.PHASE_FALLING
     and forlop[-1][4] >= forlop[1][4],
     f"samme correctionId hele veien: {ider.pop()}\n    "
     + "\n    ".join(f"{m:14s} phase={p:14s} status={s:18s} max=-{mx:.1f} % nå=-{nu:.1f} %"
                     for m, _, p, s, mx, nu in forlop))

# ── G: event risk ──
g = scan("KIT.OL", endagsfall(lag_df(sigma=2.2, drift=0.04, seed=55), 13.0))
krav("G", "Svært raskt fall på stort volum går direkte til EVENT RISK",
     g["status"] == S.STATUS_EVENT_RISK and g["phase"] == S.PHASE_EVENT_RISK,
     f"1D {g['ind']['return1d']:.1f} % · ATR i går {g['ind']['atrPctPrev']:.2f} % · "
     f"volratio {g['ind']['volumeRatio20d']} → "
     f"{[k for k, v in g['eventGrunner'].items() if v]}\n    "
     f"Correction Score {g['correctionScore']:.0f} beregnes fortsatt, men status "
     f"er {g['status']}")

# ── H: kurs under SMA200 ──
h = scan("KIT.OL", paalegg_fall(lag_df(sigma=2.0, drift=-0.02, seed=99), 22.0, 60))
krav("H", "Kurs under SMA200 reduserer Trend Score, men fjerner ikke aksjen",
     h["ind"]["close_now"] < h["ind"]["sma200"] and h is not None
     and not h["trendDeler"]["closeOverSma200"],
     f"kurs {h['ind']['close_now']:.1f} < SMA200 {h['ind']['sma200']:.1f} → "
     f"trend {h['trendScore']:.0f} ({h['trendBand']}), status {h['status']} "
     f"— fortsatt i radaren")

# ── I: REVERSAL er streng ──
i_uten = scan("KOG.OL", etter)
i_med = scan("KOG.OL", etter, {"KOG.OL": GODKJENT})
i_hoy = p_bunn   # høy Correction Score, ingen recovery
krav("I", "Høy Correction Score alene gir aldri REVERSAL",
     i_hoy["correctionScore"] >= 75 and i_hoy["status"] != S.STATUS_REVERSAL,
     f"corr {i_hoy['correctionScore']:.0f}, recovery {i_hoy['recoveryScore']:.0f}, "
     f"trend {i_hoy['trendScore']:.0f} → {i_hoy['status']}")
krav("I", "REVERSAL krever recovery, trend, fundamental gate og case intakt",
     i_uten["status"] != S.STATUS_REVERSAL and i_med["status"] == S.STATUS_REVERSAL,
     f"samme kursbilde (recovery {i_med['recoveryScore']:.0f} ≥ 70, trend "
     f"{i_med['trendScore']:.0f} ≥ 55, event risk {i_med['eventRisk']}): "
     f"uten gate → {i_uten['status']} · med gate → {i_med['status']}")

# ── J: historisk integritet ──
krav("J", "maxDrawdownPct reduseres ikke når aksjen henter seg inn",
     cc_e.maxDrawdownPct >= cc_b.maxDrawdownPct - 0.01
     and cc_e.currentDrawdownPct < cc_b.currentDrawdownPct,
     f"max: -{cc_b.maxDrawdownPct:.1f} % → -{cc_e.maxDrawdownPct:.1f} % (uendret)\n    "
     f"nå:  -{cc_b.currentDrawdownPct:.1f} % → -{cc_e.currentDrawdownPct:.1f} % (endres)")

# ── K: support 0 uten relevant sone ──
cfg_k = copy.deepcopy(S.SCANNER_CONFIG)
cfg_k["support"]["atrMaxDistanceMultiplier"] = 0.01
cfg_k["support"]["percentCap"] = 0.01
k = scan("DNB.OL", paalegg_fall(lag_df(**PROFILER["DNB.OL"]), 7.0, 18), cfg=cfg_k)
krav("K", "Support Score er 0 når ingen relevant støtte finnes i vinduet",
     k["supportScore"] == 0.0 and k["supportSone"] is None,
     f"relevansvindu strammet til {k['supportVindu']:.2f} % → support "
     f"{k['supportScore']:.0f}, ingen sone valgt (mot {d['supportScore']:.0f} "
     f"med standard vindu {d['supportVindu']:.1f} %)")

# ── L: recovery-signaler fra aktiv correction ──
l_deler = p_etter["recoveryDeler"]
cc_l = p_etter["currentCorrection"]
hl, motstand = l_deler.get("higherLowPrice"), l_deler.get("localResistance")
krav("L", "Higher low og lokal motstand kommer fra den aktive korreksjonen",
     (hl is None or hl > cc_l.troughPrice)
     and (motstand is None or motstand > cc_l.troughPrice),
     f"korreksjonsbunn {cc_l.troughPrice:.2f}\n    "
     f"bekreftet higher low {hl} (over bunnen: {hl > cc_l.troughPrice if hl else 'n/a'})\n    "
     f"lokal motstand {motstand} (dannet etter bunnen: "
     f"{motstand > cc_l.troughPrice if motstand else 'n/a'})")

# ── M: ingen gjentatte varsler ──
st_m = {"corrections": {}, "statuses": {}, "alerts": [], "varslet": {}}
serie = [f_fall, f_stab, f_stab, f_nytt, f_stab, f_stab]
antall = []
for d_ in serie:
    r_ = scan("NOD.OL", d_, state=st_m["corrections"].get("NOD.OL"))
    antall.append(len(S.evaluer_varsler([r_], st_m)))
krav("M", "Samme correctionId gir ikke samme varsel om igjen",
     sum(antall) <= 3 and antall[2] == 0 and antall[-1] == 0,
     f"varsler per skanning gjennom STRONG→STABIL→STABIL→NYTT LAV→STABIL→STABIL: "
     f"{antall}\n    totalt {sum(antall)} varsler, ingen gjentakelser")

# ── Ekstra: labels og terskler er konsistente (§19/§21) ──
b = S.SCANNER_CONFIG["recovery"]
baand = dict((navn, grense) for grense, navn in S.RECOVERY_BANDS)
krav("§19", "Recovery-labels og terskler er identiske i config og visning",
     b["stabilizing"] == 30 and b["early"] == 50 and b["confirmed"] == 70
     and b["strong"] == 85 and baand["STABILIZING"] == b["stabilizing"]
     and baand["EARLY RECOVERY"] == b["early"]
     and baand["CONFIRMED RECOVERY"] == b["confirmed"]
     and baand["STRONG RECOVERY"] == b["strong"],
     f"config {b} · bånd {baand}")

krav("§22", "Ingen BUY/SELL-signaler noe sted",
     not any(o in t.upper() for t in S.STATUS_TEKST.values()
             for o in ("BUY", "SELL", "KJØP", "SELG")),
     "statuser: " + ", ".join(sorted(S.STATUS_PRIORITY)))

print("\n" + "=" * 64)
feil = [x for x in RES if not x[0]]
print(f"{len(RES) - len(feil)}/{len(RES)} akseptansekrav oppfylt")
for _, navn, _ in feil:
    print(f"  MANGLER: {navn}")
raise SystemExit(1 if feil else 0)
