"""
Oslo Børs Swing Trading Scanner – v3
=====================================
Alle 296 aksjer fra Euronext Oslo (Oslo Børs + Growth + Expand).
Trade Score, Trade Signal, curl_cffi, auto-refresh.

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

WATCHLIST_FILE = Path("watchlist.json")
DEFAULT_MIN_AVG_VOLUME = 200_000
BATCH_SIZE = 15
BATCH_DELAY = 5

# ──────────────────────────────────────────────────────────────
# ALLE 296 AKSJER FRA EURONEXT OSLO (14. april 2026)
# Oslo Børs (200) + Euronext Growth Oslo (85) + Euronext Expand (11)
# ──────────────────────────────────────────────────────────────
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

def beregn_rsi(serie: pd.Series, periode: int = 14) -> pd.Series:
    delta = serie.diff()
    gevinst = delta.where(delta > 0, 0.0)
    tap = -delta.where(delta < 0, 0.0)
    avg_gevinst = gevinst.ewm(alpha=1 / periode, min_periods=periode).mean()
    avg_tap = tap.ewm(alpha=1 / periode, min_periods=periode).mean()
    rs = avg_gevinst / avg_tap
    return 100.0 - (100.0 / (1.0 + rs))

def _lag_session():
    try:
        from curl_cffi import requests as cffi_requests
        return cffi_requests.Session(impersonate="chrome")
    except ImportError:
        return None

@st.cache_data(ttl=600, show_spinner=False)
def hent_data(ticker_dict: dict, dager_historikk: int = 250) -> pd.DataFrame:
    tickers_liste = list(ticker_dict.keys())
    start = datetime.now() - timedelta(days=dager_historikk + 50)
    end = datetime.now()
    session = _lag_session()
    alle_data = {}
    progress = st.progress(0, text="Henter data...")
    total_batches = (len(tickers_liste) - 1) // BATCH_SIZE + 1

    for batch_nr in range(0, len(tickers_liste), BATCH_SIZE):
        batch = tickers_liste[batch_nr:batch_nr + BATCH_SIZE]
        batch_idx = batch_nr // BATCH_SIZE + 1
        progress.progress(min(batch_idx / total_batches, 1.0),
            text=f"Batch {batch_idx}/{total_batches} — {min(batch_nr + BATCH_SIZE, len(tickers_liste))}/{len(tickers_liste)} aksjer...")
        for forsok in range(2):
            try:
                raw = yf.download(batch, start=start, end=end, progress=False,
                    auto_adjust=True, timeout=30, group_by="ticker", threads=False, session=session)
                if raw is not None and not raw.empty:
                    for ticker in batch:
                        try:
                            df_t = raw.copy() if len(batch) == 1 else raw[ticker].copy()
                            df_t = df_t.dropna(how="all")
                            if len(df_t) >= 50: alle_data[ticker] = df_t
                        except (KeyError, TypeError): continue
                break
            except Exception as e:
                if forsok == 0: time.sleep(BATCH_DELAY * 2)
                else: print(f"⚠️  Batch-feil: {e}")
        if batch_nr + BATCH_SIZE < len(tickers_liste): time.sleep(BATCH_DELAY)

    mangler = [t for t in tickers_liste if t not in alle_data]
    if mangler:
        progress.progress(0.95, text=f"Retry {len(mangler)} manglende...")
        time.sleep(BATCH_DELAY)
        for ticker in mangler:
            try:
                raw = yf.download(ticker, start=start, end=end, progress=False,
                    auto_adjust=True, timeout=20, session=session)
                if raw is not None and not raw.empty:
                    if isinstance(raw.columns, pd.MultiIndex): raw.columns = raw.columns.get_level_values(0)
                    raw = raw.dropna(how="all")
                    if len(raw) >= 50: alle_data[ticker] = raw
                time.sleep(2)
            except Exception: continue
    progress.empty()

    resultater = []
    for ticker, df in alle_data.items():
        try:
            if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
            close = df["Close"].dropna(); volume = df["Volume"].dropna()
            high = df["High"].dropna(); low = df["Low"].dropna()
            if len(close) < 50: continue
            siste = float(close.iloc[-1])
            forrige = float(close.iloc[-2]) if len(close) >= 2 else siste
            sma200 = float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else None
            sma50 = float(close.rolling(50).mean().iloc[-1]) if len(close) >= 50 else None
            rsi14 = float(beregn_rsi(close, 14).iloc[-1]) if len(close) >= 20 else None
            vol_i = float(volume.iloc[-1]); snitt_vol = float(volume.tail(20).mean())
            vol_r = round(vol_i / snitt_vol, 2) if snitt_vol > 0 else 0.0
            pct = round(((siste - forrige) / forrige) * 100, 2) if forrige > 0 else 0.0
            o200 = siste > sma200 if sma200 else None; o50 = siste > sma50 if sma50 else None
            a50 = round(((siste - sma50) / sma50) * 100, 2) if sma50 else None
            h20 = float(high.tail(20).max()); l20 = float(low.tail(20).min())
            ah = round(((siste - h20) / h20) * 100, 2) if h20 > 0 else 0.0
            al = round(((siste - l20) / l20) * 100, 2) if l20 > 0 else 0.0
            resultater.append({
                "Ticker": ticker.replace(".OL", ""), "ticker_yf": ticker,
                "Selskap": ticker_dict.get(ticker, ticker),
                "Kurs": round(siste, 2), "% i dag": pct,
                "SMA 200": round(sma200, 2) if sma200 else None, "Over SMA200": o200,
                "SMA 50": round(sma50, 2) if sma50 else None, "Over SMA50": o50,
                "Avst SMA50 %": a50, "RSI 14": round(rsi14, 1) if rsi14 else None,
                "Volum": int(vol_i), "Snitt Vol 20d": int(snitt_vol),
                "Vol Ratio": vol_r, "Avst 20d High %": ah, "Avst 20d Low %": al,
            })
        except Exception as e: print(f"⚠️  {ticker}: {e}"); continue
    if not resultater: return pd.DataFrame()
    df_r = pd.DataFrame(resultater)
    return beregn_score(klassifiser_setup(df_r))

def klassifiser_setup(df: pd.DataFrame) -> pd.DataFrame:
    setups = []
    for _, row in df.iterrows():
        o200=row.get("Over SMA200"); o50=row.get("Over SMA50"); rsi=row.get("RSI 14")
        a50=row.get("Avst SMA50 %"); ah=row.get("Avst 20d High %")
        vr=row.get("Vol Ratio",0); pct=row.get("% i dag",0)
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

def beregn_score(df: pd.DataFrame) -> pd.DataFrame:
    scores, signaler, info_list = [], [], []
    for _, row in df.iterrows():
        p=0; info=[]
        if row.get("Over SMA200"): p+=2; info.append("SMA200+2")
        if row.get("Over SMA50"): p+=1; info.append("SMA50+1")
        a=row.get("Avst SMA50 %")
        if a is not None and -2.0<=a<=2.0: p+=2; info.append("NærSMA50+2")
        rsi=row.get("RSI 14")
        if rsi is not None and 40<=rsi<=60: p+=1; info.append("RSI+1")
        vr=row.get("Vol Ratio",0)
        if vr>1.2: p+=2; info.append("Vol1.2+2")
        elif vr>1.0: p+=1; info.append("Vol1.0+1")
        if row.get("Snitt Vol 20d",0)>500_000: p+=1; info.append("Likv+1")
        final=max(0,min(p,10)); scores.append(final); info_list.append(", ".join(info))
        if final>=8 and vr>1.0 and a is not None and -2.0<=a<=2.0: signaler.append("BUY")
        elif final>=6: signaler.append("WATCH")
        else: signaler.append("SKIP")
    df["Score"]=scores; df["Signal"]=signaler; df["Score info"]=info_list; return df

def last_watchlist():
    if WATCHLIST_FILE.exists():
        try:
            with open(WATCHLIST_FILE) as f: return set(json.load(f))
        except: return set()
    return set()

def lagre_watchlist(tickers):
    with open(WATCHLIST_FILE,"w") as f: json.dump(sorted(list(tickers)),f,indent=2)

SETUP_EMOJI={"Trend":"🟢","Pullback":"🟡","Breakout":"🔵","Early Pullback":"🟠","Momentum":"🟣","Extended":"🔴","No setup":"⚪"}
SIGNAL_EMOJI={"BUY":"🟢 BUY","WATCH":"🟡 WATCH","SKIP":"🔴 SKIP"}

def formater_tabell(df):
    vis=df.copy()
    vis["Setup"]=vis["Setup"].apply(lambda x:f"{SETUP_EMOJI.get(x,'')} {x}")
    vis["Signal"]=vis["Signal"].apply(lambda x:SIGNAL_EMOJI.get(x,x))
    vis["Over SMA200"]=vis["Over SMA200"].apply(lambda x:"✅" if x else("❌" if x is False else "—"))
    vis["Over SMA50"]=vis["Over SMA50"].apply(lambda x:"✅" if x else("❌" if x is False else "—"))
    vis["Vol Ratio"]=vis["Vol Ratio"].apply(lambda x:f"{'🟩 ' if x>=1.5 else ''}{x:.1f}x")
    vis["Volum"]=vis["Volum"].apply(lambda x:f"{x:,.0f}".replace(","," "))
    vis["Snitt Vol 20d"]=vis["Snitt Vol 20d"].apply(lambda x:f"{x:,.0f}".replace(","," "))
    return vis[[c for c in ["Signal","Ticker","Selskap","Kurs","% i dag","Over SMA200","Over SMA50",
        "Avst SMA50 %","RSI 14","Vol Ratio","Volum","Snitt Vol 20d","Avst 20d High %","Avst 20d Low %",
        "Setup","Score"] if c in vis.columns]]

FILTER_DEFAULTS={
    "f_pullback":False,"f_early_pullback":False,"f_breakout":False,"f_trend":False,
    "f_momentum":False,"f_extended":False,"f_no_setup":False,"f_skjul_extended":True,
    "f_signal":"Alle","f_over_sma200":False,"f_over_sma50":False,"f_rsi":(20,80),
    "f_avst_sma50":(-15.0,15.0),"f_kun_hoyt_volum":False,"f_min_vol_ratio":0.0,
    "f_min_vol":DEFAULT_MIN_AVG_VOLUME,"f_min_score":0,
}

def reset_filtre():
    for k,v in FILTER_DEFAULTS.items(): st.session_state[k]=v

def main():
    st.set_page_config(page_title="Oslo Børs Scanner",page_icon="📈",layout="wide")
    st.title("📈 Oslo Børs Swing Trading Scanner")
    refresh_options={"Av":0,"5 min":5,"10 min":10,"15 min":15,"30 min":30}
    ct,cr=st.columns([3,1])
    with ct: st.caption(f"Alle {len(OSLO_TICKERS)} aksjer — Oslo Børs + Growth + Expand")
    with cr: rv=st.selectbox("Auto-refresh",list(refresh_options.keys()),index=3,label_visibility="collapsed")
    rm=refresh_options[rv]
    if rm>0:
        t=st_autorefresh(interval=rm*60*1000,key="auto_refresh")
        if t and t>0: st.cache_data.clear()
    if "watchlist" not in st.session_state: st.session_state.watchlist=last_watchlist()
    if "data" not in st.session_state: st.session_state.data=None
    for k,v in FILTER_DEFAULTS.items():
        if k not in st.session_state: st.session_state[k]=v

    st.markdown("---"); st.subheader("🔍 Filtre")
    c1,c2=st.columns([1,1])
    with c1: scan=st.button("🔄 Scan nå",type="primary",width="stretch")
    with c2: st.button("🗑️ Reset filtre",on_click=reset_filtre,width="stretch")
    if scan:
        st.cache_data.clear(); st.session_state.data=hent_data(OSLO_TICKERS)
        st.success(f"✅ Skannet {len(st.session_state.data)} aksjer")
    elif st.session_state.data is None: st.session_state.data=hent_data(OSLO_TICKERS)
    df=st.session_state.data
    if df is None or df.empty: st.warning("Ingen data. Trykk «Scan nå»."); return

    st.markdown("**Setup-filter:**")
    sc=st.columns(7)
    with sc[0]: fp=st.checkbox("🟡 Pullback",key="f_pullback")
    with sc[1]: fep=st.checkbox("🟠 Early PB",key="f_early_pullback")
    with sc[2]: fb=st.checkbox("🔵 Breakout",key="f_breakout")
    with sc[3]: ft=st.checkbox("🟢 Trend",key="f_trend")
    with sc[4]: fm=st.checkbox("🟣 Momentum",key="f_momentum")
    with sc[5]: fe=st.checkbox("🔴 Extended",key="f_extended")
    with sc[6]: fn=st.checkbox("⚪ No setup",key="f_no_setup")

    st.markdown("**Filtre:**")
    fc=st.columns(4)
    with fc[0]:
        sf=st.selectbox("📡 Trade Signal",["Alle","BUY","WATCH","SKIP"],key="f_signal")
        se=st.checkbox("🚫 Skjul Extended",key="f_skjul_extended")
        s200=st.checkbox("Kun over SMA 200",key="f_over_sma200")
        s50=st.checkbox("Kun over SMA 50",key="f_over_sma50")
    with fc[1]:
        hv=st.checkbox("🔊 Kun høyt volum (>1x)",key="f_kun_hoyt_volum")
        mvr=st.slider("Min. Vol Ratio",0.0,5.0,step=0.1,key="f_min_vol_ratio")
    with fc[2]:
        rr=st.slider("RSI-range",0,100,key="f_rsi")
        ar=st.slider("Avstand SMA50 %",-30.0,30.0,step=0.5,key="f_avst_sma50")
    with fc[3]:
        mv=st.number_input("Min. snittvolum 20d",min_value=0,step=50_000,key="f_min_vol")
        ms=st.slider("Minimum score",0,10,key="f_min_score")

    fd=df.copy(); fd=fd[fd["Snitt Vol 20d"]>=mv]
    if sf!="Alle": fd=fd[fd["Signal"]==sf]
    vs=[]
    if fp: vs.append("Pullback")
    if fep: vs.append("Early Pullback")
    if fb: vs.append("Breakout")
    if ft: vs.append("Trend")
    if fm: vs.append("Momentum")
    if fe: vs.append("Extended")
    if fn: vs.append("No setup")
    if vs: fd=fd[fd["Setup"].isin(vs)]
    if se and not fe: fd=fd[fd["Setup"]!="Extended"]
    if s200: fd=fd[fd["Over SMA200"]==True]
    if s50: fd=fd[fd["Over SMA50"]==True]
    if hv: fd=fd[fd["Vol Ratio"]>=1.0]
    if mvr>0: fd=fd[fd["Vol Ratio"]>=mvr]
    fd=fd[fd["RSI 14"].notna()&(fd["RSI 14"]>=rr[0])&(fd["RSI 14"]<=rr[1])]
    fd=fd[fd["Avst SMA50 %"].notna()&(fd["Avst SMA50 %"]>=ar[0])&(fd["Avst SMA50 %"]<=ar[1])]
    fd=fd[fd["Score"]>=ms]; fd=fd.sort_values("Score",ascending=False).reset_index(drop=True)

    st.markdown("---")
    q=st.columns(6); n=len(fd)
    q[0].metric("Kandidater",n)
    q[1].metric("🟢 BUY",len(fd[fd["Signal"]=="BUY"]) if n else 0)
    q[2].metric("🟡 WATCH",len(fd[fd["Signal"]=="WATCH"]) if n else 0)
    q[3].metric("Pullback",len(fd[fd["Setup"].isin(["Pullback","Early Pullback"])]) if n else 0)
    q[4].metric("Breakout",len(fd[fd["Setup"]=="Breakout"]) if n else 0)
    q[5].metric("Trend",len(fd[fd["Setup"]=="Trend"]) if n else 0)

    if fd.empty: st.info("Ingen aksjer matcher filtrene.")
    else:
        st.dataframe(formater_tabell(fd),width="stretch",hide_index=True,height=min(len(fd)*38+40,700))
        if len(fd)>0:
            tp=fd.iloc[0]; st.caption(f"Topp: **{tp['Ticker']}** ({tp['Selskap']}) — Score {tp['Score']}: {tp.get('Score info','')}")
        st.markdown("**Watchlist:**")
        nc=min(len(fd),8); wc=st.columns(nc)
        for i,(_,r) in enumerate(fd.iterrows()):
            tk=r["Ticker"]
            with wc[i%nc]:
                iw=tk in st.session_state.watchlist
                if st.button(f"{'⭐' if iw else '☆'} {tk}",key=f"wl_{tk}"):
                    st.session_state.watchlist.discard(tk) if iw else st.session_state.watchlist.add(tk)
                    lagre_watchlist(st.session_state.watchlist); st.rerun()

    st.markdown("---"); st.subheader(f"⭐ Watchlist ({len(st.session_state.watchlist)})")
    if not st.session_state.watchlist: st.info("Tom watchlist.")
    else:
        wd=df[df["Ticker"].isin(st.session_state.watchlist)].sort_values("Score",ascending=False)
        if wd.empty: st.warning("Ikke funnet i siste scan.")
        else: st.dataframe(formater_tabell(wd),width="stretch",hide_index=True)
        nc=min(len(st.session_state.watchlist),8); fc2=st.columns(nc)
        for i,tk in enumerate(sorted(st.session_state.watchlist)):
            with fc2[i%nc]:
                if st.button(f"❌ {tk}",key=f"rm_{tk}"):
                    st.session_state.watchlist.discard(tk); lagre_watchlist(st.session_state.watchlist); st.rerun()

    st.markdown("---")
    with st.expander("ℹ️ Trade Score, Signal og setup-logikk (v3)"):
        st.markdown("""
