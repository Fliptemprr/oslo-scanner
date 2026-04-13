"""
Oslo Børs Swing Trading Scanner – MVP
======================================
Enkel scanner som finner swing trading-kandidater på Oslo Børs.
Bruker SMA 200, SMA 50, RSI 14, volum og relative avstander.
Kategoriserer aksjer som Trend / Pullback / Breakout / Momentum / Extended / No setup.

Kjør:  streamlit run scanner.py
"""

import streamlit as st
import pandas as pd
import yfinance as yf
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

# ──────────────────────────────────────────────────────────────
# KONFIGURASJON
# ──────────────────────────────────────────────────────────────

WATCHLIST_FILE = Path("watchlist.json")

# Minimum gjennomsnittlig daglig volum siste 20 dager for å ikke bli filtrert bort
DEFAULT_MIN_AVG_VOLUME = 50_000

# OBX-aksjer (oppdater/utvid denne listen enkelt)
# Format: {"TICKER.OL": "Selskapsnavn"}
OBX_TICKERS = {
    "EQNR.OL": "Equinor",
    "DNB.OL": "DNB Bank",
    "MOWI.OL": "Mowi",
    "TEL.OL": "Telenor",
    "YAR.OL": "Yara International",
    "ORK.OL": "Orkla",
    "AKRBP.OL": "Aker BP",
    "SALM.OL": "SalMar",
    "SUBC.OL": "Subsea 7",
    "NOD.OL": "Nordic Semiconductor",
    "AKER.OL": "Aker",
    "BAKKA.OL": "Bakkafrost",
    "KOG.OL": "Kongsberg Gruppen",
    "RECSI.OL": "REC Silicon",
    "ENTRA.OL": "Entra",
    "HAFNI.OL": "Hafnia",
    "BWLPG.OL": "BW LPG",
    "AKSO.OL": "Aker Solutions",
    "FLNG.OL": "Flex LNG",
    "BORR.OL": "Borr Drilling",
    "SCATC.OL": "Scatec",
    "PROT.OL": "Protector Forsikring",
    "CRAYN.OL": "Crayon Group",
    "AUSS.OL": "Austevoll Seafood",
    "LSG.OL": "Lerøy Seafood",
    "MPCC.OL": "MPC Container Ships",
    "SCHA.OL": "Schibsted A",
    "SCHB.OL": "Schibsted B",
    "AUTO.OL": "Autostore Holdings",
    "BWO.OL": "BW Offshore",
    "KAHOT.OL": "Kahoot!",
    "NHYDY.OL": "Norsk Hydro",  # bruker alternativ ticker under
    "NHY.OL": "Norsk Hydro",
    "FRO.OL": "Frontline",
    "GOGL.OL": "Golden Ocean Group",
    "TGS.OL": "TGS",
    "VAR.OL": "Vår Energi",
    "MEL.OL": "Meltwater",
    "OET.OL": "Okeanis Eco Tankers",
    "BWEK.OL": "BW Energy",
}

# Fjern duplikat for Norsk Hydro
if "NHYDY.OL" in OBX_TICKERS:
    del OBX_TICKERS["NHYDY.OL"]


# ──────────────────────────────────────────────────────────────
# RSI-BEREGNING (ren pandas, ingen ekstra avhengighet nødvendig)
# ──────────────────────────────────────────────────────────────

def beregn_rsi(serie: pd.Series, periode: int = 14) -> pd.Series:
    """Beregner RSI (Relative Strength Index) med Wilder's smoothing."""
    delta = serie.diff()
    gevinst = delta.where(delta > 0, 0.0)
    tap = -delta.where(delta < 0, 0.0)
    avg_gevinst = gevinst.ewm(alpha=1 / periode, min_periods=periode).mean()
    avg_tap = tap.ewm(alpha=1 / periode, min_periods=periode).mean()
    rs = avg_gevinst / avg_tap
    return 100.0 - (100.0 / (1.0 + rs))


# ──────────────────────────────────────────────────────────────
# DATAHENTING OG BEREGNINGER
# ──────────────────────────────────────────────────────────────

