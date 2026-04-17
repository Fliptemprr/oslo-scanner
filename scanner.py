"""
Oslo Børs Swing Trading Scanner – v4
=====================================
296 aksjer · Entry Readiness · Trade Signal · Volume Trend
Trend 1D · Support/Resistance · Late Move · Presets

Kjør:  streamlit run scanner.py
"""

import streamlit as st
from streamlit_autorefresh import st_autorefresh
import pandas as pd
import yfinance as yf
import json, time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

WATCHLIST_FILE = Path("watchlist.json")
DEFAULT_MIN_AVG_VOLUME = 200_000
BATCH_SIZE = 15
BATCH_DELAY = 5

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

def _lag_session():
    try:
        from curl_cffi import requests as cffi_requests
        return cffi_requests.Session(impersonate="chrome")
    except ImportError:
        return None

@st.cache_data(ttl=600, show_spinner=False)
def hent_data(ticker_dict: dict) -> pd.DataFrame:
    tickers_liste = list(ticker_dict.keys())
    start = datetime.now() - timedelta(days=300)
    end = datetime.now()
    session = _lag_session()
    alle_data = {}
    progress = st.progress(0, text="Henter data...")
    total_b = (len(tickers_liste)-1)//BATCH_SIZE+1

    for bn in range(0, len(tickers_liste), BATCH_SIZE):
        batch = tickers_liste[bn:bn+BATCH_SIZE]
        bi = bn//BATCH_SIZE+1
        progress.progress(min(bi/total_b,1.0), text=f"Batch {bi}/{total_b} — {min(bn+BATCH_SIZE,len(tickers_liste))}/{len(tickers_liste)}")
        for forsok in range(2):
            try:
                raw = yf.download(batch, start=start, end=end, progress=False,
                    auto_adjust=True, timeout=30, group_by="ticker", threads=False, session=session)
                if raw is not None and not raw.empty:
                    for t in batch:
                        try:
                            d = raw.copy() if len(batch)==1 else raw[t].copy()
                            d = d.dropna(how="all")
                            if len(d) >= 50: alle_data[t] = d
                        except (KeyError,TypeError): continue
                break
            except Exception as e:
                if forsok==0: time.sleep(BATCH_DELAY*2)
                else: print(f"⚠️ {e}")
        if bn+BATCH_SIZE < len(tickers_liste): time.sleep(BATCH_DELAY)

    mangler = [t for t in tickers_liste if t not in alle_data]
    if mangler:
        progress.progress(0.95, text=f"Retry {len(mangler)} manglende...")
        time.sleep(BATCH_DELAY)
        for t in mangler:
            try:
                raw = yf.download(t, start=start, end=end, progress=False,
                    auto_adjust=True, timeout=20, session=session)
                if raw is not None and not raw.empty:
                    if isinstance(raw.columns, pd.MultiIndex): raw.columns = raw.columns.get_level_values(0)
                    raw = raw.dropna(how="all")
                    if len(raw) >= 50: alle_data[t] = raw
                time.sleep(2)
            except: continue
    progress.empty()

    # ── Beregn alle kolonner ──
    resultater = []
    for ticker, df in alle_data.items():
        try:
            if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
            close = df["Close"].dropna(); vol = df["Volume"].dropna()
            high = df["High"].dropna(); low = df["Low"].dropna()
            if len(close) < 50: continue

            price = float(close.iloc[-1])
            prev = float(close.iloc[-2]) if len(close)>=2 else price
            day_high = float(high.iloc[-1])
            day_low = float(low.iloc[-1])

            sma200 = float(close.rolling(200).mean().iloc[-1]) if len(close)>=200 else None
            sma50 = float(close.rolling(50).mean().iloc[-1]) if len(close)>=50 else None
            rsi = float(beregn_rsi(close,14).iloc[-1]) if len(close)>=20 else None

            vol_today = float(vol.iloc[-1])
            avg_vol = float(vol.tail(20).mean())
            vol_ratio = round(vol_today/avg_vol, 2) if avg_vol>0 else 0.0

            pct_change = round(((price-prev)/prev)*100, 2) if prev>0 else 0.0
            over200 = price > sma200 if sma200 else None
            over50 = price > sma50 if sma50 else None
            dist_sma50 = round(((price-sma50)/sma50)*100, 2) if sma50 else None

            high_20d = float(high.tail(20).max())
            low_20d = float(low.tail(20).min())
            dist_high = round(((price-high_20d)/high_20d)*100, 2) if high_20d>0 else 0.0
            dist_low = round(((price-low_20d)/low_20d)*100, 2) if low_20d>0 else 0.0

            # ── NEW: Late Move % ──
            late_move = round(((price-day_low)/day_low)*100, 2) if day_low>0 else 0.0

            # ── NEW: Distance to Support % ──
            # Support = nearest of SMA50 or 20d low that is BELOW price
            support_candidates = []
            if sma50 and sma50 < price: support_candidates.append(sma50)
            if low_20d < price: support_candidates.append(low_20d)
            if support_candidates:
                nearest_support = max(support_candidates)  # closest below
                dist_support = round(((price-nearest_support)/price)*100, 2)
            else:
                dist_support = None

            # ── NEW: Distance to Resistance % ──
            dist_resistance = round(((high_20d-price)/price)*100, 2) if high_20d>0 else None

            # ── NEW: Volume Trend ──
            if vol_ratio >= 1.5: vol_trend = "Increasing"
            elif vol_ratio >= 0.8: vol_trend = "Flat"
            else: vol_trend = "Decreasing"

            # ── NEW: Trend 1D ──
            if over200 and over50: trend_1d = "UP"
            elif over200 is False: trend_1d = "DOWN"
            else: trend_1d = "NEUTRAL"

            # ── NEW: Trend 1H (approximation from intraday range) ──
            # True 1H needs intraday data; approximate using price vs day open
            day_open = float(df["Close"].iloc[-2]) if len(close)>=2 else price
            if price > day_open * 1.002 and price > (sma50 if sma50 else 0):
                trend_1h = "UP"
            elif price < day_open * 0.998:
                trend_1h = "DOWN"
            else:
                trend_1h = "NEUTRAL"

            resultater.append({
                "Ticker": ticker.replace(".OL",""), "ticker_yf": ticker,
                "Selskap": ticker_dict.get(ticker, ticker),
                "Kurs": round(price,2), "% i dag": pct_change,
                "SMA 200": round(sma200,2) if sma200 else None, "Over SMA200": over200,
                "SMA 50": round(sma50,2) if sma50 else None, "Over SMA50": over50,
                "Avst SMA50 %": dist_sma50, "RSI 14": round(rsi,1) if rsi else None,
                "Volum": int(vol_today), "Snitt Vol 20d": int(avg_vol),
                "Vol Ratio": vol_ratio,
                "Avst 20d High %": dist_high, "Avst 20d Low %": dist_low,
                "Late Move %": late_move,
                "Støtte %": dist_support,
                "Motstand %": dist_resistance,
                "Vol Trend": vol_trend,
                "Trend 1D": trend_1d,
                "Trend 1H": trend_1h,
            })
        except Exception as e: print(f"⚠️ {ticker}: {e}"); continue

    if not resultater: return pd.DataFrame()
    df_r = pd.DataFrame(resultater)
    df_r = klassifiser_setup(df_r)
    df_r = beregn_entry_readiness(df_r)
    df_r = beregn_score(df_r)
    return df_r


