"""
Oslo Børs Swing Trading Scanner – v2
=====================================
Swing trading scanner med forbedret scoring, bedre filtrering,
og tydeligere setup-klassifisering.

Kjør:  streamlit run scanner.py
"""

import streamlit as st
from streamlit_autorefresh import st_autorefresh
import pandas as pd
import yfinance as yf
import json
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

# ──────────────────────────────────────────────────────────────
# KONFIGURASJON
# ──────────────────────────────────────────────────────────────

WATCHLIST_FILE = Path("watchlist.json")

# [FIX #5] Default likviditet økt — 50k var altfor lavt for reell trading
DEFAULT_MIN_AVG_VOLUME = 200_000

BATCH_SIZE = 20
BATCH_DELAY = 5

# ──────────────────────────────────────────────────────────────
# OSLO BØRS TICKER-LISTE (verifisert april 2026)
# ──────────────────────────────────────────────────────────────
OSLO_TICKERS = {
    # ── OBX / Store selskaper ──
    "EQNR.OL": "Equinor",
    "DNB.OL": "DNB Bank",
    "KOG.OL": "Kongsberg Gruppen",
    "TEL.OL": "Telenor",
    "AKRBP.OL": "Aker BP",
    "NHY.OL": "Norsk Hydro",
    "YAR.OL": "Yara International",
    "GJF.OL": "Gjensidige Forsikring",
    "MOWI.OL": "Mowi",
    "ORK.OL": "Orkla",
    "VAR.OL": "Vår Energi",
    "AKER.OL": "Aker",
    "SALM.OL": "SalMar",
    "SUBC.OL": "Subsea 7",
    "STB.OL": "Storebrand",
    "FRO.OL": "Frontline",
    "WAWI.OL": "Wallenius Wilhelmsen",
    "PROT.OL": "Protector Forsikring",
    "CMBTO.OL": "CMB.Tech (ex Golden Ocean)",
    "AUTO.OL": "AutoStore Holdings",
    "TOM.OL": "Tomra Systems",
    "HAFNI.OL": "Hafnia",
    "NOD.OL": "Nordic Semiconductor",
    "DOFG.OL": "DOF Group",
    "WWI.OL": "Wilh. Wilhelmsen A",
    "WWIB.OL": "Wilh. Wilhelmsen B",
    "MING.OL": "SpareBank 1 SMN",
    "LSG.OL": "Lerøy Seafood",
    "SPOL.OL": "SpareBank 1 Østlandet",
    "BAKKA.OL": "Bakkafrost",
    "VEI.OL": "Veidekke",
    "HAUTO.OL": "Höegh Autoliners",
    "ODL.OL": "Odfjell Drilling",
    "TGS.OL": "TGS",
    "KIT.OL": "Kitron",
    "BWLPG.OL": "BW LPG",
    "AUSS.OL": "Austevoll Seafood",
    "SNI.OL": "Stolt-Nielsen",
    "BRG.OL": "Borregaard",
    "NONG.OL": "SpareBank 1 Nord-Norge",
    "ATEA.OL": "Atea",
    "NAS.OL": "Norwegian Air Shuttle",
    "EPR.OL": "Europris",
    "BWE.OL": "BW Energy",
    "BNOR.OL": "BlueNord",
    "BORR.OL": "Borr Drilling",
    "ELO.OL": "Elopak",
    "NORCO.OL": "Norconsult",
    "SOMA.OL": "Solstad Maritime",
    "MPCC.OL": "MPC Container Ships",

    # ── 51–100 ──
    "BONHR.OL": "Bonheur",
    "ODF.OL": "Odfjell A",
    "ODFB.OL": "Odfjell B",
    "AKBM.OL": "Aker BioMarine",
    "BWO.OL": "BW Offshore",
    "AFK.OL": "Arendals Fossekompani",
    "B2I.OL": "B2Impact",
    "SATS.OL": "SATS",
    "GSF.OL": "Grieg Seafood",
    "RING.OL": "SpareBank 1 Ringerike Hadeland",
    "ENTRA.OL": "Entra",
    "SCATC.OL": "Scatec",
    "DNO.OL": "DNO International",
    "OET.OL": "Okeanis Eco Tankers",
    "ELK.OL": "Elkem",
    "AFG.OL": "AF Gruppen",
    "AKSO.OL": "Aker Solutions",
    "KID.OL": "Kid",
    "NEL.OL": "Nel Hydrogen",
    "MEDI.OL": "Medistim",
    "BEL.OL": "Belships",
    "BOUV.OL": "Bouvet",
    "REACH.OL": "Reach Subsea",
    "MULTI.OL": "Multiconsult",
    "PEXIP.OL": "Pexip",
    "VOLUE.OL": "Volue",
    "HEX.OL": "Hexagon Composites",
    "PHO.OL": "Photocure",
    "PARB.OL": "Pareto Bank",
    "ACR.OL": "Axactor",
    "SBO.OL": "Selvaag Bolig",
    "MORG.OL": "Sparebanken Møre",
    "KAHOT.OL": "Kahoot!",
    "MEL.OL": "Meltwater",

    # ── 100+ (midcap/smallcap) ──
    "AKVA.OL": "AKVA Group",
    "KOA.OL": "Kongsberg Automotive",
    "NRC.OL": "NRC Group",
    "BMA.OL": "Byggma",
    "NAVA.OL": "Navamedic",
    "RECSI.OL": "REC Silicon",
    "HELG.OL": "Helgeland Sparebank",
    "SPOG.OL": "Sparebanken Øst",
    "ENDUR.OL": "Endúr",
    "POL.OL": "Polaris Media",
    "GYL.OL": "Gyldendal",
    "EIOF.OL": "Eidesvik Offshore",
    "ARCH.OL": "Archer",
    "ELMRA.OL": "Elmera Group",
    "EMGS.OL": "Electromagnetic Geoservices",
    "PEN.OL": "Panoro Energy",
    "NAPA.OL": "Napatech",
    "PLT.OL": "poLight",
    "ZAL.OL": "Zalaris",
    "GOD.OL": "Goodtech",
    "PRS.OL": "Prosafe",
    "JIN.OL": "Jinhui Shipping",
    "BOR.OL": "Borgestad",
    "PCIB.OL": "PCI Biotech",
    "GIG.OL": "Gaming Innovation Group",
    "VVL.OL": "Voss Veksel- og Landmandsbank",
    "NORBT.OL": "Norbit",
    "CADLR.OL": "Cadeler",
    "COSH.OL": "Constellation Oil Services",
    "VEND.OL": "Vend Marketplaces",
    "TIETO.OL": "TietoEVRY",
    "SWON.OL": "SoftwareOne",
    "PUBLI.OL": "Public Property Invest",
    "SB1NO.OL": "SpareBank 1 Sør-Norge",
    "SBNOR.OL": "Sparebanken Norge",
}


