"""
Oslo Børs Swing Trading Scanner – v4.1
========================================
296 aksjer · Entry Readiness · Trade Signal · Volume Trend
Trend 1D · Support/Resistance · Late Move · Presets

Refactored: named constants, split hent_data, fixed Trend 1H bug,
atomic watchlist writes, better error logging.

Kjør:  streamlit run scanner.py
"""

import streamlit as st
from streamlit_autorefresh import st_autorefresh
import pandas as pd
import numpy as np
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

# Volume thresholds
DEFAULT_MIN_AVG_VOLUME = 200_000
MIN_TRADING_VOLUME = 500_000          # "ekte" likviditet for Entry Readiness
VOL_RATIO_INCREASING = 1.5            # Vol Trend: Increasing
VOL_RATIO_FLAT_MIN = 0.8              # Vol Trend: Flat lower bound
VOL_RATIO_CONFIRMATION = 1.0          # BUY/WATCH krever dette
VOL_RATIO_WATCH_MIN = 0.7             # WATCH min vol ratio
VOL_RATIO_STRONG = 1.2                # Score +2 threshold

# RSI
RSI_OVERSOLD = 30                     # SKIP hvis under
RSI_OVERBOUGHT = 75                   # EXTENDED hvis over
RSI_READY_MIN, RSI_READY_MAX = 40, 60
RSI_WAIT_MIN, RSI_WAIT_MAX = 35, 70
RSI_SETUP_TREND_MIN, RSI_SETUP_TREND_MAX = 40, 70
RSI_PULLBACK_MIN, RSI_PULLBACK_MAX = 35, 55
RSI_EARLY_PB_MIN, RSI_EARLY_PB_MAX = 40, 50

# SMA50 avstand (%)
SMA50_READY_MIN, SMA50_READY_MAX = -2.0, 2.0
SMA50_WAIT_MIN, SMA50_WAIT_MAX = -5.0, 5.0
SMA50_PULLBACK_MIN, SMA50_PULLBACK_MAX = -2.0, 1.0
SMA50_EXTENDED_PCT = 8.0              # EXTENDED hvis >8% over SMA50
SMA50_BREAKOUT_MAX = 5.0              # Breakout: maks så langt over SMA50
SMA50_NEAR_HIGH_PCT = 5.0             # EXTENDED: nær 20d high OG >5% over SMA50
SMA50_SCORE_NEAR = 2.0                # Score +2 hvis innenfor ±2%

# 20d high
DIST_HIGH_NEAR_HIGH = -1.0            # innenfor 1% av high
DIST_HIGH_BREAKOUT = -2.0             # Breakout: innenfor 2% av high

# Trend 1H proxy
TREND_1H_UP_THRESHOLD = 1.002         # +0.2% over dagens åpning
TREND_1H_DOWN_THRESHOLD = 0.998       # -0.2% under dagens åpning

