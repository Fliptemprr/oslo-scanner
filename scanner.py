"""
Oslo Børs Correction Radar – v6
===============================
Personlig radar for en liten, forhåndsgodkjent watchlist.

Radaren finner situasjoner der en aksje vi allerede ønsker å eie/trade har falt
uvanlig mye *for akkurat den aksjen*, viser om hovedtrenden fortsatt er intakt,
om kursen begynner å stabilisere seg, og skiller ut event-/nyhetsdrevne fall.

Radaren gir ALDRI BUY/SELL. Den sier: «her skjer det noe – undersøk denne.»

Kjernen er at hver aksje sammenlignes med sin egen historikk. DNB -7 % og
NAS -7 % er ikke samme hendelse, og skal ikke behandles likt.

Kjør:  streamlit run scanner.py
"""

import streamlit as st
from streamlit_autorefresh import st_autorefresh
import pandas as pd
import numpy as np
import altair as alt
import yfinance as yf
import html as html_lib
import json
import math
import time
import logging
import os
import tempfile
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Optional, Any

# ──────────────────────────────────────────────────────────────
# LOGGING
# ──────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("radar")


# ══════════════════════════════════════════════════════════════
# CONFIG
# Alle terskler bor her. Ingen viktige tall skal hardkodes
# nedover i motoren.
# ══════════════════════════════════════════════════════════════

SCANNER_CONFIG: dict[str, Any] = {

    # ── Historikk ──
    "historyYears": 3,
    "minimumHistoryYears": 2,

    # ── Swing-deteksjon (ATR-normalisert ZigZag) ──
    "swingAtrMultiplier": 1.5,

    # ── Statusterskler ──
    "correction": {"follow": 60, "correction": 75, "strong": 85},
    "recovery": {"stabilizing": 50, "confirmed": 70},
    "trend": {"minimumForReversal": 55},
    "volume": {"recoveryRatio": 1.2, "eventRatio": 2.0},

    # ── Correction Score: vekting av delscorene ──
    "correctionScoreWeights": {
        "percentile": 0.60,
        "technicalStretch": 0.20,
        "support": 0.20,
    },

    # ── Technical Stretch (0–100). SMA-avstand ATR-normaliseres ──
    "stretch": {
        "weights": {"rsi": 0.40, "sma20": 0.30, "sma50": 0.30},
        "rsiZero": 70.0,      # RSI >= dette gir 0 poeng
        "rsiFull": 30.0,      # RSI <= dette gir 100 poeng
        "sma20AtrFull": 3.0,  # (SMA20 - kurs) / ATR >= dette gir 100
        "sma50AtrFull": 5.0,
    },

    # ── Support Score: trapp på avstand i % til nærmeste bekreftede swing-low ──
    "supportSteps": [[1.0, 100], [2.0, 80], [3.0, 60], [5.0, 30]],

    # ── Correction Percentile ──
    "percentile": {
        "metric": "drawdownPct",        # eller "atrNormalizedDrawdown"
        "basis": "maxDepth",            # "maxDepth" = topp→bunn, "current" = topp→nå
        "minHistoricalCorrections": 5,  # under dette flagges historikken som tynn
        "minDrawdownPct": 3.0,          # mindre fall regnes ikke som korreksjon
    },

    # Én korreksjon = én hendelse: når en tidligere topp er høyere enn den
    # nåværende, og kursen aldri kom tilbake over den, forankres korreksjonen
    # i den høyere toppen. En liten rebound oppretter da ikke ny correctionId.
    "extendToHigherPriorPeaks": True,
    "maxCorrectionLookbackDays": 400,   # eldre topper regnes som nedtrend, ikke korreksjon

    # ── Trend Score: poengfordeling (sum = 100) ──
    "trendPoints": {
        "closeOverSma200": 25,
        "sma50OverSma200": 20,
        "sma50SlopeUp": 15,
        "sma200SlopeUp": 15,
        "higherLowStructure": 15,
        "closeOverSma50": 10,
    },
    "trendSlopeLookback": 20,

    # ── Recovery Score: poengfordeling ──
    "recoveryPoints": {
        "noNewLow3Days": 10,
        "higherLowConfirmed": 25,
        "rsiRising": 10,
        "closeOverSma20": 15,
        "breaksLocalResistance": 20,
        "greenDayHighVolume": 15,
        "positiveMomentum5d": 5,
    },
    "recoveryParams": {
        "noNewLowDays": 3,
        "rsiRisingLookback": 3,
        "localResistanceLookback": 10,
        "higherLowReboundAtr": 1.0,
    },

    # ── Event Risk ──
    "eventRisk": {
        "return1dFloorPct": 7.0,
        "return1dAtrMult": 2.5,
        "return3dFloorPct": 10.0,
        "return3dAtrMult": 4.0,
        "volumeReturn1dPct": 4.0,
        "gapFloorPct": 4.0,
        "gapAtrMult": 1.5,
    },

    # ── Fundamental gate (manuell i v1) ──
    "fundamentals": {"resetOnNewCorrection": True},

    # ── Varsler ──
    "alerts": {"severityStepPct": 3.0, "maxLogEntries": 200},
}

# ── Tolkningsbånd (kun visning) ──
TREND_BANDS = [(80, "STRONG TREND"), (60, "HEALTHY"), (40, "WEAKENED"), (0, "DOWNTREND")]
RECOVERY_BANDS = [
    (85, "STRONG RECOVERY"), (70, "CONFIRMED RECOVERY"),
    (50, "EARLY RECOVERY"), (30, "STABILIZING"), (0, "NO RECOVERY"),
]

# ── Datahenting ──
BATCH_SIZE = 15
BATCH_DELAY = 5
RETRY_DELAY_PER_TICKER = 2
HISTORY_DAYS = int(SCANNER_CONFIG["historyYears"] * 365) + 45
MIN_HISTORY_BARS = int(SCANNER_CONFIG["minimumHistoryYears"] * 252)
TRADING_DAYS_YEAR = 252

# ── Filer (config/state utenfor koden) ──
UNIVERSE_FILE = Path("watchlist.json")
FUNDAMENTALS_FILE = Path("fundamentals.json")
STATE_FILE = Path("radar_state.json")

# ── V1 watchlist. Kan endres i UI uten kodeendring. ──
DEFAULT_WATCHLIST = ["KOG.OL", "NOD.OL", "KIT.OL", "PROT.OL", "DNB.OL", "WAWI.OL", "NAS.OL"]

# ── Navneoppslag for hele Euronext Oslo. Brukes til å slå opp navn og
#    til «legg til»-velgeren i UI. Watchlisten er en delmengde av denne. ──
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


# ══════════════════════════════════════════════════════════════
# TYPES
# ══════════════════════════════════════════════════════════════

@dataclass
class HistoricalCorrection:
    """En avsluttet korreksjon: bekreftet topp → bekreftet bunn."""
    ticker: str
    peakDate: str
    troughDate: str
    drawdownPct: float
    durationDays: int
    atrNormalizedDrawdown: float


@dataclass
class ActiveCorrection:
    """Korreksjonen som pågår nå (eller sist observerte topp)."""
    id: str
    ticker: str
    peakDate: str
    peakPrice: float
    troughDate: str
    troughPrice: float
    currentPrice: float
    drawdownPct: float           # topp → nå (§7)
    maxDepthPct: float           # topp → bunn, dybden på hendelsen
    daysSincePeak: int
    recoveryPct: float
    active: bool
    peakConfirmed: bool          # topp bekreftet av ZigZag, ikke bare løpende maks
    peakIdx: int                 # posisjon i serien (intern bruk)
    troughIdx: int


@dataclass
class FundamentalCheck:
    """Manuell fundamental sjekk. Lagres per ticker + correctionId."""
    reportChecked: bool = False
    guidanceChecked: bool = False
    newsChecked: bool = False
    thesisIntact: bool = False
    correctionId: str = ""
    updated: str = ""
    stale: bool = False          # sjekken gjaldt en tidligere korreksjon

    @property
    def fundamentalsChecked(self) -> bool:
        """True når de tre første er bekreftet. thesisIntact teller ikke her."""
        return self.reportChecked and self.guidanceChecked and self.newsChecked


# Statuser
STATUS_WAIT = "WAIT"
STATUS_FOLLOW = "FOLLOW"
STATUS_CORRECTION = "CORRECTION"
STATUS_STRONG_CORRECTION = "STRONG_CORRECTION"
STATUS_STABILIZING = "STABILIZING"
STATUS_REVERSAL = "REVERSAL"
STATUS_EVENT_RISK = "EVENT_RISK"

STATUS_LABEL = {
    STATUS_EVENT_RISK: "🔴 EVENT RISK",
    STATUS_REVERSAL: "🟢 REVERSAL",
    STATUS_STABILIZING: "🔵 STABILIZING",
    STATUS_STRONG_CORRECTION: "🟠 STRONG CORRECTION",
    STATUS_CORRECTION: "🟡 CORRECTION",
    STATUS_FOLLOW: "⚪ FOLLOW",
    STATUS_WAIT: "⚫ WAIT",
}

# Sortering: det som krever oppmerksomhet først (§18)
STATUS_PRIORITY = {
    STATUS_EVENT_RISK: 0,
    STATUS_REVERSAL: 1,
    STATUS_STABILIZING: 2,
    STATUS_STRONG_CORRECTION: 3,
    STATUS_CORRECTION: 4,
    STATUS_FOLLOW: 5,
    STATUS_WAIT: 6,
}


# ══════════════════════════════════════════════════════════════
# INDICATORS
# ══════════════════════════════════════════════════════════════

def beregn_rsi(serie: pd.Series, periode: int = 14) -> pd.Series:
    """Wilder RSI. Uendret fra tidligere versjoner."""
    delta = serie.diff()
    gevinst = delta.where(delta > 0, 0.0)
    tap = -delta.where(delta < 0, 0.0)
    avg_g = gevinst.ewm(alpha=1 / periode, min_periods=periode).mean()
    avg_t = tap.ewm(alpha=1 / periode, min_periods=periode).mean()
    rs = avg_g / avg_t
    return 100.0 - (100.0 / (1.0 + rs))


def beregn_atr(high: pd.Series, low: pd.Series, close: pd.Series,
               periode: int = 14) -> pd.Series:
    """Wilder ATR. Grunnlaget for all ATR-normalisering i radaren."""
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / periode, min_periods=periode).mean()


def safe_pct(num: Optional[float], denom: Optional[float]) -> Optional[float]:
    """Prosentforskjell, eller None hvis nevneren er ugyldig."""
    if num is None or denom is None or denom == 0:
        return None
    return round(((num - denom) / denom) * 100, 2)


