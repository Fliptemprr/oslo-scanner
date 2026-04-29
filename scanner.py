"""
Oslo Børs Swing Trading Scanner – v5
====================================
Swing Scanner v5 — Pullback i trend
Idégenerator for aksjer i positiv trend med rød/svak dag nær støtte.

Kjør:  streamlit run scanner.py
"""

import streamlit as st
from streamlit_autorefresh import st_autorefresh
import pandas as pd
import numpy as np  # noqa: F401  (pre-existing import, kept)
import yfinance as yf
import json
import time
import logging
import os
import tempfile
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Optional

# ──────────────────────────────────────────────────────────────
# LOGGING
# ──────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("scanner")

# ──────────────────────────────────────────────────────────────
# KONFIGURASJON & KONSTANTER
# ──────────────────────────────────────────────────────────────
WATCHLIST_FILE = Path("watchlist.json")

# Data fetching
BATCH_SIZE = 15
BATCH_DELAY = 5
HISTORY_DAYS = 300
RETRY_DELAY_PER_TICKER = 2
MIN_HISTORY_BARS = 50

# v5 thresholds
V5_MIN_AVG_VOLUME = 500_000
V5_RSI_EXTENDED = 70
V5_DIST_SMA50_EXTENDED = 6.0
V5_DAY_CHANGE_EXTENDED = 2.0
V5_PRIME_SUPPORT_MAX = 4.0
V5_PRIME_RESISTANCE_MIN = 3.0
V5_SECONDARY_SUPPORT_MAX = 8.0
V5_SECONDARY_RESISTANCE_MIN = 4.0
V5_RANGE_MIN_BUILDER = 6.0