# ──────────────────────────────────────────────────────────────
# RSI
# ──────────────────────────────────────────────────────────────

def beregn_rsi(serie: pd.Series, periode: int = 14) -> pd.Series:
    delta = serie.diff()
    gevinst = delta.where(delta > 0, 0.0)
    tap = -delta.where(delta < 0, 0.0)
    avg_gevinst = gevinst.ewm(alpha=1 / periode, min_periods=periode).mean()
    avg_tap = tap.ewm(alpha=1 / periode, min_periods=periode).mean()
    rs = avg_gevinst / avg_tap
    return 100.0 - (100.0 / (1.0 + rs))


# ──────────────────────────────────────────────────────────────
# DATAHENTING (batch med retry + curl_cffi for å unngå rate limit)
# ──────────────────────────────────────────────────────────────

def _lag_session():
    """Lager en session som etterlikner Chrome — unngår Yahoo TLS fingerprinting."""
    try:
        from curl_cffi import requests as cffi_requests
        session = cffi_requests.Session(impersonate="chrome")
        return session
    except ImportError:
        return None

@st.cache_data(ttl=600, show_spinner=False)
def hent_data(ticker_dict: dict, dager_historikk: int = 250) -> pd.DataFrame:
    tickers_liste = list(ticker_dict.keys())
    start = datetime.now() - timedelta(days=dager_historikk + 50)
    end = datetime.now()

    # Bruk curl_cffi session for å unngå 429-feil
    session = _lag_session()

    alle_data = {}
    progress = st.progress(0, text="Henter data...")
    total_batches = (len(tickers_liste) - 1) // BATCH_SIZE + 1

    for batch_nr in range(0, len(tickers_liste), BATCH_SIZE):
        batch = tickers_liste[batch_nr:batch_nr + BATCH_SIZE]
        batch_idx = batch_nr // BATCH_SIZE + 1
        progress.progress(
            min(batch_idx / total_batches, 1.0),
            text=f"Batch {batch_idx}/{total_batches} — {min(batch_nr + BATCH_SIZE, len(tickers_liste))}/{len(tickers_liste)} aksjer..."
        )

        for forsok in range(2):
            try:
                raw = yf.download(
                    batch, start=start, end=end,
                    progress=False, auto_adjust=True,
                    timeout=30, group_by="ticker", threads=True,
                    session=session
                )
                if raw is not None and not raw.empty:
                    for ticker in batch:
                        try:
                            df_t = raw.copy() if len(batch) == 1 else raw[ticker].copy()
                            df_t = df_t.dropna(how="all")
                            if len(df_t) >= 50:
                                alle_data[ticker] = df_t
                        except (KeyError, TypeError):
                            continue
                break
            except Exception as e:
                if forsok == 0:
                    time.sleep(BATCH_DELAY * 2)
                else:
                    print(f"⚠️  Batch-feil: {e}")

        if batch_nr + BATCH_SIZE < len(tickers_liste):
            time.sleep(BATCH_DELAY)

    progress.empty()

    resultater = []
    for ticker, df in alle_data.items():
        try:
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            close = df["Close"].dropna()
            volume = df["Volume"].dropna()
            high = df["High"].dropna()
            low = df["Low"].dropna()

            if len(close) < 50:
                continue

            siste_kurs = float(close.iloc[-1])
            forrige_kurs = float(close.iloc[-2]) if len(close) >= 2 else siste_kurs

            sma200 = float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else None
            sma50 = float(close.rolling(50).mean().iloc[-1]) if len(close) >= 50 else None
            rsi14 = float(beregn_rsi(close, 14).iloc[-1]) if len(close) >= 20 else None

            volum_idag = float(volume.iloc[-1])
            snitt_volum_20d = float(volume.tail(20).mean())
            volum_ratio = round(volum_idag / snitt_volum_20d, 2) if snitt_volum_20d > 0 else 0.0

            pct_endring = round(((siste_kurs - forrige_kurs) / forrige_kurs) * 100, 2) if forrige_kurs > 0 else 0.0

            over_sma200 = siste_kurs > sma200 if sma200 else None
            over_sma50 = siste_kurs > sma50 if sma50 else None
            avstand_sma50_pct = round(((siste_kurs - sma50) / sma50) * 100, 2) if sma50 else None

            high_20d = float(high.tail(20).max())
            low_20d = float(low.tail(20).min())
            avstand_high_20d_pct = round(((siste_kurs - high_20d) / high_20d) * 100, 2) if high_20d > 0 else 0.0
            avstand_low_20d_pct = round(((siste_kurs - low_20d) / low_20d) * 100, 2) if low_20d > 0 else 0.0

            resultater.append({
                "Ticker": ticker.replace(".OL", ""),
                "ticker_yf": ticker,
                "Selskap": ticker_dict.get(ticker, ticker),
                "Kurs": round(siste_kurs, 2),
                "% i dag": pct_endring,
                "SMA 200": round(sma200, 2) if sma200 else None,
                "Over SMA200": over_sma200,
                "SMA 50": round(sma50, 2) if sma50 else None,
                "Over SMA50": over_sma50,
                "Avst SMA50 %": avstand_sma50_pct,
                "RSI 14": round(rsi14, 1) if rsi14 else None,
                "Volum": int(volum_idag),
                "Snitt Vol 20d": int(snitt_volum_20d),
                "Vol Ratio": volum_ratio,
                "Avst 20d High %": avstand_high_20d_pct,
                "Avst 20d Low %": avstand_low_20d_pct,
            })
        except Exception as e:
            print(f"⚠️  Beregningsfeil {ticker}: {e}")
            continue

    if not resultater:
        return pd.DataFrame()

    df_result = pd.DataFrame(resultater)
    df_result = klassifiser_setup(df_result)
    df_result = beregn_score(df_result)
    return df_result