@st.cache_data(ttl=300, show_spinner=False)
def hent_data(ticker_dict: dict, dager_historikk: int = 250) -> pd.DataFrame:
    """
    Henter kursdata fra yfinance og beregner alle nødvendige indikatorer.
    Returnerer én rad per aksje med alle kolonner.
    """
    resultater = []
    tickers_liste = list(ticker_dict.keys())

    # Hent data for alle tickers samlet (raskere)
    start = datetime.now() - timedelta(days=dager_historikk + 50)  # litt ekstra for SMA 200
    end = datetime.now()

    for ticker in tickers_liste:
        try:
            df = yf.download(
                ticker, start=start, end=end,
                progress=False, auto_adjust=True, timeout=15
            )
            if df is None or df.empty or len(df) < 50:
                continue

            # Fiks MultiIndex-kolonner fra yfinance
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            close = df["Close"]
            volume = df["Volume"]
            high = df["High"]
            low = df["Low"]

            siste_kurs = float(close.iloc[-1])
            forrige_kurs = float(close.iloc[-2]) if len(close) >= 2 else siste_kurs

            # --- Indikatorer ---
            sma200 = float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else None
            sma50 = float(close.rolling(50).mean().iloc[-1]) if len(close) >= 50 else None
            rsi14 = float(beregn_rsi(close, 14).iloc[-1]) if len(close) >= 20 else None

            # Volum
            volum_idag = float(volume.iloc[-1])
            snitt_volum_20d = float(volume.tail(20).mean())
            volum_ratio = round(volum_idag / snitt_volum_20d, 2) if snitt_volum_20d > 0 else 0.0

            # Prosent endring i dag
            pct_endring = round(((siste_kurs - forrige_kurs) / forrige_kurs) * 100, 2) if forrige_kurs > 0 else 0.0

            # Over/under SMA
            over_sma200 = siste_kurs > sma200 if sma200 else None
            over_sma50 = siste_kurs > sma50 if sma50 else None

            # Avstand til SMA 50 i %
            avstand_sma50_pct = round(((siste_kurs - sma50) / sma50) * 100, 2) if sma50 else None

            # 20-dagers high og low
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
            # Hopp over aksjer som feiler, logg i terminal
            print(f"⚠️  Feil ved henting av {ticker}: {e}")
            continue

    if not resultater:
        return pd.DataFrame()

    df_result = pd.DataFrame(resultater)
    # Klassifiser setup og beregn score
    df_result = klassifiser_setup(df_result)
    df_result = beregn_score(df_result)
    return df_result


# ──────────────────────────────────────────────────────────────
# SETUP-KLASSIFISERING
# ──────────────────────────────────────────────────────────────

def klassifiser_setup(df: pd.DataFrame) -> pd.DataFrame:
    """
    Klassifiserer hver aksje som Trend / Pullback / Breakout / Momentum / Extended / No setup.
    Følger logikken fra spesifikasjonen nøyaktig.
    """
    setups = []
    for _, row in df.iterrows():
        over200 = row.get("Over SMA200")
        over50 = row.get("Over SMA50")
        rsi = row.get("RSI 14")
        avst_sma50 = row.get("Avst SMA50 %")
        avst_high = row.get("Avst 20d High %")
        vol_ratio = row.get("Vol Ratio", 0)
        pct_idag = row.get("% i dag", 0)

        # Sjekk om vi har nok data
        if over200 is None or rsi is None or avst_sma50 is None:
            setups.append("No setup")
            continue

        # Extended: aksjen har gått for langt opp (over SMA50 + RSI høy + langt fra SMA50)
        if over200 and over50 and rsi and rsi > 75 and avst_sma50 and avst_sma50 > 8:
            setups.append("Extended")
            continue

        # Breakout: nær 20d high, volum over snitt, RSI > 50, over SMA200
        if (over200
                and avst_high is not None and avst_high >= -1.5
                and vol_ratio >= 1.0
                and rsi > 50):
            setups.append("Breakout")
            continue

        # Pullback: over SMA200, nær SMA50 (0–3%), RSI 35–50
        if (over200
                and avst_sma50 is not None and -1.0 <= avst_sma50 <= 3.0
                and 35 <= rsi <= 55):
            setups.append("Pullback")
            continue

        # Momentum: over SMA200, positiv bevegelse, volum bekrefter
        if (over200
                and over50
                and pct_idag > 0.5
                and vol_ratio >= 1.0
                and rsi > 50):
            setups.append("Momentum")
            continue

        # Trend: over SMA200, helst over SMA50, ok RSI, god likviditet
        if over200 and over50 and 40 <= rsi <= 70:
            setups.append("Trend")
            continue

        # Ingenting matchet
        setups.append("No setup")

    df["Setup"] = setups
    return df


# ──────────────────────────────────────────────────────────────
# ENKEL SCORINGMODELL (0–10)
# ──────────────────────────────────────────────────────────────