OSLO_TICKERS = {
    "2020.OL": "2020 Bulkers",
    "5PG.OL": "5th Planet Games",
    "AASB.OL": "Aasen Sparebank",
    "ABG.OL": "Abg Sundal Collier",
    "ABL.OL": "Abl Group",
    "ABS.OL": "Arctic Bioscience",
    "ABTEC.OL": "Aqua Bio Techno",
    "ACED.OL": "Ace Digital",
    "ACR.OL": "Axactor",
    "ADS.OL": "Ads Maritime Hold",
    "AFG.OL": "Af Gruppen",
    "AFISH.OL": "Arctic Fish Holdin",
    "AFK.OL": "Arendals Fossekomp",
    "AGLX.OL": "Agilyx",
    "AIX.OL": "Ayfie Internationa",
    "AKAST.OL": "Akastor",
    "AKBM.OL": "Aker Biomarine",
    "AKER.OL": "Aker",
    "AKH.OL": "Aker Horizons",
    "AKOBO.OL": "Akobo Minerals",
    "AKRBP.OL": "Aker Bp",
    "AKSO.OL": "Aker Solutions",
    "AKVA.OL": "Akva Group",
    "ALNG.OL": "Awilco Lng",
    "ANDF.OL": "Andfjord Salmon Gr",
    "APR.OL": "Appear",
    "ARCH.OL": "Archer",
    "ARR.OL": "Arribatec Group",
    "ASA.OL": "Atlantic Sapphire",
    "ASAS.OL": "Atlantic Sapphi Tr",
    "ATEA.OL": "Atea",
    "AURG.OL": "Aurskog Sparebank",
    "AUSS.OL": "Austevoll Seafood",
    "AUTO.OL": "Autostore Holdings",
    "AZT.OL": "Arcticzymes Techno",
    "B2I.OL": "B2 Impact",
    "BAKKA.OL": "Bakkafrost",
    "BALT.OL": "Baltic Sea Prop",
    "BARRA.OL": "Barramundi Group",
    "BCS.OL": "Bergen Carbon Sol",
    "BEWI.OL": "Bewi",
    "BIEN.OL": "Bien Sparebank",
    "BMA.OL": "Byggma",
    "BNOR.OL": "Bluenord",
    "BONHR.OL": "Bonheur",
    "BOR.OL": "Borgestad",
    "BORR.OL": "Borr Drilling",
    "BOUV.OL": "Bouvet",
    "BRG.OL": "Borregaard",
    "BRUT.OL": "Bruton",
    "BSP.OL": "Black Sea Property",
    "BWE.OL": "Bw Energy Limited",
    "BWLPG.OL": "Bw Lpg",
    "BWO.OL": "Bw Offshore Ltd",
    "CADLR.OL": "Cadeler",
    "CAMBI.OL": "Cambi",
    "CAPSL.OL": "Capsol Technologi",
    "CAPT.OL": "Capital Tankers",
    "CAVEN.OL": "Cavendish Hydrogen",
    "CLOUD.OL": "Cloudberry Clean",
    "CMBTO.OL": "Cmb.tech",
    "CODE.OL": "Codelab Capital",
    "CONTX.OL": "Contextvision",
    "COSH.OL": "Constellation Oil",
    "CRNA.OL": "Circio Holding",
    "CRNAS.OL": "Circio Holding Tr",
    "CYVIZ.OL": "Cyviz",
    "DDRIL.OL": "Dolphin Drilling",
    "DELIA.OL": "Dellia Group",
    "DFENS.OL": "Fjord Defence Gr",
    "DNB.OL": "Dnb Bank",
    "DNO.OL": "Dno",
    "DOFG.OL": "Dof Group",
    "DSRT.OL": "Desert Control",
    "DVD.OL": "Deep Value Driller",
    "EAM.OL": "Eam Solar",
    "EIOF.OL": "Eidesvik Offshore",
    "ELABS.OL": "Elliptic Laborator",
    "ELIMP.OL": "ElektroimportØren",
    "ELK.OL": "Elkem",
    "ELMRA.OL": "Elmera Group",
    "ELO.OL": "Elopak",
    "EMGS.OL": "Electromagnet Geo",
    "ENDUR.OL": "EndÚr",
    "ENERG.OL": "Energeia",
    "ENH.OL": "Sed Energy Holding",
    "ENSU.OL": "Ensurge Micropower",
    "ENTRA.OL": "Entra",
    "ENVIP.OL": "Envipco Holding",
    "EPR.OL": "Europris",
    "EQNR.OL": "Equinor",
    "EQVA.OL": "Eqva",
    "EXTX.OL": "Exact Therapeutics",
    "FFSB.OL": "Flekkefjord Spareb",
    "FRO.OL": "Frontline",
    "GEM.OL": "Green Minerals",
    "GENO.OL": "General Oceans",
    "GENT.OL": "Gentian Diagnostic",
    "GEOS.OL": "Golden Energy Off",
    "GIGA.OL": "Gigante Salmon",
    "GJF.OL": "Gjensidige Forsikr",
    "GKP.OL": "Gulf Keystone Pet",
    "GOD.OL": "Goodtech",
    "GRONG.OL": "Grong Sparebank",
    "GSF.OL": "Grieg Seafood",
    "GYL.OL": "Gyldendal",
    "HAFNI.OL": "Hafnia Limited",
    "HAUTO.OL": "HÖegh Autoliners",
    "HAV.OL": "Hav Group",
    "HAVI.OL": "Havila Shipping",
    "HBC.OL": "Hofseth Biocare",
    "HDLY.OL": "Huddly",
    "HELG.OL": "Spbk1 Helgeland",
    "HERMA.OL": "Hermana Holding",
    "HEX.OL": "Hexagon Composites",
    "HGSB.OL": "Haugesund Spb",
    "HKY.OL": "Havila Kystruten",
    "HPUR.OL": "Hexagon Purus",
    "HSHP.OL": "Himalaya Shipping",
    "HSPG.OL": "HØland Og Setskog",
    "HUDL.OL": "Huddlestock Fintec",
    "HUNT.OL": "Hunter Group",
    "HYN.OL": "Hynion",
    "HYPRO.OL": "Hydrogenpro",
    "IDEX.OL": "Idex Biometrics",
    "INDCT.OL": "Induct",
    "INIFY.OL": "Inify Laboratories",
    "ININ.OL": "Inin Group",
    "INSTA.OL": "Instabank",
    "IOX.OL": "Interoil Expl Prod",
    "ISLAX.OL": "Icelandic Salmon",
    "ITERA.OL": "Itera",
    "IWS.OL": "Integrated Wind So",
    "JACK.OL": "Jacktel",
    "JAREN.OL": "JÆren Sparebank",
    "JIN.OL": "Jinhui Shipp Trans",
    "KCC.OL": "Klaveness Combinat",
    "KID.OL": "Kid",
    "KING.OL": "The Kingfish Comp",
    "KIT.OL": "Kitron",
    "KLDVK.OL": "Kaldvik",
    "KMCP.OL": "Kmc Properties",
    "KOA.OL": "Kongsberg Automot",
    "KOG.OL": "Kongsberg Gruppen",
    "KOMPL.OL": "Komplett",
    "KRAB.OL": "Kraft Bank",
    "LIFE.OL": "Lifecare",
    "LIFES.OL": "Lifecare Tr2",
    "LINK.OL": "Link Mobility Grp",
    "LOKO.OL": "Lokotech Group",
    "LSG.OL": "LerØy Seafood Gp",
    "LUMI.OL": "Lumi Gruppen",
    "LYTIX.OL": "Lytix Biopharma",
    "MAS.OL": "MÅsØval",
    "MEDI.OL": "Medistim",
    "MELG.OL": "Melhus Sparebank",
    "MGN.OL": "Magnora",
    "MING.OL": "Sparebank 1 Smn",
    "MORG.OL": "Sparebanken MØre",
    "MORLD.OL": "Moreld",
    "MOWI.OL": "Mowi",
    "MPCC.OL": "Mpc Container Ship",
    "MPCES.OL": "Mpc Energy Solutio",
    "MULTI.OL": "Multiconsult",
    "MVE.OL": "Matvareexpressen",
    "MVW.OL": "M Vest Water",
    "NAPA.OL": "Napatech",
    "NAS.OL": "Norwegian Air Shut",
    "NAVA.OL": "Navamedic",
    "NBX.OL": "Norwegian Block Ex",
    "NCOD.OL": "Norcod",
    "NEL.OL": "Nel",
    "NEXT.OL": "Next Biometrics Gp",
    "NHY.OL": "Norsk Hydro",
    "NISB.OL": "Nidaros Sparebank",
    "NKR.OL": "Nekkar",
    "NOAP.OL": "Nordic Aqua Part",
    "NOD.OL": "Nordic Semiconduc",
    "NOFIN.OL": "Nordic Financials",
    "NOHAL.OL": "Nordic Halibut",
    "NOL.OL": "Northern Ocean Ltd",
    "NOM.OL": "Nordic Mining",
    "NONG.OL": "Spbk1 Nord-norge",
    "NORAM.OL": "Noram Drilling",
    "NORBT.OL": "Norbit",
    "NORCO.OL": "Norconsult",
    "NORDH.OL": "Nordhealth A-aksje",
    "NORSE.OL": "Norse Atlantic",
    "NORTH.OL": "North Energy",
    "NOSN.OL": "Nos Nova",
    "NRC.OL": "Nrc Group",
    "NSKOG.OL": "Norske Skog",
    "NTG.OL": "Nordic Technology",
    "NTI.OL": "Norsk Titanium",
    "NYKD.OL": "Nykode Therapeutic",
    "OBSRV.OL": "Observe Medical",
    "OCEAN.OL": "Ocean Geoloop",
    "ODF.OL": "Odfjell Ser. A",
    "ODFB.OL": "Odfjell Ser. B",
    "ODL.OL": "Odfjell Drilling",
    "OET.OL": "Okeanis Eco Tanker",
    "OKEA.OL": "Okea",
    "OMDA.OL": "Omda",
    "ONCIN.OL": "Oncoinvent",
    "ORK.OL": "Orkla",
    "OSUN.OL": "Ocean Sun",
    "OTEC.OL": "Otello Corporation",
    "OTL.OL": "Odfjell Technology",
    "OTOVO.OL": "Otovo",
    "PARB.OL": "Pareto Bank",
    "PCIB.OL": "Pci Biotech Hold",
    "PEN.OL": "Panoro Energy",
    "PEXIP.OL": "Pexip Holding",
    "PHO.OL": "Photocure",
    "PLGC.OL": "Pelagic Credit",
    "PLSV.OL": "Paratus Energy Ser",
    "PLT.OL": "Polight",
    "PNOR.OL": "Petronor E&p",
    "POL.OL": "Polaris Media",
    "PPG.OL": "Pioneer Property",
    "PROT.OL": "Protector Forsikrg",
    "PROXI.OL": "Proximar Seafood",
    "PRS.OL": "Prosafe",
    "PRYME.OL": "Pryme",
    "PSE.OL": "Petrolia",
    "PUBLI.OL": "Public Property In",
    "PYRUM.OL": "Pyrum Innovations",
    "QEC.OL": "Questerre Energy",
    "RANA.OL": "Rana Gruber",
    "REACH.OL": "Reach Subsea",
    "RECSI.OL": "Rec Silicon",
    "REFL.OL": "Refuels",
    "RING.OL": "Spbk1 Ringerike",
    "RIVER.OL": "River Tech",
    "ROGS.OL": "Rogaland Sparebank",
    "ROM.OL": "Romreal",
    "ROMER.OL": "Romerike Sparebk",
    "SAGA.OL": "Saga Pure",
    "SALM.OL": "Salmar",
    "SALME.OL": "Salmon Evolution",
    "SATS.OL": "Sats",
    "SB1NO.OL": "Sparebank 1 SØr-n",
    "SB68.OL": "Sparbnk 68 Gr Nord",
    "SBNOR.OL": "Sparebanken Norge",
    "SBO.OL": "Selvaag Bolig",
    "SCANA.OL": "Scana",
    "SCATC.OL": "Scatec",
    "SDSD.OL": "S.d. Standard Etc",
    "SEA1.OL": "Sea1 Offshore",
    "SKAND.OL": "Skandia Greenpower",
    "SKUE.OL": "Skue Sparebank",
    "SMOP.OL": "Smartoptics Group",
    "SNI.OL": "Stolt-nielsen",
    "SNOR.OL": "Spbk 1 NordmØre",
    "SNTIA.OL": "Sentia",
    "SOAG.OL": "Spbk1 Østfold Ake",
    "SOFF.OL": "Solstad Offshore",
    "SOFTX.OL": "Softox Solutions",
    "SOGN.OL": "Sogn Sparebank",
    "SOMA.OL": "Solstad Maritime",
    "SPOG.OL": "Sparebanken Øst",
    "SPOL.OL": "Spbk 1 Østlandet",
    "STB.OL": "Storebrand",
    "STECH.OL": "Soiltech",
    "STRO.OL": "Strongpoint",
    "STST.OL": "Stainless Tankers",
    "SUBC.OL": "Subsea 7",
    "SWON.OL": "Softwareone Hold",
    "TECH.OL": "Techstep",
    "TEKNA.OL": "Tekna Holding",
    "TEL.OL": "Telenor",
    "TGS.OL": "Tgs",
    "TIETO.OL": "Tietoevry",
    "TINDE.OL": "Tinde Sparebank",
    "TOM.OL": "Tomra Systems",
    "TRMED.OL": "Thor Medical",
    "TRSB.OL": "TrØndelag Spbk",
    "VAR.OL": "VÅr Energi",
    "VDI.OL": "Vantage Drilling I",
    "VEI.OL": "Veidekke",
    "VEND.OL": "Vend Marketplaces",
    "VISTN.OL": "Vistin Pharma",
    "VOW.OL": "Vow",
    "VTURA.OL": "Ventura Offshore",
    "VVL.OL": "Voss Veksel Ogland",
    "WAWI.OL": "Wallenius Wilhelms",
    "WEST.OL": "Western Bulk Chart",
    "WSTEP.OL": "Webstep",
    "WWI.OL": "Wilh. Wilhelmsen A",
    "WWIB.OL": "Wilh. Wilhelmsen B",
    "XPLRA.OL": "Xplora Technologie",
    "YAR.OL": "Yara International",
    "ZAL.OL": "Zalaris",
    "ZAP.OL": "Zaptec",
    "ZENA.OL": "Zenith Energy",
    "ZLNA.OL": "Zelluna",
}