**Trade Score (0–10):**
| Poeng | Betingelse |
|-------|-----------|
| +2 | Over SMA 200 |
| +1 | Over SMA 50 |
| +2 | Nær SMA50 (±2%) |
| +1 | RSI 40–60 |
| +2 | Vol ratio > 1.2x |
| +1 | Vol ratio > 1.0x |
| +1 | Snittvolum > 500k |

**Trade Signal:**
- 🟢 **BUY** — Score ≥8, vol>1.0, nær SMA50
- 🟡 **WATCH** — Score ≥6
- 🔴 **SKIP** — Score <6

**Setup-typer:**
- 🟡 **Pullback** — Over SMA200, −2% til +1% fra SMA50, RSI 35–55
- 🟠 **Early Pullback** — Som Pullback, RSI 40–50, vol <1.0
- 🔵 **Breakout** — Over SMA200, nær 20d high, vol>1.5x, RSI>50
- 🟢 **Trend** — Over begge SMA, RSI 40–70
- 🟣 **Momentum** — Over begge SMA, positiv dag, vol>1.0
- 🔴 **Extended** — RSI>75 eller >8% over SMA50
        """)
    oslo_tid=datetime.now(ZoneInfo("Europe/Oslo")).strftime('%Y-%m-%d %H:%M')
    st.caption(f"Oppdatert: {oslo_tid} (Oslo) | {len(OSLO_TICKERS)} aksjer fra Euronext Oslo")

if __name__=="__main__": main()