def _num(x: Any) -> Optional[float]:
    """Konverter til float, eller None hvis verdien ikke er endelig."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _last(serie: pd.Series) -> Optional[float]:
    """Siste endelige verdi i en serie."""
    if serie is None or len(serie) == 0:
        return None
    return _num(serie.iloc[-1])


def _bars_ago(serie: pd.Series, n: int) -> Optional[float]:
    """Verdi n barer tilbake."""
    if serie is None or len(serie) <= n:
        return None
    return _num(serie.iloc[-1 - n])


def calculate_indicators(df: pd.DataFrame) -> Optional[dict]:
    """
    Alle indikatorer for én aksje. Returnerer både serier (til swing-motoren)
    og siste verdier (til scorer og visning). None hvis historikken er for kort.
    """
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = df.columns.get_level_values(0)

    for kol in ("Open", "High", "Low", "Close", "Volume"):
        if kol not in df.columns:
            return None

    d = df.dropna(subset=["Close"]).copy()
    if len(d) < MIN_HISTORY_BARS:
        return None

    close, high, low = d["Close"], d["High"], d["Low"]
    open_, volume = d["Open"], d["Volume"]

    sma20 = close.rolling(20).mean()
    sma50 = close.rolling(50).mean()
    sma200 = close.rolling(200).mean()
    rsi = beregn_rsi(close, 14)
    atr = beregn_atr(high, low, close, 14)
    avg_vol20 = volume.rolling(20).mean()
    vol_ratio = volume / avg_vol20

    kurs = _last(close)
    atr_now = _last(atr)
    atr_pct = round(atr_now / kurs * 100, 2) if kurs and atr_now else None

    # ATR slik den så ut FØR dagens bar. Et kraftig endagsfall gir en enorm
    # true range som blåser opp ATR med én gang, og dermed hever terskelen
    # som skal fange opp nettopp det fallet. Event Risk må derfor måles mot
    # volatiliteten slik den var før hendelsen.
    atr_prev = _bars_ago(atr, 1)
    prev_c = _bars_ago(close, 1)
    atr_pct_prev = round(atr_prev / prev_c * 100, 2) if prev_c and atr_prev else atr_pct

    high_20d = _num(high.tail(20).max())
    high_60d = _num(high.tail(60).max())
    high_52w = _num(high.tail(TRADING_DAYS_YEAR).max())

    prev_close = _bars_ago(close, 1)
    aapning = _last(open_)
    gap_down_pct = None
    if prev_close and aapning is not None and prev_close > 0:
        gap_down_pct = round((prev_close - aapning) / prev_close * 100, 2)

    return {
        # Serier
        "close": close, "high": high, "low": low, "open": open_, "volume": volume,
        "sma20_s": sma20, "sma50_s": sma50, "sma200_s": sma200,
        "rsi_s": rsi, "atr_s": atr, "volRatio_s": vol_ratio,
        "index": d.index,

        # Siste verdier
        "close_now": kurs,
        "open_now": aapning,
        "prevClose": prev_close,
        "sma20": _last(sma20),
        "sma50": _last(sma50),
        "sma200": _last(sma200),
        "sma50Slope20d": (_last(sma50) - _bars_ago(sma50, SCANNER_CONFIG["trendSlopeLookback"]))
        if _last(sma50) is not None and _bars_ago(sma50, SCANNER_CONFIG["trendSlopeLookback"]) is not None else None,
        "sma200Slope20d": (_last(sma200) - _bars_ago(sma200, SCANNER_CONFIG["trendSlopeLookback"]))
        if _last(sma200) is not None and _bars_ago(sma200, SCANNER_CONFIG["trendSlopeLookback"]) is not None else None,
        "rsi": _last(rsi),
        "rsiPrev": _bars_ago(rsi, SCANNER_CONFIG["recoveryParams"]["rsiRisingLookback"]),
        "atr": atr_now,
        "atrPct": atr_pct,
        "atrPctPrev": atr_pct_prev,
        "volume_now": _last(volume),
        "avgVolume20d": _last(avg_vol20),
        "volumeRatio20d": round(_last(vol_ratio), 2) if _last(vol_ratio) else None,

        "return1d": safe_pct(kurs, prev_close),
        "return3d": safe_pct(kurs, _bars_ago(close, 3)),
        "momentum5d": safe_pct(kurs, _bars_ago(close, 5)),
        "return1m": safe_pct(kurs, _bars_ago(close, 21)),
        "return3m": safe_pct(kurs, _bars_ago(close, 63)),
        "return6m": safe_pct(kurs, _bars_ago(close, 126)),
        "gapDownPct": gap_down_pct,

        "high20d": high_20d,
        "high60d": high_60d,
        "high52w": high_52w,
        "drawdown52w": round((high_52w - kurs) / high_52w * 100, 2)
        if high_52w and kurs and high_52w > 0 else None,

        "bars": len(d),
        "lastDate": d.index[-1],
    }


# ══════════════════════════════════════════════════════════════
# SWINGS – ATR-normalisert ZigZag
# ══════════════════════════════════════════════════════════════

@dataclass
class SwingState:
    """Resultat av swing-deteksjonen."""
    pivots: list          # bekreftede vendepunkter, kronologisk
    direction: int        # 1 = stiger (leter etter topp), -1 = faller, 0 = ukjent
    highIdx: int          # siste høye ekstrem (bekreftet topp hvis direction == -1)
    highPrice: float
    lowIdx: int           # siste lave ekstrem (bekreftet bunn hvis direction == 1)
    lowPrice: float


def detect_swings(close: pd.Series, atr: pd.Series, multiplier: float) -> SwingState:
    """
    ATR-normalisert ZigZag på lukkekurs.

    Et vendepunkt bekreftes først når kursen har snudd ATR * multiplier fra
    ekstremverdien. Det er dette som gjør at en liten rebound inni en større
    korreksjon ikke oppretter en ny korreksjon: reboundet må være stort nok
    målt i aksjens egen volatilitet før bunnen regnes som bekreftet.
    """
    c = close.to_numpy(dtype=float)
    a = atr.to_numpy(dtype=float)
    n = len(c)
    pivots: list = []

    if n == 0:
        return SwingState([], 0, 0, float("nan"), 0, float("nan"))

    direction = 0
    hi_i, hi_p = 0, c[0]
    lo_i, lo_p = 0, c[0]

    def _pivot(idx: int, price: float, kind: str) -> dict:
        return {
            "idx": idx,
            "date": close.index[idx],
            "price": float(price),
            "kind": kind,
        }

    for i in range(1, n):
        thr = a[i] * multiplier
        if not math.isfinite(thr) or thr <= 0:
            # ATR ikke klar ennå – bare følg ekstremene
            if c[i] > hi_p:
                hi_i, hi_p = i, c[i]
            if c[i] < lo_p:
                lo_i, lo_p = i, c[i]
            continue

        if direction == 1:
            if c[i] >= hi_p:
                hi_i, hi_p = i, c[i]
            elif hi_p - c[i] >= thr:
                pivots.append(_pivot(hi_i, hi_p, "peak"))
                direction = -1
                lo_i, lo_p = i, c[i]

        elif direction == -1:
            if c[i] <= lo_p:
                lo_i, lo_p = i, c[i]
            elif c[i] - lo_p >= thr:
                pivots.append(_pivot(lo_i, lo_p, "trough"))
                direction = 1
                hi_i, hi_p = i, c[i]

        else:
            if c[i] > hi_p:
                hi_i, hi_p = i, c[i]
            if c[i] < lo_p:
                lo_i, lo_p = i, c[i]
            if hi_p - c[i] >= thr:
                pivots.append(_pivot(hi_i, hi_p, "peak"))
                direction = -1
                lo_i, lo_p = i, c[i]
            elif c[i] - lo_p >= thr:
                pivots.append(_pivot(lo_i, lo_p, "trough"))
                direction = 1
                hi_i, hi_p = i, c[i]

    return SwingState(pivots, direction, hi_i, float(hi_p), lo_i, float(lo_p))


def detect_historical_corrections(ticker: str, atr: pd.Series, swing: SwingState,
                                  cfg: dict) -> list:
    """
    Alle avsluttede korreksjoner: hver bekreftede topp fulgt av bekreftet bunn.
    Dette er aksjens eget referansemateriale.
    """
    min_dd = cfg["percentile"]["minDrawdownPct"]
    out: list = []
    pivots = swing.pivots

    for k in range(len(pivots) - 1):
        p, t = pivots[k], pivots[k + 1]
        if p["kind"] != "peak" or t["kind"] != "trough":
            continue
        if p["price"] <= 0:
            continue
        fall = p["price"] - t["price"]
        dd = fall / p["price"] * 100
        if dd < min_dd:
            continue
        atr_at_peak = _num(atr.iloc[p["idx"]])
        atr_norm = round(fall / atr_at_peak, 2) if atr_at_peak and atr_at_peak > 0 else float("nan")
        out.append(HistoricalCorrection(
            ticker=ticker,
            peakDate=p["date"].strftime("%Y-%m-%d"),
            troughDate=t["date"].strftime("%Y-%m-%d"),
            drawdownPct=round(dd, 2),
            durationDays=int((t["date"] - p["date"]).days),
            atrNormalizedDrawdown=atr_norm,
        ))
    return out


def _forankre_topp(swing: SwingState, siste_dato, cfg: dict) -> tuple:
    """
    Finn toppen korreksjonen skal forankres i.

    Utgangspunktet er siste høye ekstrem. Ligger det en HØYERE bekreftet topp
    lenger tilbake, og kursen aldri kom tilbake over den, er vi fortsatt inne i
    den større korreksjonen — da flyttes ankeret dit. Det er dette som gjør at
    banen 190 → 175 → 180 → 165 → 170 → 155 behandles som ÉN correction event
    med samme correctionId, i stedet for tre separate korreksjoner.
    """
    idx, pris = int(swing.highIdx), _num(swing.highPrice)
    if pris is None or not cfg.get("extendToHigherPriorPeaks", True):
        return idx, pris

    grense = pd.Timestamp(siste_dato) - pd.Timedelta(days=cfg["maxCorrectionLookbackDays"])

    for p in reversed([q for q in swing.pivots if q["kind"] == "peak"]):
        if p["idx"] >= idx:
            continue
        if p["date"] < grense:
            # Eldre enn vinduet: dette er nedtrend, ikke en pågående korreksjon.
            break
        if p["price"] > pris:
            idx, pris = p["idx"], p["price"]
        # Lavere topper er rebound-topper inne i den samme korreksjonen
        # og skal hverken flytte ankeret eller stoppe søket.
    return idx, pris


def detect_current_correction(ticker: str, close: pd.Series, swing: SwingState,
                              cfg: dict = SCANNER_CONFIG) -> Optional[ActiveCorrection]:
    """
    Korreksjonen som pågår nå.

    Toppen hentes fra _forankre_topp(). Er trenden fallende er dette en
    ZigZag-bekreftet topp; stiger den fortsatt er det den løpende toppen, som
    gjør at en fersk korreksjon fanges opp tidlig i stedet for å vente på
    ATR-bekreftelse.
    """
    n = len(close)
    if n == 0:
        return None

    peak_idx, peak_price = _forankre_topp(swing, close.index[-1], cfg)
    if peak_price is None or peak_price <= 0:
        return None

    seg = close.iloc[peak_idx:].to_numpy(dtype=float)
    trough_off = int(np.argmin(seg))
    trough_idx = peak_idx + trough_off
    trough_price = float(seg[trough_off])
    current_price = float(close.iloc[-1])

    drawdown_pct = round((peak_price - current_price) / peak_price * 100, 2)
    max_depth_pct = round((peak_price - trough_price) / peak_price * 100, 2)
    recovery_pct = round((current_price - trough_price) / trough_price * 100, 2) \
        if trough_price > 0 else 0.0
    peak_date = close.index[peak_idx]

    return ActiveCorrection(
        id=f"{ticker}:{peak_date.strftime('%Y-%m-%d')}",
        ticker=ticker,
        peakDate=peak_date.strftime("%Y-%m-%d"),
        peakPrice=round(peak_price, 4),
        troughDate=close.index[trough_idx].strftime("%Y-%m-%d"),
        troughPrice=round(trough_price, 4),
        currentPrice=round(current_price, 4),
        drawdownPct=drawdown_pct,
        maxDepthPct=max_depth_pct,
        daysSincePeak=int((close.index[-1] - peak_date).days),
        recoveryPct=recovery_pct,
        active=drawdown_pct > 0,
        peakConfirmed=swing.direction == -1,
        peakIdx=peak_idx,
        troughIdx=trough_idx,
    )


def percentile_rank(current: float, history: list) -> float:
    """Andel av historiske korreksjoner som er mindre eller lik dagens (0–100)."""
    valid = [x for x in history if isinstance(x, (int, float)) and math.isfinite(x)]
    if not valid:
        return 0.0
    below = sum(1 for x in valid if x <= current)
    return round(below / len(valid) * 100, 1)


def calculate_correction_percentile(current: Optional[ActiveCorrection],
                                    history: list, atr_at_peak: Optional[float],
                                    cfg: dict) -> tuple:
    """
    Hvor ekstrem dagens korreksjon er mot aksjens egen historikk.
    Returnerer (percentil, antall sammenlignbare korreksjoner).

    Korreksjoner med samme topp som den aktive filtreres bort, slik at en
    korreksjon ikke sammenlignes med seg selv.
    """
    if current is None:
        return 0.0, 0

    metric = cfg["percentile"]["metric"]
    sammenlign = [h for h in history if h.peakDate != current.peakDate]

    # Historiske korreksjoner måles topp→bunn. Med basis "maxDepth" måles
    # dagens korreksjon på samme måte, slik at sammenligningen er ekte
    # eple-mot-eple og ikke krymper etter hvert som kursen henter seg inn.
    bruk_dybde = cfg["percentile"].get("basis", "maxDepth") == "maxDepth"
    referansepris = current.troughPrice if bruk_dybde else current.currentPrice

    if metric == "atrNormalizedDrawdown":
        if not atr_at_peak or atr_at_peak <= 0:
            return 0.0, 0
        naa = (current.peakPrice - referansepris) / atr_at_peak
        verdier = [h.atrNormalizedDrawdown for h in sammenlign]
    else:
        naa = current.maxDepthPct if bruk_dybde else current.drawdownPct
        verdier = [h.drawdownPct for h in sammenlign]

    return percentile_rank(naa, verdier), len(verdier)


# ══════════════════════════════════════════════════════════════
# SCORES
# Tre separate scorer. De svarer på tre forskjellige spørsmål og
# skal ikke slås sammen til én opportunity score i v1.
# ══════════════════════════════════════════════════════════════

def _skalér(verdi: Optional[float], null_ved: float, full_ved: float) -> float:
    """Lineær skalering til 0–100 mellom to grenser."""
    if verdi is None or not math.isfinite(verdi):
        return 0.0
    if full_ved == null_ved:
        return 0.0
    andel = (verdi - null_ved) / (full_ved - null_ved)
    return max(0.0, min(100.0, andel * 100.0))


def nearest_support(current_price: float, swing: SwingState,
                    current: Optional[ActiveCorrection]) -> Optional[float]:
    """
    Nærmeste bekreftede swing-low. Bunnen i den pågående korreksjonen regnes
    ikke som støtte – den er ikke bekreftet ennå. Bekreftede bunner tidligere
    inne i samme korreksjon teller derimot med; gammel støtte er ofte nettopp
    det nivået som testes på nytt.
    """
    kandidater = [
        p["price"] for p in swing.pivots
        if p["kind"] == "trough" and p["price"] > 0
        and (current is None or p["idx"] < current.troughIdx)
    ]
    if not kandidater:
        return None
    return min(kandidater, key=lambda p: abs(current_price - p))


def support_score(distance_pct: Optional[float], steps: list) -> float:
    """Trapp: jo nærmere bekreftet støtte, jo høyere score."""
    if distance_pct is None or not math.isfinite(distance_pct):
        return 0.0
    for grense, poeng in steps:
        if distance_pct <= grense:
            return float(poeng)
    return 0.0


def technical_stretch_score(ind: dict, cfg: dict) -> tuple:
    """
    Hvor teknisk utstrukket er kursen på nedsiden.
    SMA-avstand ATR-normaliseres, slik at en volatil NAS ikke måles
    med samme linjal som en rolig DNB.
    """
    s = cfg["stretch"]
    kurs, atr = ind.get("close_now"), ind.get("atr")

    rsi_del = _skalér(ind.get("rsi"), s["rsiZero"], s["rsiFull"])

    if atr and atr > 0 and kurs is not None:
        z20 = (ind["sma20"] - kurs) / atr if ind.get("sma20") is not None else None
        z50 = (ind["sma50"] - kurs) / atr if ind.get("sma50") is not None else None
    else:
        z20 = z50 = None

    sma20_del = _skalér(z20, 0.0, s["sma20AtrFull"])
    sma50_del = _skalér(z50, 0.0, s["sma50AtrFull"])

    w = s["weights"]
    total = rsi_del * w["rsi"] + sma20_del * w["sma20"] + sma50_del * w["sma50"]
    return round(total, 1), {
        "rsi": round(rsi_del, 1),
        "sma20": round(sma20_del, 1),
        "sma50": round(sma50_del, 1),
        "atrZ20": round(z20, 2) if z20 is not None else None,
        "atrZ50": round(z50, 2) if z50 is not None else None,
    }


def calculate_correction_score(percentile: float, stretch: float,
                               support: float, cfg: dict) -> float:
    """CorrectionScore = percentil 60 % + stretch 20 % + støtte 20 %."""
    w = cfg["correctionScoreWeights"]
    total = (percentile * w["percentile"]
             + stretch * w["technicalStretch"]
             + support * w["support"])
    return round(max(0.0, min(100.0, total)), 1)


def has_higher_low_structure(swing: SwingState) -> bool:
    """De to siste bekreftede bunnene: er den nyeste høyere enn den forrige?"""
    bunner = [p["price"] for p in swing.pivots if p["kind"] == "trough"]
    if len(bunner) < 2:
        return False
    return bunner[-1] > bunner[-2]


def calculate_trend_score(ind: dict, swing: SwingState, cfg: dict) -> tuple:
    """
    Er den overordnede kursstrukturen fortsatt frisk?
    Kurs under SMA200 diskvalifiserer ikke lenger – det trekker bare score.
    """
    p = cfg["trendPoints"]
    kurs = ind.get("close_now")
    sma50, sma200 = ind.get("sma50"), ind.get("sma200")

    deler = {
        "closeOverSma200": bool(kurs is not None and sma200 is not None and kurs > sma200),
        "sma50OverSma200": bool(sma50 is not None and sma200 is not None and sma50 > sma200),
        "sma50SlopeUp": bool(ind.get("sma50Slope20d") is not None and ind["sma50Slope20d"] > 0),
        "sma200SlopeUp": bool(ind.get("sma200Slope20d") is not None and ind["sma200Slope20d"] > 0),
        "higherLowStructure": has_higher_low_structure(swing),
        "closeOverSma50": bool(kurs is not None and sma50 is not None and kurs > sma50),
    }
    score = sum(p[k] for k, v in deler.items() if v)
    return float(min(score, 100)), deler


def _confirmed_higher_low(close: pd.Series, current: ActiveCorrection,
                          atr_now: Optional[float], cfg: dict) -> tuple:
    """
    Bekreftet higher low etter korreksjonsbunnen:
    kursen må først ha reist seg ATR * faktor fra bunnen, deretter satt en
    ny lokal bunn som holdt seg over korreksjonsbunnen, og nå ligge over den.
    """
    c = close.to_numpy(dtype=float)
    n = len(c)
    t_idx = current.troughIdx
    if not atr_now or atr_now <= 0 or t_idx >= n - 2:
        return False, None

    grense = current.troughPrice + atr_now * cfg["recoveryParams"]["higherLowReboundAtr"]
    rebound = next((i for i in range(t_idx + 1, n) if c[i] >= grense), None)
    if rebound is None or rebound >= n - 2:
        return False, None

    etter = c[rebound + 1:]
    if len(etter) == 0:
        return False, None
    hl_idx = rebound + 1 + int(np.argmin(etter))
    hl_pris = float(c[hl_idx])

    if hl_pris <= current.troughPrice or hl_idx >= n - 1 or c[-1] <= hl_pris:
        return False, None
    return True, round(hl_pris, 4)


def calculate_recovery_score(ind: dict, current: Optional[ActiveCorrection],
                             cfg: dict) -> tuple:
    """Har markedet begynt å vise tegn til at korreksjonen kan være ferdig?"""
    p = cfg["recoveryPoints"]
    rp = cfg["recoveryParams"]
    close = ind["close"]
    high = ind["high"]
    n = len(close)

    if current is not None:
        no_new_low = current.troughIdx <= n - 1 - rp["noNewLowDays"]
        higher_low, hl_pris = _confirmed_higher_low(close, current, ind.get("atr"), cfg)
    else:
        no_new_low, higher_low, hl_pris = False, False, None

    lookback = rp["localResistanceLookback"]
    lokal_motstand = _num(high.iloc[-(lookback + 1):-1].max()) if n > lookback else None

    rsi, rsi_prev = ind.get("rsi"), ind.get("rsiPrev")
    kurs, sma20 = ind.get("close_now"), ind.get("sma20")
    ret1d, vr = ind.get("return1d"), ind.get("volumeRatio20d")
    mom5d = ind.get("momentum5d")

    deler = {
        "noNewLow3Days": bool(no_new_low),
        "higherLowConfirmed": bool(higher_low),
        "rsiRising": bool(rsi is not None and rsi_prev is not None and rsi > rsi_prev),
        "closeOverSma20": bool(kurs is not None and sma20 is not None and kurs > sma20),
        "breaksLocalResistance": bool(kurs is not None and lokal_motstand is not None
                                      and kurs > lokal_motstand),
        "greenDayHighVolume": bool(ret1d is not None and ret1d > 0
                                   and vr is not None and vr >= cfg["volume"]["recoveryRatio"]),
        "positiveMomentum5d": bool(mom5d is not None and mom5d > 0),
    }
    score = sum(p[k] for k, v in deler.items() if v)
    deler["higherLowPrice"] = hl_pris
    deler["localResistance"] = round(lokal_motstand, 4) if lokal_motstand else None
    return float(min(score, 100)), deler


def detect_event_risk(ind: dict, cfg: dict) -> tuple:
    """
    Skiller ut fall som mest sannsynlig er hendelsesdrevne (resultatvarsel,
    emisjon, regulatorisk nyhet). Et slikt fall skal ikke belønnes som en
    fin korreksjon før noen har sett på hva som faktisk skjedde.
    """
    e = cfg["eventRisk"]
    # Bruk gårsdagens ATR – se kommentar i calculate_indicators().
    atr_pct = ind.get("atrPctPrev") or ind.get("atrPct") or 0.0
    r1d = ind.get("return1d")
    r3d = ind.get("return3d")
    vr = ind.get("volumeRatio20d")
    gap = ind.get("gapDownPct")

    abnormal_1d = bool(r1d is not None and r1d <= -max(e["return1dFloorPct"], atr_pct * e["return1dAtrMult"]))
    abnormal_3d = bool(r3d is not None and r3d <= -max(e["return3dFloorPct"], atr_pct * e["return3dAtrMult"]))
    abnormal_vol = bool(vr is not None and vr >= cfg["volume"]["eventRatio"]
                        and r1d is not None and r1d <= -e["volumeReturn1dPct"])
    abnormal_gap = bool(gap is not None and gap >= max(e["gapFloorPct"], atr_pct * e["gapAtrMult"]))

    grunner = {
        "abnormal1D": abnormal_1d,
        "abnormal3D": abnormal_3d,
        "abnormalVolume": abnormal_vol,
        "abnormalGap": abnormal_gap,
    }
    return (abnormal_1d or abnormal_3d or abnormal_vol or abnormal_gap), grunner


# ══════════════════════════════════════════════════════════════
# ENGINE
# ══════════════════════════════════════════════════════════════

def classify_status(r: dict, cfg: dict = SCANNER_CONFIG) -> str:
    """
    Statusmotor. Rekkefølgen er bindende.

    Event risk overstyrer alt inntil manuell kontroll. REVERSAL krever både
    høy recovery, frisk nok trend og godkjent fundamental sjekk – høy
    Correction Score alene er aldri nok.
    """
    c = cfg["correction"]
    rec = cfg["recovery"]

    if r["eventRisk"] and not r["fundamentalsChecked"]:
        return STATUS_EVENT_RISK

    if r["correctionScore"] < c["follow"]:
        return STATUS_WAIT

    if r["correctionScore"] < c["correction"]:
        return STATUS_FOLLOW

    if r["correctionScore"] >= c["strong"] and r["recoveryScore"] < rec["stabilizing"]:
        return STATUS_STRONG_CORRECTION

    if r["correctionScore"] >= c["correction"] and r["recoveryScore"] < rec["stabilizing"]:
        return STATUS_CORRECTION

    if (r["correctionScore"] >= c["correction"]
            and rec["stabilizing"] <= r["recoveryScore"] < rec["confirmed"]):
        return STATUS_STABILIZING

    if (r["correctionScore"] >= c["correction"]
            and r["recoveryScore"] >= rec["confirmed"]
            and r["trendScore"] >= cfg["trend"]["minimumForReversal"]
            and r["fundamentalsChecked"]
            and r["thesisIntact"]):
        return STATUS_REVERSAL

    return STATUS_FOLLOW


def _band(score: float, bands: list) -> str:
    for grense, navn in bands:
        if score >= grense:
            return navn
    return bands[-1][1]


def resolve_fundamentals(store: dict, ticker: str, correction_id: str,
                         cfg: dict) -> FundamentalCheck:
    """
    Hent lagret fundamental sjekk. Gjelder sjekken en tidligere korreksjon,
    nullstilles den – ny korreksjon krever ny gjennomgang.
    """
    raw = store.get(ticker)
    if not raw:
        return FundamentalCheck(correctionId=correction_id)

    fc = FundamentalCheck(
        reportChecked=bool(raw.get("reportChecked", False)),
        guidanceChecked=bool(raw.get("guidanceChecked", False)),
        newsChecked=bool(raw.get("newsChecked", False)),
        thesisIntact=bool(raw.get("thesisIntact", False)),
        correctionId=str(raw.get("correctionId", "")),
        updated=str(raw.get("updated", "")),
    )
    if (cfg["fundamentals"]["resetOnNewCorrection"]
            and fc.correctionId and fc.correctionId != correction_id):
        return FundamentalCheck(correctionId=correction_id, stale=True)
    fc.correctionId = correction_id
    return fc


def scan_stock(ticker: str, df: pd.DataFrame, fund_store: dict,
               cfg: dict = SCANNER_CONFIG) -> Optional[dict]:
    """
    Hovedmotoren for én aksje: indikatorer → swings → korreksjonshistorikk →
    percentil → tre scorer → event risk → fundamental gate → status.
    """
    ind = calculate_indicators(df)
    if ind is None:
        log.warning(f"[{ticker}] for kort historikk, hoppes over")
        return None

    swing = detect_swings(ind["close"], ind["atr_s"], cfg["swingAtrMultiplier"])
    historikk = detect_historical_corrections(ticker, ind["atr_s"], swing, cfg)
    current = detect_current_correction(ticker, ind["close"], swing, cfg)

    atr_at_peak = _num(ind["atr_s"].iloc[current.peakIdx]) if current else None
    percentile, n_hist = calculate_correction_percentile(current, historikk, atr_at_peak, cfg)

    stretch, stretch_deler = technical_stretch_score(ind, cfg)

    støtte_nivå = nearest_support(ind["close_now"], swing, current)
    støtte_avstand = (abs(ind["close_now"] - støtte_nivå) / støtte_nivå * 100
                      if støtte_nivå and støtte_nivå > 0 else None)
    støtte = support_score(støtte_avstand, cfg["supportSteps"])

    correction_score = calculate_correction_score(percentile, stretch, støtte, cfg)
    trend_score, trend_deler = calculate_trend_score(ind, swing, cfg)
    recovery_score, recovery_deler = calculate_recovery_score(ind, current, cfg)
    event_risk, event_grunner = detect_event_risk(ind, cfg)

    correction_id = current.id if current else f"{ticker}:ingen"
    fund = resolve_fundamentals(fund_store, ticker, correction_id, cfg)

    resultat = {
        "ticker": ticker,
        "Ticker": ticker.replace(".OL", ""),
        "Navn": OSLO_TICKERS.get(ticker, ticker),

        "correctionScore": correction_score,
        "trendScore": trend_score,
        "recoveryScore": recovery_score,
        "eventRisk": event_risk,
        "fundamentalsChecked": fund.fundamentalsChecked,
        "thesisIntact": fund.thesisIntact,
    }
    resultat["status"] = classify_status(resultat, cfg)

    # Støtteinformasjon til visning – påvirker ikke statusen
    resultat.update({
        "correctionId": correction_id,
        "currentCorrection": current,
        "correctionPercentile": percentile,
        "historiskeKorreksjoner": historikk,
        "antallHistoriske": n_hist,
        "tynnHistorikk": n_hist < cfg["percentile"]["minHistoricalCorrections"],
        "stretchScore": stretch,
        "stretchDeler": stretch_deler,
        "supportScore": støtte,
        "supportNivå": round(støtte_nivå, 4) if støtte_nivå else None,
        "supportAvstandPct": round(støtte_avstand, 2) if støtte_avstand is not None else None,
        "trendDeler": trend_deler,
        "trendBand": _band(trend_score, TREND_BANDS),
        "recoveryDeler": recovery_deler,
        "recoveryBand": _band(recovery_score, RECOVERY_BANDS),
        "eventGrunner": event_grunner,
        "fundamental": fund,
        "swingPivots": len(swing.pivots),
        "ind": ind,
    })
    return resultat


# ══════════════════════════════════════════════════════════════
# CONFIG-STATE PÅ DISK – watchlist, fundamental sjekk, varselhistorikk
# ══════════════════════════════════════════════════════════════

def _les_json(path: Path, standard):
    if not path.exists():
        return standard
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.warning(f"Kunne ikke lese {path}: {e}")
        return standard


def _skriv_json(path: Path, data) -> None:
    """Atomisk skriving: temp-fil først, så rename."""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", delete=False, dir=str(path.parent) or ".",
            suffix=".tmp", encoding="utf-8"
        ) as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            tmp = f.name
        os.replace(tmp, path)
    except OSError as e:
        log.error(f"Kunne ikke lagre {path}: {e}")


def last_universe() -> list:
    """
    Watchlist fra disk. Format: [{"ticker","name","enabled"}, ...]
    Gammelt format (flat liste med ticker-strenger) migreres automatisk.
    """
    raw = _les_json(UNIVERSE_FILE, None)
    if raw is None:
        return [_universe_rad(t) for t in DEFAULT_WATCHLIST]

    ut = []
    for e in raw:
        if isinstance(e, str):
            ut.append(_universe_rad(e))
        elif isinstance(e, dict) and e.get("ticker"):
            t = _normaliser_ticker(str(e["ticker"]))
            ut.append({
                "ticker": t,
                "name": e.get("name") or OSLO_TICKERS.get(t, t),
                "enabled": bool(e.get("enabled", True)),
            })
    return ut or [_universe_rad(t) for t in DEFAULT_WATCHLIST]


def _normaliser_ticker(t: str) -> str:
    """
    KOG → KOG.OL, men bare når tickeren faktisk finnes på Oslo Børs.
    Ukjente tickere uten suffiks står urørt, slik at amerikanske aksjer
    (AAPL, MSFT) kan legges til senere uten kodeendring.
    """
    t = t.strip().upper()
    if "." in t:
        return t
    return f"{t}.OL" if f"{t}.OL" in OSLO_TICKERS else t


def _universe_rad(t: str) -> dict:
    t = _normaliser_ticker(t)
    return {"ticker": t, "name": OSLO_TICKERS.get(t, t), "enabled": True}


def lagre_universe(universe: list) -> None:
    _skriv_json(UNIVERSE_FILE, universe)


def last_fundamentals() -> dict:
    raw = _les_json(FUNDAMENTALS_FILE, {})
    return raw if isinstance(raw, dict) else {}


def lagre_fundamentals(store: dict) -> None:
    _skriv_json(FUNDAMENTALS_FILE, store)


def last_state() -> dict:
    raw = _les_json(STATE_FILE, {})
    if not isinstance(raw, dict):
        raw = {}
    raw.setdefault("statuses", {})
    raw.setdefault("alerts", [])
    return raw


def lagre_state(state: dict) -> None:
    _skriv_json(STATE_FILE, state)


# ══════════════════════════════════════════════════════════════
# VARSLER
# Én korreksjon er én hendelse. Varsel når status endres, eller når
# alvorlighetsgraden øker vesentlig innenfor samme correctionId.
# ══════════════════════════════════════════════════════════════

VARSEL_OVERGANGER = {
    (STATUS_FOLLOW, STATUS_CORRECTION),
    (STATUS_CORRECTION, STATUS_STRONG_CORRECTION),
    (STATUS_CORRECTION, STATUS_STABILIZING),
    (STATUS_STABILIZING, STATUS_REVERSAL),
}


def _varselsammendrag(r: dict, fra: Optional[str] = None) -> str:
    """Én linje til varselpanelet."""
    cc = r.get("currentCorrection")
    biter = []
    if fra:
        biter.append(f"fra {fra.replace('_', ' ')}")
    if cc:
        biter.append(f"-{cc.drawdownPct:.1f} %")
    biter.append(f"percentil {r['correctionPercentile']:.0f}")
    biter.append(f"Trend {r['trendBand']}")
    return " · ".join(biter)


def _varseltekst(r: dict, tittel: str) -> str:
    cc = r.get("currentCorrection")
    fall = f"{cc.drawdownPct:.1f} %" if cc else "—"
    return (
        f"{r['Ticker']} – {tittel}\n\n"
        f"Correction Score: {r['correctionScore']:.0f}\n"
        f"Korreksjon: -{fall}\n"
        f"Historisk percentil: {r['correctionPercentile']:.0f}\n\n"
        f"Trend: {r['trendBand']}\n"
        f"Recovery: {r['recoveryBand']}\n\n"
        f"Åpne radaren for gjennomgang."
    )


def evaluer_varsler(resultater: list, state: dict, cfg: dict = SCANNER_CONFIG) -> list:
    """
    Sammenlign mot forrige lagrede tilstand og produser varsler.
    Aldri varsel for WAIT. Idempotent: uendret tilstand gir ingen nye varsler.
    """
    nye = []
    statuses = state.setdefault("statuses", {})
    naa = datetime.now(ZoneInfo("Europe/Oslo")).strftime("%Y-%m-%d %H:%M")

    for r in resultater:
        t = r["ticker"]
        forrige = statuses.get(t, {})
        forrige_status = forrige.get("status")
        forrige_id = forrige.get("correctionId")
        forrige_dd = forrige.get("drawdownPct")
        cc = r.get("currentCorrection")
        dd = cc.drawdownPct if cc else None

        ny_status = r["status"]

        if ny_status != forrige_status and ny_status != STATUS_WAIT:
            if ny_status == STATUS_EVENT_RISK:
                nye.append({"tid": naa, "ticker": r["Ticker"], "type": "EVENT_RISK",
                            "correctionId": r["correctionId"],
                            "sammendrag": _varselsammendrag(r),
                            "tekst": _varseltekst(r, "EVENT RISK – UNDERSØK")})
            elif (forrige_status, ny_status) in VARSEL_OVERGANGER:
                nye.append({"tid": naa, "ticker": r["Ticker"], "type": ny_status,
                            "correctionId": r["correctionId"],
                            "sammendrag": _varselsammendrag(r, forrige_status),
                            "tekst": _varseltekst(r, ny_status.replace("_", " "))})

        elif (ny_status == forrige_status
              and forrige_id == r["correctionId"]
              and dd is not None and forrige_dd is not None
              and dd - forrige_dd >= cfg["alerts"]["severityStepPct"]
              and ny_status not in (STATUS_WAIT, STATUS_FOLLOW)):
            nye.append({"tid": naa, "ticker": r["Ticker"], "type": f"{ny_status}+",
                        "correctionId": r["correctionId"],
                        "sammendrag": f"severity økt · {_varselsammendrag(r)}",
                        "tekst": _varseltekst(r, f"{ny_status.replace('_', ' ')} – severity økt")})

        statuses[t] = {
            "status": ny_status,
            "correctionId": r["correctionId"],
            "drawdownPct": dd,
            "oppdatert": naa,
        }

    if nye:
        logg = nye + state.get("alerts", [])
        state["alerts"] = logg[: cfg["alerts"]["maxLogEntries"]]
    return nye


# ══════════════════════════════════════════════════════════════
# DATAHENTING – uendret mekanikk fra tidligere versjon
# curl_cffi + threads=False er nødvendig mot Yahoo rate limit.
# ══════════════════════════════════════════════════════════════

def _lag_session():
    """Curl_cffi Chrome-session for å unngå Yahoo TLS rate limit."""
    try:
        from curl_cffi import requests as cffi_requests
        return cffi_requests.Session(impersonate="chrome")
    except ImportError:
        log.warning("curl_cffi ikke installert, bruker default session (kan gi 429)")
        return None


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


@st.cache_data(ttl=600, show_spinner=False)
def hent_prisdata(tickers: tuple) -> dict:
    """
    Last ned daglig OHLCV for watchlisten. Kun rådata caches – scoringen
    kjøres på nytt ved hver rerun, slik at fundamental-avkrysning slår
    gjennom umiddelbart uten ny nedlasting.
    """
    liste = list(tickers)
    if not liste:
        return {}

    start = datetime.now() - timedelta(days=HISTORY_DAYS)
    end = datetime.now()
    session = _lag_session()
    alle: dict = {}

    progress = st.progress(0, text="Henter data...")
    total_b = (len(liste) - 1) // BATCH_SIZE + 1
    for bn in range(0, len(liste), BATCH_SIZE):
        batch = liste[bn:bn + BATCH_SIZE]
        bi = bn // BATCH_SIZE + 1
        progress.progress(min(bi / total_b, 1.0), text=f"Batch {bi}/{total_b}")
        alle.update(_download_batch(batch, session, start, end))
        if bn + BATCH_SIZE < len(liste):
            time.sleep(BATCH_DELAY)

    mangler = [t for t in liste if t not in alle]
    if mangler:
        progress.progress(0.95, text=f"Retry {len(mangler)} manglende...")
        time.sleep(BATCH_DELAY)
        alle.update(_retry_missing(mangler, session, start, end))

    progress.empty()
    log.info(f"Lastet ned {len(alle)}/{len(liste)} aksjer")
    return alle


def kjor_scan(prisdata: dict, fund_store: dict, cfg: dict = SCANNER_CONFIG) -> list:
    """Kjør motoren på alle nedlastede aksjer."""
    ut = []
    for ticker, df in prisdata.items():
        try:
            r = scan_stock(ticker, df, fund_store, cfg)
            if r is not None:
                ut.append(r)
        except Exception as e:
            log.warning(f"[{ticker}] scan feilet: {type(e).__name__}: {e}")
    return ut


# ══════════════════════════════════════════════════════════════
# DESIGN TOKENS – fra mockupen «1a TERMINAL»
# ══════════════════════════════════════════════════════════════

DC = {
    "bg": "#0B0D10",        # hovedflate
    "rail": "#090B0E",      # sidepanel venstre og høyre
    "panel": "#10131A",     # tellere, valgt rad
    "kort": "#14171C",
    "inset": "#0D0F13",
    "spor": "#14181E",      # bakgrunn i scorebar
    "linje": "#1E232B",
    "kant": "#2A3038",
    "knapp": "#1A1F27",
    "tekst": "#E7EBF0",
    "dempet": "#8B95A5",
    "svak": "#6B7686",
    "svakest": "#4E5765",
    "blaa": "#4DA3FF",
    "gronn": "#2FD48F",
    "roed": "#FF4757",
    "orange": "#FF9130",
    "gul": "#E8C547",
}

MONO = "'IBM Plex Mono', ui-monospace, SFMono-Regular, Menlo, monospace"
SANS = "'IBM Plex Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"

STATUS_FARGE = {
    STATUS_EVENT_RISK: DC["roed"],
    STATUS_REVERSAL: DC["gronn"],
    STATUS_STABILIZING: DC["blaa"],
    STATUS_STRONG_CORRECTION: DC["orange"],
    STATUS_CORRECTION: DC["gul"],
    STATUS_FOLLOW: "#A5AFBF",
    STATUS_WAIT: "#6B7686",
}

STATUS_TEKST = {
    STATUS_EVENT_RISK: "EVENT RISK",
    STATUS_REVERSAL: "REVERSAL",
    STATUS_STABILIZING: "STABILIZING",
    STATUS_STRONG_CORRECTION: "STRONG CORRECTION",
    STATUS_CORRECTION: "CORRECTION",
    STATUS_FOLLOW: "FOLLOW",
    STATUS_WAIT: "WAIT",
}

STATUS_KORT = {**STATUS_TEKST, STATUS_STRONG_CORRECTION: "STRONG CORR"}

# Rekkefølgen på statustellerne i toppen
TELLER_REKKEFOLGE = [
    STATUS_EVENT_RISK, STATUS_REVERSAL, STATUS_STABILIZING,
    STATUS_STRONG_CORRECTION, STATUS_CORRECTION, STATUS_FOLLOW, STATUS_WAIT,
]

# Sonene brukes i KORT-visningen
SONER = [
    {"navn": "KREVER GJENNOMGANG", "farge": DC["roed"], "form": "stor",
     "statuser": [STATUS_EVENT_RISK, STATUS_REVERSAL]},
    {"navn": "FØLG", "farge": DC["blaa"], "form": "medium",
     "statuser": [STATUS_STABILIZING, STATUS_STRONG_CORRECTION, STATUS_CORRECTION]},
    {"navn": "ROLIG", "farge": DC["svak"], "form": "kompakt",
     "statuser": [STATUS_FOLLOW, STATUS_WAIT]},
]

SORTERINGSVALG = {
    "Prioritet": None,
    "Correction Score": "correctionScore",
    "Recovery Score": "recoveryScore",
    "Trend Score": "trendScore",
    "Korreksjon %": "korreksjon",
    "Ticker": "Ticker",
}

TREND_ETIKETTER = {
    "closeOverSma200": "Kurs over SMA200",
    "sma50OverSma200": "SMA50 over SMA200",
    "sma50SlopeUp": "SMA50 stigende (20D)",
    "sma200SlopeUp": "SMA200 stigende (20D)",
    "higherLowStructure": "Higher-low-struktur",
    "closeOverSma50": "Kurs over SMA50",
}

RECOVERY_ETIKETTER = {
    "noNewLow3Days": "Ingen ny bunn på 3 dager",
    "higherLowConfirmed": "Bekreftet higher low",
    "rsiRising": "RSI14 stigende",
    "closeOverSma20": "Kurs over SMA20",
    "breaksLocalResistance": "Bryter lokal motstand",
    "greenDayHighVolume": "Grønn dag med volum",
    "positiveMomentum5d": "Positiv 5D-momentum",
}

EVENT_ETIKETTER = {
    "abnormal1D": "unormalt 1D-fall",
    "abnormal3D": "unormalt 3D-fall",
    "abnormalVolume": "fall på unormalt volum",
    "abnormalGap": "unormalt gap ned",
}


def _rgba(hex_farge: str, alpha: float) -> str:
    h = hex_farge.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def f(v: Optional[float], desimaler: int = 2, suffix: str = "") -> str:
    """Formater tall for visning. None blir «—»."""
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return "—"
    return f"{v:,.{desimaler}f}".replace(",", " ") + suffix


def _esc(v: Any) -> str:
    return html_lib.escape(str(v))


def _n(t: str) -> str:
    """Ticker → trygg CSS/nøkkel-suffiks."""
    return t.replace(".", "-")


# ══════════════════════════════════════════════════════════════
# CSS
# st.html og ikke st.markdown: markdown-sanitizeren stripper <style>.
# ══════════════════════════════════════════════════════════════

def injiser_css(rad_farger: dict = None, valgt: str = None) -> None:
    """Terminal-temaet. rad_farger gir hver watchlist-rad sin statusfarge."""
    per_rad = ""
    for t, farge in (rad_farger or {}).items():
        aktiv = (t == valgt)
        per_rad += f"""
  .st-key-wl-{_n(t)} {{
    border-left: 2px solid {farge};
    background: {DC['panel'] if aktiv else 'transparent'};
  }}
  .st-key-wl-{_n(t)} .stButton > button {{ color: {DC['tekst'] if aktiv else DC['dempet']}; }}