# Score
SCORE_MAX = 10
MOMENTUM_PCT_MIN = 0.5                # % i dag for Momentum-setup

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
# DATAHENTING (curl_cffi + batch + retry)
# ──────────────────────────────────────────────────────────────

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
    """Beregn alle indikatorer for én aksje. Returner None hvis data er utilstrekkelig."""
    try:
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        close = df["Close"].dropna()
        vol = df["Volume"].dropna()
        high = df["High"].dropna()
        low = df["Low"].dropna()
        if len(close) < MIN_HISTORY_BARS:
            return None

        # ── Kursdata ──
        price = float(close.iloc[-1])
        prev_close = float(close.iloc[-2]) if len(close) >= 2 else price
        day_low = float(low.iloc[-1])
        # Fix: bruk ekte dagens åpning, ikke forrige close
        day_open = float(df["Open"].iloc[-1]) if "Open" in df.columns else prev_close

        # ── Moving averages & RSI ──
        sma200 = float(close.iloc[-200:].mean()) if len(close) >= 200 else None
        sma50 = float(close.iloc[-50:].mean()) if len(close) >= 50 else None
        rsi = float(beregn_rsi(close, 14).iloc[-1]) if len(close) >= 20 else None

        # ── Volum ──
        vol_today = float(vol.iloc[-1])
        avg_vol = float(vol.tail(20).mean())
        vol_ratio = round(vol_today / avg_vol, 2) if avg_vol > 0 else 0.0

        # ── Avstander ──
        pct_change = safe_pct(price, prev_close) or 0.0
        over200 = price > sma200 if sma200 is not None else None
        over50 = price > sma50 if sma50 is not None else None
        dist_sma50 = safe_pct(price, sma50)

        high_20d = float(high.tail(20).max())
        low_20d = float(low.tail(20).min())
        dist_high = safe_pct(price, high_20d) or 0.0
        dist_low = safe_pct(price, low_20d) or 0.0

        # ── Late Move % ──
        late_move = safe_pct(price, day_low) or 0.0

        # ── Distance to Support % (nærmeste av SMA50 / 20d low under kurs) ──
        support_candidates = []
        if sma50 is not None and sma50 < price:
            support_candidates.append(sma50)
        if low_20d < price:
            support_candidates.append(low_20d)
        if support_candidates:
            nearest_support = max(support_candidates)
            dist_support = round(((price - nearest_support) / price) * 100, 2)
        else:
            dist_support = None

        # ── Distance to Resistance % ──
        dist_resistance = round(((high_20d - price) / price) * 100, 2) if high_20d > 0 else None

        # ── Volume Trend ──
        if vol_ratio >= VOL_RATIO_INCREASING:
            vol_trend = "Increasing"
        elif vol_ratio >= VOL_RATIO_FLAT_MIN:
            vol_trend = "Flat"
        else:
            vol_trend = "Decreasing"

        # ── Trend 1D ──
        if over200 and over50:
            trend_1d = "UP"
        elif over200 is False:
            trend_1d = "DOWN"
        else:
            trend_1d = "NEUTRAL"

        # ── Trend 1H (proxy: kurs vs dagens åpning ±0.2%, med SMA50-bekreftelse) ──
        if day_open > 0:
            if price > day_open * TREND_1H_UP_THRESHOLD and (sma50 is None or price > sma50):
                trend_1h = "UP"
            elif price < day_open * TREND_1H_DOWN_THRESHOLD:
                trend_1h = "DOWN"
            else:
                trend_1h = "NEUTRAL"
        else:
            trend_1h = "NEUTRAL"

        return {
            "Ticker": ticker.replace(".OL", ""),
            "ticker_yf": ticker,
            "Selskap": ticker_dict.get(ticker, ticker),
            "Kurs": round(price, 2),
            "% i dag": pct_change,
            "SMA 200": round(sma200, 2) if sma200 else None,
            "Over SMA200": over200,
            "SMA 50": round(sma50, 2) if sma50 else None,
            "Over SMA50": over50,
            "Avst SMA50 %": dist_sma50,
            "RSI 14": round(rsi, 1) if rsi else None,
            "Volum": int(vol_today),
            "Snitt Vol 20d": int(avg_vol),
            "Vol Ratio": vol_ratio,
            "Avst 20d High %": dist_high,
            "Avst 20d Low %": dist_low,
            "Late Move %": late_move,
            "Støtte %": dist_support,
            "Motstand %": dist_resistance,
            "Vol Trend": vol_trend,
            "Trend 1D": trend_1d,
            "Trend 1H": trend_1h,
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
    """Last ned historikk for alle aksjer og beregn indikatorer."""
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
    df_r = klassifiser_setup(df_r)
    df_r = beregn_entry_readiness(df_r)
    df_r = beregn_score(df_r)
    return df_r


# ──────────────────────────────────────────────────────────────
# SETUP-KLASSIFISERING (v3 — unchanged)
# ──────────────────────────────────────────────────────────────

def klassifiser_setup(df: pd.DataFrame) -> pd.DataFrame:
    """Klassifiser hver aksje som en setup-type."""
    setups = []
    for _, r in df.iterrows():
        over200 = r.get("Over SMA200")
        over50 = r.get("Over SMA50")
        rsi = r.get("RSI 14")
        dist_sma50 = r.get("Avst SMA50 %")
        dist_high = r.get("Avst 20d High %")
        vol_ratio = r.get("Vol Ratio", 0)
        pct_today = r.get("% i dag", 0)

        if over200 is None or rsi is None or dist_sma50 is None:
            setups.append("No setup")
            continue

        # Extended: fanges først (dårlige entries)
        if rsi > RSI_OVERBOUGHT or (over200 and dist_sma50 > SMA50_EXTENDED_PCT):
            setups.append("Extended")
            continue

        # Breakout: nær 20d high + sterkt volum + IKKE for langt over SMA50
        if (over200
                and dist_high is not None and dist_high >= DIST_HIGH_BREAKOUT
                and vol_ratio >= VOL_RATIO_INCREASING
                and rsi > 50
                and dist_sma50 <= SMA50_BREAKOUT_MAX):
            setups.append("Breakout")
            continue

        # Pullback: nær SMA50 + kontrollert tilbaketrekking
        if (over200
                and SMA50_PULLBACK_MIN <= dist_sma50 <= SMA50_PULLBACK_MAX
                and RSI_PULLBACK_MIN <= rsi <= RSI_PULLBACK_MAX):
            setups.append("Pullback")
            continue

        # Early Pullback: samme som Pullback men vol < 1 (forkant)
        if (over200
                and SMA50_PULLBACK_MIN <= dist_sma50 <= SMA50_PULLBACK_MAX
                and RSI_EARLY_PB_MIN <= rsi <= RSI_EARLY_PB_MAX
                and vol_ratio < VOL_RATIO_CONFIRMATION):
            setups.append("Early Pullback")
            continue

        # Momentum: sterk dag + volum
        if (over200 and over50
                and pct_today > MOMENTUM_PCT_MIN
                and vol_ratio >= VOL_RATIO_CONFIRMATION
                and rsi > 50):
            setups.append("Momentum")
            continue

        # Trend: stabil over begge SMA
        if over200 and over50 and RSI_SETUP_TREND_MIN <= rsi <= RSI_SETUP_TREND_MAX:
            setups.append("Trend")
            continue

        setups.append("No setup")

    df["Setup"] = setups
    return df


# ──────────────────────────────────────────────────────────────
# ENTRY READINESS (READY / WAIT / EXTENDED / SKIP)
# ──────────────────────────────────────────────────────────────

def beregn_entry_readiness(df: pd.DataFrame) -> pd.DataFrame:
    """Beregn Entry Readiness (READY/WAIT/EXTENDED/SKIP) og Trade Signal."""
    readiness_list = []
    signals = []

    for _, r in df.iterrows():
        over200 = r.get("Over SMA200")
        rsi = r.get("RSI 14")
        dist_sma50 = r.get("Avst SMA50 %")
        dist_high = r.get("Avst 20d High %")
        vol_ratio = r.get("Vol Ratio", 0)
        avg_vol = r.get("Snitt Vol 20d", 0)

        # ── Entry Readiness (eksakt regelrekkefølge) ──

        # 1. SKIP
        if (over200 is False
                or avg_vol < MIN_TRADING_VOLUME
                or (rsi is not None and rsi < RSI_OVERSOLD)):
            readiness = "SKIP"

        # 2. EXTENDED: for langt opp eller overkjøpt
        # "dist_high > DIST_HIGH_NEAR_HIGH" betyr innenfor 1% av 20d high
        elif ((dist_sma50 is not None and dist_sma50 > SMA50_EXTENDED_PCT)
              or (rsi is not None and rsi > RSI_OVERBOUGHT)
              or (dist_high is not None and dist_high > DIST_HIGH_NEAR_HIGH
                  and dist_sma50 is not None and dist_sma50 > SMA50_NEAR_HIGH_PCT)):
            readiness = "EXTENDED"

        # 3. READY: optimal entry-sone
        elif (over200 is True
              and dist_sma50 is not None and SMA50_READY_MIN <= dist_sma50 <= SMA50_READY_MAX
              and rsi is not None and RSI_READY_MIN <= rsi <= RSI_READY_MAX
              and vol_ratio >= VOL_RATIO_CONFIRMATION
              and avg_vol >= MIN_TRADING_VOLUME):
            readiness = "READY"

        # 4. WAIT: bredere aksept-sone
        elif (over200 is True
              and dist_sma50 is not None and SMA50_WAIT_MIN <= dist_sma50 <= SMA50_WAIT_MAX
              and rsi is not None and RSI_WAIT_MIN <= rsi <= RSI_WAIT_MAX
              and avg_vol >= MIN_TRADING_VOLUME):
            readiness = "WAIT"

        # 5. else SKIP
        else:
            readiness = "SKIP"

        # ── Trade Signal ──
        if readiness == "READY" and vol_ratio >= VOL_RATIO_CONFIRMATION:
            signal = "BUY"
        elif readiness == "WAIT" and vol_ratio >= VOL_RATIO_WATCH_MIN:
            signal = "WATCH"
        elif readiness == "WAIT":
            signal = "WAIT"
        else:
            signal = "SKIP"

        readiness_list.append(readiness)
        signals.append(signal)

    df["Entry"] = readiness_list
    df["Signal"] = signals
    return df


# ──────────────────────────────────────────────────────────────
# TRADE SCORE (0–10)
# ──────────────────────────────────────────────────────────────

def beregn_score(df: pd.DataFrame) -> pd.DataFrame:
    """Trade Score 0–10 basert på entry timing + volumbekreftelse."""
    scores = []
    for _, r in df.iterrows():
        p = 0
        if r.get("Over SMA200"):
            p += 2
        if r.get("Over SMA50"):
            p += 1

        dist_sma50 = r.get("Avst SMA50 %")
        if dist_sma50 is not None and -SMA50_SCORE_NEAR <= dist_sma50 <= SMA50_SCORE_NEAR:
            p += 2

        rsi = r.get("RSI 14")
        if rsi is not None and RSI_READY_MIN <= rsi <= RSI_READY_MAX:
            p += 1

        vol_ratio = r.get("Vol Ratio", 0)
        if vol_ratio > VOL_RATIO_STRONG:
            p += 2
        elif vol_ratio > VOL_RATIO_CONFIRMATION:
            p += 1

        if r.get("Snitt Vol 20d", 0) > MIN_TRADING_VOLUME:
            p += 1

        scores.append(max(0, min(p, SCORE_MAX)))
    df["Score"] = scores
    return df


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
# VISNING
# ──────────────────────────────────────────────────────────────

SETUP_EMOJI = {"Trend":"🟢","Pullback":"🟡","Breakout":"🔵","Early Pullback":"🟠",
               "Momentum":"🟣","Extended":"🔴","No setup":"⚪"}

ENTRY_EMOJI = {"READY":"🟢 READY","WAIT":"🟡 WAIT","EXTENDED":"🔴 EXTENDED","SKIP":"⚪ SKIP"}
SIGNAL_EMOJI = {"BUY":"🟢 BUY","WATCH":"🟡 WATCH","WAIT":"🟠 WAIT","SKIP":"🔴 SKIP"}
TREND_EMOJI = {"UP":"🟢 UP","DOWN":"🔴 DOWN","NEUTRAL":"⚪ —"}
VOL_TREND_EMOJI = {"Increasing":"🟩 Inc","Flat":"⬜ Flat","Decreasing":"🟥 Dec"}

# Column sets for presets
COLS_SCAN = [
    "Signal","Entry","Ticker","Selskap","Kurs","% i dag",
    "Trend 1D","RSI 14","Vol Ratio","Vol Trend","Setup","Score",
]
COLS_ENTRY = [
    "Signal","Entry","Ticker","Selskap","Kurs","Avst SMA50 %",
    "RSI 14","Vol Ratio","Vol Trend","Trend 1D","Trend 1H",
    "Støtte %","Motstand %","Late Move %","Setup","Score",
]
COLS_BREAKOUT = [
    "Signal","Entry","Ticker","Selskap","Kurs","% i dag",
    "Avst 20d High %","Vol Ratio","Vol Trend","Trend 1D","Trend 1H",
    "Motstand %","Late Move %","RSI 14","Setup","Score",
]

def formater_tabell(df: pd.DataFrame, preset: str = "Scan") -> pd.DataFrame:
    """Formater DataFrame for visning, med emojis, linker og preset-spesifikke kolonner."""
    vis = df.copy()
    # Yahoo Finance direktelink — fungerer direkte med ticker (f.eks. EQNR.OL).
    # Selskapsnavn embeds som #fragment slik at LinkColumn regex viser navnet som linktekst.
    vis["Selskap"] = vis.apply(
        lambda r: f"https://finance.yahoo.com/quote/{r['ticker_yf']}#{r['Selskap']}",
        axis=1,
    )
    vis["Setup"] = vis["Setup"].apply(lambda x: f"{SETUP_EMOJI.get(x, '')} {x}")
    vis["Entry"] = vis["Entry"].apply(lambda x: ENTRY_EMOJI.get(x, x))
    vis["Signal"] = vis["Signal"].apply(lambda x: SIGNAL_EMOJI.get(x, x))
    vis["Trend 1D"] = vis["Trend 1D"].apply(lambda x: TREND_EMOJI.get(x, x))
    vis["Trend 1H"] = vis["Trend 1H"].apply(lambda x: TREND_EMOJI.get(x, x))
    vis["Vol Trend"] = vis["Vol Trend"].apply(lambda x: VOL_TREND_EMOJI.get(x, x))
    if "Over SMA200" in vis.columns:
        vis["Over SMA200"] = vis["Over SMA200"].apply(lambda x: "✅" if x else ("❌" if x is False else "—"))
    if "Over SMA50" in vis.columns:
        vis["Over SMA50"] = vis["Over SMA50"].apply(lambda x: "✅" if x else ("❌" if x is False else "—"))
    vis["Vol Ratio"] = vis["Vol Ratio"].apply(
        lambda x: f"{'🟩 ' if x >= VOL_RATIO_INCREASING else ''}{x:.1f}x"
    )
    vis["Volum"] = vis["Volum"].apply(lambda x: f"{x:,.0f}".replace(",", " "))
    vis["Snitt Vol 20d"] = vis["Snitt Vol 20d"].apply(lambda x: f"{x:,.0f}".replace(",", " "))

    if preset == "Entry":
        cols = COLS_ENTRY
    elif preset == "Breakout":
        cols = COLS_BREAKOUT
    else:
        cols = COLS_SCAN
    return vis[[c for c in cols if c in vis.columns]]


# Shared column config: Selskap shows company name (extracted from URL fragment) and links to Nordnet
SELSKAP_LINK = st.column_config.LinkColumn(
    "Selskap",
    help="Klikk for å åpne i Nordnet",
    display_text=r"#(.+)$",  # extract name after the #
)


# ──────────────────────────────────────────────────────────────
# FILTER DEFAULTS
# ──────────────────────────────────────────────────────────────

FILTER_DEFAULTS = {
    "f_pullback":False,"f_early_pullback":False,"f_breakout":False,"f_trend":False,
    "f_momentum":False,"f_extended":False,"f_no_setup":False,"f_skjul_extended":True,
    "f_signal":"Alle","f_readiness":"Alle",
    "f_over_sma200":False,"f_over_sma50":False,"f_rsi":(20,80),
    "f_avst_sma50":(-15.0,15.0),"f_kun_hoyt_volum":False,"f_min_vol_ratio":0.0,
    "f_min_vol":DEFAULT_MIN_AVG_VOLUME,"f_min_score":0,"f_preset":"Scan",
}

def reset_filtre() -> None:
    """Tilbakestill alle filtre til default-verdier."""
    for k, v in FILTER_DEFAULTS.items():
        st.session_state[k] = v


# ──────────────────────────────────────────────────────────────
# STREAMLIT APP
# ──────────────────────────────────────────────────────────────

def main() -> None:
    """Streamlit hovedapp."""
    st.set_page_config(page_title="Oslo Børs Scanner",page_icon="📈",layout="wide")
    st.title("📈 Oslo Børs Swing Trading Scanner")

    refresh_opts = {"Av":0,"5 min":5,"10 min":10,"15 min":15,"30 min":30}
    ct,cr = st.columns([3,1])
    with ct: st.caption(f"Alle {len(OSLO_TICKERS)} aksjer — Entry Readiness · Trade Signal · Presets")
    with cr: rv = st.selectbox("Auto-refresh",list(refresh_opts.keys()),index=3,label_visibility="collapsed")
    rm = refresh_opts[rv]
    if rm > 0:
        t = st_autorefresh(interval=rm*60*1000,key="auto_refresh")
        if t and t > 0: st.cache_data.clear()

    if "watchlist" not in st.session_state: st.session_state.watchlist = last_watchlist()
    if "data" not in st.session_state: st.session_state.data = None
    for k,v in FILTER_DEFAULTS.items():
        if k not in st.session_state: st.session_state[k] = v

    # ── Controls ──
    st.markdown("---")
    cc1, cc2, cc3 = st.columns([1,1,1])
    with cc1: scan = st.button("🔄 Scan nå",type="primary",width="stretch")
    with cc2: st.button("🗑️ Reset filtre",on_click=reset_filtre,width="stretch")
    with cc3: preset = st.selectbox("📋 Preset",["Scan","Entry","Breakout"],key="f_preset")

    if scan:
        st.cache_data.clear(); st.session_state.data = hent_data(OSLO_TICKERS)
        st.success(f"✅ Skannet {len(st.session_state.data)} aksjer")
    elif st.session_state.data is None:
        st.session_state.data = hent_data(OSLO_TICKERS)

    df = st.session_state.data
    if df is None or df.empty: st.warning("Ingen data. Trykk «Scan nå»."); return

    # ── Setup filter ──
    st.markdown("**Setup-filter:**")
    sc = st.columns(7)
    with sc[0]: cb_pullback = st.checkbox("🟡 Pullback", key="f_pullback")
    with sc[1]: cb_early_pb = st.checkbox("🟠 Early PB", key="f_early_pullback")
    with sc[2]: cb_breakout = st.checkbox("🔵 Breakout", key="f_breakout")
    with sc[3]: cb_trend = st.checkbox("🟢 Trend", key="f_trend")
    with sc[4]: cb_momentum = st.checkbox("🟣 Momentum", key="f_momentum")
    with sc[5]: cb_extended = st.checkbox("🔴 Extended", key="f_extended")
    with sc[6]: cb_no_setup = st.checkbox("⚪ No setup", key="f_no_setup")

    # ── Filters ──
    st.markdown("**Filtre:**")
    fc = st.columns(4)
    with fc[0]:
        signal_filter = st.selectbox("📡 Signal", ["Alle","BUY","WATCH","WAIT","SKIP"], key="f_signal")
        entry_filter = st.selectbox("🎯 Entry Readiness", ["Alle","READY","WAIT","EXTENDED","SKIP"], key="f_readiness")
        hide_extended = st.checkbox("🚫 Skjul Extended", key="f_skjul_extended")
    with fc[1]:
        require_sma200 = st.checkbox("Kun over SMA 200", key="f_over_sma200")
        require_sma50 = st.checkbox("Kun over SMA 50", key="f_over_sma50")
        high_volume_only = st.checkbox("🔊 Kun høyt volum (>1x)", key="f_kun_hoyt_volum")
        min_vol_ratio = st.slider("Min. Vol Ratio", 0.0, 5.0, step=0.1, key="f_min_vol_ratio")
    with fc[2]:
        rsi_range = st.slider("RSI-range", 0, 100, key="f_rsi")
        dist_range = st.slider("Avstand SMA50 %", -30.0, 30.0, step=0.5, key="f_avst_sma50")
    with fc[3]:
        min_avg_vol = st.number_input("Min. snittvolum 20d", min_value=0, step=50_000, key="f_min_vol")
        min_score = st.slider("Minimum score", 0, SCORE_MAX, key="f_min_score")

    # ── Apply filters ──
    fd = df.copy()
    fd = fd[fd["Snitt Vol 20d"] >= min_avg_vol]
    if signal_filter != "Alle":
        fd = fd[fd["Signal"] == signal_filter]
    if entry_filter != "Alle":
        fd = fd[fd["Entry"] == entry_filter]

    selected_setups = []
    if cb_pullback: selected_setups.append("Pullback")
    if cb_early_pb: selected_setups.append("Early Pullback")
    if cb_breakout: selected_setups.append("Breakout")
    if cb_trend: selected_setups.append("Trend")
    if cb_momentum: selected_setups.append("Momentum")
    if cb_extended: selected_setups.append("Extended")
    if cb_no_setup: selected_setups.append("No setup")
    if selected_setups:
        fd = fd[fd["Setup"].isin(selected_setups)]

    if hide_extended and not cb_extended:
        fd = fd[fd["Setup"] != "Extended"]
    if require_sma200:
        fd = fd[fd["Over SMA200"] == True]
    if require_sma50:
        fd = fd[fd["Over SMA50"] == True]
    if high_volume_only:
        fd = fd[fd["Vol Ratio"] >= VOL_RATIO_CONFIRMATION]
    if min_vol_ratio > 0:
        fd = fd[fd["Vol Ratio"] >= min_vol_ratio]

    fd = fd[fd["RSI 14"].notna() & (fd["RSI 14"] >= rsi_range[0]) & (fd["RSI 14"] <= rsi_range[1])]
    fd = fd[fd["Avst SMA50 %"].notna() & (fd["Avst SMA50 %"] >= dist_range[0]) & (fd["Avst SMA50 %"] <= dist_range[1])]
    fd = fd[fd["Score"] >= min_score]
    fd = fd.sort_values("Score", ascending=False).reset_index(drop=True)

    # ── Metrics ──
    st.markdown("---")
    q = st.columns(7); n = len(fd)
    q[0].metric("Kandidater",n)
    q[1].metric("🟢 READY",len(fd[fd["Entry"]=="READY"]) if n else 0)
    q[2].metric("🟢 BUY",len(fd[fd["Signal"]=="BUY"]) if n else 0)
    q[3].metric("🟡 WATCH",len(fd[fd["Signal"]=="WATCH"]) if n else 0)
    q[4].metric("Pullback",len(fd[fd["Setup"].isin(["Pullback","Early Pullback"])]) if n else 0)
    q[5].metric("Breakout",len(fd[fd["Setup"]=="Breakout"]) if n else 0)
    q[6].metric("Trend",len(fd[fd["Setup"]=="Trend"]) if n else 0)

    # ── Table ──
    if fd.empty:
        st.info("Ingen aksjer matcher filtrene.")
    else:
        st.dataframe(formater_tabell(fd, preset), width="stretch", hide_index=True,
                      height=min(len(fd)*38+40, 700),
                      column_config={"Selskap": SELSKAP_LINK})
        if len(fd)>0:
            tp = fd.iloc[0]
            st.caption(f"Topp: **{tp['Ticker']}** ({tp['Selskap']}) — {tp['Entry']} / {tp['Signal']} / Score {tp['Score']}")

        # Watchlist buttons
        st.markdown("**Watchlist:**")
        nc = min(len(fd),8); wc = st.columns(nc)
        for i,(_,r) in enumerate(fd.iterrows()):
            tk = r["Ticker"]
            with wc[i%nc]:
                iw = tk in st.session_state.watchlist
                if st.button(f"{'⭐' if iw else '☆'} {tk}",key=f"wl_{tk}"):
                    st.session_state.watchlist.discard(tk) if iw else st.session_state.watchlist.add(tk)
                    lagre_watchlist(st.session_state.watchlist); st.rerun()

    # ── Watchlist ──
    st.markdown("---"); st.subheader(f"⭐ Watchlist ({len(st.session_state.watchlist)})")
    if not st.session_state.watchlist: st.info("Tom watchlist.")
    else:
        wd = df[df["Ticker"].isin(st.session_state.watchlist)].sort_values("Score",ascending=False)
        if wd.empty: st.warning("Ikke funnet i siste scan.")
        else: st.dataframe(formater_tabell(wd, preset), width="stretch", hide_index=True,
                            column_config={"Selskap": SELSKAP_LINK})
        nc = min(len(st.session_state.watchlist),8); fc2 = st.columns(nc)
        for i,tk in enumerate(sorted(st.session_state.watchlist)):
            with fc2[i%nc]:
                if st.button(f"❌ {tk}",key=f"rm_{tk}"):
                    st.session_state.watchlist.discard(tk); lagre_watchlist(st.session_state.watchlist); st.rerun()

    # ── Footer ──
    st.markdown("---")
    with st.expander("ℹ️ v4 — Entry Readiness, Signal, Presets"):
        st.markdown("""
**Entry Readiness (rule order):**
| Status | Regel |
|--------|-------|
| SKIP | Price < SMA200, avg vol < 500k, RSI < 30 |
| EXTENDED | Dist SMA50 > 8%, RSI > 75, or near high + >5% over SMA50 |
| READY | Over SMA200, SMA50 ±2%, RSI 40–60, vol ratio ≥1.0, avg vol ≥500k |
| WAIT | Over SMA200, SMA50 ±5%, RSI 35–70, avg vol ≥500k |

**Trade Signal:**
| Signal | Regel |
|--------|-------|
| 🟢 BUY | READY + vol ratio ≥ 1.0 |
| 🟡 WATCH | WAIT + vol ratio ≥ 0.7 |
| 🟠 WAIT | WAIT + vol ratio < 0.7 |
| 🔴 SKIP | alt annet |

**New Columns:**
- **Vol Trend** — Increasing (≥1.5x) / Flat / Decreasing (<0.8x)
- **Trend 1D** — UP (over both SMA) / DOWN (under SMA200) / NEUTRAL
- **Trend 1H** — UP/DOWN/NEUTRAL (approx from daily data)
- **Støtte %** — distance to nearest support (SMA50 or 20d low)
- **Motstand %** — distance to 20d high
- **Late Move %** — how far price moved from day low

**Presets:**
- **Scan** — overview with Signal, Entry, Trend, Setup, Score
- **Entry** — focus on SMA50 proximity, support/resistance, late move
- **Breakout** — focus on 20d high distance, volume, momentum
        """)

    oslo_tid = datetime.now(ZoneInfo("Europe/Oslo")).strftime('%Y-%m-%d %H:%M')
    st.caption(f"Oppdatert: {oslo_tid} (Oslo) | {len(OSLO_TICKERS)} aksjer | v4")

if __name__ == "__main__": main()