# ──────────────────────────────────────────────────────────────
# SETUP-KLASSIFISERING (v3 — unchanged)
# ──────────────────────────────────────────────────────────────

def klassifiser_setup(df: pd.DataFrame) -> pd.DataFrame:
    setups = []
    for _, r in df.iterrows():
        o200=r.get("Over SMA200"); o50=r.get("Over SMA50"); rsi=r.get("RSI 14")
        a50=r.get("Avst SMA50 %"); ah=r.get("Avst 20d High %")
        vr=r.get("Vol Ratio",0); pct=r.get("% i dag",0)
        if o200 is None or rsi is None or a50 is None: setups.append("No setup"); continue
        if rsi > 75 or (o200 and a50 > 8): setups.append("Extended"); continue
        if o200 and ah is not None and ah >= -2.0 and vr >= 1.5 and rsi > 50 and a50 <= 5.0:
            setups.append("Breakout"); continue
        if o200 and a50 is not None and -2.0 <= a50 <= 1.0 and 35 <= rsi <= 55:
            setups.append("Pullback"); continue
        if o200 and a50 is not None and -2.0 <= a50 <= 1.0 and 40 <= rsi <= 50 and vr < 1.0:
            setups.append("Early Pullback"); continue
        if o200 and o50 and pct > 0.5 and vr >= 1.0 and rsi > 50:
            setups.append("Momentum"); continue
        if o200 and o50 and 40 <= rsi <= 70: setups.append("Trend"); continue
        setups.append("No setup")
    df["Setup"] = setups; return df