def beregn_score(df: pd.DataFrame) -> pd.DataFrame:
    """
    Enkel, transparent poengmodell 0–10.
    Poenglogikk:
      +2  kurs over SMA 200
      +1  kurs over SMA 50
      +1  nær SMA 50 på kontrollert måte (avstand 0–5%)
      +1  RSI i gunstig område (40–65)
      +1  RSI ikke oversolgt og ikke overkjøpt (30–70)
      +1  volum over snitt (ratio >= 1.0)
      +1  nær 20d high (breakout-signal, avstand >= -2%)
      +1  god likviditet (snittvolum > 100k)
      +1  positiv dagsbevegelse
    Maks: 10
    """
    scores = []
    detaljer = []
    for _, row in df.iterrows():
        poeng = 0
        info = []

        # +2 over SMA 200
        if row.get("Over SMA200"):
            poeng += 2
            info.append("SMA200✓")

        # +1 over SMA 50
        if row.get("Over SMA50"):
            poeng += 1
            info.append("SMA50✓")

        # +1 nær SMA 50 kontrollert (0–5%)
        avst = row.get("Avst SMA50 %")
        if avst is not None and 0 <= avst <= 5:
            poeng += 1
            info.append("NærSMA50")

        # +1 RSI gunstig (40–65)
        rsi = row.get("RSI 14")
        if rsi is not None and 40 <= rsi <= 65:
            poeng += 1
            info.append("RSI-sweet")

        # +1 RSI ikke ekstrem (30–70)
        if rsi is not None and 30 <= rsi <= 70:
            poeng += 1
            info.append("RSI-ok")

        # +1 volum over snitt
        if row.get("Vol Ratio", 0) >= 1.0:
            poeng += 1
            info.append("Vol✓")

        # +1 nær 20d high
        avst_h = row.get("Avst 20d High %")
        if avst_h is not None and avst_h >= -2.0:
            poeng += 1
            info.append("NærHigh")

        # +1 god likviditet
        if row.get("Snitt Vol 20d", 0) > 100_000:
            poeng += 1
            info.append("Likv✓")

        # +1 positiv dag
        if row.get("% i dag", 0) > 0:
            poeng += 1
            info.append("Grønn dag")

        scores.append(min(poeng, 10))
        detaljer.append(", ".join(info))

    df["Score"] = scores
    df["Score detaljer"] = detaljer
    return df


# ──────────────────────────────────────────────────────────────
# WATCHLIST – LAGRING/LASTING
# ──────────────────────────────────────────────────────────────

def last_watchlist() -> set:
    """Laster watchlist fra JSON-fil."""
    if WATCHLIST_FILE.exists():
        try:
            with open(WATCHLIST_FILE, "r") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()


def lagre_watchlist(tickers: set):
    """Lagrer watchlist til JSON-fil."""
    with open(WATCHLIST_FILE, "w") as f:
        json.dump(sorted(list(tickers)), f, indent=2)


# ──────────────────────────────────────────────────────────────
# HJELPEFUNKSJONER FOR VISNING
# ──────────────────────────────────────────────────────────────

SETUP_FARGER = {
    "Trend": "🟢",
    "Pullback": "🟡",
    "Breakout": "🔵",
    "Momentum": "🟣",
    "Extended": "🔴",
    "No setup": "⚪",
}


def formater_visningstabell(df: pd.DataFrame) -> pd.DataFrame:
    """Lager en pen visningstabell med emoji-markører og formatering."""
    vis = df.copy()
    vis["Setup"] = vis["Setup"].apply(lambda x: f"{SETUP_FARGER.get(x, '')} {x}")
    vis["Over SMA200"] = vis["Over SMA200"].apply(lambda x: "✅" if x else ("❌" if x is False else "—"))
    vis["Over SMA50"] = vis["Over SMA50"].apply(lambda x: "✅" if x else ("❌" if x is False else "—"))
    vis["Vol Ratio"] = vis["Vol Ratio"].apply(lambda x: f"{x:.1f}x")
    vis["Volum"] = vis["Volum"].apply(lambda x: f"{x:,.0f}".replace(",", " "))
    vis["Snitt Vol 20d"] = vis["Snitt Vol 20d"].apply(lambda x: f"{x:,.0f}".replace(",", " "))

    # Velg og omdøp kolonner for visning
    kolonner = [
        "Ticker", "Selskap", "Kurs", "% i dag",
        "Over SMA200", "Over SMA50", "Avst SMA50 %",
        "RSI 14", "Volum", "Snitt Vol 20d", "Vol Ratio",
        "Avst 20d High %", "Avst 20d Low %",
        "Setup", "Score",
    ]
    return vis[[k for k in kolonner if k in vis.columns]]