# ──────────────────────────────────────────────────────────────
# SETUP-KLASSIFISERING (v3)
# Oppdatert: Pullback/Breakout strammet, ny Early Pullback
# ──────────────────────────────────────────────────────────────

def klassifiser_setup(df: pd.DataFrame) -> pd.DataFrame:
    setups = []
    for _, row in df.iterrows():
        over200 = row.get("Over SMA200")
        over50 = row.get("Over SMA50")
        rsi = row.get("RSI 14")
        avst_sma50 = row.get("Avst SMA50 %")
        avst_high = row.get("Avst 20d High %")
        vol_ratio = row.get("Vol Ratio", 0)
        pct_idag = row.get("% i dag", 0)

        if over200 is None or rsi is None or avst_sma50 is None:
            setups.append("No setup"); continue

        # Extended: RSI>75 ELLER >8% over SMA50 — dårlige entries, fang først
        if rsi > 75 or (over200 and avst_sma50 > 8):
            setups.append("Extended"); continue

        # Breakout: nær 20d high, STERK volum, IKKE for langt over SMA50
        if (over200
                and avst_high is not None and avst_high >= -2.0
                and vol_ratio >= 1.5
                and rsi > 50
                and avst_sma50 <= 5.0):
            setups.append("Breakout"); continue

        # Pullback: kontrollert tilbaketrekking nær SMA50
        if (over200
                and avst_sma50 is not None and -2.0 <= avst_sma50 <= 1.0
                and 35 <= rsi <= 55):
            setups.append("Pullback"); continue

        # Early Pullback: forkant-trade, volum ennå ikke kommet
        if (over200
                and avst_sma50 is not None and -2.0 <= avst_sma50 <= 1.0
                and 40 <= rsi <= 50
                and vol_ratio < 1.0):
            setups.append("Early Pullback"); continue

        # Momentum: sterk dag med volumbekreftelse
        if (over200 and over50
                and pct_idag > 0.5
                and vol_ratio >= 1.0
                and rsi > 50):
            setups.append("Momentum"); continue

        # Trend: stabil over begge SMA
        if over200 and over50 and 40 <= rsi <= 70:
            setups.append("Trend"); continue

        setups.append("No setup")

    df["Setup"] = setups
    return df