"""

    st.html(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap');

  .stApp {{ background: {DC['bg']}; }}
  html, body, [class*="css"] {{ font-family: {SANS}; color: {DC['tekst']}; }}

  /* Chrome vekk, ingen luft i toppen */
  #MainMenu, footer, header [data-testid="stStatusWidget"] {{ visibility: hidden; }}
  [data-testid="stDecoration"], [data-testid="stToolbar"] {{ display: none; }}
  [data-testid="stAppViewBlockContainer"] {{ padding: 0.6rem 1.2rem 3rem; max-width: 100%; }}
  [data-testid="stVerticalBlock"] {{ gap: 0.35rem; }}
  [data-testid="stHorizontalBlock"] {{ gap: 0.7rem; }}
  [data-testid="stElementContainer"]:has(> [data-testid="stHtml"]) {{ margin: 0; }}

  /* ── Venstre sidepanel ── */
  [data-testid="stSidebar"] {{
    background: {DC['rail']}; border-right: 1px solid {DC['linje']}; width: 252px !important;
  }}
  [data-testid="stSidebar"] [data-testid="stSidebarContent"] {{ padding: 0 0 2rem; }}
  [data-testid="stSidebar"] [data-testid="stVerticalBlock"] {{ gap: 0; }}
  [data-testid="stSidebarCollapseButton"] {{ display: none; }}

  /* Watchlist-rader: knapp + navnelinje presset sammen til én rad */
  [class*="st-key-wl-"] {{ padding: 6px 0 6px 14px; margin: 0; }}
  [class*="st-key-wl-"]:hover {{ background: {DC['panel']}; }}
  [class*="st-key-wl-"] [data-testid="stVerticalBlock"] {{ gap: 0; }}
  [class*="st-key-wl-"] .stButton > button {{
    background: transparent; border: none; padding: 0; min-height: 0; height: 17px;
    font-family: {MONO}; font-size: 13px; font-weight: 600; text-align: left;
    justify-content: flex-start; letter-spacing: 0.02em;
  }}
  [class*="st-key-wl-"] .stButton > button:hover {{ color: {DC['tekst']}; }}
  [class*="st-key-wl-"] .stButton > button:focus {{ box-shadow: none; }}

  /* Knapper generelt */
  .stButton > button {{
    background: {DC['knapp']}; border: 1px solid {DC['kant']}; color: {DC['tekst']};
    border-radius: 6px; font-family: {MONO}; font-size: 11px; letter-spacing: 0.06em;
    padding: 6px 12px; transition: none; min-height: 0;
  }}
  .stButton > button:hover {{ border-color: {DC['gronn']}; color: {DC['gronn']}; }}
  .stButton > button[kind="primary"] {{
    background: {DC['knapp']}; border: 1px solid {DC['kant']}; color: {DC['tekst']};
  }}
  .stButton > button[kind="primary"]:hover {{
    border-color: {DC['gronn']}; color: {DC['gronn']}; background: {DC['knapp']};
  }}

  /* Segmented control – TABELL / KORT */
  [data-testid="stSegmentedControl"] button {{
    font-family: {MONO}; font-size: 11px; letter-spacing: 0.06em;
    background: transparent; color: {DC['svak']}; border-color: {DC['kant']};
    padding: 4px 14px; min-height: 0;
  }}
  [data-testid="stSegmentedControl"] button[aria-checked="true"] {{
    background: {DC['panel']}; color: {DC['tekst']}; border-color: {DC['gronn']};
  }}

  /* Selectbox */
  [data-testid="stSelectbox"] div[data-baseweb="select"] > div {{
    background: transparent; border: none; font-family: {MONO}; font-size: 11px;
    color: {DC['tekst']}; min-height: 26px;
  }}
  [data-testid="stSelectbox"] label {{
    font-family: {SANS}; font-size: 12px; color: {DC['dempet']};
  }}
  [data-testid="stSelectbox"] svg {{ fill: {DC['svak']}; }}
  div[data-baseweb="popover"] li {{ font-family: {MONO}; font-size: 12px; }}

  /* Tekstfelt */
  [data-testid="stTextInput"] input {{
    background: transparent; border: 1px solid {DC['kant']}; border-radius: 6px;
    font-family: {MONO}; font-size: 11px; color: {DC['tekst']}; padding: 6px 10px;
  }}
  [data-testid="stTextInput"] input::placeholder {{ color: {DC['svakest']}; }}

  /* Checkbox og toggle */
  [data-testid="stCheckbox"] label {{ font-size: 12px; color: {DC['dempet']}; gap: 8px; }}
  [data-testid="stCheckbox"] label span[data-baseweb="checkbox"] div:first-child {{
    background: {DC['inset']}; border-color: {DC['kant']}; border-radius: 4px;
  }}
  [data-testid="stToggle"] label {{ font-size: 12px; color: {DC['dempet']}; }}

  /* Expander */
  [data-testid="stExpander"] {{ border: none; background: transparent; }}
  [data-testid="stExpander"] details {{
    border: 1px solid {DC['linje']}; border-radius: 6px; background: transparent;
  }}
  [data-testid="stExpander"] summary {{
    font-family: {MONO}; font-size: 10px; letter-spacing: 0.1em; color: {DC['svak']};
    padding: 7px 12px;
  }}
  [data-testid="stExpander"] summary:hover {{ color: {DC['blaa']}; }}

  /* Faner i høyrepanelet */
  [data-testid="stTabs"] [data-baseweb="tab-list"] {{
    gap: 0; border-bottom: 1px solid {DC['linje']};
  }}
  [data-testid="stTabs"] button {{
    font-family: {MONO}; font-size: 11px; letter-spacing: 0.06em; color: {DC['svak']};
    padding: 10px 14px;
  }}
  [data-testid="stTabs"] button[aria-selected="true"] {{ color: {DC['tekst']}; }}
  [data-testid="stTabs"] [data-baseweb="tab-highlight"] {{ background: {DC['gronn']}; }}
  [data-testid="stTabs"] [data-baseweb="tab-border"] {{ display: none; }}

  /* Høyrepanelet får egen flate */
  .st-key-panel {{
    background: {DC['rail']}; border: 1px solid {DC['linje']}; border-radius: 8px;
    padding: 0 16px 14px;
  }}
  .st-key-gate {{ border-top: 1px solid {DC['linje']}; padding-top: 10px; margin-top: 6px; }}

  /* Tabellen: monospace, tette rader, terminal-farger */
  [data-testid="stDataFrame"] {{ border: 1px solid {DC['linje']}; border-radius: 6px; }}
  [data-testid="stDataFrame"] [data-testid="stTable"] {{ font-family: {MONO}; }}
  [data-testid="stDataFrame"] * {{ font-family: {MONO} !important; font-size: 12px; }}
  [data-testid="stDataFrame"] [role="columnheader"] {{
    font-size: 9px !important; letter-spacing: 0.11em; color: {DC['svakest']};
    text-transform: uppercase;
  }}
  [data-testid="stDataFrame"] [role="row"]:hover {{ background: {DC['panel']}; }}

  ::-webkit-scrollbar {{ width: 10px; height: 10px; }}
  ::-webkit-scrollbar-track {{ background: {DC['bg']}; }}
  ::-webkit-scrollbar-thumb {{ background: {DC['kant']}; border-radius: 5px; }}
{per_rad}
</style>
""")


