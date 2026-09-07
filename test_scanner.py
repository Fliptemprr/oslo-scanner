"""
Akseptansetester for Correction Radar (§24 i spesifikasjonen).

Kjør:  python test_scanner.py

Testene bruker syntetiske kursserier med bevisst ulik volatilitet, fordi
poenget nettopp er at radaren skal behandle en rolig bank og et volatilt
flyselskap forskjellig. Ingen nettverkstilgang kreves.
"""

import copy
import numpy as np
import pandas as pd

import scanner as S


# ══════════════════════════════════════════════════════════════
# SYNTETISKE KURSSERIER
# ══════════════════════════════════════════════════════════════

# Omtrentlig daglig standardavvik, valgt for å ligne den faktiske forskjellen
# mellom en bank, en halvlederaksje og et flyselskap.
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


# ══════════════════════════════════════════════════════════════
# AKSEPTANSEKRAV §24
# ══════════════════════════════════════════════════════════════

RES = []


def krav(navn, ok, detalj=""):
    RES.append((ok, navn, detalj))
    print(f"{'✓' if ok else '✗'} {navn}" + (f"\n    {detalj}" if detalj else ""))


def scan(ticker, df, fund=None, cfg=None):
    return S.scan_stock(ticker, df, fund or {}, cfg or S.SCANNER_CONFIG)


# ── 1+2: DNB og NAS reagerer ikke likt på samme prosentfall ──
FALL = 7.0
r = {}
for t in ("DNB.OL", "NAS.OL", "NOD.OL"):
    df = paalegg_fall(lag_df(**PROFILER[t]), FALL, 18)
    r[t] = scan(t, df)

d, n = r["DNB.OL"], r["NAS.OL"]
krav("DNB og NAS reagerer IKKE likt på samme prosentfall",
     abs(d["correctionPercentile"] - n["correctionPercentile"]) >= 20
     and d["correctionScore"] != n["correctionScore"],
     f"DNB -{d['currentCorrection'].drawdownPct:.1f}% → percentil {d['correctionPercentile']:.0f}, "
     f"score {d['correctionScore']:.0f}, status {d['status']}\n    "
     f"NAS -{n['currentCorrection'].drawdownPct:.1f}% → percentil {n['correctionPercentile']:.0f}, "
     f"score {n['correctionScore']:.0f}, status {n['status']}")