# ──────────────────────────────────────────────────────────────
# TRADE SCORE (0–10) — prioriterer entry timing
#
#   +2  Kurs over SMA 200
#   +1  Kurs over SMA 50
#   +2  Nær SMA50 (−2% til +2%)
#   +1  RSI mellom 40–60
#   +2  Vol ratio > 1.2
#   +1  Vol ratio > 1.0 (men < 1.2)
#   +1  Snittvolum 20d > 500k
#   Maks = 10
# ──────────────────────────────────────────────────────────────

def beregn_score(df: pd.DataFrame) -> pd.DataFrame:
    scores = []
    signaler = []
    forklaringer = []

    for _, row in df.iterrows():
        p = 0
        info = []

        # Over SMA 200
        if row.get("Over SMA200"):
            p += 2; info.append("SMA200 +2")

        # Over SMA 50
        if row.get("Over SMA50"):
            p += 1; info.append("SMA50 +1")

        # Nær SMA50 (−2% til +2%) — entry timing
        avst = row.get("Avst SMA50 %")
        if avst is not None and -2.0 <= avst <= 2.0:
            p += 2; info.append("NærSMA50 +2")

        # RSI sweet spot (40–60)
        rsi = row.get("RSI 14")
        if rsi is not None and 40 <= rsi <= 60:
            p += 1; info.append("RSI +1")

        # Volum — sterk bekreftelse
        vol_ratio = row.get("Vol Ratio", 0)
        if vol_ratio > 1.2:
            p += 2; info.append("Vol>1.2 +2")
        elif vol_ratio > 1.0:
            p += 1; info.append("Vol>1.0 +1")

        # Likviditet
        if row.get("Snitt Vol 20d", 0) > 500_000:
            p += 1; info.append("Likv +1")

        # Klamp 0–10
        final = max(0, min(p, 10))
        scores.append(final)
        forklaringer.append(", ".join(info))

        # ── Trade Signal ──
        if (final >= 8
                and vol_ratio > 1.0
                and avst is not None and -2.0 <= avst <= 2.0):
            signaler.append("BUY")
        elif final >= 6:
            signaler.append("WATCH")
        else:
            signaler.append("SKIP")

    df["Score"] = scores
    df["Signal"] = signaler
    df["Score info"] = forklaringer
    return df