# ──────────────────────────────────────────────────────────────
# ENTRY READINESS (READY / WAIT / EXTENDED / SKIP)
# ──────────────────────────────────────────────────────────────

def beregn_entry_readiness(df: pd.DataFrame) -> pd.DataFrame:
    readiness_list = []
    signals = []
    for _, r in df.iterrows():
        o200 = r.get("Over SMA200")
        rsi = r.get("RSI 14")
        a50 = r.get("Avst SMA50 %")
        ah = r.get("Avst 20d High %")
        vr = r.get("Vol Ratio", 0)
        avg_vol = r.get("Snitt Vol 20d", 0)

        # --- Entry Readiness (exact rule order) ---

        # 1. SKIP
        if o200 is False or avg_vol < 500_000 or (rsi is not None and rsi < 30):
            readiness = "SKIP"

        # 2. EXTENDED
        elif (a50 is not None and a50 > 8) or \
             (rsi is not None and rsi > 75) or \
             (ah is not None and ah > -1 and a50 is not None and a50 > 5):
            readiness = "EXTENDED"

        # 3. READY
        elif (o200 is True
              and a50 is not None and -2.0 <= a50 <= 2.0
              and rsi is not None and 40 <= rsi <= 60
              and vr >= 1.0
              and avg_vol >= 500_000):
            readiness = "READY"

        # 4. WAIT
        elif (o200 is True
              and a50 is not None and -5.0 <= a50 <= 5.0
              and rsi is not None and 35 <= rsi <= 70
              and avg_vol >= 500_000):
            readiness = "WAIT"

        # 5. else SKIP
        else:
            readiness = "SKIP"

        # --- Trade Signal ---
        if readiness == "READY" and vr >= 1.0:
            signal = "BUY"
        elif readiness == "WAIT" and vr >= 0.7:
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
    scores = []
    for _, r in df.iterrows():
        p = 0
        if r.get("Over SMA200"): p += 2
        if r.get("Over SMA50"): p += 1
        a = r.get("Avst SMA50 %")
        if a is not None and -2.0 <= a <= 2.0: p += 2
        rsi = r.get("RSI 14")
        if rsi is not None and 40 <= rsi <= 60: p += 1
        vr = r.get("Vol Ratio", 0)
        if vr > 1.2: p += 2
        elif vr > 1.0: p += 1
        if r.get("Snitt Vol 20d", 0) > 500_000: p += 1
        scores.append(max(0, min(p, 10)))
    df["Score"] = scores
    return df


# ──────────────────────────────────────────────────────────────
# WATCHLIST
# ──────────────────────────────────────────────────────────────

def last_watchlist():
    if WATCHLIST_FILE.exists():
        try:
            with open(WATCHLIST_FILE) as f: return set(json.load(f))
        except: return set()
    return set()

def lagre_watchlist(tickers):
    with open(WATCHLIST_FILE,"w") as f: json.dump(sorted(list(tickers)),f,indent=2)


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

def formater_tabell(df, preset="Scan"):
    vis = df.copy()
    # Yahoo Finance direktelink — fungerer direkte med ticker (f.eks. EQNR.OL).
    # Selskapsnavn embeds som #fragment slik at LinkColumn regex viser navnet som linktekst.
    vis["Selskap"] = vis.apply(
        lambda r: f"https://finance.yahoo.com/quote/{r['ticker_yf']}#{r['Selskap']}",
        axis=1,
    )
    vis["Setup"] = vis["Setup"].apply(lambda x: f"{SETUP_EMOJI.get(x,'')} {x}")
    vis["Entry"] = vis["Entry"].apply(lambda x: ENTRY_EMOJI.get(x, x))
    vis["Signal"] = vis["Signal"].apply(lambda x: SIGNAL_EMOJI.get(x, x))
    vis["Trend 1D"] = vis["Trend 1D"].apply(lambda x: TREND_EMOJI.get(x, x))
    vis["Trend 1H"] = vis["Trend 1H"].apply(lambda x: TREND_EMOJI.get(x, x))
    vis["Vol Trend"] = vis["Vol Trend"].apply(lambda x: VOL_TREND_EMOJI.get(x, x))
    if "Over SMA200" in vis.columns:
        vis["Over SMA200"] = vis["Over SMA200"].apply(lambda x: "✅" if x else ("❌" if x is False else "—"))
    if "Over SMA50" in vis.columns:
        vis["Over SMA50"] = vis["Over SMA50"].apply(lambda x: "✅" if x else ("❌" if x is False else "—"))
    vis["Vol Ratio"] = vis["Vol Ratio"].apply(lambda x: f"{'🟩 ' if x>=1.5 else ''}{x:.1f}x")
    vis["Volum"] = vis["Volum"].apply(lambda x: f"{x:,.0f}".replace(","," "))
    vis["Snitt Vol 20d"] = vis["Snitt Vol 20d"].apply(lambda x: f"{x:,.0f}".replace(","," "))

    if preset == "Entry": cols = COLS_ENTRY
    elif preset == "Breakout": cols = COLS_BREAKOUT
    else: cols = COLS_SCAN
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