# ──────────────────────────────────────────────────────────────
# RSI
# ──────────────────────────────────────────────────────────────

def beregn_rsi(serie: pd.Series, periode: int = 14) -> pd.Series:
    delta = serie.diff()
    gevinst = delta.where(delta > 0, 0.0)
    tap = -delta.where(delta < 0, 0.0)
    avg_g = gevinst.ewm(alpha=1/periode, min_periods=periode).mean()
    avg_t = tap.ewm(alpha=1/periode, min_periods=periode).mean()
    rs = avg_g / avg_t
    return 100.0 - (100.0 / (1.0 + rs))


# ──────────────────────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────────────────────

def safe_pct(num: float, denom: Optional[float]) -> Optional[float]:
    """Returner prosentforskjell, eller None hvis denominator er ugyldig."""
    if denom is None or denom == 0:
        return None
    return round(((num - denom) / denom) * 100, 2)


# ──────────────────────────────────────────────────────────────
# DATAHENTING — split i mindre funksjoner
# ──────────────────────────────────────────────────────────────

def _download_batch(batch: list, session, start, end) -> dict:
    """Last ned én batch, returner dict {ticker: df}."""
    result = {}
    for forsok in range(2):
        try:
            raw = yf.download(
                batch, start=start, end=end, progress=False,
                auto_adjust=True, timeout=30, group_by="ticker",
                threads=False, session=session,
            )
            if raw is None or raw.empty:
                return result
            for t in batch:
                try:
                    d = raw.copy() if len(batch) == 1 else raw[t].copy()
                    d = d.dropna(how="all")
                    if len(d) >= MIN_HISTORY_BARS:
                        result[t] = d
                except (KeyError, TypeError):
                    continue
            return result
        except Exception as e:
            if forsok == 0:
                time.sleep(BATCH_DELAY * 2)
            else:
                log.warning(f"Batch-feil ({type(e).__name__}): {e}")
    return result