# ══════════════════════════════════════════════════════════════
# HTML-KOMPONENTER
# ══════════════════════════════════════════════════════════════

def badge(status: str, kort: bool = False) -> str:
    farge = STATUS_FARGE[status]
    tekst = (STATUS_KORT if kort else STATUS_TEKST)[status]
    if status == STATUS_WAIT:
        return (f'<span style="font-family:{MONO};font-size:10px;letter-spacing:0.08em;'
                f'color:{farge};border:1px solid {DC["kant"]};padding:3px 7px;'
                f'border-radius:3px;white-space:nowrap;">{tekst}</span>')
    return (f'<span style="font-family:{MONO};font-size:10px;letter-spacing:0.08em;'
            f'color:{farge};border:1px solid {_rgba(farge, 0.4)};'
            f'background:{_rgba(farge, 0.12)};padding:3px 7px;border-radius:3px;'
            f'white-space:nowrap;">{tekst}</span>')


def reversal_blokkert(r: dict, cfg: dict = SCANNER_CONFIG) -> bool:
    """
    Alle tekniske REVERSAL-krav er oppfylt, men fundamental godkjenning mangler.
    Statusmotoren lar den da falle gjennom til FOLLOW (§15), så uten dette
    flagget ville aksjen ligget ett hakemerke unna REVERSAL uten synlig grunn.
    """
    return (r["correctionScore"] >= cfg["correction"]["correction"]
            and r["recoveryScore"] >= cfg["recovery"]["confirmed"]
            and r["trendScore"] >= cfg["trend"]["minimumForReversal"]
            and not (r["fundamentalsChecked"] and r["thesisIntact"]))


