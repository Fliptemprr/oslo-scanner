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
# HANDELSKALENDER OG DATAFRISKHET (P0)
# ══════════════════════════════════════════════════════════════

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

OSLO = ZoneInfo("Europe/Oslo")


def serie_til(sluttdato, n=760, seed=5):
    """Kursserie med siste bar på en gitt dato."""
    d = lag_df(n=n, sigma=1.5, drift=0.05, seed=seed, start=100.0)
    d.index = pd.bdate_range(end=pd.Timestamp(sluttdato), periods=len(d))
    return d


def uten_siste_bar(df):
    """Samme serie som mangler siste handelsdag — slik feilen faktisk arter seg."""
    return df.iloc[:-1]


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


# ── P0: siste avsluttede handelsdag ──
kalender = [
    ("tirsdag 08.09 kl 07:00, før åpning", datetime(2026, 9, 8, 7, 0, tzinfo=OSLO),
     date(2026, 9, 7)),
    ("tirsdag 08.09 kl 12:00, midt i sesjonen", datetime(2026, 9, 8, 12, 0, tzinfo=OSLO),
     date(2026, 9, 7)),
    ("tirsdag 08.09 kl 17:30, etter stengetid", datetime(2026, 9, 8, 17, 30, tzinfo=OSLO),
     date(2026, 9, 8)),
    ("lørdag 12.09", datetime(2026, 9, 12, 10, 0, tzinfo=OSLO), date(2026, 9, 11)),
    ("søndag 13.09", datetime(2026, 9, 13, 10, 0, tzinfo=OSLO), date(2026, 9, 11)),
    ("2. påskedag 06.04", datetime(2026, 4, 6, 8, 0, tzinfo=OSLO), date(2026, 4, 1)),
    ("1. januar", datetime(2026, 1, 1, 12, 0, tzinfo=OSLO), date(2025, 12, 30)),
]
feil_kal = [(n, S.siste_avsluttede_handelsdag(t), v) for n, t, v in kalender
            if S.siste_avsluttede_handelsdag(t) != v]
krav("P0", "Siste avsluttede handelsdag hopper over helg og børshelligdager",
     not feil_kal,
     "\n    ".join(f"{n:38s} → {S.siste_avsluttede_handelsdag(t)}"
                    for n, t, _ in kalender))

# Det konkrete tilfellet: skanning tirsdag morgen med data kun t.o.m. fredag
naa = datetime(2026, 9, 8, 7, 0, tzinfo=OSLO)
ferskt = {"KOG.OL": serie_til(date(2026, 9, 7)), "KIT.OL": serie_til(date(2026, 9, 7), seed=6)}
gammelt = {t: uten_siste_bar(df) for t, df in ferskt.items()}
st_gammel = S.datastatus(gammelt, naa)
st_fersk = S.datastatus(ferskt, naa)
krav("P0", "Data som mangler siste handelsdag flagges som STALE",
     st_gammel["stale"] and not st_fersk["stale"]
     and st_gammel["handelsdagerBak"] == 1
     and set(st_gammel["etterslep"]) == {"KOG.OL", "KIT.OL"},
     f"skanning tirsdag 08.09 kl 07:00 → forventet handelsdag "
     f"{st_gammel['forventet']}\n    "
     f"data t.o.m. 04.09 → stale={st_gammel['stale']}, "
     f"{st_gammel['handelsdagerBak']} handelsdag bak, mangler "
     f"{sorted(t.replace('.OL','') for t in st_gammel['etterslep'])}\n    "
     f"data t.o.m. 07.09 → stale={st_fersk['stale']}")

# Uferdig candle skal forkastes før noe beregnes
i_sesjon = datetime(2026, 9, 8, 12, 0, tzinfo=OSLO)
med_uferdig = {"KOG.OL": serie_til(date(2026, 9, 8))}
renset = S.rens_prisdata(med_uferdig, i_sesjon)
krav("P0", "Uferdig candle forkastes før scores beregnes",
     S._bar_dato(renset["KOG.OL"]) == date(2026, 9, 7)
     and len(renset["KOG.OL"]) == len(med_uferdig["KOG.OL"]) - 1
     and not S.datastatus(renset, i_sesjon)["stale"],
     f"skanning midt i sesjonen 08.09 kl 12:00: siste bar 08.09 (uferdig) "
     f"→ forkastet, beregnes nå på {S._bar_dato(renset['KOG.OL'])}")