# ──────────────────────────────────────────────────────────────
# WATCHLIST
# ──────────────────────────────────────────────────────────────

def last_watchlist() -> set:
    if WATCHLIST_FILE.exists():
        try:
            with open(WATCHLIST_FILE, "r") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()

def lagre_watchlist(tickers: set):
    with open(WATCHLIST_FILE, "w") as f:
        json.dump(sorted(list(tickers)), f, indent=2)


# ──────────────────────────────────────────────────────────────
# VISNING
# ──────────────────────────────────────────────────────────────

SETUP_EMOJI = {
    "Trend": "🟢", "Pullback": "🟡", "Breakout": "🔵",
    "Early Pullback": "🟠", "Momentum": "🟣",
    "Extended": "🔴", "No setup": "⚪",
}

SIGNAL_EMOJI = {"BUY": "🟢 BUY", "WATCH": "🟡 WATCH", "SKIP": "🔴 SKIP"}

def formater_tabell(df: pd.DataFrame) -> pd.DataFrame:
    vis = df.copy()
    vis["Setup"] = vis["Setup"].apply(lambda x: f"{SETUP_EMOJI.get(x, '')} {x}")
    vis["Signal"] = vis["Signal"].apply(lambda x: SIGNAL_EMOJI.get(x, x))
    vis["Over SMA200"] = vis["Over SMA200"].apply(lambda x: "✅" if x else ("❌" if x is False else "—"))
    vis["Over SMA50"] = vis["Over SMA50"].apply(lambda x: "✅" if x else ("❌" if x is False else "—"))
    vis["Vol Ratio"] = vis["Vol Ratio"].apply(lambda x: f"{'🟩 ' if x >= 1.5 else ''}{x:.1f}x")
    vis["Volum"] = vis["Volum"].apply(lambda x: f"{x:,.0f}".replace(",", " "))
    vis["Snitt Vol 20d"] = vis["Snitt Vol 20d"].apply(lambda x: f"{x:,.0f}".replace(",", " "))
    cols = [
        "Signal", "Ticker", "Selskap", "Kurs", "% i dag",
        "Over SMA200", "Over SMA50", "Avst SMA50 %",
        "RSI 14", "Vol Ratio", "Volum", "Snitt Vol 20d",
        "Avst 20d High %", "Avst 20d Low %", "Setup", "Score",
    ]
    return vis[[c for c in cols if c in vis.columns]]


# ──────────────────────────────────────────────────────────────
# [FIX #4] FILTER DEFAULTS – checkboxes for setup, ikke dropdown
# [FIX #5] Default likviditet 200k
# ──────────────────────────────────────────────────────────────

FILTER_DEFAULTS = {
    "f_pullback": False,
    "f_early_pullback": False,
    "f_breakout": False,
    "f_trend": False,
    "f_momentum": False,
    "f_extended": False,
    "f_no_setup": False,
    "f_skjul_extended": True,
    "f_signal": "Alle",
    "f_over_sma200": False,
    "f_over_sma50": False,
    "f_rsi": (20, 80),
    "f_avst_sma50": (-15.0, 15.0),
    "f_kun_hoyt_volum": False,
    "f_min_vol_ratio": 0.0,
    "f_min_vol": DEFAULT_MIN_AVG_VOLUME,
    "f_min_score": 0,
}

