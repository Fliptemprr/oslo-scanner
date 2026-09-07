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
# DESIGN TOKENS – hentet fra mockupen «1b TRIAGE»
# ══════════════════════════════════════════════════════════════

DC = {
    "bg": "#101216",
    "kort": "#14171C",
    "inset": "#0D0F13",
    "rail": "#0C0E11",
    "linje": "#23272F",
    "linjeSvak": "#1C2027",
    "kant": "#2B313A",
    "tekst": "#ECEEF2",
    "dempet": "#79828F",
    "dempet2": "#9AA3B0",
    "svak": "#6A7280",
    "blaa": "#4DA3FF",
    "gronn": "#2FD48F",
    "roed": "#FF5B6A",
    "orange": "#FF9130",
    "gul": "#E8C547",
}

MONO = "'IBM Plex Mono', ui-monospace, SFMono-Regular, Menlo, monospace"
SANS = "'IBM Plex Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
DISPLAY = "'Space Grotesk', 'IBM Plex Sans', sans-serif"

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

# Tre soner ovenfra og ned. Rekkefølgen inni hver sone følger
# STATUS_PRIORITY, så §18 styrer fortsatt sorteringen.
SONER = [
    {"navn": "KREVER GJENNOMGANG", "farge": DC["roed"], "form": "stor",
     "statuser": [STATUS_EVENT_RISK, STATUS_REVERSAL]},
    {"navn": "FØLG", "farge": DC["blaa"], "form": "medium",
     "statuser": [STATUS_STABILIZING, STATUS_STRONG_CORRECTION, STATUS_CORRECTION]},
    {"navn": "ROLIG", "farge": DC["svak"], "form": "kompakt",
     "statuser": [STATUS_FOLLOW, STATUS_WAIT]},
]

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

SORTERINGSVALG = {
    "PRIORITET": None,
    "CORRECTION SCORE": "correctionScore",
    "RECOVERY SCORE": "recoveryScore",
    "TREND SCORE": "trendScore",
    "KORREKSJON %": "korreksjon",
    "TICKER": "Ticker",
}


def _rgba(hex_farge: str, alpha: float) -> str:
    h = hex_farge.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


# ══════════════════════════════════════════════════════════════
# CSS
# Streamlit-chrome tones ned og widgets tilpasses terminal-temaet.
# Alle selektorer bruker data-testid eller st-key-*, som er de
# stabile krokene på tvers av Streamlit-versjoner.
# ══════════════════════════════════════════════════════════════