# Etter stengetid skal dagens ferdige bar beholdes
etter_close = datetime(2026, 9, 8, 17, 30, tzinfo=OSLO)
beholdt = S.rens_prisdata({"KOG.OL": serie_til(date(2026, 9, 8))}, etter_close)
krav("P0", "Dagens bar beholdes når sesjonen er avsluttet",
     S._bar_dato(beholdt["KOG.OL"]) == date(2026, 9, 8)
     and not S.datastatus(beholdt, etter_close)["stale"],
     f"skanning 08.09 kl 17:30, etter stengetid 16:20 + 40 min margin "
     f"→ beholder {S._bar_dato(beholdt['KOG.OL'])}")

# Fallback: kort period-forespørsel som fletter inn manglende dag
class FalskSesjon:
    """Simulerer at den korte forespørselen svarer med ferske data."""
    def __init__(self, fasit): self.fasit = fasit


_fasit = ferskt


def _falsk_hent(ticker, session, dager=10):
    return _fasit[ticker].tail(dager)


_ekte_hent = S._hent_siste_dager
S._hent_siste_dager = _falsk_hent
try:
    kopi = {t: df.copy() for t, df in gammelt.items()}
    for_kurs = {t: float(df["Close"].iloc[-1]) for t, df in kopi.items()}
    toppet, fikset = S.topp_opp_siste_dager(kopi, date(2026, 9, 7), None)
    st_topp = S.datastatus(toppet, naa)
    # Historikken skal være urørt: bare nye rader lagt til
    urort = all(
        toppet[t].iloc[:-1]["Close"].round(6).equals(gammelt[t]["Close"].round(6))
        for t in toppet)
finally:
    S._hent_siste_dager = _ekte_hent

krav("P0", "Fallback henter manglende handelsdag og fletter den inn",
     not st_topp["stale"] and set(fikset) == {"KOG.OL", "KIT.OL"} and urort,
     f"før: siste data {st_gammel['faktisk']}, stale={st_gammel['stale']}\n    "
     f"etter fallback: siste data {st_topp['faktisk']}, stale={st_topp['stale']}, "
     f"toppet opp {sorted(t.replace('.OL','') for t in fikset)}\n    "
     f"eksisterende rader urørt: {urort} (kun nyere barer legges til, så "
     f"justeringsgrunnlaget i historikken blandes ikke)")

# Fallback skal ikke gjøre noe når dataene allerede er ferske
S._hent_siste_dager = lambda *a, **k: (_ for _ in ()).throw(
    AssertionError("skulle ikke kalles"))
try:
    _, ingen = S.topp_opp_siste_dager({t: df.copy() for t, df in ferskt.items()},
                                      date(2026, 9, 7), None)
finally:
    S._hent_siste_dager = _ekte_hent
krav("P0", "Fallback kalles ikke når dataene allerede er ferske",
     ingen == [],
     "ingen ekstra Yahoo-forespørsler når siste handelsdag allerede er på plass")

# ── Stooq som andrekilde ──
def stooq_csv(df, faktor=1.0):
    """Lag Stooq-lignende CSV fra en serie, eventuelt på et annet prisnivå."""
    linjer = ["Date,Open,High,Low,Close,Volume"]
    for ts, rad in df.iterrows():
        linjer.append(
            f"{ts.strftime('%Y-%m-%d')},{rad['Open'] * faktor:.4f},"
            f"{rad['High'] * faktor:.4f},{rad['Low'] * faktor:.4f},"
            f"{rad['Close'] * faktor:.4f},{int(rad['Volume'])}")
    return "\n".join(linjer) + "\n"


parset = S.parse_stooq_csv(stooq_csv(ferskt["KOG.OL"].tail(40)))
krav("P0", "Stooq-CSV tolkes til riktig form",
     parset is not None and list(parset.columns) == ["Open", "High", "Low", "Close", "Volume"]
     and S._bar_dato(parset) == date(2026, 9, 7) and len(parset) == 40,
     f"40 rader, siste bar {S._bar_dato(parset)}, kolonner {list(parset.columns)}")