def reset_filtre():
    for k,v in FILTER_DEFAULTS.items(): st.session_state[k] = v


# ──────────────────────────────────────────────────────────────
# STREAMLIT APP
# ──────────────────────────────────────────────────────────────

def main():
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
    with sc[0]: fp=st.checkbox("🟡 Pullback",key="f_pullback")
    with sc[1]: fep=st.checkbox("🟠 Early PB",key="f_early_pullback")
    with sc[2]: fb=st.checkbox("🔵 Breakout",key="f_breakout")
    with sc[3]: ft=st.checkbox("🟢 Trend",key="f_trend")
    with sc[4]: fm=st.checkbox("🟣 Momentum",key="f_momentum")
    with sc[5]: fe=st.checkbox("🔴 Extended",key="f_extended")
    with sc[6]: fn=st.checkbox("⚪ No setup",key="f_no_setup")

    # ── Filters ──
    st.markdown("**Filtre:**")
    fc = st.columns(4)
    with fc[0]:
        sf = st.selectbox("📡 Signal",["Alle","BUY","WATCH","WAIT","SKIP"],key="f_signal")
        er = st.selectbox("🎯 Entry Readiness",["Alle","READY","WAIT","EXTENDED","SKIP"],key="f_readiness")
        se = st.checkbox("🚫 Skjul Extended",key="f_skjul_extended")
    with fc[1]:
        s200 = st.checkbox("Kun over SMA 200",key="f_over_sma200")
        s50 = st.checkbox("Kun over SMA 50",key="f_over_sma50")
        hv = st.checkbox("🔊 Kun høyt volum (>1x)",key="f_kun_hoyt_volum")
        mvr = st.slider("Min. Vol Ratio",0.0,5.0,step=0.1,key="f_min_vol_ratio")
    with fc[2]:
        rr = st.slider("RSI-range",0,100,key="f_rsi")
        ar = st.slider("Avstand SMA50 %",-30.0,30.0,step=0.5,key="f_avst_sma50")
    with fc[3]:
        mv = st.number_input("Min. snittvolum 20d",min_value=0,step=50_000,key="f_min_vol")
        ms = st.slider("Minimum score",0,10,key="f_min_score")

    # ── Apply filters ──
    fd = df.copy()
    fd = fd[fd["Snitt Vol 20d"] >= mv]
    if sf != "Alle": fd = fd[fd["Signal"]==sf]
    if er != "Alle": fd = fd[fd["Entry"]==er]

    vs = []
    if fp: vs.append("Pullback")
    if fep: vs.append("Early Pullback")
    if fb: vs.append("Breakout")
    if ft: vs.append("Trend")
    if fm: vs.append("Momentum")
    if fe: vs.append("Extended")
    if fn: vs.append("No setup")
    if vs: fd = fd[fd["Setup"].isin(vs)]
    if se and not fe: fd = fd[fd["Setup"]!="Extended"]
    if s200: fd = fd[fd["Over SMA200"]==True]
    if s50: fd = fd[fd["Over SMA50"]==True]
    if hv: fd = fd[fd["Vol Ratio"]>=1.0]
    if mvr > 0: fd = fd[fd["Vol Ratio"]>=mvr]
    fd = fd[fd["RSI 14"].notna() & (fd["RSI 14"]>=rr[0]) & (fd["RSI 14"]<=rr[1])]
    fd = fd[fd["Avst SMA50 %"].notna() & (fd["Avst SMA50 %"]>=ar[0]) & (fd["Avst SMA50 %"]<=ar[1])]
    fd = fd[fd["Score"]>=ms]
    fd = fd.sort_values("Score",ascending=False).reset_index(drop=True)

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