medianer = {}
for t, res in r.items():
    dd = sorted(h.drawdownPct for h in res["historiskeKorreksjoner"])
    medianer[t] = round(dd[len(dd) // 2], 1) if dd else 0
krav("Historiske korreksjoner er forskjellige per ticker",
     len(set(medianer.values())) == 3,
     "median historisk fall: " + ", ".join(f"{k.replace('.OL','')} {v} %" for k, v in medianer.items()))

# ── 3: liten rebound oppretter ikke ny correctionId ──
# Banen fra spesifikasjonen, som ÉN sammenhengende serie som forlenges i tid:
# 190 → 175 → 180 → 165 → 170 → 155
bane_df = trendserie(start=120.0, seed=41)
bane_df = legg_til_ben(bane_df, 190 / float(bane_df["Close"].iloc[-1]), 40, seed=1)
ider, fall, dybder = [], [], []
for i, punkt in enumerate([175, 180, 165, 170, 155]):
    bane_df = legg_til_ben(bane_df, punkt / float(bane_df["Close"].iloc[-1]), 12, seed=10 + i)
    cc = scan("TEST.OL", bane_df)["currentCorrection"]
    ider.append(cc.id)
    fall.append(round(cc.drawdownPct, 1))
    dybder.append(round(cc.maxDepthPct, 1))
krav("En liten rebound oppretter IKKE ny correctionId",
     len(set(ider)) == 1 and dybder[-1] > dybder[0],
     f"190→175→180→165→170→155 gir én hendelse: {ider[0]}\n    "
     f"fall fra topp: {fall}\n    dybde topp→bunn: {dybder}")

# ── 4: kraftig endagsfall gir EVENT RISK ──
ev = scan("NOD.OL", endagsfall(lag_df(**PROFILER["NOD.OL"]), 14.0))
krav("Kraftig endagsfall gir EVENT RISK",
     ev["status"] == S.STATUS_EVENT_RISK,
     f"1D {ev['ind']['return1d']:.1f} %, volratio {ev['ind']['volumeRatio20d']}, "
     f"utløst av {[k for k, v in ev['eventGrunner'].items() if v]} → {ev['status']}")

rolig = scan("NOD.OL", paalegg_fall(lag_df(**PROFILER["NOD.OL"]), 14.0, 20))
krav("Samme fall fordelt over 20 dager gir IKKE event risk",
     not rolig["eventRisk"],
     f"-14 % over 20 dager → eventRisk={rolig['eventRisk']}, status {rolig['status']}")

# ── 5-7: REVERSAL-kjeden ──
# Rolig aksje i klar opptrend → -14 % korreksjon → rebound → higher low → brudd opp.
def bygg(fall=0.86, reb=1.06, opp=1.10, med_opptur=True):
    d = trendserie(n_pre=640, sigma=1.1, drift=0.16, seed=17, start=30.0)
    d = legg_til_ben(d, fall, 24, seed=2)
    if not med_opptur:
        return d
    d = legg_til_ben(d, reb, 9, seed=3)
    d = legg_til_ben(d, 0.972, 6, seed=4)
    d = legg_til_ben(d, opp, 14, seed=5)
    return d

GODKJENT = {"KOG.OL": {"reportChecked": True, "guidanceChecked": True,
                       "newsChecked": True, "thesisIntact": True}}

dyp = scan("KOG.OL", bygg(med_opptur=False))
krav("Høy Correction Score alene gir IKKE REVERSAL",
     dyp["correctionScore"] >= 75 and dyp["status"] != S.STATUS_REVERSAL,
     f"corr {dyp['correctionScore']:.0f} (høy), rec {dyp['recoveryScore']:.0f}, "
     f"trend {dyp['trendScore']:.0f} → {dyp['status']}")

gj_df = bygg()
uten = scan("KOG.OL", gj_df)
med = scan("KOG.OL", gj_df, GODKJENT)
krav("REVERSAL krever godkjent fundamental sjekk",
     uten["status"] != S.STATUS_REVERSAL and med["status"] == S.STATUS_REVERSAL,
     f"samme kursbilde (corr {med['correctionScore']:.0f}, rec {med['recoveryScore']:.0f}, "
     f"trend {med['trendScore']:.0f}): uten sjekk → {uten['status']} · med sjekk → {med['status']}")

# Bare 4 barer opp fra bunnen: ingen bekreftet higher low ennå.
tidlig = scan("KOG.OL", legg_til_ben(bygg(med_opptur=False), 1.03, 4, seed=8), GODKJENT)
krav("REVERSAL krever Recovery >= terskel selv med godkjent fundamental",
     tidlig["recoveryScore"] < S.SCANNER_CONFIG["recovery"]["confirmed"]
     and tidlig["status"] != S.STATUS_REVERSAL,
     f"corr {tidlig['correctionScore']:.0f} (høy nok), fundamental godkjent, "
     f"men rec {tidlig['recoveryScore']:.0f} < {S.SCANNER_CONFIG['recovery']['confirmed']} "
     f"→ {tidlig['status']}")

brutt = scan("KOG.OL", gj_df, {"KOG.OL": {"reportChecked": True, "guidanceChecked": True,
                                          "newsChecked": True, "thesisIntact": False}})
krav("Case ikke intakt blokkerer REVERSAL",
     brutt["status"] != S.STATUS_REVERSAL,
     f"tre sjekker gjort, men thesisIntact=False → {brutt['status']}")

# ── 8: kurs under SMA200 fjerner ikke aksjen ──
ned = scan("KIT.OL", paalegg_fall(lag_df(sigma=2.0, drift=-0.02, seed=99), 22.0, 60))
krav("Kurs under SMA200 fjerner IKKE aksjen",
     ned["ind"]["close_now"] < ned["ind"]["sma200"] and ned is not None,
     f"kurs {ned['ind']['close_now']:.1f} < SMA200 {ned['ind']['sma200']:.1f}, "
     f"trend {ned['trendScore']:.0f} ({ned['trendBand']}), status {ned['status']} "
     "— fortsatt med i radaren")

# ── 9: eksisterende RSI/SMA/volumdata fungerer fortsatt ──
i = r["DNB.OL"]["ind"]
krav("Eksisterende RSI/SMA/volumdata fungerer fortsatt",
     all(i[k] is not None for k in ("rsi", "sma20", "sma50", "sma200",
                                    "volumeRatio20d", "avgVolume20d", "high52w",
                                    "return6m", "drawdown52w", "atr")),
     f"RSI {i['rsi']:.1f} · SMA20 {i['sma20']:.1f} · SMA50 {i['sma50']:.1f} · "
     f"SMA200 {i['sma200']:.1f} · VolRatio {i['volumeRatio20d']} · 6M {i['return6m']:.1f} % · "
     f"52W drawdown {i['drawdown52w']:.1f} %")

# ── 10: ingen BUY/SELL ──
forbudt = {"BUY", "SELL", "STRONG BUY", "KJØP", "SELG"}
alle_tekster = set(S.STATUS_LABEL.values()) | set(S.STATUS_PRIORITY) | \
    {S.STATUS_WAIT, S.STATUS_FOLLOW, S.STATUS_REVERSAL}
lekkasje = [t for t in alle_tekster if any(o in t.upper() for o in forbudt)]
krav("Ingen BUY/SELL-signaler genereres", not lekkasje,
     "statuser: " + ", ".join(sorted(S.STATUS_PRIORITY)))

# ── 11: terskler kan endres i config ──
cfg2 = copy.deepcopy(S.SCANNER_CONFIG)
cfg2["correction"]["follow"] = 1
cfg2["correction"]["correction"] = 2
cfg2["correction"]["strong"] = 3
laav = scan("NAS.OL", paalegg_fall(lag_df(**PROFILER["NAS.OL"]), 3.0, 10))
laav2 = scan("NAS.OL", paalegg_fall(lag_df(**PROFILER["NAS.OL"]), 3.0, 10), cfg=cfg2)
krav("Alle sentrale terskler kan endres i config",
     laav["status"] != laav2["status"],
     f"samme aksje, score {laav['correctionScore']:.0f}: "
     f"standard config → {laav['status']} · endret config → {laav2['status']}")

cfg3 = copy.deepcopy(S.SCANNER_CONFIG)
cfg3["swingAtrMultiplier"] = 3.0
h1 = len(scan("NOD.OL", lag_df(**PROFILER["NOD.OL"]))["historiskeKorreksjoner"])
h2 = len(scan("NOD.OL", lag_df(**PROFILER["NOD.OL"]), cfg=cfg3)["historiskeKorreksjoner"])
krav("swingAtrMultiplier styrer swing-deteksjonen fra config",
     h1 != h2, f"multiplier 1.5 → {h1} korreksjoner · multiplier 3.0 → {h2}")

# ── 12: watchlist endres uten å endre motoren ──
uni = [S._universe_rad(t) for t in ["KOG", "NOD", "DNB"]]
uni.append(S._universe_rad("AAPL"))
uni[0]["enabled"] = False
aktive = [e["ticker"] for e in uni if e["enabled"]]
krav("Watchlist kan endres uten å endre scanner-motoren",
     aktive == ["NOD.OL", "DNB.OL", "AAPL"] and uni[0]["enabled"] is False,
     f"ADD/REMOVE/DISABLE via config: aktive = {aktive} (KOG deaktivert, AAPL lagt til)")

# ── Varselmotor: idempotent, ingen spam ──
state = {"statuses": {}, "alerts": []}
res_liste = [r["DNB.OL"]]
v1 = S.evaluer_varsler(res_liste, state)
v2 = S.evaluer_varsler(res_liste, state)
krav("Uendret status gir ikke nye varsler (ingen spam)",
     len(v2) == 0,
     f"første kjøring: {len(v1)} varsel · andre kjøring: {len(v2)}")


# ══════════════════════════════════════════════════════════════
# VARSELMOTOR §16 + §19
# ══════════════════════════════════════════════════════════════

def fake(ticker, status, corr_id, dd):
    class CC:
        drawdownPct = dd
        id = corr_id
    return {"ticker": ticker, "Ticker": ticker.replace(".OL", ""), "status": status,
            "correctionId": corr_id, "currentCorrection": CC(),
            "correctionScore": 80.0, "correctionPercentile": 89.0,
            "trendBand": "HEALTHY", "recoveryBand": "NO RECOVERY"}

state = {"statuses": {}, "alerts": []}
CID = "NOD.OL:2026-08-01"
forlop = [
    ("WAIT", CID, 2.0, 0, "WAIT varsler aldri"),
    ("FOLLOW", CID, 6.0, 0, "WAIT → FOLLOW står ikke i §19"),
    ("CORRECTION", CID, 10.0, 1, "FOLLOW → CORRECTION"),
    ("CORRECTION", CID, 10.4, 0, "uendret, lite dypere → ingen spam"),
    ("CORRECTION", CID, 14.0, 1, "samme status, severity økt 3,6 pp"),
    ("STRONG_CORRECTION", CID, 18.0, 1, "CORRECTION → STRONG CORRECTION"),
    ("STRONG_CORRECTION", CID, 18.1, 0, "uendret"),
    ("STABILIZING", CID, 12.0, 0, "STRONG → STABILIZING står ikke i §19"),
    ("REVERSAL", CID, 6.0, 1, "STABILIZING → REVERSAL"),
    ("EVENT_RISK", CID, 20.0, 1, "ANY → EVENT RISK"),
]
ok = True
print(f"{'status':20s} {'dd':>6s} {'varsler':>8s} {'ventet':>7s}  merknad")
for status, cid, dd, ventet, merknad in forlop:
    n = len(S.evaluer_varsler([fake("NOD.OL", status, cid, dd)], state))
    treff = n == ventet
    ok &= treff
    print(f"{'✓' if treff else '✗'} {status:18s} {dd:6.1f} {n:8d} {ventet:7d}  {merknad}")

print(f"\nVarsellogg: {len(state['alerts'])} oppføringer, alle med samme correctionId: "
      f"{len({a['correctionId'] for a in state['alerts']}) == 1}")
print("Eksempel på varseltekst:\n")
print(state["alerts"][-1]["tekst"])
print("\nIngen kjøps-/salgsord i varsler:",
      not any(o in a["tekst"].upper() for a in state["alerts"]
              for o in ("BUY", "SELL", "KJØP", "SELG")))
krav("Varselovergangene følger §19", ok,
     "WAIT varsler aldri · FOLLOW→CORRECTION · CORRECTION→STRONG · "
     "STABILIZING→REVERSAL · ANY→EVENT RISK · severity-økning innen samme correctionId")

print("\n" + "=" * 62)
feil = [x for x in RES if not x[0]]
print(f"{len(RES) - len(feil)}/{len(RES)} krav oppfylt")
for _, navn, _ in feil:
    print(f"  MANGLER: {navn}")
raise SystemExit(1 if feil else 0)