krav("P0", "Feilsvar fra Stooq gir None, ikke en ødelagt serie",
     all(S.parse_stooq_csv(t) is None for t in [
         "", "Exceeded the daily hits limit", "<html><body>404</body></html>",
         "Date,Foo\n2026-09-07,1"]),
     "tom tekst, rate limit-melding, HTML-feilside og feil kolonner avvises alle")

krav("P0", "Stooq-symboler mappes riktig",
     S.stooq_symbol("KOG.OL") == "kog.ol" and S.stooq_symbol("AAPL") == "aapl.us"
     and S.stooq_symbol("EQNR.OL") == "eqnr.ol",
     "KOG.OL → kog.ol · EQNR.OL → eqnr.ol · AAPL → aapl.us")

# Skalering: Stooq på et annet prisnivå enn Yahoo (ujustert vs utbyttejustert)
UJUSTERT = 1.08
stooq_data = S.parse_stooq_csv(stooq_csv(ferskt["KOG.OL"].tail(40), UJUSTERT))
basis = gammelt["KOG.OL"]
flettet, grunn = S.flett_inn_kilde(basis, stooq_data)
ny_bar = float(flettet["Close"].iloc[-1])
fasit = float(ferskt["KOG.OL"]["Close"].iloc[-1])
krav("P0", "Nye barer skaleres til basisseriens nivå før innfletting",
     grunn is None and S._bar_dato(flettet) == date(2026, 9, 7)
     and abs(ny_bar - fasit) < 0.01
     and float(flettet["Close"].iloc[-2]) == float(basis["Close"].iloc[-1]),
     f"Stooq lå {(UJUSTERT - 1) * 100:.0f} % over Yahoo-nivået\n    "
     f"skaleringsfaktor {flettet.attrs['skalering']} → innflettet kurs "
     f"{ny_bar:.2f} mot fasit {fasit:.2f}\n    "
     f"uten skalering ville siste bar hoppet {(UJUSTERT - 1) * 100:.0f} % og "
     f"forgiftet % i dag, RSI, ATR og drawdown")

# For stort avvik skal avvises, ikke flettes
feil_data = S.parse_stooq_csv(stooq_csv(ferskt["KOG.OL"].tail(40), 1.9))
uendret, grunn2 = S.flett_inn_kilde(basis, feil_data)
krav("P0", "Urimelig skaleringsfaktor avvises i stedet for å flettes inn",
     grunn2 is not None and S._bar_dato(uendret) == S._bar_dato(basis),
     f"faktor 1.9 → «{grunn2}», serien står urørt på {S._bar_dato(uendret)}")

# Ingen overlapp = ingen felles anker = ingen fletting
ingen_overlapp = S.parse_stooq_csv(stooq_csv(
    serie_til(date(2020, 1, 10), n=30, seed=9)))
_, grunn3 = S.flett_inn_kilde(basis, ingen_overlapp)
krav("P0", "Uten overlappende datoer flettes ingenting inn",
     grunn3 == "ingen overlappende datoer",
     "skalering krever en felles dato å ankre mot, ellers er nivåene ukjente")

# Hele kjeden: Yahoo mangler, Stooq redder. Flagget er av i produksjon
# (stooq.com svarer med et JS-challenge), så testen slår det på eksplisitt.
CFG_STOOQ = copy.deepcopy(S.SCANNER_CONFIG)
CFG_STOOQ["data"]["stooqEnabled"] = True
# Patcher på HTTP-nivå, slik at CSV-tolkning og skalering også testes
_ekte_url = S._hent_url


def _falsk_url(url, session, timeout):
    sym = url.split("s=")[1].split("&")[0]          # kog.ol
    tick = sym.upper()                               # KOG.OL
    return stooq_csv(ferskt[tick].tail(40), UJUSTERT)


S._hent_url = _falsk_url
try:
    kjede = {t: df.copy() for t, df in gammelt.items()}
    diag = {}
    kjede, fra_stooq = S.topp_opp_fra_stooq(kjede, date(2026, 9, 7), None, diag,
                                            CFG_STOOQ)
    st_kjede = S.datastatus(kjede, naa)