def gate_pille() -> str:
    return (f'<span style="font-family:{MONO};font-size:9px;letter-spacing:0.08em;'
            f'color:{DC["orange"]};border:1px solid {_rgba(DC["orange"], 0.35)};'
            f'background:{_rgba(DC["orange"], 0.1)};padding:2px 6px;border-radius:3px;'
            f'margin-left:6px;white-space:nowrap;">GATE</span>')


def event_detalj(r: dict) -> str:
    ind, g = r["ind"], r["eventGrunner"]
    biter = []
    if g.get("abnormalGap") and ind.get("gapDownPct") is not None:
        biter.append(f'GAP NED {f(ind["gapDownPct"], 1)} %')
    if g.get("abnormal1D") and ind.get("return1d") is not None:
        biter.append(f'1D {f(ind["return1d"], 1)} %')
    if g.get("abnormal3D") and ind.get("return3d") is not None:
        biter.append(f'3D {f(ind["return3d"], 1)} %')
    if g.get("abnormalVolume") and ind.get("volumeRatio20d") is not None:
        biter.append(f'VOL {f(ind["volumeRatio20d"], 1)}×')
    return " · ".join(biter)


def fremdriftsstripe(andel: float) -> str:
    """2px-stripen øverst, som i mockupen."""
    pct = max(0.0, min(1.0, andel)) * 100
    return (f'<div style="height:2px;background:{DC["linje"]};position:relative;'
            f'margin:0 0 0;"><div style="position:absolute;top:0;left:0;bottom:0;'
            f'width:{pct:.0f}%;background:linear-gradient(90deg,{DC["gronn"]},'
            f'{DC["blaa"]});"></div></div>')


def statustellere(resultater: list) -> str:
    telling = {s: 0 for s in TELLER_REKKEFOLGE}
    for r in resultater:
        telling[r["status"]] += 1

    celler = []
    for i, s in enumerate(TELLER_REKKEFOLGE):
        farge = STATUS_FARGE[s]
        antall = telling[s]
        bg = _rgba(farge, 0.06) if antall and s in (STATUS_EVENT_RISK, STATUS_REVERSAL) else "transparent"
        hoyre = f"border-right:1px solid {DC['linje']};" if i < len(TELLER_REKKEFOLGE) - 1 else ""
        tallfarge = DC["svak"] if s == STATUS_WAIT or antall == 0 else DC["tekst"]
        celler.append(
            f'<div style="padding:12px 14px;{hoyre}border-top:2px solid {farge};'
            f'background:{bg};">'
            f'<div style="font-family:{MONO};font-size:10px;letter-spacing:0.1em;'
            f'color:{farge};white-space:nowrap;">{STATUS_KORT[s]}</div>'
            f'<div style="font-family:{MONO};font-size:28px;font-weight:500;'
            f'line-height:1.15;color:{tallfarge};">{antall}</div></div>')

    return (f'<div style="display:grid;grid-template-columns:repeat(7,minmax(0,1fr));'
            f'border-bottom:1px solid {DC["linje"]};">{"".join(celler)}</div>')


def varselrader(nye: list) -> str:
    if not nye:
        return (f'<div style="padding:11px 4px;font-family:{MONO};font-size:11px;'
                f'color:{DC["svakest"]};border-bottom:1px solid {DC["linje"]};">'
                f'INGEN STATUSENDRINGER SIDEN FORRIGE SKANNING</div>')
    rader = []
    for v in nye:
        status = v["type"].rstrip("+")
        farge = STATUS_FARGE.get(status, DC["blaa"])
        rader.append(
            f'<div style="display:flex;gap:10px;align-items:flex-start;'
            f'border-left:2px solid {farge};background:{_rgba(farge, 0.07)};'
            f'padding:9px 12px;border-radius:0 6px 6px 0;margin-bottom:6px;">'
            f'<div style="font-family:{MONO};font-size:10px;letter-spacing:0.1em;'
            f'color:{farge};padding-top:2px;">NY</div>'
            f'<div style="font-size:13px;line-height:1.5;color:{DC["tekst"]};">'
            f'<span style="font-family:{MONO};font-weight:600;">{_esc(v["ticker"])}</span> '
            f'{STATUS_TEKST.get(status, status)} · {_esc(v.get("sammendrag", ""))}</div>'
            f'<div style="flex:1;"></div>'
            f'<div style="font-family:{MONO};font-size:11px;color:{DC["svak"]};'
            f'white-space:nowrap;">{_esc(v["tid"][-5:])}</div></div>')
    return (f'<div style="padding:10px 0 4px;border-bottom:1px solid {DC["linje"]};">'
            f'{"".join(rader)}</div>')


def tabell_rader(resultater: list) -> pd.DataFrame:
    """Tabellen som DataFrame, slik at rader kan klikkes."""
    rader = []
    for r in resultater:
        cc, ind = r.get("currentCorrection"), r["ind"]
        navn = r["Navn"]
        if r["tynnHistorikk"]:
            navn += " · tynn historikk"
        status = STATUS_KORT[r["status"]]
        if reversal_blokkert(r):
            status += " · GATE"
        rader.append({
            "": "▌",                       # statusspine
            "TICKER": r["Ticker"],
            "NAVN": navn,
            "STATUS": status,
            "CORR": r["correctionScore"],
            "TREND": r["trendScore"],
            "RECOV": r["recoveryScore"],
            "KORR %": -cc.drawdownPct if cc else None,
            "PCTL": r["correctionPercentile"],
            "DAGER": cc.daysSincePeak if cc else None,
            "KURS": ind["close_now"],
            "% I DAG": ind["return1d"],
            "RSI": round(ind["rsi"], 1) if ind["rsi"] else None,
            "VOL R": ind["volumeRatio20d"],
            "F": "✓" if r["fundamentalsChecked"] else "",
        })
    return pd.DataFrame(rader)


def tabell_stil(df: pd.DataFrame, resultater: list):
    """
    Fargelegger tabellen. Spinekolonnen «▌» får statusfargen, som gir samme
    venstre-spine som mockupen uten å ofre klikkbare rader.
    """
    farger = [STATUS_FARGE[r["status"]] for r in resultater]
    dempet = [r["status"] == STATUS_WAIT for r in resultater]

    def per_rad(kolonne, velg):
        return [velg(i) for i in range(len(kolonne))]

    sty = df.style
    sty = sty.apply(lambda k: per_rad(k, lambda i: f"color: {farger[i]}"),
                    subset=["", "STATUS", "CORR"])
    sty = sty.apply(lambda k: per_rad(
        k, lambda i: f"color: {DC['svak'] if dempet[i] else DC['tekst']}"),
        subset=["TICKER", "TREND", "RECOV", "PCTL", "DAGER", "KURS", "RSI"])
    sty = sty.apply(lambda k: per_rad(k, lambda i: f"color: {DC['dempet']}"),
                    subset=["NAVN"])
    sty = sty.apply(lambda k: per_rad(
        k, lambda i: f"color: {DC['svak'] if dempet[i] else DC['orange']}"),
        subset=["KORR %"])
    sty = sty.apply(lambda k: [
        f"color: {DC['svak'] if dempet[i] else (DC['gronn'] if (v or 0) >= 0 else DC['roed'])}"
        for i, v in enumerate(k)], subset=["% I DAG"])
    sty = sty.apply(lambda k: [
        f"color: {DC['blaa'] if (v or 0) >= SCANNER_CONFIG['volume']['eventRatio'] else DC['dempet']}"
        for v in k], subset=["VOL R"])
    sty = sty.apply(lambda k: [f"color: {DC['gronn'] if v else DC['svakest']}" for v in k],
                    subset=["F"])
    return sty.format({
        "CORR": "{:.0f}", "TREND": "{:.0f}", "RECOV": "{:.0f}", "PCTL": "{:.0f}",
        "KORR %": "{:.1f}", "KURS": "{:.2f}", "% I DAG": "{:+.2f}",
        "RSI": "{:.1f}", "VOL R": "{:.2f}", "DAGER": "{:.0f}",
    }, na_rep="—")