# ──────────────────────────────────────────────────────────────
# STREAMLIT APP
# ──────────────────────────────────────────────────────────────

def main():
    st.set_page_config(
        page_title="Oslo Børs Scanner",
        page_icon="📈",
        layout="wide",
    )

    st.title("📈 Oslo Børs Swing Trading Scanner")
    st.caption("Enkel MVP-scanner — finner kandidater basert på SMA, RSI, volum og pris-avstander.")

    # --- Initier session state ---
    if "watchlist" not in st.session_state:
        st.session_state.watchlist = last_watchlist()
    if "data" not in st.session_state:
        st.session_state.data = None

    # ──────────────────────────────────────────────────────
    # DEL 1 – FILTERPANEL ØVERST
    # ──────────────────────────────────────────────────────
    st.markdown("---")
    st.subheader("🔍 Filtre og kontroll")

    # Scan-knapp
    col_scan, col_reset = st.columns([1, 1])
    with col_scan:
        scan_klikket = st.button("🔄 Scan nå", type="primary", use_container_width=True)
    with col_reset:
        reset_klikket = st.button("🗑️ Reset filtre", use_container_width=True)

    # Hent data ved scan eller bruk cache
    if scan_klikket:
        st.cache_data.clear()
        with st.spinner("Henter data fra Oslo Børs via yfinance..."):
            st.session_state.data = hent_data(OBX_TICKERS)
        st.success(f"✅ Skannet {len(st.session_state.data)} aksjer")
    elif st.session_state.data is None:
        with st.spinner("Henter data for første gang..."):
            st.session_state.data = hent_data(OBX_TICKERS)

    df = st.session_state.data

    if df is None or df.empty:
        st.warning("Ingen data tilgjengelig. Trykk «Scan nå» for å prøve igjen.")
        return

    # --- Filterrader ---
    f1, f2, f3, f4 = st.columns(4)

    with f1:
        setup_filter = st.multiselect(
            "Setup-type",
            options=["Trend", "Pullback", "Breakout", "Momentum", "Extended", "No setup"],
            default=[] if not reset_klikket else [],
            help="Velg én eller flere setup-typer å vise"
        )

    with f2:
        kun_over_sma200 = st.checkbox("Kun over SMA 200", value=False)
        kun_over_sma50 = st.checkbox("Kun over SMA 50", value=False)

    with f3:
        rsi_range = st.slider("RSI-range", 0, 100, (20, 80))

    with f4:
        avst_sma50_range = st.slider("Avstand SMA50 %", -30.0, 30.0, (-15.0, 15.0), step=0.5)

    f5, f6, f7 = st.columns(3)

    with f5:
        kun_vol_over_snitt = st.checkbox("Kun volum over snitt", value=False)

    with f6:
        min_snitt_volum = st.number_input(
            "Min. snittvolum 20d",
            min_value=0, value=DEFAULT_MIN_AVG_VOLUME, step=10_000,
            help="Filtrer bort illikvide aksjer"
        )

    with f7:
        min_score = st.slider("Minimum score", 0, 10, 0)

    # --- Appliser filtre ---
    filtrert = df.copy()

    # Illikviditet-filter (alltid aktivt)
    filtrert = filtrert[filtrert["Snitt Vol 20d"] >= min_snitt_volum]

    if setup_filter:
        filtrert = filtrert[filtrert["Setup"].isin(setup_filter)]

    if kun_over_sma200:
        filtrert = filtrert[filtrert["Over SMA200"] == True]

    if kun_over_sma50:
        filtrert = filtrert[filtrert["Over SMA50"] == True]

    if kun_vol_over_snitt:
        filtrert = filtrert[filtrert["Vol Ratio"] >= 1.0]

    filtrert = filtrert[
        filtrert["RSI 14"].notna() &
        (filtrert["RSI 14"] >= rsi_range[0]) &
        (filtrert["RSI 14"] <= rsi_range[1])
    ]

    filtrert = filtrert[
        filtrert["Avst SMA50 %"].notna() &
        (filtrert["Avst SMA50 %"] >= avst_sma50_range[0]) &
        (filtrert["Avst SMA50 %"] <= avst_sma50_range[1])
    ]

    filtrert = filtrert[filtrert["Score"] >= min_score]

    # Sorter på score (høyest først)
    filtrert = filtrert.sort_values("Score", ascending=False).reset_index(drop=True)

    # ──────────────────────────────────────────────────────
    # DEL 2 – HOVEDTABELL
    # ──────────────────────────────────────────────────────
    st.markdown("---")
    st.subheader(f"📊 Kandidater ({len(filtrert)} aksjer)")

    if filtrert.empty:
        st.info("Ingen aksjer matcher valgte filtre. Prøv å justere filtrene.")
    else:
        # Vis tabell
        vis_df = formater_visningstabell(filtrert)
        st.dataframe(
            vis_df,
            use_container_width=True,
            hide_index=True,
            height=min(len(vis_df) * 38 + 40, 700),
        )

        # Watchlist-knapper for å legge til/fjerne
        st.markdown("**Legg til / fjern fra watchlist:**")
        wl_cols = st.columns(min(len(filtrert), 8))
        for i, (_, row) in enumerate(filtrert.iterrows()):
            ticker = row["Ticker"]
            col_idx = i % min(len(filtrert), 8)
            with wl_cols[col_idx]:
                in_wl = ticker in st.session_state.watchlist
                label = f"{'⭐' if in_wl else '☆'} {ticker}"
                if st.button(label, key=f"wl_{ticker}"):
                    if in_wl:
                        st.session_state.watchlist.discard(ticker)
                    else:
                        st.session_state.watchlist.add(ticker)
                    lagre_watchlist(st.session_state.watchlist)
                    st.rerun()

    # ──────────────────────────────────────────────────────
    # DEL 3 – WATCHLIST
    # ──────────────────────────────────────────────────────
    st.markdown("---")
    st.subheader(f"⭐ Watchlist ({len(st.session_state.watchlist)} aksjer)")

    if not st.session_state.watchlist:
        st.info("Watchlisten er tom. Klikk ☆-knappene over for å legge til aksjer.")
    else:
        wl_tickers = st.session_state.watchlist
        wl_df = df[df["Ticker"].isin(wl_tickers)].sort_values("Score", ascending=False).reset_index(drop=True)

        if wl_df.empty:
            st.warning("Aksjene i watchlisten ble ikke funnet i siste scan. Trykk «Scan nå».")
        else:
            vis_wl = formater_visningstabell(wl_df)
            st.dataframe(
                vis_wl,
                use_container_width=True,
                hide_index=True,
            )

        # Fjern-knapper
        fjern_cols = st.columns(min(len(wl_tickers), 8))
        for i, ticker in enumerate(sorted(wl_tickers)):
            col_idx = i % min(len(wl_tickers), 8)
            with fjern_cols[col_idx]:
                if st.button(f"❌ {ticker}", key=f"fjern_{ticker}"):
                    st.session_state.watchlist.discard(ticker)
                    lagre_watchlist(st.session_state.watchlist)
                    st.rerun()

    # ──────────────────────────────────────────────────────
    # FOOTER – SCOREFORKLARING
    # ──────────────────────────────────────────────────────
    st.markdown("---")
    with st.expander("ℹ️ Scoringmodell og setup-logikk"):
        st.markdown("""
**Poengmodell (0–10):**
| Poeng | Betingelse |
|-------|-----------|
| +2 | Kurs over SMA 200 |
| +1 | Kurs over SMA 50 |
| +1 | Nær SMA 50 (0–5 %) |
| +1 | RSI i sweet spot (40–65) |
| +1 | RSI ikke ekstrem (30–70) |
| +1 | Volum over snitt (ratio ≥ 1.0x) |
| +1 | Nær 20-dagers high (≥ −2 %) |
| +1 | God likviditet (snittvolum > 100k) |
| +1 | Positiv dagsendring |

**Setup-klassifisering:**
- 🟢 **Trend** — Over SMA200 + SMA50, RSI 40–70
- 🟡 **Pullback** — Over SMA200, nær SMA50 (−1 til 3 %), RSI 35–55
- 🔵 **Breakout** — Over SMA200, nær 20d high, volum over snitt, RSI > 50
- 🟣 **Momentum** — Over SMA200+SMA50, positiv dag, volum bekrefter, RSI > 50
- 🔴 **Extended** — Over SMA200+SMA50, RSI > 75, langt fra SMA50 (> 8 %)
- ⚪ **No setup** — Matcher ingen kriterier
        """)

    st.caption(f"Sist oppdatert: {datetime.now().strftime('%Y-%m-%d %H:%M')} | Data: yfinance (forsinkede kurser)")


if __name__ == "__main__":
    main()