finally:
    S._hent_url = _ekte_url
krav("P0", "Stooq dekker inn når Yahoo ikke leverer siste handelsdag",
     not st_kjede["stale"] and set(fra_stooq) == {"KOG.OL", "KIT.OL"},
     f"Yahoo t.o.m. 04.09 → Stooq toppet opp "
     f"{sorted(t.replace('.OL','') for t in fra_stooq)} → "
     f"siste data {st_kjede['faktisk']}, stale={st_kjede['stale']}\n    "
     f"diagnose: {diag['KOG.OL']['stooq']}")


# ── Yahoo-hull: dagen finnes hos kilden, men som null-bar ──
# 07.09.2026 leverte Yahoo mandagen som null-bar for HELE Oslo Børs, ikke bare
# watchlisten. yfinance sin dropna fjerner raden. Siden serien samtidig hadde
# tirsdagens uferdige bar, så «siste bar < forventet»-sjekken en fersk serie,
# og hele fallback-kjeden slo aldri til. Hullet lå BAK en nyere bar.

def uten_dag(df, dag):
    """Fjern én dag midt i serien — slik Yahoos null-bar faktisk arter seg."""
    return df[df.index.date != dag]


hull = uten_dag(serie_til(date(2026, 9, 8)), date(2026, 9, 7))
komplett = serie_til(date(2026, 9, 7))
krav("P0", "Hull bak en uferdig bar oppdages, ikke bare etterslep på slutten",
     S.manglende_handelsdager(hull, date(2026, 9, 7)) == [date(2026, 9, 7)]
     and S.manglende_handelsdager(komplett, date(2026, 9, 7)) == []
     and S._bar_dato(hull) == date(2026, 9, 8),
     f"serie t.o.m. {S._bar_dato(hull)} (uferdig tirsdagsbar) med mandag 07.09 borte\n    "
     f"siste bar er nyere enn forventet dag, så etterslepssjekken ser ingenting\n    "
     f"hullsjekken finner {S.manglende_handelsdager(hull, date(2026, 9, 7))}")

# Aggregering av intradag til dagsbar. Feeden gir epoch i UTC mens børsen står
# i Oslo-tid, og Streamlit Cloud kjører i UTC — konverteringen må være eksplisitt.
def epoch(m, d, t, mi):
    return int(datetime(2026, m, d, t, mi, tzinfo=OSLO).timestamp())


ts_intra = [epoch(9, 7, 9, 0), epoch(9, 7, 12, 0), epoch(9, 7, 16, 15),
            epoch(9, 8, 9, 0)]
kvote_intra = {
    "open":   [308.4, 305.0, 299.0, 298.1],
    "high":   [309.5, 311.6, 300.0, 302.5],
    "low":    [307.0, 299.5, 298.9, 295.8],
    "close":  [309.0, 300.0, 298.9, 298.6],
    "volume": [100, 200, 300, 400],
}
agg = S.aggreger_intradag(ts_intra, kvote_intra, "Europe/Oslo")
man = agg.get(date(2026, 9, 7), {})
krav("P0", "Intradag-barer aggregeres til korrekt dagsbar i børsens tidssone",
     man.get("Open") == 308.4 and man.get("High") == 311.6
     and man.get("Low") == 298.9 and man.get("Close") == 298.9
     and man.get("Volume") == 600 and date(2026, 9, 8) in agg,
     f"3 barer 07.09 → O {man.get('Open')} H {man.get('High')} "
     f"L {man.get('Low')} C {man.get('Close')} V {man.get('Volume')}\n    "
     f"open fra første bar, high/low som maks/min, close fra siste, volum summert")

# Null-verdier finnes også i intradag-serien og skal hoppes over
agg_hull = S.aggreger_intradag(
    [epoch(9, 7, 9, 0), epoch(9, 7, 12, 0)],
    {"open": [None, 305.0], "high": [None, 311.6], "low": [None, 299.5],
     "close": [None, 300.0], "volume": [None, 200]}, "Europe/Oslo")