def _retry_missing(missing: list, session, start, end) -> dict:
    """Prøv manglende tickers én og én med pause mellom."""
    result = {}
    for t in missing:
        try:
            raw = yf.download(
                t, start=start, end=end, progress=False,
                auto_adjust=True, timeout=20, session=session,
            )
            if raw is not None and not raw.empty:
                if isinstance(raw.columns, pd.MultiIndex):
                    raw.columns = raw.columns.get_level_values(0)
                raw = raw.dropna(how="all")
                if len(raw) >= MIN_HISTORY_BARS:
                    result[t] = raw
            time.sleep(RETRY_DELAY_PER_TICKER)
        except Exception as e:
            log.debug(f"[{t}] retry failed: {type(e).__name__}: {e}")
            continue
    return result


def _compute_metrics(ticker: str, df: pd.DataFrame, ticker_dict: dict) -> Optional[dict]:
    """Beregn alle v5-indikatorer for én aksje. Returner None hvis data er utilstrekkelig."""
    try:
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        close = df["Close"].dropna()
        vol = df["Volume"].dropna()
        high = df["High"].dropna()
        low = df["Low"].dropna()
        if len(close) < MIN_HISTORY_BARS:
            return None

        price = float(close.iloc[-1])
        prev_close = float(close.iloc[-2]) if len(close) >= 2 else price

        # Moving averages & RSI
        sma20 = float(close.iloc[-20:].mean()) if len(close) >= 20 else None
        sma50 = float(close.iloc[-50:].mean()) if len(close) >= 50 else None
        sma200 = float(close.iloc[-200:].mean()) if len(close) >= 200 else None
        rsi = float(beregn_rsi(close, 14).iloc[-1]) if len(close) >= 20 else None

        # Volume
        vol_today = float(vol.iloc[-1])
        avg_vol = float(vol.tail(20).mean())
        vol_ratio = round(vol_today / avg_vol, 2) if avg_vol > 0 else 0.0

        # Day change %
        pct_change = safe_pct(price, prev_close)
        if pct_change is None:
            pct_change = 0.0

        # Distances to SMAs
        dist_sma20 = safe_pct(price, sma20)
        dist_sma50 = safe_pct(price, sma50)
        dist_sma200 = safe_pct(price, sma200)

        # 20D high/low/range
        high_20d = float(high.tail(20).max())
        low_20d = float(low.tail(20).min())
        range_20d = round(((high_20d - low_20d) / low_20d) * 100, 2) if low_20d > 0 else None

        # Distance to 20D high/low (v5 def: positive when below high / above low)
        dist_20d_high = round(((high_20d - price) / price) * 100, 2) if price > 0 else None
        dist_20d_low = round(((price - low_20d) / low_20d) * 100, 2) if low_20d > 0 else None

        # Support: max of {SMA20, SMA50, 20D low} where candidate ≤ price
        support_candidates = []
        if sma20 is not None and sma20 <= price:
            support_candidates.append(sma20)
        if sma50 is not None and sma50 <= price:
            support_candidates.append(sma50)
        if low_20d <= price:
            support_candidates.append(low_20d)
        if support_candidates:
            nearest_support = max(support_candidates)
            support_pct = round(((price - nearest_support) / nearest_support) * 100, 2)
        else:
            support_pct = None

        # Resistance: 20D high
        resistance_pct = round(((high_20d - price) / price) * 100, 2) if price > 0 else None

        # 3D / 5D change %
        change_3d = safe_pct(price, float(close.iloc[-4])) if len(close) >= 4 else None
        change_5d = safe_pct(price, float(close.iloc[-6])) if len(close) >= 6 else None

        # Candles last 5 days
        candles = ""
        last5 = df.tail(5)
        if "Open" in df.columns:
            for _, row in last5.iterrows():
                o = row.get("Open")
                c = row.get("Close")
                if pd.isna(o) or pd.isna(c):
                    candles += "⚪"
                elif c > o:
                    candles += "🟢"
                elif c < o:
                    candles += "🔴"
                else:
                    candles += "⚪"

        return {
            "Ticker": ticker.replace(".OL", ""),
            "ticker_yf": ticker,
            "Navn": ticker_dict.get(ticker, ticker),
            "Kurs": round(price, 2),
            "% i dag": pct_change,
            "Volum": int(vol_today),
            "Snittvolum 20D": int(avg_vol),
            "Vol Ratio": vol_ratio,
            "SMA20": round(sma20, 2) if sma20 else None,
            "SMA50": round(sma50, 2) if sma50 else None,
            "SMA200": round(sma200, 2) if sma200 else None,
            "Avst SMA20 %": dist_sma20,
            "Avst SMA50 %": dist_sma50,
            "Avst SMA200 %": dist_sma200,
            "RSI 14": round(rsi, 1) if rsi else None,
            "20D High": round(high_20d, 2),
            "20D Low": round(low_20d, 2),
            "20D Range %": range_20d,
            "Avst 20D High %": dist_20d_high,
            "Avst 20D Low %": dist_20d_low,
            "Støtte %": support_pct,
            "Motstand %": resistance_pct,
            "3D %": change_3d,
            "5D %": change_5d,
            "Candles 5D": candles,
        }
    except Exception as e:
        log.warning(f"[{ticker}] compute failed: {type(e).__name__}: {e}")
        return None