TABELL_KOLONNER = {
    "": st.column_config.TextColumn("", width=6),
    "TICKER": st.column_config.TextColumn("TICKER", width=72),
    "NAVN": st.column_config.TextColumn("NAVN", width=170),
    "STATUS": st.column_config.TextColumn("STATUS", width=150),
    "CORR": st.column_config.NumberColumn("CORR", width=58),
    "TREND": st.column_config.NumberColumn("TREND", width=62),
    "RECOV": st.column_config.NumberColumn("RECOV", width=62),
    "KORR %": st.column_config.NumberColumn("KORR %", width=68),
    "PCTL": st.column_config.NumberColumn("PCTL", width=56),
    "DAGER": st.column_config.NumberColumn("DAGER", width=62),
    "KURS": st.column_config.NumberColumn("KURS", width=76),
    "% I DAG": st.column_config.NumberColumn("% I DAG", width=70),
    "RSI": st.column_config.NumberColumn("RSI", width=56),
    "VOL R": st.column_config.NumberColumn("VOL R", width=62),
    "F": st.column_config.TextColumn("F", width=34),
}


def tabell_fotnote(resultater: list) -> str:
    tynn = any(r["tynnHistorikk"] for r in resultater)
    fot = "* TYNN HISTORIKK — PERCENTILEN ER LITE PÅLITELIG · " if tynn else ""
    return (f'<div style="padding:8px 4px;font-family:{MONO};font-size:10px;'
            f'letter-spacing:0.06em;color:{DC["svakest"]};">KLIKK EN RAD FOR DETALJER · '
            f'{fot}RADAREN GIR INGEN KJØPS- ELLER SALGSSIGNALER</div>')



# ── Kursgraf ──────────────────────────────────────────────────
# Paletten er validert mot mørk flate: lyshetsbånd, kromagulv,
# fargeblindhets-separasjon (ΔE 24.5 protan) og kontrast, alle PASS.
GRAF_INK = "#ECEEF2"      # kurs — hovedserien, blekkfarge
GRAF_SMA50 = "#B5892F"    # rav
GRAF_SMA200 = "#3585D6"   # blå
GRAF_GRID = "#1A1E26"
GRAF_FONT = "IBM Plex Mono, ui-monospace, Menlo, monospace"

GRAF_VINDUER = {"KORREKSJON": None, "3M": 63, "6M": 126, "1Å": 252}


def kursgraf(r: dict, vindu: str = "KORREKSJON"):
    """
    Kurs med SMA50 og SMA200, korreksjonsvinduet skyggelagt og topp/bunn
    merket. Poenget er å vise korreksjonen radaren snakker om, ikke å være
    et handelschart.
    """
    ind = r["ind"]
    cc = r.get("currentCorrection")
    n_bars = len(ind["close"])

    if vindu == "KORREKSJON" and cc is not None:
        n = min(n_bars, max(90, n_bars - cc.peakIdx + 40))
    else:
        n = min(n_bars, GRAF_VINDUER.get(vindu) or 126)

    d = pd.DataFrame({
        "Dato": ind["index"], "Kurs": ind["close"].to_numpy(),
        "SMA50": ind["sma50_s"].to_numpy(), "SMA200": ind["sma200_s"].to_numpy(),
    }).tail(n).dropna(subset=["Kurs"])
    if len(d) < 5:
        return None

    lang = d.melt("Dato", var_name="Serie", value_name="Verdi").dropna(subset=["Verdi"])
    lav, hoy = float(d["Kurs"].min()), float(d["Kurs"].max())
    for kol in ("SMA50", "SMA200"):
        if d[kol].notna().any():
            lav = min(lav, float(d[kol].min()))
            hoy = max(hoy, float(d[kol].max()))
    marg = (hoy - lav) * 0.08 or 1.0

    akse_x = alt.Axis(format="%b %y", tickCount=5, grid=False, domainColor=GRAF_GRID,
                      tickColor=GRAF_GRID, labelColor="#6B7686", labelFontSize=9,
                      labelFont=GRAF_FONT, title=None)
    akse_y = alt.Axis(tickCount=4, gridColor=GRAF_GRID, gridWidth=1, domain=False,
                      ticks=False, labelColor="#6B7686", labelFontSize=9,
                      labelFont=GRAF_FONT, title=None, labelPadding=4)
    skala_y = alt.Scale(domain=[lav - marg, hoy + marg])

    lag = []

    # Korreksjonsvinduet som svak skygge
    if cc is not None and cc.peakIdx < n_bars:
        topp_dato = pd.Timestamp(cc.peakDate)
        if topp_dato >= d["Dato"].iloc[0]:
            lag.append(
                alt.Chart(pd.DataFrame({"start": [topp_dato], "slutt": [d["Dato"].iloc[-1]]}))
                .mark_rect(color=STATUS_FARGE[r["status"]], opacity=0.07)
                .encode(x="start:T", x2="slutt:T"))

    lag.append(
        alt.Chart(lang).mark_line(interpolate="monotone").encode(
            x=alt.X("Dato:T", axis=akse_x, title=None),
            y=alt.Y("Verdi:Q", scale=skala_y, axis=akse_y, title=None),
            color=alt.Color("Serie:N", scale=alt.Scale(
                domain=["Kurs", "SMA50", "SMA200"],
                range=[GRAF_INK, GRAF_SMA50, GRAF_SMA200]),
                legend=alt.Legend(orient="top", direction="horizontal", title=None,
                                  labelColor="#8B95A5", labelFontSize=10,
                                  labelFont=GRAF_FONT, symbolType="stroke",
                                  symbolStrokeWidth=2, symbolSize=110, offset=2)),
            strokeWidth=alt.StrokeWidth("Serie:N", scale=alt.Scale(
                domain=["Kurs", "SMA50", "SMA200"], range=[2, 1.3, 1.3]), legend=None),
            order=alt.Order("Serie:N", sort="descending"),
        ))

    # Topp og bunn direktemerket — de to punktene som definerer korreksjonen
    if cc is not None:
        merker = []
        for etikett, dato, pris, farge in [
            ("TOPP", cc.peakDate, cc.peakPrice, "#8B95A5"),
            ("BUNN", cc.troughDate, cc.troughPrice, DC["roed"]),
        ]:
            ts = pd.Timestamp(dato)
            if ts >= d["Dato"].iloc[0]:
                # Ligger punktet nær høyre kant, snus etiketten innover
                # så teksten ikke blir klippet av plottkanten.
                spenn = (d["Dato"].iloc[-1] - d["Dato"].iloc[0]).days or 1
                andel = (ts - d["Dato"].iloc[0]).days / spenn
                merker.append({"Dato": ts, "Verdi": pris,
                               "Etikett": f"{etikett} {pris:,.2f}".replace(",", " "),
                               "Farge": farge,
                               "Just": "right" if andel > 0.72 else "left",
                               "Dx": -9 if andel > 0.72 else 9})
        if merker:
            m = pd.DataFrame(merker)
            lag.append(alt.Chart(m).mark_point(size=48, filled=True, stroke=DC["rail"],
                                               strokeWidth=2).encode(
                x="Dato:T", y=alt.Y("Verdi:Q", scale=skala_y),
                color=alt.Color("Farge:N", scale=None)))
            # To tekstlag: align kan ikke være en feltkoding, så venstre- og
            # høyrestilte etiketter tegnes hver for seg.
            for just, dx in (("left", 9), ("right", -9)):
                del_m = m[m["Just"] == just]
                if not del_m.empty:
                    lag.append(alt.Chart(del_m).mark_text(
                        align=just, dx=dx, dy=-9, fontSize=9, font=GRAF_FONT).encode(
                        x="Dato:T", y=alt.Y("Verdi:Q", scale=skala_y),
                        text="Etikett:N", color=alt.Color("Farge:N", scale=None)))

    # Hover: hårlinje og verdier for alle tre seriene
    naerme = alt.selection_point(nearest=True, on="pointerover", fields=["Dato"], empty=False)
    lag.append(
        alt.Chart(d).mark_rule(color="#8B95A5", strokeWidth=1)
        .encode(x="Dato:T",
                opacity=alt.condition(naerme, alt.value(0.5), alt.value(0)),
                tooltip=[alt.Tooltip("Dato:T", title="Dato", format="%d.%m.%Y"),
                         alt.Tooltip("Kurs:Q", format=".2f"),
                         alt.Tooltip("SMA50:Q", format=".2f"),
                         alt.Tooltip("SMA200:Q", format=".2f")])
        .add_params(naerme))

    # configure_view må komme sist: et etterfølgende configure()-kall
    # overskriver hele config-objektet og slukte stroke-innstillingen.
    return (alt.layer(*lag).properties(height=190)
            .configure(background=DC["rail"], font="IBM Plex Sans, sans-serif")
            .configure_axis(domainWidth=0)
            .configure_view(stroke=None, fill=DC["rail"], strokeWidth=0))


def panel_topp_html(r: dict) -> str:
    """Høyrepanelets hode: ticker, badge, kurs og de tre scorebarene."""
    ind, cc = r["ind"], r.get("currentCorrection")
    d1 = ind.get("return1d")
    d1f = DC["gronn"] if (d1 or 0) >= 0 else DC["roed"]

    def bar(etikett, verdi, hoyre, farge):
        return (f'<div style="margin-top:11px;">'
                f'<div style="display:flex;justify-content:space-between;'
                f'font-family:{MONO};font-size:10px;letter-spacing:0.08em;'
                f'color:{DC["svak"]};"><span>{etikett}</span><span>{hoyre}</span></div>'
                f'<div style="height:6px;background:{DC["spor"]};border-radius:3px;'
                f'margin-top:5px;overflow:hidden;">'
                f'<div style="width:{max(0, min(100, verdi)):.0f}%;height:100%;'
                f'background:{farge};"></div></div></div>')

    return f"""
<div style="border-bottom:1px solid {DC['linje']};padding:14px 0 14px;">
  <div style="display:flex;align-items:flex-start;gap:10px;">
    <div style="flex:1;min-width:0;">
      <div style="display:flex;align-items:center;gap:8px;">
        <span style="font-family:{MONO};font-size:18px;font-weight:600;
              color:{DC['tekst']};">{_esc(r['Ticker'])}</span>
        <a href="https://finance.yahoo.com/quote/{_esc(r['ticker'])}" target="_blank"
           style="font-size:11px;color:{DC['blaa']};text-decoration:none;">Yahoo ↗</a>
      </div>
      <div style="font-size:12px;color:{DC['dempet']};">{_esc(r['Navn'])} ·
        <span style="font-family:{MONO};">{_esc(r['ticker'])}</span></div>
    </div>
    {badge(r['status'], kort=True)}
  </div>
  <div style="display:flex;align-items:baseline;gap:8px;margin-top:12px;">
    <span style="font-family:{MONO};font-size:26px;color:{DC['tekst']};">
      {f(ind['close_now'])}</span>
    <span style="font-family:{MONO};font-size:13px;color:{d1f};">
      {f(d1, 2, ' %')}</span>
    <span style="flex:1;"></span>
    <span style="font-family:{MONO};font-size:13px;color:{DC['gul']};">
      {f'-{f(cc.drawdownPct, 1)} % fra topp' if cc else '—'}</span>
  </div>
  {bar('CORRECTION', r['correctionScore'],
       f"{r['correctionScore']:.0f} · PCTL {r['correctionPercentile']:.0f}"
       + ('*' if r['tynnHistorikk'] else ''), DC['gul'])}
  {bar('TREND', r['trendScore'], f"{r['trendScore']:.0f} · {r['trendBand']}", DC['blaa'])}
  {bar('RECOVERY', r['recoveryScore'],
       f"{r['recoveryScore']:.0f} · {r['recoveryBand'].replace('RECOVERY', '').strip() or 'NONE'}",
       DC['gronn'])}
</div>"""


def kriterieliste_html(tittel: str, score: float, deler: dict,
                       poeng: dict, etiketter: dict) -> str:
    rader = []
    for nokkel, etikett in etiketter.items():
        truffet = bool(deler.get(nokkel))
        maks = poeng[nokkel]
        rader.append(
            f'<div style="display:grid;grid-template-columns:16px 1fr 50px;gap:8px;'
            f'align-items:center;padding:4px 0;font-size:12px;'
            f'color:{DC["dempet"] if truffet else DC["svakest"]};">'
            f'<span style="color:{DC["gronn"] if truffet else DC["kant"]};">'
            f'{"✓" if truffet else "·"}</span><span>{etikett}</span>'
            f'<span style="font-family:{MONO};font-size:11px;text-align:right;'
            f'color:{DC["tekst"] if truffet else DC["svakest"]};">'
            f'{maks if truffet else 0}/{maks}</span></div>')
    return (f'<div style="margin-bottom:14px;"><div style="display:flex;'
            f'align-items:baseline;gap:8px;margin-bottom:4px;">'
            f'<span style="font-family:{MONO};font-size:10px;letter-spacing:0.1em;'
            f'color:{DC["svak"]};">{tittel}</span>'
            f'<span style="font-family:{MONO};font-size:17px;color:{DC["tekst"]};">'
            f'{score:.0f}</span></div>{"".join(rader)}</div>')


def korreksjonsforlop_html(r: dict) -> str:
    cc = r.get("currentCorrection")
    if not cc:
        return f'<div style="color:{DC["svak"]};font-size:12px;">Ingen korreksjon registrert.</div>'

    def punkt(etikett, verdi, under, farge=None):
        return (f'<div><div style="font-family:{MONO};font-size:9px;letter-spacing:0.1em;'
                f'color:{DC["svak"]};">{etikett}</div>'
                f'<div style="font-family:{MONO};font-size:17px;'
                f'color:{farge or DC["tekst"]};">{verdi}</div>'
                f'<div style="font-family:{MONO};font-size:10px;color:{DC["svakest"]};">'
                f'{under}</div></div>')

    w = SCANNER_CONFIG["correctionScoreWeights"]
    pil = f'<div style="color:{DC["kant"]};align-self:center;">→</div>'
    stotte = (f'Nærmeste bekreftede swing-low {f(r["supportNivå"])}, '
              f'{f(r["supportAvstandPct"], 2)} % fra kurs.'
              if r.get("supportNivå") else "Ingen bekreftet swing-low funnet ennå.")

    return (
        f'<div style="display:flex;gap:16px;flex-wrap:wrap;">'
        f'{punkt("TOPP", f(cc.peakPrice), cc.peakDate)}{pil}'
        f'{punkt("BUNN", f(cc.troughPrice), cc.troughDate, DC["roed"])}{pil}'
        f'{punkt("NÅ", f(cc.currentPrice), f"{cc.daysSincePeak} dager siden topp")}</div>'
        f'<div style="margin-top:12px;font-size:12px;color:{DC["dempet"]};'
        f'line-height:1.6;">Dybde topp→bunn <b>-{f(cc.maxDepthPct, 1)} %</b>, '
        f'nå <b>-{f(cc.drawdownPct, 1)} %</b> fra topp. '
        f'Correction Score {r["correctionScore"]:.0f} = percentil '
        f'{r["correctionPercentile"]:.0f} × {w["percentile"]:.0%} + stretch '
        f'{r["stretchScore"]:.0f} × {w["technicalStretch"]:.0%} + støtte '
        f'{r["supportScore"]:.0f} × {w["support"]:.0%}. {stotte}</div>'
        + (f'<div style="margin-top:8px;font-size:12px;color:{DC["orange"]};">'
           f'⚠️ Kun {r["antallHistoriske"]} tidligere korreksjoner — percentilen er '
           f'lite pålitelig.</div>' if r["tynnHistorikk"] else "")
        + (f'<div style="margin-top:10px;background:{_rgba(DC["roed"], 0.08)};'
           f'border:1px solid {_rgba(DC["roed"], 0.3)};border-radius:6px;padding:9px 11px;'
           f'font-size:12px;color:{DC["dempet"]};"><b style="color:{DC["roed"]};">'
           f'Event risk:</b> '
           + ", ".join(v for k, v in EVENT_ETIKETTER.items() if r["eventGrunner"].get(k))
           + f'. Målt mot ATR {f(r["ind"]["atrPctPrev"], 2)} % fra dagen før hendelsen.'
           f'</div>' if r["eventRisk"] else "")
    )