krav("P0", "Null-barer i intradag-serien forkastes i stedet for å bli null",
     agg_hull[date(2026, 9, 7)]["Open"] == 305.0
     and agg_hull[date(2026, 9, 7)]["Volume"] == 200,
     "en null-bar først i dagen ville ellers gitt Open=0 og forgiftet hele baren")

# Sluttauksjonen 16:20-16:25 ligger ikke i den kontinuerlige intradag-feeden,
# så aggregatet bommer litt på close. meta.chartPreviousClose har den
# offisielle kursen, men er relativ til chartens REKKEVIDDE, ikke til siste
# sesjon: fra range=1mo pekte den en måned tilbake og ga KIT 88.90 mot
# riktige 98.40. Datoen må derfor utledes av svarets egen sesjon.
svar_1d = {"timestamp": [epoch(9, 8, 9, 0)],
           "meta": {"exchangeTimezoneName": "Europe/Oslo",
                    "chartPreviousClose": 298.0}}
krav("P0", "Offisiell sluttkurs bindes til sesjonen før svarets egen sesjon",
     S.offisiell_close(svar_1d, date(2026, 9, 7), "Europe/Oslo") == 298.0
     and S.offisiell_close(svar_1d, date(2026, 9, 4), "Europe/Oslo") is None
     and S.offisiell_close(svar_1d, date(2026, 9, 8), "Europe/Oslo") is None
     and S.offisiell_close(None, date(2026, 9, 7), "Europe/Oslo") is None,
     "svaret gjelder sesjonen 08.09 → chartPreviousClose tilhører 07.09\n    "
     "intradag-aggregatet ga KOG 298.90, offisiell close 298.00")

# Innfletting midt i serien skal ikke røre eksisterende rader
bar_inn = {"Open": 308.4, "High": 311.6, "Low": 298.9, "Close": 298.0,
           "Volume": 609494}
fylt = S.flett_inn_dagsbar(hull, date(2026, 9, 7), bar_inn)
krav("P0", "Rekonstruert dagsbar settes inn på riktig plass, historikken urørt",
     len(fylt) == len(hull) + 1
     and float(fylt.loc[pd.Timestamp(date(2026, 9, 7)), "Close"]) == 298.0
     and fylt.index.is_monotonic_increasing
     and fylt.drop(index=pd.Timestamp(date(2026, 9, 7)))["Close"].round(6).equals(
         hull["Close"].round(6)),
     f"{len(hull)} → {len(fylt)} rader, mandagen inn mellom fredag og tirsdag, "
     f"alle andre rader identiske")

# Hele kjeden: Yahoo mangler dagen på dagsoppløsning, intradag redder den
def falsk_intradag_svar(df, dager, tz="Europe/Oslo"):
    """Yahoo chart-svar med intradag-barer som aggregerer til dagsbarene."""
    ts, o, h, l, c, v = [], [], [], [], [], []
    n = 12                                   # over backfillMinBars
    for dag in dager:
        rad = df.loc[pd.Timestamp(dag)]
        for k in range(n):
            # Siste bar bringer dagens high, low og close. De andre ligger på
            # openkursen, slik at maks/min/siste gir nøyaktig dagsbaren igjen.
            if k == n - 1:
                aapne, hoy = rad["Open"], rad["High"]
                lav, lukk = rad["Low"], rad["Close"]
            else:
                aapne = hoy = lav = lukk = rad["Open"]
            ts.append(int(datetime(dag.year, dag.month, dag.day,
                                   9 + (k * 7) // n, (k * 37) % 60,
                                   tzinfo=OSLO).timestamp()))
            o.append(float(aapne))
            h.append(float(hoy))
            l.append(float(lav))
            c.append(float(lukk))
            v.append(float(rad["Volume"]) / n)
    return {"timestamp": ts,
            "indicators": {"quote": [{"open": o, "high": h, "low": l,
                                      "close": c, "volume": v}]},
            "meta": {"exchangeTimezoneName": tz, "chartPreviousClose": None}}


_fasit_hull = {"KOG.OL": serie_til(date(2026, 9, 8)),
               "KIT.OL": serie_til(date(2026, 9, 8), seed=6)}
# Offisiell close settes bevisst 0.3 % over intradag-aggregatets close, slik
# at testen ser HVILKEN av de to som faktisk havner i serien.
_OFFISIELL = {t: float(df.loc[pd.Timestamp(date(2026, 9, 7)), "Close"]) * 1.003
              for t, df in _fasit_hull.items()}