def reset_filtre():
    for key, val in FILTER_DEFAULTS.items():
        st.session_state[key] = val


# ──────────────────────────────────────────────────────────────
# STREAMLIT APP
# ──────────────────────────────────────────────────────────────

def main():
    st.set_page_config(page_title="Oslo Børs Scanner", page_icon="📈", layout="wide")
    st.title("📈 Oslo Børs Swing Trading Scanner")

    # ── AUTO-REFRESH ──
    refresh_options = {"Av": 0, "5 min": 5, "10 min": 10, "15 min": 15, "30 min": 30}
    col_title, col_refresh = st.columns([3, 1])
    with col_title:
        st.caption(f"Scanner {len(OSLO_TICKERS)} aksjer — SMA 200/50, RSI 14, volum, pris-avstander.")
    with col_refresh:
        refresh_valg = st.selectbox("Auto-refresh", list(refresh_options.keys()), index=3, label_visibility="collapsed")

    refresh_min = refresh_options[refresh_valg]
    if refresh_min > 0:
        teller = st_autorefresh(interval=refresh_min * 60 * 1000, key="auto_refresh")
        if teller and teller > 0:
            st.cache_data.clear()

    # ── Session state ──
    if "watchlist" not in st.session_state:
        st.session_state.watchlist = last_watchlist()
    if "data" not in st.session_state:
        st.session_state.data = None
    for key, val in FILTER_DEFAULTS.items():
        if key not in st.session_state:
            st.session_state[key] = val

    # ══════════════════════════════════════════════════════
    # DEL 1 – FILTERPANEL
    # ══════════════════════════════════════════════════════
    st.markdown("---")
    st.subheader("🔍 Filtre")

    # Scan + Reset
    col_scan, col_reset = st.columns([1, 1])
    with col_scan:
        scan_klikket = st.button("🔄 Scan nå", type="primary", width="stretch")
    with col_reset:
        st.button("🗑️ Reset filtre", on_click=reset_filtre, width="stretch")

    if scan_klikket:
        st.cache_data.clear()
        st.session_state.data = hent_data(OSLO_TICKERS)
        st.success(f"✅ Skannet {len(st.session_state.data)} aksjer")
    elif st.session_state.data is None:
        st.session_state.data = hent_data(OSLO_TICKERS)

    df = st.session_state.data
    if df is None or df.empty:
        st.warning("Ingen data. Trykk «Scan nå».")
        return

    # ── Setup-filter som checkboxes ──
    st.markdown("**Setup-filter:**")
    sc1, sc2, sc3, sc4, sc5, sc6, sc7 = st.columns(7)
    with sc1:
        f_pullback = st.checkbox("🟡 Pullback", key="f_pullback")
    with sc2:
        f_early_pb = st.checkbox("🟠 Early PB", key="f_early_pullback")
    with sc3:
        f_breakout = st.checkbox("🔵 Breakout", key="f_breakout")
    with sc4:
        f_trend = st.checkbox("🟢 Trend", key="f_trend")
    with sc5:
        f_momentum = st.checkbox("🟣 Momentum", key="f_momentum")
    with sc6:
        f_extended = st.checkbox("🔴 Extended", key="f_extended")
    with sc7:
        f_no_setup = st.checkbox("⚪ No setup", key="f_no_setup")

    # ── Filtre ──
    st.markdown("**Filtre:**")
    fc1, fc2, fc3, fc4 = st.columns(4)

    with fc1:
        signal_filter = st.selectbox("📡 Trade Signal", ["Alle", "BUY", "WATCH", "SKIP"], key="f_signal")
        skjul_extended = st.checkbox("🚫 Skjul Extended", key="f_skjul_extended",
                                      help="Fjerner aksjer >8% over SMA50 / RSI>75")
        kun_sma200 = st.checkbox("Kun over SMA 200", key="f_over_sma200")
        kun_sma50 = st.checkbox("Kun over SMA 50", key="f_over_sma50")

    with fc2:
        kun_hoyt_vol = st.checkbox("🔊 Kun høyt volum (>1x)", key="f_kun_hoyt_volum")
        min_vol_ratio = st.slider("Min. Vol Ratio", 0.0, 5.0, step=0.1, key="f_min_vol_ratio")

    with fc3:
        rsi_range = st.slider("RSI-range", 0, 100, key="f_rsi")
        avst_range = st.slider("Avstand SMA50 %", -30.0, 30.0, step=0.5, key="f_avst_sma50")

    with fc4:
        min_vol = st.number_input("Min. snittvolum 20d", min_value=0, step=50_000, key="f_min_vol",
                                   help="200k+ anbefalt for trading")
        min_score = st.slider("Minimum score", 0, 10, key="f_min_score")

    # ── Appliser filtre ──
    f_df = df.copy()

    # Likviditet
    f_df = f_df[f_df["Snitt Vol 20d"] >= min_vol]

    # Signal-filter
    if signal_filter != "Alle":
        f_df = f_df[f_df["Signal"] == signal_filter]

    # Setup-checkboxes: vis kun valgte, eller alle hvis ingen valgt
    valgte_setups = []
    if f_pullback: valgte_setups.append("Pullback")
    if f_early_pb: valgte_setups.append("Early Pullback")
    if f_breakout: valgte_setups.append("Breakout")
    if f_trend: valgte_setups.append("Trend")
    if f_momentum: valgte_setups.append("Momentum")
    if f_extended: valgte_setups.append("Extended")
    if f_no_setup: valgte_setups.append("No setup")

    if valgte_setups:
        f_df = f_df[f_df["Setup"].isin(valgte_setups)]

    # [FIX #3] Skjul Extended
    if skjul_extended and not f_extended:
        f_df = f_df[f_df["Setup"] != "Extended"]

    # SMA-filtre
    if kun_sma200:
        f_df = f_df[f_df["Over SMA200"] == True]
    if kun_sma50:
        f_df = f_df[f_df["Over SMA50"] == True]

    # [FIX #1] Volumfiltre
    if kun_hoyt_vol:
        f_df = f_df[f_df["Vol Ratio"] >= 1.0]
    if min_vol_ratio > 0:
        f_df = f_df[f_df["Vol Ratio"] >= min_vol_ratio]

    # RSI + SMA50 avstand
    f_df = f_df[f_df["RSI 14"].notna() & (f_df["RSI 14"] >= rsi_range[0]) & (f_df["RSI 14"] <= rsi_range[1])]
    f_df = f_df[f_df["Avst SMA50 %"].notna() & (f_df["Avst SMA50 %"] >= avst_range[0]) & (f_df["Avst SMA50 %"] <= avst_range[1])]

    # Score
    f_df = f_df[f_df["Score"] >= min_score]

    # Sorter
    f_df = f_df.sort_values("Score", ascending=False).reset_index(drop=True)

    # ══════════════════════════════════════════════════════
    # DEL 2 – HOVEDTABELL
    # ══════════════════════════════════════════════════════
    st.markdown("---")

    # Quick-stats
    qs1, qs2, qs3, qs4, qs5, qs6 = st.columns(6)
    total = len(f_df)
    qs1.metric("Kandidater", total)
    qs2.metric("🟢 BUY", len(f_df[f_df["Signal"] == "BUY"]) if total else 0)
    qs3.metric("🟡 WATCH", len(f_df[f_df["Signal"] == "WATCH"]) if total else 0)
    qs4.metric("Pullback", len(f_df[f_df["Setup"].isin(["Pullback", "Early Pullback"])]) if total else 0)
    qs5.metric("Breakout", len(f_df[f_df["Setup"] == "Breakout"]) if total else 0)
    qs6.metric("Trend", len(f_df[f_df["Setup"] == "Trend"]) if total else 0)

    if f_df.empty:
        st.info("Ingen aksjer matcher filtrene. Juster filtre eller trykk «Reset filtre».")
    else:
        st.dataframe(formater_tabell(f_df), width="stretch", hide_index=True,
                      height=min(len(f_df) * 38 + 40, 700))

        # Score-forklaring for topp-aksje
        if len(f_df) > 0:
            topp = f_df.iloc[0]
            st.caption(f"Topp: **{topp['Ticker']}** ({topp['Selskap']}) — Score {topp['Score']}: {topp.get('Score info', '')}")

        # Watchlist-knapper
        st.markdown("**Legg til / fjern fra watchlist:**")
        n_cols = min(len(f_df), 8)
        wl_cols = st.columns(n_cols)
        for i, (_, row) in enumerate(f_df.iterrows()):
            t = row["Ticker"]
            with wl_cols[i % n_cols]:
                in_wl = t in st.session_state.watchlist
                if st.button(f"{'⭐' if in_wl else '☆'} {t}", key=f"wl_{t}"):
                    st.session_state.watchlist.discard(t) if in_wl else st.session_state.watchlist.add(t)
                    lagre_watchlist(st.session_state.watchlist)
                    st.rerun()

    # ══════════════════════════════════════════════════════
    # DEL 3 – WATCHLIST
    # ══════════════════════════════════════════════════════
    st.markdown("---")
    st.subheader(f"⭐ Watchlist ({len(st.session_state.watchlist)})")

    if not st.session_state.watchlist:
        st.info("Tom watchlist. Klikk ☆ for å legge til.")
    else:
        wl_df = df[df["Ticker"].isin(st.session_state.watchlist)].sort_values("Score", ascending=False)
        if wl_df.empty:
            st.warning("Watchlist-aksjer ikke funnet i siste scan.")
        else:
            st.dataframe(formater_tabell(wl_df), width="stretch", hide_index=True)

        n_cols = min(len(st.session_state.watchlist), 8)
        fjern_cols = st.columns(n_cols)
        for i, t in enumerate(sorted(st.session_state.watchlist)):
            with fjern_cols[i % n_cols]:
                if st.button(f"❌ {t}", key=f"rm_{t}"):
                    st.session_state.watchlist.discard(t)
                    lagre_watchlist(st.session_state.watchlist)
                    st.rerun()

    # ══════════════════════════════════════════════════════
    # FOOTER
    # ══════════════════════════════════════════════════════
    st.markdown("---")
    with st.expander("ℹ️ Trade Score, Signal og setup-logikk (v3)"):
        st.markdown("""
**Trade Score (0–10) — fokus på entry timing:**
| Poeng | Betingelse | Hvorfor |
|-------|-----------|---------|
| +2 | Over SMA 200 | Langsiktig trend |
| +1 | Over SMA 50 | Kortsiktig trend |
| +2 | Nær SMA50 (−2% til +2%) | Optimal entry-sone |
| +1 | RSI 40–60 | Ikke overkjøpt/oversolgt |
| +2 | Vol ratio > 1.2x | Sterk volumbekreftelse |
| +1 | Vol ratio > 1.0x (men <1.2) | Moderat volum |
| +1 | Snittvolum > 500k | Tradebar størrelse |

**Trade Signal:**
| Signal | Regel |
|--------|-------|
| 🟢 **BUY** | Score ≥ 8, vol ratio > 1, nær SMA50 (±2%) |
| 🟡 **WATCH** | Score ≥ 6 |
| 🔴 **SKIP** | Score < 6 |

**Setup-typer:**
- 🟡 **Pullback** — Over SMA200, avst SMA50 −2% til +1%, RSI 35–55
- 🟠 **Early Pullback** — Som Pullback, men RSI 40–50 og vol < 1.0 (forkant-trade)
- 🔵 **Breakout** — Over SMA200, nær 20d high (<2%), vol > 1.5x, RSI > 50, maks +5% over SMA50
- 🟢 **Trend** — Over SMA200+50, RSI 40–70
- 🟣 **Momentum** — Over begge SMA, positiv dag, vol > 1.0, RSI > 50
- 🔴 **Extended** — RSI > 75 eller >8% over SMA50 (unngå)
- ⚪ **No setup** — Matcher ingen kriterier
        """)

    oslo_tid = datetime.now(ZoneInfo("Europe/Oslo")).strftime('%Y-%m-%d %H:%M')
    st.caption(f"Oppdatert: {oslo_tid} (Oslo) | yfinance (forsinket) | {len(OSLO_TICKERS)} aksjer")


if __name__ == "__main__":
    main()