def _lag_session():
    """Curl_cffi Chrome-session for å unngå Yahoo TLS rate limit."""
    try:
        from curl_cffi import requests as cffi_requests
        return cffi_requests.Session(impersonate="chrome")
    except ImportError:
        log.warning("curl_cffi ikke installert, bruker default session (kan gi 429)")
        return None


@st.cache_data(ttl=600, show_spinner=False)
def hent_data(ticker_dict: dict) -> pd.DataFrame:
    """Last ned historikk for alle aksjer og beregn v5-indikatorer."""
    tickers_liste = list(ticker_dict.keys())
    start = datetime.now() - timedelta(days=HISTORY_DAYS)
    end = datetime.now()
    session = _lag_session()

    alle_data = {}
    progress = st.progress(0, text="Henter data...")
    total_b = (len(tickers_liste) - 1) // BATCH_SIZE + 1

    # ── Batch download ──
    for bn in range(0, len(tickers_liste), BATCH_SIZE):
        batch = tickers_liste[bn:bn + BATCH_SIZE]
        bi = bn // BATCH_SIZE + 1
        progress.progress(
            min(bi / total_b, 1.0),
            text=f"Batch {bi}/{total_b} — {min(bn + BATCH_SIZE, len(tickers_liste))}/{len(tickers_liste)}"
        )
        alle_data.update(_download_batch(batch, session, start, end))
        if bn + BATCH_SIZE < len(tickers_liste):
            time.sleep(BATCH_DELAY)

    # ── Retry manglende én og én ──
    mangler = [t for t in tickers_liste if t not in alle_data]
    if mangler:
        progress.progress(0.95, text=f"Retry {len(mangler)} manglende...")
        time.sleep(BATCH_DELAY)
        alle_data.update(_retry_missing(mangler, session, start, end))

    progress.empty()
    log.info(f"Lastet ned {len(alle_data)}/{len(tickers_liste)} aksjer")

    # ── Beregn indikatorer ──
    resultater = []
    for ticker, df in alle_data.items():
        metrics = _compute_metrics(ticker, df, ticker_dict)
        if metrics is not None:
            resultater.append(metrics)

    if not resultater:
        return pd.DataFrame()

    df_r = pd.DataFrame(resultater)
    df_r = compute_v5_status(df_r)
    return df_r