_ekte_intradag = S._hent_intradag
_ekte_dagsmeta = S._hent_dagsmeta
S._hent_intradag = lambda t, session, cfg=S.SCANNER_CONFIG: falsk_intradag_svar(
    _fasit_hull[t], [date(2026, 9, 4), date(2026, 9, 7), date(2026, 9, 8)])
S._hent_dagsmeta = lambda t, session, cfg=S.SCANNER_CONFIG: {
    "timestamp": [epoch(9, 8, 9, 0)],
    "meta": {"exchangeTimezoneName": "Europe/Oslo",
             "chartPreviousClose": _OFFISIELL[t]}}
try:
    med_hull = {t: uten_dag(df, date(2026, 9, 7)) for t, df in _fasit_hull.items()}
    diag_bf = {}
    fylt_alle, bf_fikset = S.backfill_manglende_dager(
        {t: df.copy() for t, df in med_hull.items()}, date(2026, 9, 7), None, diag_bf)
    renset_bf = S.rens_prisdata(fylt_alle, i_sesjon)
    st_bf = S.datastatus(renset_bf, i_sesjon)
    st_uten = S.datastatus(S.rens_prisdata(med_hull, i_sesjon), i_sesjon)
finally:
    S._hent_intradag = _ekte_intradag
    S._hent_dagsmeta = _ekte_dagsmeta

brukt_close = {t: float(df.loc[pd.Timestamp(date(2026, 9, 7)), "Close"])
               for t, df in fylt_alle.items()}
krav("P0", "Intradag-backfill tetter hullet Yahoo etterlater på dagsoppløsning",
     set(bf_fikset) == {"KOG.OL", "KIT.OL"} and not st_bf["stale"]
     and st_uten["stale"] and st_uten["handelsdagerBak"] == 1
     and all(S.manglende_handelsdager(df, date(2026, 9, 7)) == []
             for df in fylt_alle.values())
     and all(abs(brukt_close[t] - _OFFISIELL[t]) < 1e-6 for t in brukt_close),
     f"uten backfill: siste data {st_uten['faktisk']}, stale={st_uten['stale']} "
     f"(mandagen borte, tirsdagen forkastet som uferdig)\n    "
     f"med backfill: siste data {st_bf['faktisk']}, stale={st_bf['stale']}, "
     f"tettet {sorted(t.replace('.OL', '') for t in bf_fikset)}\n    "
     f"kilde merket {renset_bf['KOG.OL'].attrs.get('sisteKilde')}\n    "
     f"close hentet fra offisiell sluttkurs, ikke fra aggregatet: "
     f"{brukt_close['KOG.OL']:.4f}")


# Scores skal faktisk endre seg når siste dag kommer inn
r_gammel = scan("KOG.OL", gammelt["KOG.OL"])
r_fersk = scan("KOG.OL", ferskt["KOG.OL"])
endret = [n for n, a, b in [
    ("kurs", r_gammel["ind"]["close_now"], r_fersk["ind"]["close_now"]),
    ("RSI14", r_gammel["ind"]["rsi"], r_fersk["ind"]["rsi"]),
    ("SMA20", r_gammel["ind"]["sma20"], r_fersk["ind"]["sma20"]),
    ("SMA50", r_gammel["ind"]["sma50"], r_fersk["ind"]["sma50"]),
    ("ATR14", r_gammel["ind"]["atr"], r_fersk["ind"]["atr"]),
    ("Vol Ratio", r_gammel["ind"]["volumeRatio20d"], r_fersk["ind"]["volumeRatio20d"]),
    ("Correction Score", r_gammel["correctionScore"], r_fersk["correctionScore"]),
    ("Trend Score", r_gammel["trendScore"], r_fersk["trendScore"]),
] if a != b]
krav("P0", "Manglende handelsdag påvirker mer enn KURS og % I DAG",
     len(endret) >= 5 and "kurs" in endret and "RSI14" in endret,
     f"én manglende candle endrer: {', '.join(endret)}\n    "
     "derfor er STALE et blokkerende varsel, ikke en fotnote")