def historikk_html(r: dict) -> str:
    h = r["historiskeKorreksjoner"]
    if not h:
        return (f'<div style="color:{DC["svak"]};font-size:12px;">'
                f'Ingen avsluttede korreksjoner funnet i historikken.</div>')
    naa = r["currentCorrection"].maxDepthPct if r.get("currentCorrection") else None
    rader = []
    if naa is not None:
        rader.append(
            f'<tr style="color:{DC["roed"]};"><td colspan="2" style="padding:6px 8px 6px 0;'
            f'font-family:{MONO};font-size:11px;">NÅ</td>'
            f'<td style="padding:6px 8px 6px 0;font-family:{MONO};font-size:12px;'
            f'text-align:right;">-{naa:.1f} %</td><td colspan="2"></td></tr>')
    for k in sorted(h, key=lambda x: -x.drawdownPct)[:30]:
        storre = naa is not None and k.drawdownPct > naa
        rader.append(
            f'<tr style="color:{DC["svak"] if storre else DC["dempet"]};">'
            f'<td style="padding:4px 8px 4px 0;font-family:{MONO};font-size:11px;">'
            f'{k.peakDate}</td>'
            f'<td style="padding:4px 8px 4px 0;font-family:{MONO};font-size:11px;">'
            f'{k.troughDate}</td>'
            f'<td style="padding:4px 8px 4px 0;font-family:{MONO};font-size:12px;'
            f'text-align:right;">-{k.drawdownPct:.1f} %</td>'
            f'<td style="padding:4px 8px 4px 0;font-family:{MONO};font-size:11px;'
            f'text-align:right;">{k.durationDays} d</td>'
            f'<td style="padding:4px 0;font-family:{MONO};font-size:11px;'
            f'text-align:right;">{k.atrNormalizedDrawdown:.1f}×</td></tr>')
    hoder = "".join(f'<th style="text-align:{"right" if i > 1 else "left"};'
                    f'padding:0 8px 6px 0;font-family:{MONO};font-size:9px;'
                    f'letter-spacing:0.1em;font-weight:400;color:{DC["svakest"]};">{t}</th>'
                    for i, t in enumerate(["TOPP", "BUNN", "FALL", "VARIGHET", "I ATR"]))
    return (f'<table style="width:100%;border-collapse:collapse;">'
            f'<thead><tr>{hoder}</tr></thead><tbody>{"".join(rader)}</tbody></table>')


def nokkeltall_html(r: dict) -> str:
    ind = r["ind"]
    rader = [
        ("1M %", f(ind["return1m"], 1, " %")), ("3M %", f(ind["return3m"], 1, " %")),
        ("6M %", f(ind["return6m"], 1, " %")), ("52W DD", f(ind["drawdown52w"], 1, " %")),
        ("RSI 14", f(ind["rsi"], 1)), ("ATR %", f(ind["atrPct"], 2)),
        ("SMA20", f(ind["sma20"])), ("SMA50", f(ind["sma50"])),
        ("SMA200", f(ind["sma200"])), ("VOL R", f(ind["volumeRatio20d"])),
        ("20D HIGH", f(ind["high20d"])), ("52W HIGH", f(ind["high52w"])),
    ]
    celler = "".join(
        f'<div style="border:1px solid {DC["linje"]};border-radius:6px;padding:7px 9px;">'
        f'<div style="font-family:{MONO};font-size:9px;letter-spacing:0.1em;'
        f'color:{DC["svak"]};">{e}</div>'
        f'<div style="font-family:{MONO};font-size:13px;color:{DC["tekst"]};">{v}</div></div>'
        for e, v in rader)
    return (f'<div style="display:grid;grid-template-columns:repeat(3,minmax(0,1fr));'
            f'gap:6px;">{celler}</div>')


# 1b-kortene beholdes som KORT-visning
def sone_header(navn: str, farge: str, antall: int) -> str:
    grad = (f"linear-gradient(90deg, {_rgba(farge, 0.4)}, transparent)"
            if navn != "ROLIG" else DC["linje"])
    return (f'<div style="display:flex;align-items:center;gap:10px;margin:16px 0 8px;">'
            f'<div style="font-family:{MONO};font-size:11px;letter-spacing:0.14em;'
            f'color:{farge};">{navn} · {antall}</div>'
            f'<div style="flex:1;height:1px;background:{grad};"></div></div>')


def kort_html(r: dict, form: str) -> str:
    """Kortvisning. «stor» får målerblokker, ellers én tett rad."""
    cc, ind = r.get("currentCorrection"), r["ind"]
    farge = STATUS_FARGE[r["status"]]
    gate = gate_pille() if reversal_blokkert(r) else ""

    if form != "stor":
        dempet = form == "kompakt"
        tf = DC["svak"] if dempet else DC["tekst"]
        kant = (f'background:{DC["kort"]};border:1px solid {DC["linje"]};'
                f'border-left:3px solid {farge};' if not dempet
                else f'border:1px solid {DC["linje"]};')

        def sc(etikett, verdi, fg=None):
            return (f'<div style="text-align:right;">'
                    f'<div style="font-family:{MONO};font-size:9px;color:{DC["svak"]};">'
                    f'{etikett}</div><div style="font-family:{MONO};font-size:14px;'
                    f'color:{fg or tf};">{verdi:.0f}</div></div>')

        return (f'<div style="display:grid;grid-template-columns:90px 1fr 170px 90px '
                f'70px 70px 70px;align-items:center;gap:10px;{kant}border-radius:8px;'
                f'padding:{"10px 14px" if dempet else "13px 15px"};margin-bottom:6px;">'
                f'<div style="font-family:{MONO};font-size:{"15px" if dempet else "17px"};'
                f'font-weight:600;color:{DC["dempet"] if dempet else DC["tekst"]};">'
                f'{_esc(r["Ticker"])}</div>'
                f'<div style="font-size:12px;color:{DC["dempet"] if not dempet else DC["svak"]};'
                f'overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">'
                f'{_esc(r["Navn"])}'
                + (f' · {cc.daysSincePeak} dager siden topp' if cc and not dempet else '')
                + f'</div><div>{badge(r["status"], kort=True)}{gate}</div>'
                f'<div style="font-family:{MONO};font-size:15px;color:{DC["orange"] if not dempet else DC["svak"]};'
                f'text-align:right;">{f"-{f(cc.drawdownPct, 1)} %" if cc else "—"}</div>'
                + sc("CORR", r["correctionScore"]) + sc("TREND", r["trendScore"])
                + sc("RECOV", r["recoveryScore"]) + '</div>')

    def maaler(etikett, verdi, under, vf=None, uf=None):
        return (f'<div style="background:{DC["inset"]};border:1px solid {DC["linje"]};'
                f'border-radius:8px;padding:12px;">'
                f'<div style="font-family:{MONO};font-size:10px;letter-spacing:0.1em;'
                f'color:{DC["svak"]};">{etikett}</div>'
                f'<div style="font-family:{MONO};font-size:26px;line-height:1.2;'
                f'color:{vf or DC["tekst"]};">{verdi}</div>'
                f'<div style="font-family:{MONO};font-size:11px;color:{uf or DC["svak"]};">'
                f'{under}</div></div>')

    detalj = event_detalj(r) if r["eventRisk"] else ""
    return (f'<div style="border:1px solid {_rgba(farge, 0.35)};background:'
            f'linear-gradient(180deg,{_rgba(farge, 0.09)},{_rgba(farge, 0.02)});'
            f'border-radius:10px;padding:18px;margin-bottom:8px;">'
            f'<div style="display:flex;align-items:flex-start;gap:14px;">'
            f'<div style="flex:1;min-width:0;">'
            f'<div style="display:flex;align-items:center;gap:9px;flex-wrap:wrap;">'
            f'<span style="font-family:{MONO};font-size:24px;font-weight:600;'
            f'color:{DC["tekst"]};">{_esc(r["Ticker"])}</span>'
            f'<span style="font-size:13px;color:{DC["dempet"]};">{_esc(r["Navn"])}</span>'
            f'<a href="https://finance.yahoo.com/quote/{_esc(r["ticker"])}" target="_blank"'
            f' style="font-size:11px;color:{DC["blaa"]};text-decoration:none;">Yahoo ↗</a>'
            f'</div><div style="font-family:{MONO};font-size:12px;color:{DC["svak"]};'
            f'margin-top:4px;">{f(ind["close_now"])} · '
            + (f'{cc.daysSincePeak} dager siden topp · korreksjon {_esc(cc.id)}' if cc else '—')
            + f'</div></div><div style="text-align:right;">{badge(r["status"])}{gate}'
            + (f'<div style="font-family:{MONO};font-size:11px;color:{DC["svak"]};'
               f'margin-top:6px;">{detalj}</div>' if detalj else '')
            + f'</div></div>'
            f'<div style="display:grid;grid-template-columns:repeat(4,minmax(0,1fr));'
            f'gap:12px;margin-top:16px;">'
            + maaler("KORREKSJON", f"-{f(cc.drawdownPct, 1)} %" if cc else "—",
                     f"dybde -{f(cc.maxDepthPct, 1)} %" if cc else "",
                     DC["roed"] if cc else None)
            + maaler("PERCENTIL", f"{r['correctionPercentile']:.0f}",
                     "tynn historikk" if r["tynnHistorikk"] else f"{r['antallHistoriske']} tidligere",
                     uf=DC["orange"] if r["tynnHistorikk"] else None)
            + maaler("TREND", f"{r['trendScore']:.0f}", r["trendBand"],
                     uf=DC["gronn"] if r["trendScore"] >= 60 else DC["orange"])
            + maaler("RECOVERY", f"{r['recoveryScore']:.0f}", r["recoveryBand"],
                     uf=DC["gronn"] if r["recoveryScore"] >= 50 else DC["roed"])
            + '</div></div>')


def sorter_resultater(resultater: list, valg: str) -> list:
    if valg == "Ticker":
        return sorted(resultater, key=lambda r: r["Ticker"])
    if valg == "Korreksjon %":
        return sorted(resultater, key=lambda r: -(r["currentCorrection"].drawdownPct
                                                  if r.get("currentCorrection") else -999))
    felt = SORTERINGSVALG.get(valg)
    if felt:
        return sorted(resultater, key=lambda r: -r[felt])
    return sorted(resultater, key=lambda r: (STATUS_PRIORITY.get(r["status"], 99),
                                             -r["correctionScore"]))


# ══════════════════════════════════════════════════════════════
# STREAMLIT APP
# ══════════════════════════════════════════════════════════════

def _sidepanel(resultater: list) -> tuple:
    """Venstre kolonne: watchlist som navigasjon, og visningskontroller."""
    universe = st.session_state.universe
    per_ticker = {r["ticker"]: r for r in resultater}

    with st.sidebar:
        st.html(
            f'<div style="padding:14px 16px;border-bottom:1px solid {DC["linje"]};'
            f'display:flex;align-items:center;gap:8px;">'
            f'<div style="width:8px;height:8px;border-radius:50%;background:{DC["gronn"]};'
            f'box-shadow:0 0 8px {DC["gronn"]};"></div>'
            f'<div style="font-family:{MONO};font-size:13px;font-weight:600;'
            f'letter-spacing:0.06em;color:{DC["tekst"]};">CORRECTION RADAR</div></div>')

        aktive = sum(1 for e in universe if e["enabled"])
        st.html(f'<div style="padding:14px 16px 6px;font-family:{MONO};font-size:10px;'
                f'letter-spacing:0.14em;color:{DC["svak"]};">'
                f'WATCHLIST · {aktive}/{len(universe)} AKTIVE</div>')

        for e in universe:
            t = e["ticker"]
            r = per_ticker.get(t)
            navn = e["name"] + ("" if e["enabled"] else " — av")
            score = f"{r['correctionScore']:.0f}" if r else "—"
            farge = STATUS_FARGE[r["status"]] if r else DC["kant"]
            with st.container(key=f"wl-{_n(t)}"):
                if st.button(t.replace(".OL", ""), key=f"sel_{t}", width="stretch"):
                    st.session_state.valgt = t
                st.html(
                    f'<div style="display:flex;align-items:baseline;gap:8px;'
                    f'margin-top:-2px;padding-right:14px;">'
                    f'<div style="flex:1;min-width:0;font-size:11px;color:{DC["svak"]};'
                    f'overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">'
                    f'{_esc(navn)}</div>'
                    f'<div style="font-family:{MONO};font-size:12px;color:{farge};">'
                    f'{score}</div></div>')

        st.html(f'<div style="height:1px;background:{DC["linje"]};margin:12px 0 0;"></div>')

        with st.expander("REDIGER WATCHLIST"):
            endret = False
            for i, e in enumerate(universe):
                c = st.columns([1.6, 0.4])
                paa = c[0].checkbox(e["ticker"].replace(".OL", ""),
                                    value=e["enabled"], key=f"uni_{e['ticker']}")
                if paa != e["enabled"]:
                    universe[i]["enabled"] = paa
                    endret = True
                if c[1].button("✕", key=f"del_{e['ticker']}"):
                    universe.pop(i)
                    lagre_universe(universe)
                    st.cache_data.clear()
                    st.rerun()
            if endret:
                lagre_universe(universe)
                st.rerun()

            ledige = {v: k for k, v in OSLO_TICKERS.items()
                      if k not in {e["ticker"] for e in universe}}
            valg = st.selectbox("Legg til fra Oslo Børs", ["—"] + sorted(ledige.keys()))
            if valg != "—" and st.button("LEGG TIL", key="add_oslo", width="stretch"):
                universe.append(_universe_rad(ledige[valg]))
                lagre_universe(universe)
                st.cache_data.clear()
                st.rerun()
            fri = st.text_input("Ticker manuelt", placeholder="EQNR.OL / AAPL")
            if fri and st.button("LEGG TIL TICKER", key="add_fri", width="stretch"):
                t = _normaliser_ticker(fri)
                if t not in {e["ticker"] for e in universe}:
                    universe.append(_universe_rad(t))
                    lagre_universe(universe)
                    st.cache_data.clear()
                    st.rerun()

        st.html(f'<div style="padding:14px 16px 4px;font-family:{MONO};font-size:10px;'
                f'letter-spacing:0.14em;color:{DC["svak"]};">VISNING</div>')
        with st.container(key="visning"):
            sortering = st.selectbox("Sorter", list(SORTERINGSVALG.keys()))
            refresh = st.selectbox("Auto-refresh", ["Av", "5 min", "10 min", "15 min", "30 min"],
                                   index=3)
            vis_wait = st.toggle("Vis WAIT", value=True)

        st.html(f'<div style="padding:16px;font-size:10px;color:{DC["svakest"]};'
                f'line-height:1.6;">Lagres på disk. På Streamlit Cloud nullstilles listen '
                f'ved omstart og faller tilbake til DEFAULT_WATCHLIST.</div>')

    minutter = {"Av": 0, "5 min": 5, "10 min": 10, "15 min": 15, "30 min": 30}[refresh]
    if minutter > 0:
        tick = st_autorefresh(interval=minutter * 60 * 1000, key="auto_refresh")
        if tick and tick > 0:
            st.cache_data.clear()
    return sortering, vis_wait