# ──────────────────────────────────────────────────────────────
# V5 STATUS / VIEWS / SORT / FORMAT
# ──────────────────────────────────────────────────────────────

def compute_v5_status(df: pd.DataFrame) -> pd.DataFrame:
    """Status-rekkefølge: EXTENDED → PRIME → SECONDARY → SKIP."""
    statuses = []
    for _, r in df.iterrows():
        rsi = r.get("RSI 14")
        dist_sma50 = r.get("Avst SMA50 %")
        day_change = r.get("% i dag")
        sma50 = r.get("SMA50")
        sma200 = r.get("SMA200")
        kurs = r.get("Kurs")
        support_pct = r.get("Støtte %")
        resistance_pct = r.get("Motstand %")

        # 1. EXTENDED (precedence)
        if (rsi is not None and rsi > V5_RSI_EXTENDED) \
                or (dist_sma50 is not None and dist_sma50 > V5_DIST_SMA50_EXTENDED) \
                or (day_change is not None and day_change > V5_DAY_CHANGE_EXTENDED):
            statuses.append("EXTENDED")
            continue

        in_trend = (
            sma200 is not None and kurs is not None and kurs > sma200
            and sma50 is not None and kurs > sma50
        )

        # 2. PRIME
        if (in_trend
                and day_change is not None and day_change <= 0
                and support_pct is not None and 0 <= support_pct <= V5_PRIME_SUPPORT_MAX
                and resistance_pct is not None and resistance_pct >= V5_PRIME_RESISTANCE_MIN):
            statuses.append("PRIME")
            continue

        # 3. SECONDARY
        if (in_trend
                and day_change is not None and day_change <= 0
                and support_pct is not None and V5_PRIME_SUPPORT_MAX < support_pct <= V5_SECONDARY_SUPPORT_MAX
                and resistance_pct is not None and resistance_pct >= V5_SECONDARY_RESISTANCE_MIN):
            statuses.append("SECONDARY")
            continue

        # 4. SKIP
        statuses.append("SKIP")

    df["Status"] = statuses
    return df


def apply_v5_view_filter(df: pd.DataFrame, view: str, min_avg_vol: int) -> pd.DataFrame:
    """Filtrer DataFrame for valgt visning."""
    if df.empty:
        return df
    f = df.copy()
    in_trend = (
        f["SMA200"].notna() & (f["Kurs"] > f["SMA200"])
        & f["SMA50"].notna() & (f["Kurs"] > f["SMA50"])
    )
    if view == "Today Pullback":
        f = f[in_trend & (f["Snittvolum 20D"] >= min_avg_vol) & (f["% i dag"] <= 0)]
    elif view == "Watchlist Builders":
        f = f[in_trend & (f["Snittvolum 20D"] >= min_avg_vol)
              & f["20D Range %"].notna() & (f["20D Range %"] >= V5_RANGE_MIN_BUILDER)]
    elif view == "Extended / Wait":
        cond_ext = (
            (f["RSI 14"].notna() & (f["RSI 14"] > V5_RSI_EXTENDED))
            | (f["Avst SMA50 %"].notna() & (f["Avst SMA50 %"] > V5_DIST_SMA50_EXTENDED))
            | (f["% i dag"].notna() & (f["% i dag"] > V5_DAY_CHANGE_EXTENDED))
        )
        f = f[in_trend & cond_ext]
    return f


def sort_v5(df: pd.DataFrame, view: str) -> pd.DataFrame:
    """Sorter etter visning."""
    if df.empty:
        return df
    f = df.copy()
    if view == "Today Pullback":
        order = {"PRIME": 0, "SECONDARY": 1, "EXTENDED": 2, "SKIP": 3}
        f["_rank"] = f["Status"].map(order).fillna(99)
        f = f.sort_values(
            ["_rank", "Støtte %", "Motstand %"],
            ascending=[True, True, False]
        ).drop(columns=["_rank"])
    elif view == "Watchlist Builders":
        f = f.sort_values(["Støtte %", "Motstand %"], ascending=[True, False])
    elif view == "Extended / Wait":
        f = f.sort_values(["Avst SMA50 %", "RSI 14"], ascending=[False, False])
    return f.reset_index(drop=True)


STATUS_EMOJI = {
    "PRIME": "🟢 PRIME",
    "SECONDARY": "🟡 SECONDARY",
    "EXTENDED": "🟠 EXTENDED",
    "SKIP": "⚫ SKIP",
}