# ── Corporate actions: fisjon skal ikke telle som kursfall ──
# Yahoo justerer for utbytte og splitt, men IKKE for fisjon. KOG falt
# 398.50 → 328.38 ved åpning 15.04.2026, med 15.04 sin high under 14.04 sin
# low: hele bevegelsen lå mellom to sesjoner. Utskillelsen av Kongsberg
# Maritime ble dermed lest som et markedsfall på 16 %, og forurenset topp,
# drawdown, percentil, ATR, SMA200 og Trend Score.

CA_EX = "2026-04-15"
CA_FAKTOR = 0.8
CA_CFG = copy.deepcopy(S.SCANNER_CONFIG)
CA_CFG["corporateActions"] = {
    "TEST.OL": [{"exDato": CA_EX, "faktor": CA_FAKTOR, "note": "syntetisk fisjon"}]
}

ca_raa = serie_til(date(2026, 9, 7), n=400)
ca_just = S.juster_corporate_actions({"TEST.OL": ca_raa.copy()}, CA_CFG)["TEST.OL"]
ca_for = ca_just.index < pd.Timestamp(CA_EX)

krav("P0", "Corporate action skalerer historikken, men ikke dagens kurs",
     bool((ca_just.loc[ca_for, "Close"] / ca_raa.loc[ca_for, "Close"]
           ).round(9).eq(CA_FAKTOR).all())
     and ca_just.loc[~ca_for, "Close"].round(9).equals(
         ca_raa.loc[~ca_for, "Close"].round(9))
     and ca_just["Volume"].equals(ca_raa["Volume"])
     and float(ca_just["Close"].iloc[-1]) == float(ca_raa["Close"].iloc[-1]),
     f"{int(ca_for.sum())} barer før {CA_EX} skalert med {CA_FAKTOR}, "
     f"{int((~ca_for).sum())} barer fra ex-dato urørt\n    "
     f"siste kurs {float(ca_just['Close'].iloc[-1]):.2f} = faktisk markedskurs\n    "
     f"volum urørt: ved fisjon endres ikke antall aksjer i selskapet")

# Det kunstige fallet skal forsvinne helt
ca_flat = pd.DataFrame(
    {"Open": [100.0, 100.0, 80.0, 80.0], "High": [100.0, 100.0, 80.0, 80.0],
     "Low": [100.0, 100.0, 80.0, 80.0], "Close": [100.0, 100.0, 80.0, 80.0],
     "Volume": [1000, 1000, 1000, 1000]},
    index=pd.to_datetime(["2026-04-13", "2026-04-14", "2026-04-15", "2026-04-16"]))
ca_flat_j = S.juster_corporate_actions({"TEST.OL": ca_flat}, CA_CFG)["TEST.OL"]
fall_for = (ca_flat["Close"].pct_change().iloc[2]) * 100
fall_etter = (ca_flat_j["Close"].pct_change().iloc[2]) * 100
krav("P0", "Det kunstige fisjonsfallet forsvinner fra kursutviklingen",
     abs(fall_for + 20.0) < 1e-9 and abs(fall_etter) < 1e-9,
     f"flat serie 100 → 80 over ex-dato: {fall_for:.1f} % før justering, "
     f"{fall_etter:.1f} % etter\n    "
     f"et reelt markedsfall samme dag ville overlevd, siden faktoren kun "
     f"flytter nivået på historikken")

# Flere hendelser skal komponeres, ikke overskrive hverandre
CA_TO = copy.deepcopy(S.SCANNER_CONFIG)
CA_TO["corporateActions"] = {"TEST.OL": [
    {"exDato": "2025-06-02", "faktor": 0.5, "note": "eldst"},
    {"exDato": CA_EX, "faktor": CA_FAKTOR, "note": "nyest"}]}