def injiser_css() -> None:
    st.markdown(f"""
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&family=Space+Grotesk:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  .stApp {{ background: {DC['bg']}; }}
  html, body, [class*="css"] {{ font-family: {SANS}; color: {DC['tekst']}; }}

  /* Streamlit-chrome vekk */
  #MainMenu, footer, header [data-testid="stStatusWidget"] {{ visibility: hidden; }}
  [data-testid="stDecoration"] {{ display: none; }}
  [data-testid="stAppViewBlockContainer"] {{ padding: 1.6rem 2.2rem 4rem; max-width: 1500px; }}
  [data-testid="stVerticalBlock"] {{ gap: 0.55rem; }}
  [data-testid="stHorizontalBlock"] {{ gap: 0.9rem; }}

  /* Knapper */
  .stButton > button {{
    background: transparent; border: 1px solid {DC['kant']}; color: {DC['dempet']};
    border-radius: 8px; font-family: {MONO}; font-size: 11px; letter-spacing: 0.06em;
    padding: 8px 14px; transition: none;
  }}
  .stButton > button:hover {{ border-color: {DC['blaa']}; color: {DC['blaa']}; }}
  .stButton > button[kind="primary"] {{
    background: {DC['gronn']}; border: none; color: #06180F; font-weight: 600;
    border-radius: 999px; padding: 9px 18px;
  }}
  .stButton > button[kind="primary"]:hover {{ background: #4AE0A2; color: #06180F; }}

  /* Segmented control / pills */
  [data-testid="stSegmentedControl"] button {{
    font-family: {MONO}; font-size: 11px; letter-spacing: 0.06em;
    background: transparent; color: {DC['dempet']}; border-color: {DC['kant']};
  }}
  [data-testid="stSegmentedControl"] button[aria-checked="true"] {{
    background: #ECEEF2; color: {DC['bg']};
  }}

  /* Selectbox */
  [data-testid="stSelectbox"] div[data-baseweb="select"] > div {{
    background: transparent; border-color: {DC['kant']}; border-radius: 8px;
    font-family: {MONO}; font-size: 11px; color: {DC['dempet']}; min-height: 34px;
  }}
  [data-testid="stSelectbox"] label {{
    font-family: {MONO}; font-size: 10px; letter-spacing: 0.12em; color: {DC['dempet']};
  }}
  div[data-baseweb="popover"] li {{ font-family: {MONO}; font-size: 12px; }}

  /* Checkbox – fundamental gate */
  [data-testid="stCheckbox"] label {{ font-size: 12px; color: {DC['dempet2']}; gap: 8px; }}
  [data-testid="stCheckbox"] label span[data-baseweb="checkbox"] div:first-child {{
    background: {DC['inset']}; border-color: {DC['kant']}; border-radius: 4px;
  }}

  /* Expander – DETALJER */
  [data-testid="stExpander"] {{ border: none; background: transparent; }}
  [data-testid="stExpander"] details {{
    border: 1px solid {DC['kant']}; border-radius: 8px; background: transparent;
  }}
  [data-testid="stExpander"] summary {{
    font-family: {MONO}; font-size: 11px; letter-spacing: 0.06em; color: {DC['dempet']};
    padding: 9px 14px;
  }}
  [data-testid="stExpander"] summary:hover {{ color: {DC['blaa']}; }}

  /* Faner inne i detaljer */
  [data-testid="stTabs"] button {{
    font-family: {MONO}; font-size: 10px; letter-spacing: 0.1em; color: {DC['dempet']};
  }}
  [data-testid="stTabs"] button[aria-selected="true"] {{ color: {DC['tekst']}; }}
  [data-testid="stTabs"] [data-baseweb="tab-highlight"] {{ background: {DC['blaa']}; }}

  /* Fundamental-widgets pakkes tett inn i kortet over */
  [class*="st-key-gate-"] {{
    background: {DC['inset']}; border: 1px solid {DC['linje']};
    border-radius: 10px; padding: 10px 14px 4px; margin-top: -6px;
  }}
  [class*="st-key-rail-"] [data-testid="stCheckbox"] label {{ font-size: 11px; }}

  /* Skjuler tomme markdown-wrappere som ellers lager luft */
  [data-testid="stMarkdown"]:empty {{ display: none; }}

  /* Scrollbar */
  ::-webkit-scrollbar {{ width: 10px; height: 10px; }}
  ::-webkit-scrollbar-track {{ background: {DC['bg']}; }}
  ::-webkit-scrollbar-thumb {{ background: {DC['kant']}; border-radius: 5px; }}
</style>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════
# HTML-KOMPONENTER
# ══════════════════════════════════════════════════════════════

def f(v: Optional[float], desimaler: int = 2, suffix: str = "") -> str:
    """Formater tall for visning. None blir «—»."""
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return "—"
    return f"{v:,.{desimaler}f}".replace(",", " ") + suffix


def _esc(s: Any) -> str:
    return html_lib.escape(str(s))


def badge(status: str, kort: bool = False, stor: bool = False) -> str:
    """Statusbadge. Erstatter emoji-prikkene fra forrige versjon."""
    farge = STATUS_FARGE[status]
    tekst = (STATUS_KORT if kort else STATUS_TEKST)[status]
    if stor:
        return (f'<span style="display:inline-flex;align-items:center;background:{farge};'
                f'color:{DC["bg"]};border-radius:999px;padding:7px 14px;font-family:{MONO};'
                f'font-size:12px;font-weight:600;letter-spacing:0.08em;">{tekst}</span>')
    if status == STATUS_WAIT:
        return (f'<span style="font-family:{MONO};font-size:10px;letter-spacing:0.08em;'
                f'color:{farge};border:1px solid {DC["kant"]};padding:3px 7px;'
                f'border-radius:3px;white-space:nowrap;">{tekst}</span>')
    return (f'<span style="font-family:{MONO};font-size:10px;letter-spacing:0.08em;'
            f'color:{farge};border:1px solid {_rgba(farge, 0.38)};'
            f'background:{_rgba(farge, 0.11)};padding:3px 7px;border-radius:3px;'
            f'white-space:nowrap;">{tekst}</span>')


def sone_header(navn: str, farge: str, antall: int) -> str:
    grad = (f"linear-gradient(90deg, {_rgba(farge, 0.4)}, transparent)"
            if navn != "ROLIG" else DC["linje"])
    return (f'<div style="display:flex;align-items:center;gap:10px;margin:14px 0 10px;">'
            f'<div style="font-family:{MONO};font-size:11px;letter-spacing:0.14em;'
            f'color:{farge};">{navn} · {antall}</div>'
            f'<div style="flex:1;height:1px;background:{grad};"></div></div>')


def _maalerblokk(etikett: str, verdi: str, under: str,
                 verdifarge: str = None, underfarge: str = None) -> str:
    return (f'<div style="background:{DC["inset"]};border:1px solid {DC["linje"]};'
            f'border-radius:10px;padding:14px;">'
            f'<div style="font-family:{MONO};font-size:10px;letter-spacing:0.1em;'
            f'color:{DC["dempet"]};">{etikett}</div>'
            f'<div style="font-family:{MONO};font-size:28px;line-height:1.2;'
            f'color:{verdifarge or DC["tekst"]};">{verdi}</div>'
            f'<div style="font-family:{MONO};font-size:11px;'
            f'color:{underfarge or DC["dempet"]};">{under}</div></div>')


def reversal_blokkert(r: dict, cfg: dict = SCANNER_CONFIG) -> bool:
    """
    Aksjen oppfyller alle tekniske REVERSAL-krav, men mangler fundamental
    godkjenning. Statusmotoren lar den da falle gjennom til FOLLOW (§15), så
    uten dette flagget ville den havnet i den rolige sonen ett hakemerke unna
    REVERSAL, uten synlig grunn.
    """
    return (r["correctionScore"] >= cfg["correction"]["correction"]
            and r["recoveryScore"] >= cfg["recovery"]["confirmed"]
            and r["trendScore"] >= cfg["trend"]["minimumForReversal"]
            and not (r["fundamentalsChecked"] and r["thesisIntact"]))


def gate_pille() -> str:
    return (f'<span style="font-family:{MONO};font-size:9px;letter-spacing:0.08em;'
            f'color:{DC["orange"]};border:1px solid {_rgba(DC["orange"], 0.35)};'
            f'background:{_rgba(DC["orange"], 0.1)};padding:2px 6px;border-radius:3px;'
            f'margin-left:8px;white-space:nowrap;">GATE</span>')


def event_detalj(r: dict) -> str:
    """Kort forklaring på hva som utløste event risk, som i mockupen."""
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


def stort_kort_html(r: dict) -> str:
    """Sone 1: full bredde, status som overskrift, fire målerblokker."""
    ind, cc = r["ind"], r.get("currentCorrection")
    farge = STATUS_FARGE[r["status"]]

    meta = [f'{f(ind["close_now"])}']
    if ind.get("return1d") is not None:
        rf = DC["gronn"] if ind["return1d"] >= 0 else DC["roed"]
        meta[0] += f' <span style="color:{rf};">{f(ind["return1d"], 2, " %")}</span>'
    if cc:
        meta.append(f"{cc.daysSincePeak} dager siden topp")
        if cc.recoveryPct > 0.5:
            meta.append(f"opp {f(cc.recoveryPct, 1)} % fra bunn")
        meta.append(f"korreksjon {_esc(cc.id)}")

    detalj = event_detalj(r) if r["eventRisk"] else (
        f"FUNDAMENTAL {sum([r['fundamental'].reportChecked, r['fundamental'].guidanceChecked, r['fundamental'].newsChecked, r['fundamental'].thesisIntact])}/4"
    )

    maalere = "".join([
        _maalerblokk("KORREKSJON",
                     f"-{f(cc.drawdownPct, 1)} %" if cc else "—",
                     f"dybde -{f(cc.maxDepthPct, 1)} %" if cc else "",
                     DC["roed"] if cc and cc.drawdownPct > 0 else None),
        _maalerblokk("PERCENTIL", f"{r['correctionPercentile']:.0f}",
                     "tynn historikk" if r["tynnHistorikk"]
                     else f"{r['antallHistoriske']} tidligere",
                     underfarge=DC["orange"] if r["tynnHistorikk"] else None),
        _maalerblokk("TREND", f"{r['trendScore']:.0f}", r["trendBand"],
                     underfarge=DC["gronn"] if r["trendScore"] >= 60 else DC["orange"]),
        _maalerblokk("RECOVERY", f"{r['recoveryScore']:.0f}", r["recoveryBand"],
                     underfarge=DC["gronn"] if r["recoveryScore"] >= 50 else DC["roed"]),
    ])

    gate = ""
    if not r["fundamentalsChecked"]:
        antall = sum([r["fundamental"].reportChecked,
                      r["fundamental"].guidanceChecked, r["fundamental"].newsChecked])
        gate = (f'<div style="display:flex;gap:10px;align-items:flex-start;'
                f'background:{_rgba(DC["orange"], 0.07)};border:1px solid {_rgba(DC["orange"], 0.25)};'
                f'border-radius:8px;padding:10px 12px;">'
                f'<span style="font-family:{MONO};font-size:10px;letter-spacing:0.1em;'
                f'color:{DC["orange"]};">GATE</span>'
                f'<span style="font-size:12px;color:{DC["dempet2"]};line-height:1.5;">'
                f'Fundamental sjekk ikke fullført — REVERSAL er blokkert. '
                f'{antall} av 3 punkter avkrysset.</span></div>')
    elif r["fundamental"].stale:
        gate = (f'<div style="font-size:12px;color:{DC["orange"]};">'
                f'Forrige fundamentale sjekk gjaldt en tidligere korreksjon og er nullstilt.</div>')

    return f"""
<div style="border:1px solid {_rgba(farge, 0.35)};
     background:linear-gradient(180deg, {_rgba(farge, 0.09)}, {_rgba(farge, 0.02)});
     border-radius:12px;padding:20px;display:flex;flex-direction:column;gap:18px;">
  <div style="display:flex;align-items:flex-start;gap:16px;">
    <div style="flex:1;min-width:0;">
      <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
        <span style="font-family:{DISPLAY};font-size:26px;font-weight:600;
              letter-spacing:-0.01em;color:{DC['tekst']};">{_esc(r['Ticker'])}</span>
        <span style="font-size:13px;color:{DC['dempet2']};">{_esc(r['Navn'])}</span>
        <a href="https://finance.yahoo.com/quote/{_esc(r['ticker'])}" target="_blank"
           style="font-size:11px;color:{DC['blaa']};text-decoration:none;">Yahoo ↗</a>
      </div>
      <div style="font-family:{MONO};font-size:12px;color:{DC['dempet']};margin-top:4px;">
        {' · '.join(meta)}
      </div>
    </div>
    <div style="text-align:right;">
      {badge(r['status'], stor=True)}
      <div style="font-family:{MONO};font-size:11px;color:{DC['dempet']};margin-top:6px;">
        {detalj}</div>
    </div>
  </div>
  <div style="display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;">
    {maalere}
  </div>
  {gate}
</div>"""


def medium_rad_html(r: dict) -> str:
    """Sone 2: én linje per aksje med de tre scorene."""
    cc = r.get("currentCorrection")
    farge = STATUS_FARGE[r["status"]]
    dager = f" · {cc.daysSincePeak} dager siden topp" if cc else ""
    tynn = (f' <span style="color:{DC["orange"]};">· kun {r["antallHistoriske"]} '
            f'tidligere korreksjoner</span>') if r["tynnHistorikk"] else ""
    gate = gate_pille() if reversal_blokkert(r) else ""

    def score(etikett, verdi, fargelegg=None):
        return (f'<div style="text-align:right;">'
                f'<div style="font-family:{MONO};font-size:9px;color:{DC["dempet"]};">{etikett}</div>'
                f'<div style="font-family:{MONO};font-size:14px;'
                f'color:{fargelegg or DC["tekst"]};">{verdi:.0f}</div></div>')

    return f"""
<div style="display:grid;grid-template-columns:96px 1fr 160px 90px 76px 76px 76px;
     align-items:center;gap:12px;background:{DC['kort']};border:1px solid {DC['linje']};
     border-left:3px solid {farge};border-radius:10px;padding:14px 16px;">
  <div style="font-family:{DISPLAY};font-size:18px;font-weight:600;
       color:{DC['tekst']};">{_esc(r['Ticker'])}</div>
  <div style="font-size:12px;color:{DC['dempet2']};overflow:hidden;
       text-overflow:ellipsis;white-space:nowrap;">{_esc(r['Navn'])}{dager}{tynn}</div>
  <div>{badge(r['status'], kort=True)}{gate}</div>
  <div style="font-family:{MONO};font-size:15px;color:{DC['orange']};text-align:right;">
    {f'-{f(cc.drawdownPct, 1)} %' if cc else '—'}</div>
  {score('CORR', r['correctionScore'])}
  {score('TREND', r['trendScore'], DC['gronn'] if r['trendScore'] >= 60 else None)}
  {score('RECOV', r['recoveryScore'], DC['dempet'] if r['recoveryScore'] < 30 else None)}
</div>"""


def kompakt_rad_html(r: dict) -> str:
    """Sone 3: dempet rad, minst mulig visuell vekt."""
    cc = r.get("currentCorrection")

    def tall(v, farge=None):
        return (f'<div style="font-family:{MONO};font-size:13px;text-align:right;'
                f'color:{farge or "inherit"};">{v}</div>')

    return f"""
<div style="display:grid;grid-template-columns:96px 1fr 160px 90px 76px 76px 76px;
     align-items:center;gap:12px;padding:10px 16px;border:1px solid {DC['linjeSvak']};
     border-radius:10px;color:{DC['svak']};">
  <div style="font-family:{DISPLAY};font-size:15px;font-weight:600;
       color:{DC['dempet2']};">{_esc(r['Ticker'])}</div>
  <div style="font-size:12px;overflow:hidden;text-overflow:ellipsis;
       white-space:nowrap;">{_esc(r['Navn'])}</div>
  <div>{badge(r['status'], kort=True)}{gate_pille() if reversal_blokkert(r) else ""}</div>
  {tall(f'-{f(cc.drawdownPct, 1)} %' if cc else '—')}
  {tall(f"{r['correctionScore']:.0f}")}
  {tall(f"{r['trendScore']:.0f}", DC['gronn'] if r['trendScore'] >= 80 else None)}
  {tall(f"{r['recoveryScore']:.0f}")}
</div>"""


def kriterieliste_html(tittel: str, score: float, deler: dict,
                       poeng: dict, etiketter: dict) -> str:
    """Full poengoppdeling, som i mockupens KRITERIER-fane."""
    rader = []
    for nokkel, etikett in etiketter.items():
        truffet = bool(deler.get(nokkel))
        maks = poeng[nokkel]
        rader.append(
            f'<div style="display:grid;grid-template-columns:18px 1fr 54px;gap:10px;'
            f'align-items:center;padding:5px 0;font-size:12px;'
            f'color:{DC["dempet2"] if truffet else DC["svak"]};">'
            f'<span style="color:{DC["gronn"] if truffet else DC["kant"]};">'
            f'{"✓" if truffet else "·"}</span>'
            f'<span>{etikett}</span>'
            f'<span style="font-family:{MONO};font-size:11px;text-align:right;'
            f'color:{DC["tekst"] if truffet else DC["svak"]};">'
            f'{maks if truffet else 0}/{maks}</span></div>'
        )
    return (f'<div><div style="display:flex;align-items:baseline;gap:10px;'
            f'margin-bottom:6px;"><span style="font-family:{MONO};font-size:10px;'
            f'letter-spacing:0.1em;color:{DC["dempet"]};">{tittel}</span>'
            f'<span style="font-family:{MONO};font-size:18px;color:{DC["tekst"]};">'
            f'{score:.0f}</span></div>{"".join(rader)}</div>')


def korreksjonsforlop_html(r: dict) -> str:
    cc = r.get("currentCorrection")
    if not cc:
        return f'<div style="color:{DC["dempet"]};font-size:12px;">Ingen korreksjon registrert.</div>'

    def punkt(etikett, verdi, dato, farge=None):
        return (f'<div><div style="font-family:{MONO};font-size:10px;letter-spacing:0.1em;'
                f'color:{DC["dempet"]};">{etikett}</div>'
                f'<div style="font-family:{MONO};font-size:19px;color:{farge or DC["tekst"]};">'
                f'{verdi}</div>'
                f'<div style="font-family:{MONO};font-size:11px;color:{DC["dempet"]};">'
                f'{dato}</div></div>')

    pil = (f'<div style="color:{DC["kant"]};font-size:18px;align-self:center;">→</div>')
    w = r["correctionScoreWeights"] if "correctionScoreWeights" in r else SCANNER_CONFIG["correctionScoreWeights"]

    return (
        f'<div style="display:flex;gap:22px;align-items:stretch;flex-wrap:wrap;">'
        f'{punkt("TOPP", f(cc.peakPrice), cc.peakDate)}{pil}'
        f'{punkt("BUNN", f(cc.troughPrice), cc.troughDate, DC["roed"])}{pil}'
        f'{punkt("NÅ", f(cc.currentPrice), f"{cc.daysSincePeak} dager siden topp")}'
        f'</div>'
        f'<div style="margin-top:14px;font-size:12px;color:{DC["dempet2"]};line-height:1.6;">'
        f'Correction Score {r["correctionScore"]:.0f} = percentil {r["correctionPercentile"]:.0f} '
        f'× {w["percentile"]:.0%} + stretch {r["stretchScore"]:.0f} × {w["technicalStretch"]:.0%} '
        f'+ støtte {r["supportScore"]:.0f} × {w["support"]:.0%}.'
        + (f' Nærmeste bekreftede swing-low {f(r["supportNivå"])}, '
           f'{f(r["supportAvstandPct"], 2)} % fra kurs.' if r.get("supportNivå") else
           " Ingen bekreftet swing-low funnet ennå.")
        + f'</div>'
        + (f'<div style="margin-top:8px;font-size:12px;color:{DC["orange"]};">'
           f'⚠️ Kun {r["antallHistoriske"]} tidligere korreksjoner — percentilen er '
           f'lite pålitelig.</div>' if r["tynnHistorikk"] else "")
    )


def nokkeltall_html(r: dict) -> str:
    ind = r["ind"]
    rader = [
        ("Kurs", f(ind["close_now"])), ("1D %", f(ind["return1d"], 2, " %")),
        ("1M %", f(ind["return1m"], 1, " %")), ("3M %", f(ind["return3m"], 1, " %")),
        ("6M %", f(ind["return6m"], 1, " %")), ("52W drawdown", f(ind["drawdown52w"], 1, " %")),
        ("RSI 14", f(ind["rsi"], 1)), ("SMA20", f(ind["sma20"])),
        ("SMA50", f(ind["sma50"])), ("SMA200", f(ind["sma200"])),
        ("ATR 14", f(ind["atr"])), ("ATR %", f(ind["atrPct"], 2, " %")),
        ("Volum", f(ind["volume_now"], 0)), ("Vol Ratio", f(ind["volumeRatio20d"])),
        ("20D high", f(ind["high20d"])), ("60D high", f(ind["high60d"])),
        ("52W high", f(ind["high52w"])), ("Gap ned %", f(ind["gapDownPct"], 2, " %")),
    ]
    celler = "".join(
        f'<div style="border:1px solid {DC["linje"]};border-radius:8px;padding:9px 11px;">'
        f'<div style="font-family:{MONO};font-size:9px;letter-spacing:0.1em;'
        f'color:{DC["dempet"]};">{e.upper()}</div>'
        f'<div style="font-family:{MONO};font-size:14px;color:{DC["tekst"]};">{v}</div></div>'
        for e, v in rader
    )
    return (f'<div style="display:grid;grid-template-columns:repeat(6,minmax(0,1fr));'
            f'gap:8px;">{celler}</div>')


def historikk_html(r: dict) -> str:
    h = r["historiskeKorreksjoner"]
    if not h:
        return (f'<div style="color:{DC["dempet"]};font-size:12px;">'
                f'Ingen avsluttede korreksjoner funnet i historikken.</div>')
    sortert = sorted(h, key=lambda x: -x.drawdownPct)[:25]
    naa = r["currentCorrection"].maxDepthPct if r.get("currentCorrection") else None
    rader = []
    for k in sortert:
        storre = naa is not None and k.drawdownPct > naa
        rader.append(
            f'<tr style="color:{DC["dempet2"] if storre else DC["tekst"]};">'
            f'<td style="padding:4px 10px 4px 0;font-family:{MONO};font-size:11px;">{k.peakDate}</td>'
            f'<td style="padding:4px 10px 4px 0;font-family:{MONO};font-size:11px;">{k.troughDate}</td>'
            f'<td style="padding:4px 10px 4px 0;font-family:{MONO};font-size:12px;text-align:right;">'
            f'-{k.drawdownPct:.1f} %</td>'
            f'<td style="padding:4px 10px 4px 0;font-family:{MONO};font-size:11px;text-align:right;">'
            f'{k.durationDays} d</td>'
            f'<td style="padding:4px 0;font-family:{MONO};font-size:11px;text-align:right;">'
            f'{k.atrNormalizedDrawdown:.1f}×</td></tr>'
        )
    naa_rad = ""
    if naa is not None:
        naa_rad = (f'<tr style="color:{DC["roed"]};border-top:1px solid {DC["kant"]};">'
                   f'<td colspan="2" style="padding:7px 10px 4px 0;font-family:{MONO};'
                   f'font-size:11px;">NÅ</td>'
                   f'<td style="padding:7px 10px 4px 0;font-family:{MONO};font-size:12px;'
                   f'text-align:right;">-{naa:.1f} %</td><td colspan="2"></td></tr>')
    return (f'<table style="width:100%;border-collapse:collapse;">'
            f'<thead><tr style="color:{DC["dempet"]};">'
            + "".join(f'<th style="text-align:{"right" if i > 1 else "left"};'
                      f'padding:0 10px 6px 0;font-family:{MONO};font-size:9px;'
                      f'letter-spacing:0.1em;font-weight:400;">{t}</th>'
                      for i, t in enumerate(["TOPP", "BUNN", "FALL", "VARIGHET", "I ATR"]))
            + f'</tr></thead><tbody>{naa_rad}{"".join(rader)}</tbody></table>')


def tabell_html(resultater: list) -> str:
    """TABELL-visningen: tett rad per aksje med statusfarge som venstre-spine."""
    kolonner = ["TICKER", "NAVN", "STATUS", "CORR", "TREND", "RECOV",
                "KORR %", "PCTL", "DAGER", "KURS", "% I DAG", "RSI", "VOL R", "F"]
    hoder = "".join(
        f'<th style="text-align:{"left" if i < 3 else "right"};padding:0 10px 8px;'
        f'font-family:{MONO};font-size:9px;letter-spacing:0.11em;font-weight:400;'
        f'color:{DC["dempet"]};white-space:nowrap;">{k}</th>'
        for i, k in enumerate(kolonner))

    rader = []
    tynn_finnes = False
    for r in resultater:
        cc, ind = r.get("currentCorrection"), r["ind"]
        farge = STATUS_FARGE[r["status"]]
        dempet = r["status"] == STATUS_WAIT
        tekstfarge = DC["svak"] if dempet else DC["tekst"]
        pct = f"{r['correctionPercentile']:.0f}"
        if r["tynnHistorikk"]:
            pct += "*"
            tynn_finnes = True
        d1 = ind.get("return1d")

        def c(v, align="right", farge_=None, mono=True, nowrap=True):
            return (f'<td style="padding:9px 10px;text-align:{align};'
                    f'font-family:{MONO if mono else SANS};font-size:12px;'
                    f'color:{farge_ or tekstfarge};'
                    f'{"white-space:nowrap;" if nowrap else ""}'
                    f'border-bottom:1px solid {DC["linjeSvak"]};">{v}</td>')

        rader.append(
            f'<tr>'
            f'<td style="padding:0;width:3px;background:{farge};'
            f'border-bottom:1px solid {DC["linjeSvak"]};"></td>'
            + c(f'<span style="font-family:{DISPLAY};font-size:14px;font-weight:600;">'
                f'{_esc(r["Ticker"])}</span>', "left")
            + c(f'<span style="overflow:hidden;text-overflow:ellipsis;">{_esc(r["Navn"])}</span>',
                "left", DC["dempet2"], mono=False)
            + c(badge(r["status"], kort=True)
                + (gate_pille() if reversal_blokkert(r) else ""), "left")
            + c(f"{r['correctionScore']:.0f}")
            + c(f"{r['trendScore']:.0f}", farge_=DC["gronn"] if r["trendScore"] >= 60 and not dempet else None)
            + c(f"{r['recoveryScore']:.0f}")
            + c(f"-{f(cc.drawdownPct, 1)}" if cc else "—", farge_=DC["orange"] if cc and not dempet else None)
            + c(pct)
            + c(cc.daysSincePeak if cc else "—")
            + c(f(ind["close_now"]))
            + c(f(d1, 2), farge_=(DC["gronn"] if d1 >= 0 else DC["roed"]) if d1 is not None and not dempet else None)
            + c(f(ind["rsi"], 1))
            + c(f(ind["volumeRatio20d"]))
            + c("✓" if r["fundamentalsChecked"] else "—",
                farge_=DC["gronn"] if r["fundamentalsChecked"] else None)
            + f'</tr>'
        )

    fotnote = ("* TYNN HISTORIKK — PERCENTILEN ER LITE PÅLITELIG · " if tynn_finnes else "")
    return (f'<div style="background:{DC["kort"]};border:1px solid {DC["linje"]};'
            f'border-radius:10px;overflow:hidden;">'
            f'<div style="overflow-x:auto;">'
            f'<table style="width:100%;border-collapse:collapse;">'
            f'<thead><tr><th style="width:3px;padding:0;"></th>{hoder}</tr></thead>'
            f'<tbody>{"".join(rader)}</tbody></table></div>'
            f'<div style="padding:10px 14px;font-family:{MONO};font-size:9px;'
            f'letter-spacing:0.1em;color:{DC["dempet"]};border-top:1px solid {DC["linje"]};">'
            f'{fotnote}RADAREN GIR INGEN KJØPS- ELLER SALGSSIGNALER</div></div>')


def varsel_html(v: dict) -> str:
    status = v["type"].rstrip("+")
    farge = STATUS_FARGE.get(status, DC["blaa"])
    return (f'<div style="background:{DC["kort"]};border-left:3px solid {farge};'
            f'border-radius:0 8px 8px 0;padding:11px 12px;margin-bottom:8px;">'
            f'<div style="font-family:{MONO};font-size:12px;font-weight:600;'
            f'color:{DC["tekst"]};">{_esc(v["ticker"])} → {STATUS_TEKST.get(status, status)}</div>'
            f'<div style="font-size:11px;color:{DC["dempet"]};margin-top:3px;'
            f'line-height:1.5;">{_esc(v.get("sammendrag", v["tid"]))}</div></div>')


def sorter_resultater(resultater: list, valg: str) -> list:
    """Standard: det som krever oppmerksomhet først (§18)."""
    if valg == "TICKER":
        return sorted(resultater, key=lambda r: r["Ticker"])
    if valg == "KORREKSJON %":
        return sorted(resultater, key=lambda r: -(r["currentCorrection"].drawdownPct
                                                  if r.get("currentCorrection") else -999))
    felt = SORTERINGSVALG.get(valg)
    if felt:
        return sorted(resultater, key=lambda r: -r[felt])
    return sorted(resultater, key=lambda r: (
        STATUS_PRIORITY.get(r["status"], 99), -r["correctionScore"]
    ))


# ══════════════════════════════════════════════════════════════
# STREAMLIT APP
# ══════════════════════════════════════════════════════════════

def _fundamental_gate(r: dict, fund_store: dict) -> bool:
    """Fire avkrysninger. Returnerer True hvis noe ble endret."""
    fund: FundamentalCheck = r["fundamental"]
    t = r["ticker"]

    with st.container(key=f"gate-{t.replace('.', '-')}"):
        c = st.columns(4)
        ny = FundamentalCheck(
            reportChecked=c[0].checkbox("Siste rapport", value=fund.reportChecked, key=f"fr_{t}"),
            guidanceChecked=c[1].checkbox("Guiding", value=fund.guidanceChecked, key=f"fg_{t}"),
            newsChecked=c[2].checkbox("Nyheter", value=fund.newsChecked, key=f"fn_{t}"),
            thesisIntact=c[3].checkbox("Case intakt", value=fund.thesisIntact, key=f"ft_{t}"),
            correctionId=r["correctionId"],
        )

    endret = (ny.reportChecked != fund.reportChecked
              or ny.guidanceChecked != fund.guidanceChecked
              or ny.newsChecked != fund.newsChecked
              or ny.thesisIntact != fund.thesisIntact
              or fund.stale)
    if endret:
        ny.updated = datetime.now(ZoneInfo("Europe/Oslo")).strftime("%Y-%m-%d %H:%M")
        d = asdict(ny)
        d.pop("stale", None)
        fund_store[t] = d
        lagre_fundamentals(fund_store)
    return endret


def _detaljer(r: dict, fund_store: dict = None) -> bool:
    """
    DETALJER-panelet: fire faner, som i mockupen.

    Sone 2-kort får fundamental-gaten her inne. Uten det ville en aksje i
    CORRECTION/STABILIZING aldri kunne få sjekken fullført, og dermed aldri
    kunne nå REVERSAL — trakten ville vært låst.
    """
    endret = False
    with st.expander("DETALJER"):
        if fund_store is not None:
            st.markdown(
                f'<div style="font-family:{MONO};font-size:10px;letter-spacing:0.1em;'
                f'color:{DC["dempet"]};margin-bottom:2px;">FUNDAMENTAL GATE</div>',
                unsafe_allow_html=True)
            endret = _fundamental_gate(r, fund_store)
        t1, t2, t3, t4 = st.tabs(["KRITERIER", "KORREKSJONSFORLØP",
                                  f"HISTORIKK ({r['antallHistoriske']})", "NØKKELTALL"])
        with t1:
            c = st.columns(2)
            c[0].markdown(kriterieliste_html("TREND SCORE", r["trendScore"], r["trendDeler"],
                                             SCANNER_CONFIG["trendPoints"], TREND_ETIKETTER),
                          unsafe_allow_html=True)
            c[1].markdown(kriterieliste_html("RECOVERY SCORE", r["recoveryScore"],
                                             r["recoveryDeler"],
                                             SCANNER_CONFIG["recoveryPoints"], RECOVERY_ETIKETTER),
                          unsafe_allow_html=True)
            if r["eventRisk"]:
                traff = [v for k, v in EVENT_ETIKETTER.items() if r["eventGrunner"].get(k)]
                st.markdown(
                    f'<div style="margin-top:14px;background:{_rgba(DC["roed"], 0.08)};'
                    f'border:1px solid {_rgba(DC["roed"], 0.3)};border-radius:8px;padding:11px 13px;'
                    f'font-size:12px;color:{DC["dempet2"]};"><b style="color:{DC["roed"]};">'
                    f'Event risk utløst av:</b> {", ".join(traff)}. Målt mot ATR '
                    f'{f(r["ind"]["atrPctPrev"], 2)} % fra dagen før hendelsen.</div>',
                    unsafe_allow_html=True)
        with t2:
            st.markdown(korreksjonsforlop_html(r), unsafe_allow_html=True)
        with t3:
            st.markdown(historikk_html(r), unsafe_allow_html=True)
        with t4:
            st.markdown(nokkeltall_html(r), unsafe_allow_html=True)
    return endret


def _toppbar(antall_aktive: int, antall_total: int) -> tuple:
    """Tittel, metalinje og kontrollene. Returnerer (visning, sortering, scan)."""
    oslo = datetime.now(ZoneInfo("Europe/Oslo")).strftime("%H:%M")
    c = st.columns([3.2, 1.15, 1.05, 0.95, 0.85])
    with c[0]:
        st.markdown(
            f'<div style="padding-top:2px;">'
            f'<div style="font-family:{DISPLAY};font-size:23px;font-weight:600;'
            f'color:{DC["tekst"]};letter-spacing:-0.01em;">Correction Radar</div>'
            f'<div style="font-family:{MONO};font-size:11px;color:{DC["dempet"]};'
            f'margin-top:2px;">{antall_aktive} av {antall_total} aktive · oppdatert {oslo} '
            f'· ingen kjøps- eller salgssignaler</div></div>',
            unsafe_allow_html=True)
    sortering = c[1].selectbox("SORTER", list(SORTERINGSVALG.keys()), label_visibility="collapsed")
    refresh = c[2].selectbox("AUTO", ["AUTO AV", "AUTO 5 MIN", "AUTO 10 MIN",
                                      "AUTO 15 MIN", "AUTO 30 MIN"],
                             index=3, label_visibility="collapsed")
    visning = c[3].segmented_control("VISNING", ["KORT", "TABELL"], default="KORT",
                                     label_visibility="collapsed")
    scan = c[4].button("SCAN NÅ", type="primary", width="stretch")

    minutter = {"AUTO AV": 0, "AUTO 5 MIN": 5, "AUTO 10 MIN": 10,
                "AUTO 15 MIN": 15, "AUTO 30 MIN": 30}[refresh]
    if minutter > 0:
        tick = st_autorefresh(interval=minutter * 60 * 1000, key="auto_refresh")
        if tick and tick > 0:
            st.cache_data.clear()

    st.markdown(f'<div style="height:1px;background:{DC["linje"]};margin:14px 0 4px;"></div>',
                unsafe_allow_html=True)
    return visning or "KORT", sortering, scan


def _rail(resultater: list, state: dict) -> None:
    """Høyre kolonne: varsler og watchlist."""
    nye = st.session_state.get("nye_varsler", [])
    st.markdown(
        f'<div style="font-family:{MONO};font-size:10px;letter-spacing:0.14em;'
        f'color:{DC["dempet"]};margin-bottom:8px;">NYE VARSLER · {len(nye)}</div>',
        unsafe_allow_html=True)

    if nye:
        st.markdown("".join(varsel_html(v) for v in nye), unsafe_allow_html=True)
    else:
        st.markdown(f'<div style="font-size:12px;color:{DC["svak"]};margin-bottom:10px;">'
                    f'Ingen statusendringer siden forrige skanning.</div>',
                    unsafe_allow_html=True)

    logg = state.get("alerts", [])
    if logg:
        with st.expander(f"VARSELHISTORIKK ({len(logg)})"):
            st.dataframe(
                pd.DataFrame([{"Tid": v["tid"], "Ticker": v["ticker"],
                               "Type": STATUS_TEKST.get(v["type"].rstrip("+"), v["type"])}
                              for v in logg]),
                width="stretch", hide_index=True, height=240)

    st.markdown(f'<div style="height:1px;background:{DC["linje"]};margin:18px 0 12px;"></div>',
                unsafe_allow_html=True)

    universe = st.session_state.universe
    aktive = sum(1 for e in universe if e["enabled"])
    st.markdown(
        f'<div style="font-family:{MONO};font-size:10px;letter-spacing:0.14em;'
        f'color:{DC["dempet"]};margin-bottom:4px;">WATCHLIST · {aktive}/{len(universe)}</div>',
        unsafe_allow_html=True)

    score = {r["ticker"]: r["correctionScore"] for r in resultater}
    farge = {r["ticker"]: STATUS_FARGE[r["status"]] for r in resultater}

    with st.container(key="rail-watchlist"):
        endret = False
        for i, e in enumerate(universe):
            t = e["ticker"]
            c = st.columns([0.14, 1.5, 0.42, 0.3])
            c[0].markdown(
                f'<div style="width:7px;height:7px;border-radius:50%;margin-top:9px;'
                f'background:{farge.get(t, DC["kant"])};"></div>', unsafe_allow_html=True)
            paa = c[1].checkbox(t.replace(".OL", ""), value=e["enabled"], key=f"uni_{t}")
            c[2].markdown(
                f'<div style="font-family:{MONO};font-size:11px;color:{DC["svak"]};'
                f'text-align:right;padding-top:6px;">'
                f'{score[t]:.0f}</div>' if t in score else "", unsafe_allow_html=True)
            if c[3].button("✕", key=f"del_{t}", help="Fjern fra watchlist"):
                universe.pop(i)
                lagre_universe(universe)
                st.cache_data.clear()
                st.rerun()
            if paa != e["enabled"]:
                universe[i]["enabled"] = paa
                endret = True
        if endret:
            lagre_universe(universe)
            st.rerun()

    ledige = {v: k for k, v in OSLO_TICKERS.items()
              if k not in {e["ticker"] for e in universe}}
    with st.expander("+ LEGG TIL"):
        valg = st.selectbox("Fra Oslo Børs", ["—"] + sorted(ledige.keys()),
                            label_visibility="collapsed")
        if valg != "—" and st.button("LEGG TIL", key="add_oslo", width="stretch"):
            universe.append(_universe_rad(ledige[valg]))
            lagre_universe(universe)
            st.cache_data.clear()
            st.rerun()
        fri = st.text_input("Ticker manuelt", placeholder="AAPL / EQNR.OL",
                            label_visibility="collapsed")
        if fri and st.button("LEGG TIL TICKER", key="add_fri", width="stretch"):
            t = _normaliser_ticker(fri)
            if t not in {e["ticker"] for e in universe}:
                universe.append(_universe_rad(t))
                lagre_universe(universe)
                st.cache_data.clear()
                st.rerun()

    st.markdown(
        f'<div style="font-size:10px;color:{DC["svak"]};line-height:1.6;margin-top:10px;">'
        f'Lagres på disk. På Streamlit Cloud nullstilles listen ved omstart og faller '
        f'tilbake til DEFAULT_WATCHLIST.</div>', unsafe_allow_html=True)


def _fotnote() -> None:
    c = SCANNER_CONFIG
    with st.expander("SLIK VIRKER RADAREN"):
        st.markdown(f"""
**Kjerneprinsippet:** ingen felles prosentgrense. Hver aksje sammenlignes med sin egen
historikk av korreksjoner, funnet med ATR-normalisert ZigZag
(terskel {c['swingAtrMultiplier']} × ATR14). Et fall på 7 % kan være en stor DNB-korreksjon
og samtidig helt normal NAS-støy.

**Tre separate scorer** — de slås bevisst *ikke* sammen:

| Score | Spørsmål | Sammensetning |
|---|---|---|
| Correction | Hvor uvanlig er dagens fall for denne aksjen? | Percentil {c['correctionScoreWeights']['percentile']:.0%} + teknisk stretch {c['correctionScoreWeights']['technicalStretch']:.0%} + støtte {c['correctionScoreWeights']['support']:.0%} |
| Trend | Er kursstrukturen fortsatt frisk? | SMA-struktur, helning, higher lows |
| Recovery | Er fallet i ferd med å ta slutt? | Higher low, RSI, SMA20, motstandsbrudd, volum |

**Sonene** følger statusprioriteringen: EVENT RISK og REVERSAL krever gjennomgang,
STABILIZING/STRONG CORRECTION/CORRECTION følges, FOLLOW og WAIT ligger rolig.

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

**REVERSAL betyr ikke kjøp.** Det betyr at det tekniske oppsettet nå er interessant
nok til at traden bør vurderes manuelt.

**Én korreksjon = én hendelse.** Samme `correctionId` beholdes selv om fallet
utdypes. Varsel går ved statusendring, eller når fallet øker
{c['alerts']['severityStepPct']} prosentpoeng innenfor samme korreksjon.
        """)
    with st.expander("AKTIV CONFIG"):
        st.caption("Alle terskler ligger i SCANNER_CONFIG øverst i scanner.py.")
        st.json(SCANNER_CONFIG)


def main() -> None:
    """Streamlit hovedapp – retning 1b TRIAGE."""
    st.set_page_config(page_title="Correction Radar", page_icon="📡",
                       layout="wide", initial_sidebar_state="collapsed")
    injiser_css()

    if "universe" not in st.session_state:
        st.session_state.universe = last_universe()
        if not UNIVERSE_FILE.exists():
            lagre_universe(st.session_state.universe)
    if "fundamentals" not in st.session_state:
        st.session_state.fundamentals = last_fundamentals()
    if "radar_state" not in st.session_state:
        st.session_state.radar_state = last_state()

    universe = st.session_state.universe
    aktive = [e["ticker"] for e in universe if e["enabled"]]

    visning, sortering, scan = _toppbar(len(aktive), len(universe))
    if scan:
        st.cache_data.clear()

    if not aktive:
        st.warning("Ingen aktive selskaper i watchlisten. Legg til i høyre kolonne.")
        return

    prisdata = hent_prisdata(tuple(sorted(aktive)))
    if not prisdata:
        st.error("Fikk ikke data fra Yahoo. Prøv «SCAN NÅ» igjen om litt.")
        return

    resultater = kjor_scan(prisdata, st.session_state.fundamentals)
    if not resultater:
        st.error("Ingen aksjer hadde nok historikk "
                 f"(krever minst {SCANNER_CONFIG['minimumHistoryYears']} år).")
        return

    st.session_state.nye_varsler = evaluer_varsler(resultater, st.session_state.radar_state)
    lagre_state(st.session_state.radar_state)

    sortert = sorter_resultater(resultater, sortering)
    hoved, rail = st.columns([3.35, 1], gap="large")

    with hoved:
        manglende = [t for t in aktive if t not in prisdata]
        if manglende:
            st.warning("Mangler data for: "
                       + ", ".join(t.replace(".OL", "") for t in manglende))

        if visning == "TABELL":
            st.markdown(tabell_html(sortert), unsafe_allow_html=True)
        else:
            for sone in SONER:
                i_sone = [r for r in sortert if r["status"] in sone["statuser"]]
                st.markdown(sone_header(sone["navn"], sone["farge"], len(i_sone)),
                            unsafe_allow_html=True)
                if not i_sone:
                    st.markdown(
                        f'<div style="font-size:12px;color:{DC["svak"]};'
                        f'padding:2px 0 6px;">Ingen aksjer i denne sonen nå.</div>',
                        unsafe_allow_html=True)
                    continue

                for r in i_sone:
                    if sone["form"] == "stor":
                        st.markdown(stort_kort_html(r), unsafe_allow_html=True)
                        if _fundamental_gate(r, st.session_state.fundamentals):
                            st.rerun()
                        _detaljer(r)
                    elif sone["form"] == "medium":
                        st.markdown(medium_rad_html(r), unsafe_allow_html=True)
                        if _detaljer(r, st.session_state.fundamentals):
                            st.rerun()
                    else:
                        st.markdown(kompakt_rad_html(r), unsafe_allow_html=True)
                        if _detaljer(r, st.session_state.fundamentals):
                            st.rerun()

        st.markdown(f'<div style="height:1px;background:{DC["linje"]};margin:24px 0 8px;"></div>',
                    unsafe_allow_html=True)
        _fotnote()
        st.markdown(
            f'<div style="font-family:{MONO};font-size:10px;letter-spacing:0.1em;'
            f'color:{DC["svak"]};margin-top:14px;">CORRECTION RADAR · '
            f'{len(resultater)} SELSKAPER · {SCANNER_CONFIG["historyYears"]} ÅRS HISTORIKK · '
            f'INGEN KJØPS- ELLER SALGSSIGNALER</div>', unsafe_allow_html=True)

    with rail:
        _rail(resultater, st.session_state.radar_state)


if __name__ == "__main__":
    main()