def _hoyrepanel(r: dict, fund_store: dict) -> bool:
    """Detaljpanelet. Returnerer True hvis fundamental-sjekken ble endret."""
    with st.container(key="panel"):
        st.html(panel_topp_html(r))

        vindu = st.segmented_control("Vindu", list(GRAF_VINDUER.keys()),
                                     default="KORREKSJON", key=f"graf_{r['ticker']}",
                                     label_visibility="collapsed")
        graf = kursgraf(r, vindu or "KORREKSJON")
        if graf is not None:
            st.altair_chart(graf, width="stretch", theme=None)
        else:
            st.html(f'<div style="font-size:12px;color:{DC["svak"]};padding:8px 0;">'
                    f'For lite kursdata til å tegne graf.</div>')

        t1, t2, t3, t4 = st.tabs(["KRITERIER", "KORREKSJON", "HISTORIKK", "NØKKELTALL"])
        with t1:
            st.html(kriterieliste_html("TREND SCORE", r["trendScore"], r["trendDeler"],
                                       SCANNER_CONFIG["trendPoints"], TREND_ETIKETTER)
                    + kriterieliste_html("RECOVERY SCORE", r["recoveryScore"],
                                         r["recoveryDeler"],
                                         SCANNER_CONFIG["recoveryPoints"], RECOVERY_ETIKETTER))
        with t2:
            st.html(korreksjonsforlop_html(r))
        with t3:
            st.html(historikk_html(r))
        with t4:
            st.html(nokkeltall_html(r))

        fund: FundamentalCheck = r["fundamental"]
        antall = sum([fund.reportChecked, fund.guidanceChecked,
                      fund.newsChecked, fund.thesisIntact])
        with st.container(key="gate"):
            st.html(f'<div style="font-family:{MONO};font-size:10px;letter-spacing:0.1em;'
                    f'color:{DC["svak"]};">FUNDAMENTAL GATE · {antall}/4</div>')
            c1 = st.columns(2)
            c2 = st.columns(2)
            ny = FundamentalCheck(
                reportChecked=c1[0].checkbox("Siste rapport", value=fund.reportChecked,
                                             key=f"fr_{r['ticker']}"),
                guidanceChecked=c1[1].checkbox("Guiding", value=fund.guidanceChecked,
                                               key=f"fg_{r['ticker']}"),
                newsChecked=c2[0].checkbox("Nyheter", value=fund.newsChecked,
                                           key=f"fn_{r['ticker']}"),
                thesisIntact=c2[1].checkbox("Case intakt", value=fund.thesisIntact,
                                            key=f"ft_{r['ticker']}"),
                correctionId=r["correctionId"],
            )
            merknad = (f'SJEKKET {fund.updated} · ' if fund.updated else "")
            st.html(f'<div style="font-family:{MONO};font-size:9px;letter-spacing:0.06em;'
                    f'color:{DC["svakest"]};padding-top:6px;">{merknad}'
                    f'KORREKSJON {_esc(r["correctionId"])}</div>'
                    + (f'<div style="font-size:11px;color:{DC["orange"]};padding-top:6px;">'
                       f'Forrige sjekk gjaldt en tidligere korreksjon og er nullstilt.</div>'
                       if fund.stale else ""))

    endret = (ny.reportChecked != fund.reportChecked
              or ny.guidanceChecked != fund.guidanceChecked
              or ny.newsChecked != fund.newsChecked
              or ny.thesisIntact != fund.thesisIntact
              or fund.stale)
    if endret:
        ny.updated = datetime.now(ZoneInfo("Europe/Oslo")).strftime("%Y-%m-%d %H:%M")
        d = asdict(ny)
        d.pop("stale", None)
        fund_store[r["ticker"]] = d
        lagre_fundamentals(fund_store)
    return endret


def _fotnote() -> None:
    c = SCANNER_CONFIG
    with st.expander("SLIK VIRKER RADAREN"):
        st.markdown(f"""
**Kjerneprinsippet:** ingen felles prosentgrense. Hver aksje sammenlignes med sin egen
historikk av korreksjoner, funnet med ATR-normalisert ZigZag
(terskel {c['swingAtrMultiplier']} × ATR14). Et fall på 7 % kan være en stor DNB-korreksjon
og samtidig helt normal NAS-støy.

| Score | Spørsmål | Sammensetning |
|---|---|---|
| Correction | Hvor uvanlig er dagens fall for denne aksjen? | Percentil {c['correctionScoreWeights']['percentile']:.0%} + teknisk stretch {c['correctionScoreWeights']['technicalStretch']:.0%} + støtte {c['correctionScoreWeights']['support']:.0%} |
| Trend | Er kursstrukturen fortsatt frisk? | SMA-struktur, helning, higher lows |
| Recovery | Er fallet i ferd med å ta slutt? | Higher low, RSI, SMA20, motstandsbrudd, volum |

| Status | Krav |
|---|---|
| EVENT RISK | Unormalt raskt fall og fundamental sjekk ikke fullført — overstyrer alt |
| WAIT | Correction Score < {c['correction']['follow']} |
| FOLLOW | Correction Score {c['correction']['follow']}–{c['correction']['correction'] - 1} |
| CORRECTION | Corr ≥ {c['correction']['correction']}, Recovery < {c['recovery']['stabilizing']} |
| STRONG CORRECTION | Corr ≥ {c['correction']['strong']}, Recovery < {c['recovery']['stabilizing']} |
| STABILIZING | Corr ≥ {c['correction']['correction']}, Recovery {c['recovery']['stabilizing']}–{c['recovery']['confirmed'] - 1} |
| REVERSAL | Corr ≥ {c['correction']['correction']}, Recovery ≥ {c['recovery']['confirmed']}, Trend ≥ {c['trend']['minimumForReversal']}, fundamental sjekk fullført **og** case intakt |

**Kurs under SMA200 fjerner ikke aksjen** — det trekker bare Trend Score.
**GATE-merket** betyr at alle tekniske REVERSAL-krav er oppfylt, men fundamental
sjekk mangler.

**REVERSAL betyr ikke kjøp.** Det betyr at oppsettet er verdt en manuell gjennomgang.

**Én korreksjon = én hendelse.** Samme `correctionId` beholdes selv om fallet
utdypes. Varsel går ved statusendring, eller når fallet øker
{c['alerts']['severityStepPct']} prosentpoeng innenfor samme korreksjon.
        """)
    with st.expander("AKTIV CONFIG"):
        st.caption("Alle terskler ligger i SCANNER_CONFIG øverst i scanner.py.")
        st.json(SCANNER_CONFIG)


def main() -> None:
    """Streamlit hovedapp – retning 1a TERMINAL."""
    st.set_page_config(page_title="Correction Radar", page_icon="📡",
                       layout="wide", initial_sidebar_state="expanded")

    if "universe" not in st.session_state:
        st.session_state.universe = last_universe()
        if not UNIVERSE_FILE.exists():
            lagre_universe(st.session_state.universe)
    for nokkel, standard in [("fundamentals", last_fundamentals),
                             ("radar_state", last_state)]:
        if nokkel not in st.session_state:
            st.session_state[nokkel] = standard()
    st.session_state.setdefault("valgt", None)

    universe = st.session_state.universe
    aktive = [e["ticker"] for e in universe if e["enabled"]]

    # CSS injiseres to ganger: først uten radfarger så sidepanelet er stylet
    # mens data lastes, deretter med statusfarger når resultatene finnes.
    injiser_css()

    if not aktive:
        _sidepanel([])
        st.warning("Ingen aktive selskaper i watchlisten. Legg til i sidepanelet.")
        return

    prisdata = hent_prisdata(tuple(sorted(aktive)))
    resultater = kjor_scan(prisdata, st.session_state.fundamentals) if prisdata else []

    if not resultater:
        _sidepanel([])
        st.error("Fikk ikke brukbare data. Prøv «SCAN NÅ» igjen om litt.")
        return

    st.session_state.nye_varsler = evaluer_varsler(resultater, st.session_state.radar_state)
    lagre_state(st.session_state.radar_state)

    sortering, vis_wait = _sidepanel(resultater)
    injiser_css({r["ticker"]: STATUS_FARGE[r["status"]] for r in resultater},
                st.session_state.valgt)

    sortert = sorter_resultater(resultater, sortering)
    synlig = sortert if vis_wait else [r for r in sortert if r["status"] != STATUS_WAIT]

    gyldige = {r["ticker"] for r in resultater}
    if st.session_state.valgt not in gyldige:
        st.session_state.valgt = sortert[0]["ticker"]
    valgt_r = next(r for r in resultater if r["ticker"] == st.session_state.valgt)

    hoved, panel = st.columns([2.55, 1], gap="medium")

    with hoved:
        st.html(fremdriftsstripe(len(prisdata) / max(len(aktive), 1)))

        oslo = datetime.now(ZoneInfo("Europe/Oslo")).strftime("%H:%M")
        c = st.columns([2.6, 1.5, 0.75])
        c[0].html(f'<div style="font-family:{MONO};font-size:11px;color:{DC["dempet"]};'
                  f'padding-top:9px;">RADAR · {len(resultater)}/{len(aktive)} SELSKAPER · '
                  f'{SCANNER_CONFIG["historyYears"]} ÅRS HISTORIKK</div>')
        c[1].html(f'<div style="font-family:{MONO};font-size:11px;color:{DC["svakest"]};'
                  f'padding-top:9px;text-align:right;">OPPDATERT {oslo} OSLO</div>')
        if c[2].button("SCAN NÅ", key="scan", width="stretch"):
            st.cache_data.clear()
            st.rerun()

        st.html(statustellere(resultater))
        st.html(varselrader(st.session_state.nye_varsler))

        logg = st.session_state.radar_state.get("alerts", [])
        if logg:
            with st.expander(f"VARSELHISTORIKK ({len(logg)})"):
                st.dataframe(
                    pd.DataFrame([{"Tid": v["tid"], "Ticker": v["ticker"],
                                   "Type": STATUS_TEKST.get(v["type"].rstrip("+"), v["type"])}
                                  for v in logg]),
                    width="stretch", hide_index=True, height=220)

        cv = st.columns([0.9, 3])
        visning = cv[0].segmented_control("V", ["TABELL", "KORT"], default="TABELL",
                                          label_visibility="collapsed")
        cv[1].html(f'<div style="font-family:{MONO};font-size:10px;letter-spacing:0.1em;'
                   f'color:{DC["svakest"]};padding-top:9px;">{len(synlig)} SELSKAPER · '
                   f'SORTERT PÅ {sortering.upper()}</div>')

        manglende = [t for t in aktive if t not in prisdata]
        if manglende:
            st.html(f'<div style="font-family:{MONO};font-size:11px;color:{DC["orange"]};'
                    f'padding:6px 0;">MANGLER DATA: '
                    f'{", ".join(t.replace(".OL", "") for t in manglende)}</div>')

        if (visning or "TABELL") == "TABELL":
            valgt_pos = next((i for i, r in enumerate(synlig)
                              if r["ticker"] == st.session_state.valgt), None)
            # Sortering og WAIT-filter inngår i nøkkelen. Ellers ville et lagret
            # radvalg overlevd en sorteringsendring og plutselig pekt på en
            # annen aksje enn den du klikket på.
            hendelse = st.dataframe(
                tabell_stil(tabell_rader(synlig), synlig),
                key=f"tabell-{sortering}-{vis_wait}",
                on_select="rerun", selection_mode="single-row",
                hide_index=True, width="stretch", row_height=34,
                height=min(len(synlig) * 34 + 40, 700),
                column_config=TABELL_KOLONNER,
                selection_default={"selection": {"rows": [valgt_pos]}}
                if valgt_pos is not None else None,
            )
            traff = hendelse.selection.rows if hendelse and hendelse.selection else []
            if traff and synlig[traff[0]]["ticker"] != st.session_state.valgt:
                st.session_state.valgt = synlig[traff[0]]["ticker"]
                st.rerun()
            st.html(tabell_fotnote(synlig))
        else:
            for sone in SONER:
                i_sone = [r for r in synlig if r["status"] in sone["statuser"]]
                st.html(sone_header(sone["navn"], sone["farge"], len(i_sone)))
                if not i_sone:
                    st.html(f'<div style="font-size:12px;color:{DC["svakest"]};'
                            f'padding:2px 0 6px;">Ingen aksjer i denne sonen nå.</div>')
                for r in i_sone:
                    st.html(kort_html(r, sone["form"]))

        st.html(f'<div style="height:1px;background:{DC["linje"]};margin:18px 0 8px;"></div>')
        _fotnote()

    with panel:
        if _hoyrepanel(valgt_r, st.session_state.fundamentals):
            st.rerun()


if __name__ == "__main__":
    main()