ca_to = S.juster_corporate_actions({"TEST.OL": ca_raa.copy()}, CA_TO)["TEST.OL"]
tidlig = ca_to.index < pd.Timestamp("2025-06-02")
mellom = (ca_to.index >= pd.Timestamp("2025-06-02")) & (ca_to.index < pd.Timestamp(CA_EX))
krav("P0", "Flere corporate actions komponeres i stedet for å overskrive",
     bool((ca_to.loc[tidlig, "Close"] / ca_raa.loc[tidlig, "Close"]
           ).round(9).eq(0.5 * CA_FAKTOR).all())
     and bool((ca_to.loc[mellom, "Close"] / ca_raa.loc[mellom, "Close"]
               ).round(9).eq(CA_FAKTOR).all()),
     f"før begge: faktor {0.5 * CA_FAKTOR:.2f} · mellom dem: {CA_FAKTOR} · "
     f"etter siste: 1.0")

# Ticker uten hendelser skal ikke røres
ca_urort = S.juster_corporate_actions({"ANNEN.OL": ca_raa.copy()}, CA_CFG)["ANNEN.OL"]
krav("P0", "Tickere uten registrerte corporate actions står urørt",
     ca_urort["Close"].equals(ca_raa["Close"]),
     "tabellen er eksplisitt — automatisk gap-deteksjon er bevisst valgt bort, "
     "fordi\n    watchlisten har 15 andre gap uten overlapp som er ekte "
     "resultatreaksjoner")

# Hele veien: forurenset historikk gir oppblåst topp, justering gir basis tilbake
# Toppen må ligge FØR ex-datoen, slik den gjør for KOG: topp 09.04, fisjon
# 15.04, og hele fallet deretter måles fra en topp som inneholdt KMAR.
ca_basis = paalegg_fall(lag_df(n=600, sigma=1.5, drift=0.05, seed=7), 20, 100)
ca_basis.index = pd.bdate_range(end=pd.Timestamp("2026-09-07"), periods=600)
CA_EX2 = ca_basis.index[-95]
ca_skitten = ca_basis.copy()
_m = ca_skitten.index < CA_EX2
for _k in ("Open", "High", "Low", "Close"):
    ca_skitten.loc[_m, _k] = ca_skitten.loc[_m, _k] / CA_FAKTOR   # fisjonen «ujustert»

CA_CFG2 = copy.deepcopy(S.SCANNER_CONFIG)
CA_CFG2["corporateActions"] = {
    "TEST.OL": [{"exDato": CA_EX2.strftime("%Y-%m-%d"), "faktor": CA_FAKTOR,
                 "note": "syntetisk fisjon"}]}
ca_renset = S.juster_corporate_actions({"TEST.OL": ca_skitten.copy()},
                                       CA_CFG2)["TEST.OL"]

r_basis = scan("TEST.OL", ca_basis)
r_skitten = scan("TEST.OL", ca_skitten)
r_renset = scan("TEST.OL", ca_renset)
krav("P0", "Ujustert fisjon blåser opp topp og Trend, justering gir basis tilbake",
     r_skitten["currentCorrection"].peakPrice > r_basis["currentCorrection"].peakPrice
     and r_skitten["currentCorrection"].maxDrawdownPct
         > r_basis["currentCorrection"].maxDrawdownPct
     and r_skitten["trendScore"] < r_basis["trendScore"]
     and abs(r_renset["currentCorrection"].peakPrice
             - r_basis["currentCorrection"].peakPrice) < 1e-6
     and abs(r_renset["currentCorrection"].maxDrawdownPct
             - r_basis["currentCorrection"].maxDrawdownPct) < 1e-6
     and r_renset["trendScore"] == r_basis["trendScore"]
     and r_renset["correctionPercentile"] == r_basis["correctionPercentile"],
     f"forurenset: topp {r_skitten['currentCorrection'].peakPrice:.2f} · "
     f"max {r_skitten['currentCorrection'].maxDrawdownPct:.1f} % · "
     f"trend {r_skitten['trendScore']:.0f}\n    "
     f"justert:    topp {r_renset['currentCorrection'].peakPrice:.2f} · "
     f"max {r_renset['currentCorrection'].maxDrawdownPct:.1f} % · "
     f"trend {r_renset['trendScore']:.0f}\n    "
     f"basis:      topp {r_basis['currentCorrection'].peakPrice:.2f} · "
     f"max {r_basis['currentCorrection'].maxDrawdownPct:.1f} % · "
     f"trend {r_basis['trendScore']:.0f}  (justeringen inverterer forurensningen)")


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