V5_DISPLAY_COLS = [
    "Ticker", "Navn", "Kurs", "% i dag",
    "Volum", "Snittvolum 20D", "Vol Ratio",
    "SMA20", "SMA50", "SMA200",
    "Avst SMA20 %", "Avst SMA50 %", "Avst SMA200 %",
    "RSI 14",
    "20D High", "20D Low", "20D Range %",
    "Avst 20D High %", "Avst 20D Low %",
    "Støtte %", "Motstand %",
    "3D %", "5D %", "Candles 5D",
    "Status",
]


def format_v5_table(df: pd.DataFrame) -> pd.DataFrame:
    """Formater for visning. Klikkbar Navn-link, Status med emoji, volum med mellomrom."""
    vis = df.copy()
    # Yahoo Finance link med #navn fragment så LinkColumn viser navn som linktekst
    vis["Navn"] = vis.apply(
        lambda r: f"https://finance.yahoo.com/quote/{r['ticker_yf']}#{r['Navn']}",
        axis=1,
    )
    vis["Status"] = vis["Status"].apply(lambda x: STATUS_EMOJI.get(x, x))
    vis["Volum"] = vis["Volum"].apply(lambda x: f"{x:,.0f}".replace(",", " "))
    vis["Snittvolum 20D"] = vis["Snittvolum 20D"].apply(lambda x: f"{x:,.0f}".replace(",", " "))
    return vis[[c for c in V5_DISPLAY_COLS if c in vis.columns]]


# ──────────────────────────────────────────────────────────────
# WATCHLIST
# ──────────────────────────────────────────────────────────────

def last_watchlist() -> set:
    """Les watchlist fra disk, returner tom set ved feil."""
    if WATCHLIST_FILE.exists():
        try:
            with open(WATCHLIST_FILE) as f:
                return set(json.load(f))
        except (json.JSONDecodeError, OSError) as e:
            log.warning(f"Kunne ikke lese watchlist: {e}")
            return set()
    return set()


def lagre_watchlist(tickers: set) -> None:
    """Atomic write: skriv til tempfil, rename deretter."""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", delete=False, dir=".",
            suffix=".tmp", encoding="utf-8"
        ) as f:
            json.dump(sorted(list(tickers)), f, indent=2, ensure_ascii=False)
            tmp_path = f.name
        os.replace(tmp_path, WATCHLIST_FILE)
    except OSError as e:
        log.error(f"Kunne ikke lagre watchlist: {e}")


# ──────────────────────────────────────────────────────────────
# UI HELPERS
# ──────────────────────────────────────────────────────────────

NAVN_LINK = st.column_config.LinkColumn(
    "Navn",
    help="Klikk for å åpne i Yahoo Finance",
    display_text=r"#(.+)$",
)


def _vis_metrics(view_df: pd.DataFrame) -> None:
    """Vis metric cards for valgt visning."""
    n = len(view_df)
    sup_vals = view_df["Støtte %"].dropna() if n else pd.Series(dtype=float)
    res_vals = view_df["Motstand %"].dropna() if n else pd.Series(dtype=float)

    c = st.columns(7)
    c[0].metric("Kandidater", n)
    c[1].metric("🟢 PRIME", int((view_df["Status"] == "PRIME").sum()) if n else 0)
    c[2].metric("🟡 SECONDARY", int((view_df["Status"] == "SECONDARY").sum()) if n else 0)
    c[3].metric("🟠 EXTENDED", int((view_df["Status"] == "EXTENDED").sum()) if n else 0)
    c[4].metric("Avg Støtte %", f"{sup_vals.mean():.1f}" if not sup_vals.empty else "—")
    c[5].metric("Avg Motstand %", f"{res_vals.mean():.1f}" if not res_vals.empty else "—")
    c[6].metric("Røde i dag", int((view_df["% i dag"] < 0).sum()) if n else 0)


def _render_view(df: pd.DataFrame, view: str, min_avg_vol: int) -> None:
    """Filtrer, sorter, vis metrics + tabell + watchlist-knapper for én visning."""
    view_df = apply_v5_view_filter(df, view, min_avg_vol)
    view_df = sort_v5(view_df, view)
    _vis_metrics(view_df)

    if view_df.empty:
        st.info("Ingen kandidater i denne visningen.")
        return

    st.dataframe(
        format_v5_table(view_df),
        width="stretch",
        hide_index=True,
        height=min(len(view_df) * 38 + 40, 700),
        column_config={"Navn": NAVN_LINK},
    )

    st.markdown("**Watchlist:**")
    nc = min(len(view_df), 8)
    wc = st.columns(nc)
    for i, (_, r) in enumerate(view_df.iterrows()):
        tk = r["Ticker"]
        with wc[i % nc]:
            iw = tk in st.session_state.watchlist
            if st.button(f"{'⭐' if iw else '☆'} {tk}", key=f"wl_{view}_{tk}"):
                st.session_state.watchlist.discard(tk) if iw else st.session_state.watchlist.add(tk)
                lagre_watchlist(st.session_state.watchlist)
                st.rerun()


# ──────────────────────────────────────────────────────────────
# STREAMLIT APP
# ──────────────────────────────────────────────────────────────

def main() -> None:
    """Streamlit hovedapp."""
    st.set_page_config(page_title="Oslo Børs Scanner v5", page_icon="📈", layout="wide")
    st.title("📈 Swing Scanner v5 — Pullback i trend")
    st.caption("Idégenerator for aksjer i positiv trend med rød/svak dag nær støtte. Status er ikke kjøpssignal — kandidater må vurderes manuelt.")

    refresh_opts = {"Av": 0, "5 min": 5, "10 min": 10, "15 min": 15, "30 min": 30}
    ct, cr = st.columns([3, 1])
    with ct:
        st.caption(f"{len(OSLO_TICKERS)} aksjer på Oslo Børs")
    with cr:
        rv = st.selectbox("Auto-refresh", list(refresh_opts.keys()), index=3, label_visibility="collapsed")
    rm = refresh_opts[rv]
    if rm > 0:
        t = st_autorefresh(interval=rm * 60 * 1000, key="auto_refresh")
        if t and t > 0:
            st.cache_data.clear()

    if "watchlist" not in st.session_state:
        st.session_state.watchlist = last_watchlist()
    if "data" not in st.session_state:
        st.session_state.data = None

    # ── Controls ──
    st.markdown("---")
    cc1, cc2 = st.columns([1, 2])
    with cc1:
        scan = st.button("🔄 Scan nå", type="primary", width="stretch")
    with cc2:
        min_avg_vol = st.number_input(
            "Min Snittvolum 20D",
            min_value=V5_MIN_AVG_VOLUME,
            value=V5_MIN_AVG_VOLUME,
            step=100_000,
        )

    if scan:
        st.cache_data.clear()
        st.session_state.data = hent_data(OSLO_TICKERS)
        st.success(f"✅ Skannet {len(st.session_state.data)} aksjer")
    elif st.session_state.data is None:
        st.session_state.data = hent_data(OSLO_TICKERS)

    df = st.session_state.data
    if df is None or df.empty:
        st.warning("Ingen data. Trykk «Scan nå».")
        return

    # ── Tabs ──
    st.markdown("---")
    tab1, tab2, tab3 = st.tabs([
        "🟢 Today Pullback",
        "📊 Watchlist Builders",
        "🟠 Extended / Wait",
    ])
    with tab1:
        _render_view(df, "Today Pullback", min_avg_vol)
    with tab2:
        _render_view(df, "Watchlist Builders", min_avg_vol)
    with tab3:
        _render_view(df, "Extended / Wait", min_avg_vol)

    # ── Watchlist ──
    st.markdown("---")
    st.subheader(f"⭐ Watchlist ({len(st.session_state.watchlist)})")
    if not st.session_state.watchlist:
        st.info("Tom watchlist.")
    else:
        wd = df[df["Ticker"].isin(st.session_state.watchlist)]
        if wd.empty:
            st.warning("Ikke funnet i siste scan.")
        else:
            wd = sort_v5(wd, "Today Pullback")
            st.dataframe(
                format_v5_table(wd),
                width="stretch",
                hide_index=True,
                column_config={"Navn": NAVN_LINK},
            )
        nc = min(len(st.session_state.watchlist), 8)
        fc2 = st.columns(nc)
        for i, tk in enumerate(sorted(st.session_state.watchlist)):
            with fc2[i % nc]:
                if st.button(f"❌ {tk}", key=f"rm_{tk}"):
                    st.session_state.watchlist.discard(tk)
                    lagre_watchlist(st.session_state.watchlist)
                    st.rerun()

    # ── Footer ──
    st.markdown("---")
    with st.expander("ℹ️ v5 — Status-regler"):
        st.markdown("""
**Status-rekkefølge (EXTENDED → PRIME → SECONDARY → SKIP):**

| Status | Regel |
|--------|-------|
| 🟠 EXTENDED | RSI 14 > 70, eller Avst SMA50 > 6 %, eller % i dag > 2 % |
| 🟢 PRIME | Over SMA200 og SMA50, % i dag ≤ 0, Støtte 0–4 %, Motstand ≥ 3 % |
| 🟡 SECONDARY | Over SMA200 og SMA50, % i dag ≤ 0, Støtte 4–8 %, Motstand ≥ 4 % |
| ⚫ SKIP | alt annet |

**Visninger:**
- **Today Pullback** — Hovedvisning. Trend + Snittvolum 20D ≥ 500k + % i dag ≤ 0. Sortert: status → Støtte ↑ → Motstand ↓.
- **Watchlist Builders** — Trend + Snittvolum 20D ≥ 500k + 20D Range ≥ 6 %. Volatile aksjer for swing-tracking.
- **Extended / Wait** — Aksjer i trend som er overstrukket. Vent på pullback.

**Status er ikke kjøpssignal.** Det er en kandidat. Må vurderes manuelt.
        """)

    oslo_tid = datetime.now(ZoneInfo("Europe/Oslo")).strftime("%Y-%m-%d %H:%M")
    st.caption(f"Oppdatert: {oslo_tid} (Oslo) | {len(OSLO_TICKERS)} aksjer | Swing Scanner v5 — Pullback i trend")


if __name__ == "__main__":
    main()
