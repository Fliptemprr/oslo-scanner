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
import io
import json
import math
import time
import logging
import os
import tempfile
from dataclasses import dataclass, asdict
from datetime import date, datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from urllib import request as urlrequest
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
    "swing": {"atrMultiplier": 1.5},

    # ── Statusterskler ──
    "correction": {
        "follow": 60, "correction": 75, "strong": 85,
        # §4/§D: severity skal huske hvor alvorlig korreksjonen HAR vært.
        # Percentilen måles på hendelsens dybde og krymper aldri, mens stretch
        # og støtte faller når kursen henter seg inn. Uten dette gulvet mister
        # en aksje i recovery severity-historien sin så snart lagret tilstand
        # går tapt.
        "severityPercentileFloor": True,
        # Absolutt bunnkrav: fallet må være minst like stort som swing-
        # terskelen for aksjen, ellers er det støy og ikke en korreksjon.
        "minDepthPct": 3.0,
    },
    # §19/§21: samme definisjon overalt — 0-29 NO RECOVERY, 30-49 STABILIZING,
    # 50-69 EARLY RECOVERY, 70-84 CONFIRMED, 85-100 STRONG.
    "recovery": {
        "stabilizing": 30, "early": 50, "confirmed": 70, "strong": 85,
        # §17/§18/§E: recovery-poeng krever en meningsfull aktiv korreksjon.
        # En grønn volumdag på ATH er ikke recovery.
        "requiresSeverity": "CORRECTION",
    },
    "trend": {"minimumForReversal": 55},
    "volume": {"recoveryRatio": 1.2, "eventRatio": 2.0},

    # ── §3/§36: korreksjonens livssyklus ──
    "lifecycle": {
        # Andel av fallet topp→bunn som må være gjenvunnet før eventet lukkes
        "closeRegainFraction": 0.80,
        # Minste antall barer uten ny bunn før fasen kan forlate FALLING
        "baseBuildingDays": 3,
    },

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

    # ── §9 Support: soner, ikke én enkelt gammel swing-low ──
    "support": {
        # Trapp på avstand i % til nærmeste relevante sone
        "steps": [[1.0, 100], [2.0, 80], [3.0, 60], [5.0, 30]],
        # Relevansvindu: max(atrMaxDistanceMultiplier × ATR%, percentCap)
        "atrMaxDistanceMultiplier": 3.0,
        "percentCap": 8.0,
        # Nivåer nærmere hverandre enn dette (× ATR) slås sammen til én sone
        "clusterAtr": 0.5,
        # Styrke etter antall prisreaksjoner i sonen
        "strengthBase": 0.6,
        "strengthPerTouch": 0.2,
        # Hvor langt tilbake et nivå kan komme fra og fortsatt telle
        "maxAgeDays": 400,
    },

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
        # §16: brudd må skje over et nivå dannet ETTER bunnen, med ATR-buffer
        "resistanceBufferAtr": 0.15,
        "minBarsAfterTrough": 2,
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
        # §25: fall som går uvanlig fort. -12 % over 40 dager er ikke det
        # samme som -12 % på 2 dager.
        "velocityEnabled": True,
        "velocityMinDrawdownPct": 10.0,
        "velocityMaxDays": 5,
    },

    # ── Fundamental gate (manuell i v1) ──
    "fundamentals": {"resetOnNewCorrection": True},

    # ── Markedsdata: hvilken handelsdag analysen skal bygge på ──
    "data": {
        "exchangeTimezone": "Europe/Oslo",
        # Oslo Børs stenger 16:20. Marginen er hvor lenge etter stengetid vi
        # godtar at dagens sluttdata ennå ikke har landet hos datakilden.
        "marketCloseHour": 16,
        "marketCloseMinute": 20,
        "dataLagMinutes": 40,
        # Forkast en uferdig candle. Skanner du midt i sesjonen er dagens bar
        # halvferdig, og RSI, ATR og volumratio ville blitt regnet på den.
        "dropIncompleteSession": True,

        # Andrekilde når Yahoo ikke leverer siste avsluttede handelsdag.
        # 08.09.2026: stooq.com svarer med et JavaScript proof-of-work-
        # challenge i stedet for CSV, så en ren HTTP-klient kommer aldri
        # fram. Koden og testene beholdes bak flagget i tilfelle det endrer
        # seg igjen, men kjeden kan ikke regnes som en reell andrekilde.
        "stooqEnabled": False,
        # Stooq leverer ujusterte kurser, Yahoo utbyttejusterte. Nye barer
        # skaleres derfor til Yahoos nivå før de flettes inn. Avviker faktoren
        # mer enn dette fra 1, er noe galt og vi fletter ikke.
        "stooqMaxScaleDeviation": 0.20,
        "stooqOverlapDays": 40,
        "stooqTimeout": 20,

        # Yahoo kan levere en handelsdag som null-bar på dagsoppløsning selv
        # om intradag-serien har dagen. 07.09.2026 gjaldt det hele Oslo Børs.
        # Da rekonstrueres dagsbaren fra intradag-barene.
        "backfillEnabled": True,
        "backfillInterval": "5m",
        "backfillRange": "1mo",
        # Hvor mange handelsdager bakover det tettes. 5m-historikken hos
        # Yahoo rekker uansett bare rundt en måned.
        "backfillMaxDays": 5,
        # Færre barer enn dette er en halv sesjon, og da blir high og low
        # feil. Da er det ærligere å la dagen stå tom og beholde STALE.
        "backfillMinBars": 10,
        "backfillTimeout": 20,

        # Yahoo droppet 07.09.2026 helt: dagen kom som null-bar og er nå borte
        # fra dagsserien. For en dag som verken er i dag eller i går finnes
        # ingen offisiell sluttkurs hos kilden — chartPreviousClose dekker
        # gårsdagen, regularMarketPrice dagens sesjon. Intradag-aggregatet
        # bommer med inntil 0.3 %, fordi sluttauksjonen 16:20-16:25 ikke ligger
        # i den kontinuerlige feeden.
        #
        # Kursene under er lest fra Yahoos egen meta.chartPreviousClose den
        # 08.09.2026, altså kildens egen offisielle verdi. KOG 298.00 og KIT
        # 98.40 er i tillegg bekreftet manuelt mot markedet.
        #
        # Nye dager havner normalt ikke her: de fanges automatisk samme kveld
        # via regularMarketPrice, eller neste morgen via chartPreviousClose.
        "kjenteSluttkurser": {
            "2026-09-07": {
                "KOG.OL": 298.00, "NOD.OL": 172.00, "KIT.OL": 98.40,
                "PROT.OL": 471.20, "DNB.OL": 320.70, "WAWI.OL": 165.90,
                "NAS.OL": 12.76,
            },
        },
    },

    # ── Tidlig lag: BOTTOM WATCH og LYTTEPOST ──
    # Laget kommer FØR full reversal-bekreftelse og tåler høyere feilrate.
    # Alle tall her er V1-forslag fra spesifikasjonen og ment å kalibreres.
    "early": {
        "enabled": True,
        "minBarer": 30,

        # §2 Falling Knife Guard: minst to av fire sperrer hele laget
        "knifeMinTreff": 2,
        "knifeReturn3d": -6.0,
        "knifeRsiDrop": 5.0,
        "knifeVolRatio": 1.5,

        # §3 Turn Score
        "grupper": {
            "A": ["ingenNy10dLow", "bedre3d", "mindreNegativeDager"],
            "B": ["reaksjonFraSone", "loeftFraLow", "testetUtenNyLow"],
            "C": ["momentum3d", "momentum5d", "rsiOpp", "toAvTrePositive"],
            "D": ["positivDagMedVolum", "oppVolumOverNed", "volumMedSnuing"],
            "E": ["higherLow", "sma20Reclaim", "bryterMotstand"],
            "F": ["rsiAkselerasjon", "momentumSnudd"],
        },
        # A 20 · B 20 · C 20 · D 15 · E 15 · F 10 = 100
        "poeng": {
            "ingenNy10dLow": 10, "bedre3d": 5, "mindreNegativeDager": 5,
            "reaksjonFraSone": 10, "loeftFraLow": 5, "testetUtenNyLow": 5,
            "momentum3d": 8, "momentum5d": 5, "rsiOpp": 4, "toAvTrePositive": 3,
            "positivDagMedVolum": 8, "oppVolumOverNed": 4, "volumMedSnuing": 3,
            "higherLow": 7, "sma20Reclaim": 5, "bryterMotstand": 3,
            "rsiAkselerasjon": 5, "momentumSnudd": 5,
        },
        # Kun A-E teller i kravet om minst tre positive grupper. F er et
        # tillegg om endringstakt, ikke en selvstendig evidensgruppe.
        "hovedgrupper": ["A", "B", "C", "D", "E"],
        "bottomZonePct": 3.0,       # «innen 3 % av lokal 20D-low»
        "bottomLookback": 5,        # hvor mange dager tilbake sonen kan ha vært testet
        "loeftFraLowPct": 1.5,      # close minst så mye over dagens low
        "momentum3dPct": 2.0,
        "rsiOppPoeng": 4.0,
        "volumeSpikeRatio": 1.2,
        "volumeConfirmRatio": 1.0,
        "volumeLookback": 5,
        "motstandLookback": 5,      # siste 5D swing high

        # §4 Entry Value: avstand fra lokal bunn, i prosent
        "entryBands": [(3, 100), (5, 90), (8, 80), (12, 65), (16, 50), (20, 35)],
        "entryElse": 20,
        "entryDrawdownMin": 15.0,
        "entryDrawdownBonus": 10,

        # §5 statusterskler
        "bottomWatch": 35,
        "lyttepost": 55,
        "entryMin": 60,
        "minGrupper": 3,

        # §6 styrke, og §7 decay tilbake til BOTTOM WATCH
        "styrkebaand": [(75, "STRONG"), (65, "GOOD"), (55, "EARLY")],
        "lyttepostDecay": 50,

        # §8 invalidasjon
        "bruttUnderLowPct": 2.0,
        "bruttTurn": 35,
        # Bruddet står i minst én handelsdag etter at det inntraff, slik at
        # brukeren rekker å se at hypotesen feilet.
        "bruttSynligHandelsdager": 1,

        # §10 rangering
        "opportunityTurnVekt": 0.6,
        "opportunityEntryVekt": 0.4,
    },

    # ── Corporate actions ──
    # Yahoo justerer for utbytte og splitt, men IKKE for fisjon/spin-off.
    # KOG falt 398.50 → 328.38 ved åpning 15.04.2026, med 15.04 sin high under
    # 14.04 sin low: hele bevegelsen lå mellom to sesjoner. Uten justering
    # leses utskillelsen av Kongsberg Maritime som et markedsfall på 16 %, og
    # forurenser topp, drawdown, percentil, ATR, SMA200 og Trend Score.
    #
    # Faktoren skalerer alle barer FØR exDato. Volum røres ikke: ved fisjon
    # endres ikke antall aksjer i selskapet det fisjoneres fra. Dagens kurs
    # står alltid urørt, så UI-et viser faktisk markedskurs.
    #
    # Tabellen er bevisst eksplisitt. Automatisk gap-deteksjon ble vurdert og
    # valgt bort: watchlisten har 15 andre gap uten overlapp mellom dagene som
    # er ekte resultatreaksjoner, og de skal telle som korreksjoner.
    "corporateActions": {
        "KOG.OL": [
            {
                "exDato": "2026-04-15",
                "faktor": 0.83789,
                "kort": "fisjon av Kongsberg Maritime",
                "note": "Fisjon Kongsberg Maritime (KMAR.OL), 1:1. Utledet av "
                        "(398.50 - 64.60) / 398.50, der 64.60 er KMARs første "
                        "omsetning 23.04.2026. Gir topp 349.90, som stemmer "
                        "med den bakoverjusterte serien hos Nordnet og "
                        "Finansavisen. Bytt til Oslo Børs' offisielle faktor "
                        "når den er bekreftet.",
            },
        ],
    },

    # ── §32/§33: varsler ──
    "alerts": {
        "severityStepPct": 3.0,
        "maxLogEntries": 200,
        # Samme overgang på samme correctionId varsles bare én gang
        "eventRiskCooldownDays": 5,
    },
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


# §4: fasene korreksjonen går gjennom. UI viser én status, men motoren
# holder fase og alvorlighetsgrad hver for seg — slik at en aksje kan være
# STRONG_CORRECTION i severity og RECOVERING i phase samtidig.
PHASE_NORMAL = "NORMAL"
PHASE_FALLING = "FALLING"
PHASE_BASE_BUILDING = "BASE_BUILDING"
PHASE_RECOVERING = "RECOVERING"
PHASE_EVENT_RISK = "EVENT_RISK"
PHASE_CLOSED = "CLOSED"

SEV_NONE = "NONE"
SEV_FOLLOW = "FOLLOW"
SEV_CORRECTION = "CORRECTION"
SEV_STRONG = "STRONG_CORRECTION"

SEVERITY_RANG = {SEV_NONE: 0, SEV_FOLLOW: 1, SEV_CORRECTION: 2, SEV_STRONG: 3}


@dataclass
class CorrectionEvent:
    """
    §3/§5: korreksjonen som ett event med samme id gjennom hele forløpet.

    maxDrawdownPct og currentDrawdownPct må aldri blandes. Den første er
    hendelsens dybde og skal aldri krympe når kursen henter seg inn; den
    andre er hvor kursen står akkurat nå.
    """
    correctionId: str
    ticker: str

    peakDate: str
    peakPrice: float
    troughDate: str
    troughPrice: float
    currentPrice: float

    maxDrawdownPct: float        # topp → bunn, fryses når bunnen står
    currentDrawdownPct: float    # topp → nå

    daysPeakToTrough: int
    daysSincePeak: int
    daysSinceTrough: int
    barsSinceTrough: int

    recoveryFromTroughPct: float
    regainedFraction: float      # hvor mye av fallet som er hentet inn, 0–1
    correctionVelocity: float    # %-fall per dag, §25
    correctionPercentile: float

    phase: str
    severity: str
    active: bool

    peakConfirmed: bool
    peakIdx: int
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

# Tidlig lag, mellom WAIT og STABILIZING i stigen
STATUS_BOTTOM_WATCH = "BOTTOM_WATCH"
STATUS_LYTTEPOST = "LYTTEPOST"
STATUS_LYTTEPOST_BRUTT = "LYTTEPOST_BRUTT"

STATUS_LABEL = {
    STATUS_EVENT_RISK: "🔴 EVENT RISK",
    STATUS_REVERSAL: "🟢 REVERSAL",
    STATUS_STABILIZING: "🔵 STABILIZING",
    STATUS_LYTTEPOST: "🟣 LYTTEPOST",
    STATUS_LYTTEPOST_BRUTT: "⛔ LYTTEPOST BRUTT",
    STATUS_STRONG_CORRECTION: "🟠 STRONG CORRECTION",
    STATUS_CORRECTION: "🟡 CORRECTION",
    STATUS_BOTTOM_WATCH: "🔎 BOTTOM WATCH",
    STATUS_FOLLOW: "⚪ FOLLOW",
    STATUS_WAIT: "⚫ WAIT",
}

# Sortering: det som krever oppmerksomhet først (§18). LYTTEPOST legger seg
# rett under STABILIZING, slik stigen i spesifikasjonen sier.
STATUS_PRIORITY = {
    STATUS_EVENT_RISK: 0,
    STATUS_REVERSAL: 1,
    STATUS_STABILIZING: 2,
    STATUS_LYTTEPOST: 3,
    STATUS_LYTTEPOST_BRUTT: 4,
    STATUS_STRONG_CORRECTION: 5,
    STATUS_CORRECTION: 6,
    STATUS_BOTTOM_WATCH: 7,
    STATUS_FOLLOW: 8,
    STATUS_WAIT: 9,
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


def detect_current_correction(ticker: str, ind: dict, swing: SwingState,
                              lagret: Optional[dict] = None,
                              cfg: dict = SCANNER_CONFIG) -> Optional[CorrectionEvent]:
    """
    §3: korreksjonen som pågår, bygget fra kursdata og flettet med forrige
    lagrede tilstand.

    Toppen forankres deterministisk, så correctionId er stabil selv om lagret
    tilstand går tapt. Et nytt lavpunkt oppdaterer eksisterende event i stedet
    for å opprette et nytt (§34), og maxDrawdownPct krymper aldri (§8, §J).
    """
    close, low = ind["close"], ind["low"]
    n = len(close)
    if n == 0:
        return None

    peak_idx, peak_price = _forankre_topp(swing, close.index[-1], cfg)
    if peak_price is None or peak_price <= 0:
        return None

    seg = close.iloc[peak_idx:].to_numpy(dtype=float)
    trough_idx = peak_idx + int(np.argmin(seg))
    trough_price = float(seg[int(np.argmin(seg))])
    current_price = float(close.iloc[-1])
    peak_date = close.index[peak_idx]
    trough_date = close.index[trough_idx]
    siste_dato = close.index[-1]

    current_dd = round((peak_price - current_price) / peak_price * 100, 2)
    max_dd = round((peak_price - trough_price) / peak_price * 100, 2)
    correction_id = f"{ticker}:{peak_date.strftime('%Y-%m-%d')}"

    # Flett med lagret tilstand: dybden kan bare vokse, aldri krympe.
    if lagret and lagret.get("correctionId") == correction_id:
        max_dd = max(max_dd, float(lagret.get("maxDrawdownPct", max_dd)))

    fall = peak_price - trough_price
    regained = ((current_price - trough_price) / fall) if fall > 0 else 1.0
    dager_siden_topp = int((siste_dato - peak_date).days)

    return CorrectionEvent(
        correctionId=correction_id,
        ticker=ticker,
        peakDate=peak_date.strftime("%Y-%m-%d"),
        peakPrice=round(peak_price, 4),
        troughDate=trough_date.strftime("%Y-%m-%d"),
        troughPrice=round(trough_price, 4),
        currentPrice=round(current_price, 4),
        maxDrawdownPct=max_dd,
        currentDrawdownPct=current_dd,
        # §27: tre forskjellige varigheter som ikke må forveksles
        daysPeakToTrough=int((trough_date - peak_date).days),
        daysSincePeak=dager_siden_topp,
        daysSinceTrough=int((siste_dato - trough_date).days),
        barsSinceTrough=n - 1 - trough_idx,
        recoveryFromTroughPct=round((current_price - trough_price) / trough_price * 100, 2)
        if trough_price > 0 else 0.0,
        regainedFraction=round(max(0.0, min(1.0, regained)), 3),
        # §25: %-fall per dag skiller -12 % over 40 dager fra -12 % på to
        correctionVelocity=round(max_dd / max(int((trough_date - peak_date).days), 1), 3),
        correctionPercentile=0.0,       # fylles av scan_stock
        phase=PHASE_NORMAL,             # fylles av bestem_fase
        severity=SEV_NONE,              # fylles av bestem_severity
        active=current_dd > 0,
        peakConfirmed=swing.direction == -1,
        peakIdx=peak_idx,
        troughIdx=trough_idx,
    )


def bestem_severity(correction_score: float, percentile: float,
                    cc: CorrectionEvent, ind: dict, lagret: Optional[dict],
                    correction_id: str, cfg: dict = SCANNER_CONFIG) -> str:
    """
    §4/§D: alvorlighetsgrad fra Correction Score, men den høyeste graden
    eventet har nådd huskes. En aksje som har hatt en sterk korreksjon og
    deretter stiger fra bunnen mister ikke historien sin.
    """
    c = cfg["correction"]

    # §38: bunnkravet normaliseres mot aksjens egen volatilitet, så et fall
    # som bare er vanlig dagsstøy aldri regnes som en korreksjon.
    swing_terskel = cfg["swing"]["atrMultiplier"] * (ind.get("atrPct") or 0.0)
    if cc.maxDrawdownPct < max(swing_terskel, c["minDepthPct"]):
        return SEV_NONE

    if c.get("severityPercentileFloor", True):
        correction_score = max(correction_score, percentile)

    if correction_score >= c["strong"]:
        naa = SEV_STRONG
    elif correction_score >= c["correction"]:
        naa = SEV_CORRECTION
    elif correction_score >= c["follow"]:
        naa = SEV_FOLLOW
    else:
        naa = SEV_NONE

    if lagret and lagret.get("correctionId") == correction_id:
        tidligere = lagret.get("severity", SEV_NONE)
        if SEVERITY_RANG.get(tidligere, 0) > SEVERITY_RANG[naa]:
            return tidligere
    return naa


def bestem_fase(cc: CorrectionEvent, recovery_score: float, higher_low: bool,
                event_risk: bool, fundamentals_checked: bool,
                cfg: dict = SCANNER_CONFIG) -> str:
    """
    §4/§20/§36: hvilken fase korreksjonen er i.

    Rekkefølgen er bindende. Et nytt lavpunkt sender fasen tilbake til
    FALLING (§34, §35) fordi barsSinceTrough da nullstilles.
    """
    lc = cfg["lifecycle"]
    rec = cfg["recovery"]

    if event_risk and not fundamentals_checked:
        return PHASE_EVENT_RISK

    if cc.severity == SEV_NONE and cc.currentDrawdownPct <= 0:
        return PHASE_NORMAL

    # §36: eventet lukkes først når fallet i hovedsak er hentet inn og
    # strukturen er positiv, eller kursen har tatt ut toppen.
    if cc.currentPrice > cc.peakPrice or (
            cc.regainedFraction >= lc["closeRegainFraction"] and higher_low):
        return PHASE_CLOSED

    if cc.severity == SEV_NONE:
        return PHASE_NORMAL

    if cc.barsSinceTrough < lc["baseBuildingDays"]:
        return PHASE_FALLING

    if recovery_score >= rec["early"] and higher_low:
        return PHASE_RECOVERING

    return PHASE_BASE_BUILDING


def percentile_rank(current: float, history: list) -> float:
    """Andel av historiske korreksjoner som er mindre eller lik dagens (0–100)."""
    valid = [x for x in history if isinstance(x, (int, float)) and math.isfinite(x)]
    if not valid:
        return 0.0
    below = sum(1 for x in valid if x <= current)
    return round(below / len(valid) * 100, 1)


def calculate_correction_percentile(current: Optional[CorrectionEvent],
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
    # §8: historiske korreksjoner måles topp→bunn, så dagens måles på samme
    # måte. Recovery etter bunnen skal ikke gjøre den historiske korreksjonen
    # mindre.
    bruk_dybde = cfg["percentile"].get("basis", "maxDepth") == "maxDepth"
    referansepris = current.troughPrice if bruk_dybde else current.currentPrice

    if metric == "atrNormalizedDrawdown":
        if not atr_at_peak or atr_at_peak <= 0:
            return 0.0, 0
        naa = (current.peakPrice - referansepris) / atr_at_peak
        verdier = [h.atrNormalizedDrawdown for h in sammenlign]
    else:
        naa = current.maxDrawdownPct if bruk_dybde else current.currentDrawdownPct
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


def finn_supportsoner(ind: dict, swing: SwingState,
                      current: Optional[CorrectionEvent],
                      cfg: dict = SCANNER_CONFIG) -> list:
    """
    §9: støtte som soner, ikke ett enkelt gammelt bunnpunkt.

    Kandidater er bekreftede swing-lows, tidligere topper som er brutt og
    kan retestes, og SMA50/SMA200 som dynamisk støtte. Nivåer som ligger
    tett slås sammen, og antall prisreaksjoner gir sonen styrke.
    """
    sc = cfg["support"]
    kurs, atr = ind.get("close_now"), ind.get("atr")
    if not kurs or not atr or atr <= 0:
        return []

    siste = ind["index"][-1]
    grense_dato = pd.Timestamp(siste) - pd.Timedelta(days=sc["maxAgeDays"])
    aktiv_bunn = current.troughIdx if current else len(ind["close"])

    kandidater = []
    for p in swing.pivots:
        if p["date"] < grense_dato or p["idx"] >= aktiv_bunn:
            continue
        if p["kind"] == "trough":
            kandidater.append((p["price"], "swing-low"))
        elif p["kind"] == "peak" and p["price"] < kurs:
            # Brutt topp som kan retestes nedenfra
            kandidater.append((p["price"], "brutt topp"))

    for navn, verdi in (("SMA50", ind.get("sma50")), ("SMA200", ind.get("sma200"))):
        if verdi and verdi <= kurs:
            kandidater.append((verdi, navn))

    kandidater = [(p, k) for p, k in kandidater if p and p > 0 and p <= kurs * 1.005]
    if not kandidater:
        return []

    # Slå sammen nivåer som ligger nærmere hverandre enn clusterAtr × ATR
    kandidater.sort(key=lambda x: x[0])
    toleranse = atr * sc["clusterAtr"]
    soner = []
    for pris, kilde in kandidater:
        if soner and abs(pris - soner[-1]["nivå"]) <= toleranse:
            z = soner[-1]
            z["nivå"] = (z["nivå"] * z["treff"] + pris) / (z["treff"] + 1)
            z["treff"] += 1
            if kilde not in z["kilder"]:
                z["kilder"].append(kilde)
        else:
            soner.append({"nivå": pris, "treff": 1, "kilder": [kilde]})

    for z in soner:
        z["nivå"] = round(z["nivå"], 4)
        z["avstandPct"] = round((kurs - z["nivå"]) / z["nivå"] * 100, 2) if z["nivå"] else None
        z["styrke"] = min(1.0, sc["strengthBase"] + sc["strengthPerTouch"] * (z["treff"] - 1))
    return sorted(soner, key=lambda z: z["avstandPct"])


def relevansvindu(ind: dict, cfg: dict = SCANNER_CONFIG) -> float:
    """§9: hvor langt under kursen en støtte fortsatt er relevant for traden."""
    sc = cfg["support"]
    atr_pct = ind.get("atrPct") or 0.0
    return max(atr_pct * sc["atrMaxDistanceMultiplier"], sc["percentCap"])


def support_score(soner: list, vindu: float, cfg: dict = SCANNER_CONFIG) -> tuple:
    """
    §9/§K: 0 poeng dersom ingen relevant sone finnes.

    En gammel swing-low 20–30 % under kursen er ikke entry-støtte, og skal
    ikke gi poeng bare fordi den eksisterer.
    """
    sc = cfg["support"]
    relevante = [z for z in soner
                 if z["avstandPct"] is not None and 0 <= z["avstandPct"] <= vindu]
    if not relevante:
        return 0.0, None

    beste, beste_score = None, 0.0
    for z in relevante:
        grunn = 0.0
        for grense, poeng in sc["steps"]:
            if z["avstandPct"] <= grense:
                grunn = float(poeng)
                break
        score = grunn * z["styrke"]
        if score > beste_score:
            beste_score, beste = score, z
    return round(beste_score, 1), beste


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
    """
    §11: trendens higher-low vurderer den BREDE strukturen — de to siste
    bekreftede bunnene i hele serien. Recovery-scorens confirmed higher low
    er et annet signal som må ligge etter den aktive korreksjonens bunn, og
    beregnes i confirmed_higher_low(). De to skal aldri være samme test.
    """
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


def confirmed_higher_low(swing: SwingState, current: Optional[CorrectionEvent],
                         ind: dict, cfg: dict = SCANNER_CONFIG) -> tuple:
    """
    §14: bunn → bounce → pullback → pullback holder over bunnen → bekreftet.

    Den nye bunnen må være bekreftet av ZigZag-logikken, altså at kursen har
    snudd ATR × multiplier fra den. En eldre higher-low teller ikke — pivoten
    må ligge etter korreksjonsbunnen (§11).
    """
    if current is None or not current.active:
        return False, None

    etter_bunn = [p for p in swing.pivots
                  if p["kind"] == "trough" and p["idx"] > current.troughIdx
                  and p["price"] > current.troughPrice]
    if not etter_bunn:
        return False, None

    hl = etter_bunn[-1]
    kurs = ind.get("close_now")
    if kurs is None or kurs <= hl["price"]:
        return False, None
    return True, round(hl["price"], 4)


def lokal_motstand(swing: SwingState, current: Optional[CorrectionEvent],
                   ind: dict, cfg: dict = SCANNER_CONFIG) -> Optional[float]:
    """
    §16: motstanden må være dannet ETTER bunnen i den aktive korreksjonen,
    ikke et tilfeldig gammelt nivå lenger tilbake i charten.
    """
    if current is None or not current.active:
        return None

    etter_bunn = [p["price"] for p in swing.pivots
                  if p["kind"] == "peak" and p["idx"] > current.troughIdx]
    if etter_bunn:
        return round(max(etter_bunn), 4)

    # Ingen bekreftet topp ennå: bruk høyeste high mellom bunn og i går
    high = ind["high"]
    fra = current.troughIdx + 1
    til = len(high) - 1
    if til - fra < cfg["recoveryParams"]["minBarsAfterTrough"]:
        return None
    return _num(high.iloc[fra:til].max())


def calculate_recovery_score(ind: dict, swing: SwingState,
                             current: Optional[CorrectionEvent],
                             cfg: dict = SCANNER_CONFIG) -> tuple:
    """
    §12/§17/§18/§E: samtlige recovery-signaler krever en aktiv, meningsfull
    korreksjon. En grønn volumdag på ATH er ikke recovery.
    """
    p = cfg["recoveryPoints"]
    rp = cfg["recoveryParams"]
    tom = {k: False for k in p}

    krav = SEVERITY_RANG.get(cfg["recovery"].get("requiresSeverity", "CORRECTION"), 2)
    meningsfull = (current is not None and current.active
                   and SEVERITY_RANG.get(current.severity, 0) >= krav
                   and current.phase != PHASE_CLOSED)
    if not meningsfull:
        tom["higherLowPrice"] = None
        tom["localResistance"] = None
        tom["ingenAktivKorreksjon"] = True
        return 0.0, tom

    # §13: krever at en bunnkandidat faktisk finnes, og at det har gått
    # minst noen dager siden den uten nytt lavpunkt.
    no_new_low = current.barsSinceTrough >= rp["noNewLowDays"]

    higher_low, hl_pris = confirmed_higher_low(swing, current, ind, cfg)

    motstand = lokal_motstand(swing, current, ind, cfg)
    kurs, atr = ind.get("close_now"), ind.get("atr")
    buffer_ = (atr or 0) * rp["resistanceBufferAtr"]
    bryter = bool(kurs is not None and motstand is not None
                  and kurs > motstand + buffer_)

    rsi, rsi_prev = ind.get("rsi"), ind.get("rsiPrev")
    sma20 = ind.get("sma20")
    ret1d, vr = ind.get("return1d"), ind.get("volumeRatio20d")
    mom5d = ind.get("momentum5d")

    deler = {
        "noNewLow3Days": bool(no_new_low),
        "higherLowConfirmed": bool(higher_low),
        "rsiRising": bool(rsi is not None and rsi_prev is not None and rsi > rsi_prev),
        "closeOverSma20": bool(kurs is not None and sma20 is not None and kurs > sma20),
        "breaksLocalResistance": bryter,
        "greenDayHighVolume": bool(ret1d is not None and ret1d > 0 and vr is not None
                                   and vr >= cfg["volume"]["recoveryRatio"]),
        "positiveMomentum5d": bool(mom5d is not None and mom5d > 0),
    }
    score = sum(p[k] for k, v in deler.items() if v)
    deler["higherLowPrice"] = hl_pris
    deler["localResistance"] = round(motstand, 4) if motstand else None
    deler["ingenAktivKorreksjon"] = False
    return float(min(score, 100)), deler


def detect_event_risk(ind: dict, current: Optional[CorrectionEvent],
                      cfg: dict = SCANNER_CONFIG) -> tuple:
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

    # §25: et fall som går uvanlig fort er en annen type hendelse enn det
    # samme fallet fordelt over mange uker.
    abnormal_fart = False
    if e.get("velocityEnabled") and current is not None:
        abnormal_fart = bool(current.maxDrawdownPct >= e["velocityMinDrawdownPct"]
                             and current.daysPeakToTrough <= e["velocityMaxDays"]
                             and current.barsSinceTrough <= e["velocityMaxDays"])

    grunner = {
        "abnormal1D": abnormal_1d,
        "abnormal3D": abnormal_3d,
        "abnormalVolume": abnormal_vol,
        "abnormalGap": abnormal_gap,
        "abnormalVelocity": abnormal_fart,
    }
    return any(grunner.values()), grunner


# ══════════════════════════════════════════════════════════════
# ENGINE
# ══════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════
# TIDLIG LAG – BOTTOM WATCH og LYTTEPOST
#
# Stigen er WAIT → BOTTOM WATCH → LYTTEPOST → STABILIZING →
# REVERSAL. Laget kommer bevisst FØR full reversal-bekreftelse og
# tåler høyere feilrate: hensikten er å finne et attraktivt tidlig
# inngangsområde mens kursen fortsatt ligger nær korreksjonsbunnen.
#
# LYTTEPOST er ikke et kjøpssignal. Den sier at fallet er i ferd
# med å endre karakter, og at inngangen fortsatt er nær bunnen.
#
# Spesifikasjonen er stedvis løst formulert. Hver tolkning står som
# kommentar på kriteriet den gjelder, og alle tall ligger i
# SCANNER_CONFIG["early"] slik at de kan justeres uten kodeendring.
# ══════════════════════════════════════════════════════════════

def _abs_neg_snitt(avk) -> float:
    """Gjennomsnittlig størrelse på de negative dagene i et utsnitt."""
    neg = [abs(float(x)) for x in avk if pd.notna(x) and float(x) < 0]
    return sum(neg) / len(neg) if neg else 0.0


def tidlige_features(ind: dict, cfg: dict = SCANNER_CONFIG) -> Optional[dict]:
    """
    Råstoffet til Turn Score og Entry Value, alt utledet av daglig OHLCV.

    Ingen nye eksterne datakilder: dette er de samme seriene motoren
    allerede har, satt sammen på nytt.
    """
    e = cfg["early"]
    c, h, l, v = ind["close"], ind["high"], ind["low"], ind["volume"]
    rsi, volr = ind["rsi_s"], ind["volRatio_s"]
    if len(c) < e["minBarer"]:
        return None

    kurs = float(c.iloc[-1])
    dagsavk = c.pct_change() * 100

    low20 = _num(l.tail(20).min())
    low10 = _num(l.tail(10).min())
    if low20 is None or low20 <= 0:
        return None

    # «Close lager nytt low» måles på close, slik spesifikasjonen sier —
    # ikke på intradag low. En veke under et gammelt lavpunkt er noe annet
    # enn en close under det.
    ny20 = c <= c.rolling(20).min() + 1e-9
    ny10 = c <= c.rolling(10).min() + 1e-9

    r3 = safe_pct(kurs, _bars_ago(c, 3))
    r5 = safe_pct(kurs, _bars_ago(c, 5))
    r3_forrige = safe_pct(_bars_ago(c, 3), _bars_ago(c, 6))

    rsi_naa, rsi_3, rsi_6 = _last(rsi), _bars_ago(rsi, 3), _bars_ago(rsi, 6)
    rsi_endring3 = (rsi_naa - rsi_3) if None not in (rsi_naa, rsi_3) else None
    rsi_endring_forrige = (rsi_3 - rsi_6) if None not in (rsi_3, rsi_6) else None

    # Volum på positive mot negative dager, siste fem
    opp = dagsavk > 0
    vol5, opp5 = v.tail(5), opp.tail(5)
    vol_opp = _num(vol5[opp5].mean()) if bool(opp5.any()) else None
    vol_ned = _num(vol5[~opp5].mean()) if bool((~opp5).any()) else None

    vindu = min(e["volumeLookback"], len(c) - 1)
    positiv_med_volum = any(
        float(dagsavk.iloc[-k]) > 0
        and _num(volr.iloc[-k]) is not None
        and float(volr.iloc[-k]) >= e["volumeSpikeRatio"]
        for k in range(1, vindu + 1))

    # Bunnsonen: 20D-low pluss en margin. «Innen 3 % av lokal 20D-low.»
    sone = low20 * (1 + e["bottomZonePct"] / 100)
    testet_sone = bool(_num(l.tail(e["bottomLookback"]).min()) <= sone)
    dagens_low = float(l.iloc[-1])
    loeft_fra_dagens_low = safe_pct(kurs, dagens_low)

    forrige_low10 = _num(l.iloc[-20:-10].min()) if len(l) >= 20 else None
    swing_high5 = _num(h.iloc[-(e["motstandLookback"] + 1):-1].max())

    momentum_snudd = bool(r3 is not None and r3_forrige is not None
                          and r3_forrige < 0 < r3)

    return {
        "kurs": kurs,
        "localLow20d": low20,
        "localLow10d": low10,
        "distanceFromLowPct": safe_pct(kurs, low20),
        "return3d": r3,
        "return5d": r5,
        "return3dForrige": r3_forrige,
        "rsi": rsi_naa,
        "rsiChange3d": rsi_endring3,
        "rsiChangeForrige3d": rsi_endring_forrige,
        "volumeRatio20d": _num(volr.iloc[-1]) if len(volr) else None,
        "upVolumeRatio": (vol_opp / vol_ned) if vol_opp and vol_ned else None,
        "volOpp": vol_opp,
        "volNed": vol_ned,
        "positivDagMedVolum": positiv_med_volum,
        "negSnitt3d": _abs_neg_snitt(dagsavk.tail(3)),
        "negSnittForrige3d": _abs_neg_snitt(dagsavk.iloc[-6:-3]),
        "nyttLow20dIDag": bool(ny20.iloc[-1]),
        "nyLow20dSiste3": bool(ny20.tail(3).any()),
        "ingenNyLow10dSiste3": not bool(ny10.tail(3).any()),
        "testetBunnsone": testet_sone,
        "overBunnsone": bool(kurs > sone),
        "testerSoneIDag": bool(dagens_low <= sone),
        "loeftFraDagensLow": loeft_fra_dagens_low,
        "positiveSiste3": int(sum(1 for x in dagsavk.tail(3)
                                  if pd.notna(x) and float(x) > 0)),
        "dagensDagErNegativ": bool(pd.notna(dagsavk.iloc[-1])
                                   and float(dagsavk.iloc[-1]) < 0),
        "higherLow": bool(low10 is not None and forrige_low10 is not None
                          and low10 > forrige_low10),
        "sma20Reclaim": bool(ind.get("sma20") and kurs > ind["sma20"]),
        "bryterMotstand": bool(swing_high5 and kurs > swing_high5),
        "momentumSnudd": momentum_snudd,
    }


def falling_knife_guard(f: dict, cfg: dict = SCANNER_CONFIG) -> tuple:
    """
    §2: sperren som skal hindre at en stor korreksjon alene blir et signal.

    En aksje i fritt fall skal aldri kunne få LYTTEPOST. Minst to av fire
    tegn på akselererende fall er nok til å sperre hele laget.
    """
    e = cfg["early"]
    treff = {
        "nyttLow": f["nyttLow20dIDag"],
        "fall3d": f["return3d"] is not None and f["return3d"] < e["knifeReturn3d"],
        "rsiStuper": (f["rsiChange3d"] is not None
                      and f["rsiChange3d"] < -e["knifeRsiDrop"]),
        "negativtVolum": (f["dagensDagErNegativ"]
                          and f["volumeRatio20d"] is not None
                          and f["volumeRatio20d"] > e["knifeVolRatio"]),
    }
    aktive = [k for k, v in treff.items() if v]
    return len(aktive) >= e["knifeMinTreff"], aktive


def turn_score(f: dict, cfg: dict = SCANNER_CONFIG) -> tuple:
    """
    §3: hvor mye evidens vi har for at fallet er i ferd med å stoppe. 0–100.

    Seks grupper, ingen av dem obligatorisk. Det er bevisst: en vending kan
    like gjerne vise seg som bunnreaksjon pluss volum som via higher low og
    RSI. Derfor kreves ikke én bestemt kombinasjon.
    """
    e = cfg["early"]
    p, grupper = e["poeng"], e["grupper"]

    k = {
        # A – fallmomentum avtar
        "ingenNy10dLow": f["ingenNyLow10dSiste3"],
        "bedre3d": (f["return3d"] is not None and f["return3dForrige"] is not None
                    and f["return3d"] > f["return3dForrige"]),
        "mindreNegativeDager": (f["negSnittForrige3d"] > 0
                                and f["negSnitt3d"] < f["negSnittForrige3d"]),
        # B – bunnreaksjon. Tyngste enkeltkriterium ligger her.
        "reaksjonFraSone": f["testetBunnsone"] and f["overBunnsone"],
        "loeftFraLow": (f["testerSoneIDag"] and f["loeftFraDagensLow"] is not None
                        and f["loeftFraDagensLow"] >= e["loeftFraLowPct"]),
        "testetUtenNyLow": f["testetBunnsone"] and not f["nyLow20dSiste3"],
        # C – kort momentum snur. Ingen krav om tre grønne dager.
        "momentum3d": f["return3d"] is not None and f["return3d"] > e["momentum3dPct"],
        "momentum5d": f["return5d"] is not None and f["return5d"] > 0,
        "rsiOpp": (f["rsiChange3d"] is not None
                   and f["rsiChange3d"] >= e["rsiOppPoeng"]),
        "toAvTrePositive": f["positiveSiste3"] >= 2,
        # D – volum er støtte, ikke krav
        "positivDagMedVolum": f["positivDagMedVolum"],
        "oppVolumOverNed": (f["upVolumeRatio"] is not None
                            and f["upVolumeRatio"] > 1.0),
        "volumMedSnuing": (f["momentumSnudd"] and f["volumeRatio20d"] is not None
                           and f["volumeRatio20d"] >= e["volumeConfirmRatio"]),
        # E – prisstruktur. Ingen krav om SMA50 eller SMA200 i LYTTEPOST.
        "higherLow": f["higherLow"],
        "sma20Reclaim": f["sma20Reclaim"],
        "bryterMotstand": f["bryterMotstand"],
        # F – signalhastighet: endring, ikke bare nivå
        "rsiAkselerasjon": (f["rsiChange3d"] is not None
                            and f["rsiChangeForrige3d"] is not None
                            and f["rsiChange3d"] > 0
                            and f["rsiChange3d"] > f["rsiChangeForrige3d"]),
        "momentumSnudd": f["momentumSnudd"],
    }

    gruppepoeng = {navn: sum(p[n] for n in nokler if k[n])
                   for navn, nokler in grupper.items()}
    return float(sum(gruppepoeng.values())), gruppepoeng, k


def entry_value(f: dict, cc: Optional["CorrectionEvent"],
                cfg: dict = SCANNER_CONFIG) -> float:
    """
    §4: hvor attraktiv inngangen fortsatt er HVIS vendingen lykkes.

    Holdes bevisst utenfor Turn Score. Det er dette som gjør at KIT på 87.60
    kan være en bedre inngang enn KIT på 95.40, selv om den tekniske
    vendingen er bedre bekreftet på 95.40.
    """
    e = cfg["early"]
    d = f["distanceFromLowPct"]
    if d is None:
        return 0.0

    score = e["entryElse"]
    for grense, verdi in e["entryBands"]:
        if d <= grense:
            score = verdi
            break

    if cc is not None and cc.currentDrawdownPct >= e["entryDrawdownMin"]:
        score += e["entryDrawdownBonus"]
    return float(min(100, score))


def lyttepost_styrke(turn: float, cfg: dict = SCANNER_CONFIG) -> Optional[str]:
    """§6: EARLY, GOOD eller STRONG. Alle heter fortsatt LYTTEPOST."""
    for grense, navn in cfg["early"]["styrkebaand"]:
        if turn >= grense:
            return navn
    return None


def opportunity_score(turn: float, entry: float,
                      cfg: dict = SCANNER_CONFIG) -> float:
    """
    §10: rangering av tidlige signaler.

    Turn alene ville rangert den bekreftede, men utstrakte aksjen over den
    tidlige. Vekting mot Entry Value er nettopp poenget med laget.
    """
    e = cfg["early"]
    return round(e["opportunityTurnVekt"] * turn
                 + e["opportunityEntryVekt"] * entry, 1)


def vurder_tidlig_lag(ind: dict, cc: Optional["CorrectionEvent"],
                      lagret: Optional[dict] = None,
                      cfg: dict = SCANNER_CONFIG) -> dict:
    """
    §5, §7 og §8: status, forsterkning, svekkelse og invalidasjon.

    Livssyklusen er aktivering → decay tilbake til BOTTOM WATCH → eventuelt
    LYTTEPOST BRUTT. Signalet lagres ved aktivering, slik at brudd kan måles
    mot der hypotesen faktisk startet og ikke mot dagens bunn.
    """
    e = cfg["early"]
    tom = {"aktiv": False, "status": None, "turnScore": None, "entryValue": None,
           "styrke": None, "opportunityScore": None, "fallingKnife": False,
           "knivGrunner": [], "grupper": {}, "kriterier": {}, "features": None,
           "bruddGrunner": [], "tilstand": dict(lagret or {})}
    if not e["enabled"]:
        return tom

    f = tidlige_features(ind, cfg)
    if f is None:
        return tom

    kniv, knivgrunner = falling_knife_guard(f, cfg)
    turn, grupper, kriterier = turn_score(f, cfg)
    entry = entry_value(f, cc, cfg)
    positive_grupper = sum(1 for g in e["hovedgrupper"] if grupper.get(g, 0) > 0)

    bar_dato = ind["lastDate"].date() if hasattr(ind["lastDate"], "date") else ind["lastDate"]
    tilstand = dict(lagret or {})
    aktivt_signal = bool(tilstand.get("signalDato")) and not tilstand.get("bruttDato")

    # §8: invalidasjon måles mot det lagrede signalet, ikke mot dagens bunn
    brudd = []
    if aktivt_signal:
        lagret_low = _num(tilstand.get("signalLocalLow"))
        if lagret_low and f["kurs"] < lagret_low * (1 - e["bruttUnderLowPct"] / 100):
            brudd.append("close under lagret low")
        if f["nyttLow20dIDag"] and (f["return3d"] or 0) < 0:
            brudd.append("ny 20D-low med negativt 3D-momentum")
        if turn < e["bruttTurn"]:
            brudd.append("Turn Score under bruddterskel")
        if kniv:
            brudd.append("falling knife aktiv igjen")

    status = None
    if brudd:
        status = STATUS_LYTTEPOST_BRUTT
        tilstand["bruttDato"] = str(bar_dato)
        tilstand["bruddGrunner"] = brudd
    elif aktivt_signal:
        # §7: signalet beholdes ned til decay-terskelen, ikke bare så lenge
        # aktiveringskravet er oppfylt. Uten hysterese ville et signal blinke
        # av og på rundt 55.
        status = (STATUS_LYTTEPOST if turn >= e["lyttepostDecay"]
                  else STATUS_BOTTOM_WATCH)
    elif kniv:
        # §2/§5: ingen LYTTEPOST, og BOTTOM WATCH krever falling_knife = FALSE
        status = None
    elif (turn >= e["lyttepost"] and entry >= e["entryMin"]
          and positive_grupper >= e["minGrupper"]):
        status = STATUS_LYTTEPOST
        tilstand = {"signalDato": str(bar_dato), "signalKurs": f["kurs"],
                    "signalLocalLow": f["localLow20d"], "signalTurnScore": turn}
    elif turn >= e["bottomWatch"]:
        status = STATUS_BOTTOM_WATCH

    # §8: bruddet skal stå en stund, ellers rekker brukeren aldri å se at
    # den tidlige hypotesen feilet.
    if status in (None, STATUS_BOTTOM_WATCH) and tilstand.get("bruttDato"):
        try:
            brutt_dato = date.fromisoformat(str(tilstand["bruttDato"]))
            if _handelsdager_mellom(brutt_dato, bar_dato) <= e["bruttSynligHandelsdager"]:
                status = STATUS_LYTTEPOST_BRUTT
        except ValueError:
            pass

    styrke = lyttepost_styrke(turn, cfg) if status == STATUS_LYTTEPOST else None
    return {
        "aktiv": status is not None,
        "status": status,
        "turnScore": round(turn),
        "entryValue": round(entry),
        "styrke": styrke,
        "opportunityScore": opportunity_score(turn, entry, cfg),
        "fallingKnife": kniv,
        "knivGrunner": knivgrunner,
        "grupper": grupper,
        "kriterier": kriterier,
        "features": f,
        "bruddGrunner": brudd,
        "positiveGrupper": positive_grupper,
        "tilstand": tilstand,
    }


def classify_status(r: dict, cfg: dict = SCANNER_CONFIG) -> str:
    """
    §20: brukerstatusen utledes av intern fase og alvorlighetsgrad.

    Det viktige er at en aksje som har falt mye, og deretter begynner å hente
    seg inn, får lov til å gå STRONG CORRECTION → STABILIZING selv om
    severity internt fortsatt er STRONG_CORRECTION. Vi tvinger den ikke til å
    bli værende på det verste den har vært.
    """
    klassisk = _klassisk_status(r, cfg)

    # Det tidlige laget legges foran de klassiske statusene, men aldri foran
    # EVENT RISK, STABILIZING eller REVERSAL: de står lenger ute i stigen og
    # er allerede bekreftet. Korreksjonsdybden er fortsatt synlig i CORR- og
    # PCTL-kolonnene, så ingenting går tapt ved at LYTTEPOST vises i stedet
    # for CORRECTION.
    tidlig = (r.get("early") or {}).get("status")
    if tidlig and klassisk not in (STATUS_EVENT_RISK, STATUS_STABILIZING,
                                   STATUS_REVERSAL):
        return tidlig
    return klassisk


def _klassisk_status(r: dict, cfg: dict = SCANNER_CONFIG) -> str:
    """Statusen slik den var før det tidlige laget. Uendret logikk."""
    fase, sev = r["phase"], r["severity"]

    if fase == PHASE_EVENT_RISK:
        return STATUS_EVENT_RISK

    if fase in (PHASE_CLOSED, PHASE_NORMAL) or sev == SEV_NONE:
        return STATUS_WAIT

    if sev == SEV_FOLLOW:
        return STATUS_FOLLOW

    if fase == PHASE_RECOVERING:
        # §22: REVERSAL er strengt. Alle vilkårene må være oppfylt samtidig.
        if (r["recoveryScore"] >= cfg["recovery"]["confirmed"]
                and r["trendScore"] >= cfg["trend"]["minimumForReversal"]
                and r["fundamentalsChecked"] and r["thesisIntact"]
                and not r["eventRisk"]):
            return STATUS_REVERSAL
        return STATUS_STABILIZING

    if fase == PHASE_BASE_BUILDING:
        return STATUS_STABILIZING

    if fase == PHASE_FALLING:
        return STATUS_STRONG_CORRECTION if sev == SEV_STRONG else STATUS_CORRECTION

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
               lagret_state: Optional[dict] = None,
               cfg: dict = SCANNER_CONFIG,
               lyttepost_state: Optional[dict] = None) -> Optional[dict]:
    """
    Hovedmotoren for én aksje.

    Rekkefølgen er bindende, fordi ledd henger på hverandre: severity trengs
    for å gate recovery (§17), recovery og higher low trengs for å bestemme
    fase (§4), og fasen bestemmer statusen (§20).
    """
    ind = calculate_indicators(df)
    if ind is None:
        log.warning(f"[{ticker}] for kort historikk, hoppes over")
        return None

    swing = detect_swings(ind["close"], ind["atr_s"], cfg["swing"]["atrMultiplier"])
    historikk = detect_historical_corrections(ticker, ind["atr_s"], swing, cfg)
    cc = detect_current_correction(ticker, ind, swing, lagret_state, cfg)
    if cc is None:
        return None

    # 1. Percentil på hendelsens dybde (§8)
    atr_at_peak = _num(ind["atr_s"].iloc[cc.peakIdx])
    percentile, n_hist = calculate_correction_percentile(cc, historikk, atr_at_peak, cfg)
    cc.correctionPercentile = percentile

    # 2. Correction Score (§7)
    stretch, stretch_deler = technical_stretch_score(ind, cfg)
    soner = finn_supportsoner(ind, swing, cc, cfg)
    vindu = relevansvindu(ind, cfg)
    støtte, beste_sone = support_score(soner, vindu, cfg)
    correction_score = calculate_correction_score(percentile, stretch, støtte, cfg)

    # 3. Severity huskes på sitt høyeste for eventet (§4, §D)
    cc.severity = bestem_severity(correction_score, percentile, cc, ind,
                                  lagret_state, cc.correctionId, cfg)

    # 4. Trend (§10) og recovery (§12), sistnevnte gatet på aktiv korreksjon
    trend_score, trend_deler = calculate_trend_score(ind, swing, cfg)
    recovery_score, recovery_deler = calculate_recovery_score(ind, swing, cc, cfg)
    higher_low = bool(recovery_deler.get("higherLowConfirmed"))

    # 5. Event risk (§24) og fundamental gate (§23)
    event_risk, event_grunner = detect_event_risk(ind, cc, cfg)
    fund = resolve_fundamentals(fund_store, ticker, cc.correctionId, cfg)

    # 6. Fase, og dermed status (§4, §20)
    cc.phase = bestem_fase(cc, recovery_score, higher_low, event_risk,
                           fund.fundamentalsChecked, cfg)

    tidlig = vurder_tidlig_lag(ind, cc, lyttepost_state, cfg)

    resultat = {
        "ticker": ticker,
        "Ticker": ticker.replace(".OL", ""),
        "early": tidlig,
        "Navn": OSLO_TICKERS.get(ticker, ticker),
        "correctionScore": correction_score,
        "trendScore": trend_score,
        "recoveryScore": recovery_score,
        "eventRisk": event_risk,
        "fundamentalsChecked": fund.fundamentalsChecked,
        "thesisIntact": fund.thesisIntact,
        "phase": cc.phase,
        "severity": cc.severity,
    }
    resultat["status"] = classify_status(resultat, cfg)

    resultat.update({
        "correctionId": cc.correctionId,
        "currentCorrection": cc,
        "corporateActions": list(df.attrs.get("corporateActions", [])),
        "correctionPercentile": percentile,
        "historiskeKorreksjoner": historikk,
        "antallHistoriske": n_hist,
        "tynnHistorikk": n_hist < cfg["percentile"]["minHistoricalCorrections"],
        "stretchScore": stretch,
        "stretchDeler": stretch_deler,
        "supportScore": støtte,
        "supportSoner": soner,
        "supportSone": beste_sone,
        "supportVindu": round(vindu, 2),
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
    raw.setdefault("corrections", {})
    raw.setdefault("varslet", {})
    raw.setdefault("lyttepost", {})
    return raw


def lagre_state(state: dict) -> None:
    _skriv_json(STATE_FILE, state)


# ══════════════════════════════════════════════════════════════
# VARSLER
# Én korreksjon er én hendelse. Varsel når status endres, eller når
# alvorlighetsgraden øker vesentlig innenfor samme correctionId.
# ══════════════════════════════════════════════════════════════

# §32: hvilke overganger som fortjener et varsel. FOLLOW trenger ikke push,
# WAIT skal aldri gi push.
VARSEL_OVERGANGER = {
    (None, STATUS_CORRECTION),
    (STATUS_WAIT, STATUS_CORRECTION),
    (STATUS_FOLLOW, STATUS_CORRECTION),
    (STATUS_CORRECTION, STATUS_STRONG_CORRECTION),
    (STATUS_CORRECTION, STATUS_STABILIZING),
    (STATUS_STRONG_CORRECTION, STATUS_STABILIZING),
    (STATUS_STABILIZING, STATUS_REVERSAL),
    (STATUS_CORRECTION, STATUS_REVERSAL),
    (STATUS_STRONG_CORRECTION, STATUS_REVERSAL),
}


def _varselsammendrag(r: dict, fra: Optional[str] = None) -> str:
    """Én linje til varselpanelet."""
    cc = r.get("currentCorrection")
    biter = []
    if fra:
        biter.append(f"fra {fra.replace('_', ' ')}")
    if cc:
        biter.append(f"nå -{cc.currentDrawdownPct:.1f} %")
        if cc.maxDrawdownPct > cc.currentDrawdownPct + 0.5:
            biter.append(f"max -{cc.maxDrawdownPct:.1f} %")
    biter.append(f"percentil {r['correctionPercentile']:.0f}")
    biter.append(f"Trend {r['trendBand']}")
    return " · ".join(biter)


def _varseltekst(r: dict, tittel: str) -> str:
    cc = r.get("currentCorrection")
    return (
        f"{r['Ticker']} – {tittel}\n\n"
        f"Correction Score: {r['correctionScore']:.0f}\n"
        + (f"Korreksjon nå: -{cc.currentDrawdownPct:.1f} %\n"
           f"Maks dybde: -{cc.maxDrawdownPct:.1f} %\n" if cc else "")
        + f"Historisk percentil: {r['correctionPercentile']:.0f}\n\n"
        f"Trend: {r['trendBand']}\n"
        f"Recovery: {r['recoveryBand']}\n\n"
        f"Åpne radaren for gjennomgang."
    )


def evaluer_varsler(resultater: list, state: dict, cfg: dict = SCANNER_CONFIG) -> list:
    """
    §32/§33/§M: varsler på meningsfulle overganger, aldri samme overgang to
    ganger for samme correctionId.

    Går en aksje STRONG → STABILIZING → STRONG → STABILIZING skal den ikke
    spamme identisk varsel hver dag. EVENT RISK har egen cooldown, fordi en ny
    alvorlig hendelse skal kunne varsles på nytt.
    """
    nye = []
    statuses = state.setdefault("statuses", {})
    korreksjoner = state.setdefault("corrections", {})
    varslet = state.setdefault("varslet", {})
    naa_dt = datetime.now(ZoneInfo("Europe/Oslo"))
    naa = naa_dt.strftime("%Y-%m-%d %H:%M")

    for r in resultater:
        t = r["ticker"]
        cc = r.get("currentCorrection")
        forrige = statuses.get(t, {})
        forrige_status = forrige.get("status")
        ny_status = r["status"]
        cid = r["correctionId"]

        # Sendte overganger huskes per correctionId, ikke per ticker.
        sendt = varslet.setdefault(cid, [])
        overgang = f"{forrige_status or 'NY'}->{ny_status}"

        if ny_status == STATUS_EVENT_RISK:
            sist = forrige.get("eventRiskVarslet")
            moden = True
            if sist:
                try:
                    dager = (naa_dt - datetime.fromisoformat(sist)).days
                    moden = dager >= cfg["alerts"]["eventRiskCooldownDays"]
                except ValueError:
                    moden = True
            if moden:
                nye.append({"tid": naa, "ticker": r["Ticker"], "type": STATUS_EVENT_RISK,
                            "correctionId": cid, "overgang": overgang,
                            "sammendrag": _varselsammendrag(r),
                            "tekst": _varseltekst(r, "EVENT RISK – UNDERSØK")})
                forrige["eventRiskVarslet"] = naa_dt.isoformat()

        elif (ny_status != forrige_status
              and (forrige_status, ny_status) in VARSEL_OVERGANGER
              and overgang not in sendt):
            sendt.append(overgang)
            nye.append({"tid": naa, "ticker": r["Ticker"], "type": ny_status,
                        "correctionId": cid, "overgang": overgang,
                        "sammendrag": _varselsammendrag(r, forrige_status),
                        "tekst": _varseltekst(r, ny_status.replace("_", " "))})

        elif (ny_status == forrige_status and forrige.get("correctionId") == cid
              and cc is not None and forrige.get("maxDrawdownPct") is not None
              and cc.maxDrawdownPct - forrige["maxDrawdownPct"] >= cfg["alerts"]["severityStepPct"]
              and ny_status in (STATUS_CORRECTION, STATUS_STRONG_CORRECTION)):
            merke = f"{overgang}@{cc.maxDrawdownPct:.0f}"
            if merke not in sendt:
                sendt.append(merke)
                nye.append({"tid": naa, "ticker": r["Ticker"], "type": f"{ny_status}+",
                            "correctionId": cid, "overgang": merke,
                            "sammendrag": f"dypere · {_varselsammendrag(r)}",
                            "tekst": _varseltekst(
                                r, f"{ny_status.replace('_', ' ')} – dypere")})

        statuses[t] = {
            **{k: v for k, v in forrige.items() if k == "eventRiskVarslet"},
            "status": ny_status,
            "correctionId": cid,
            "maxDrawdownPct": cc.maxDrawdownPct if cc else None,
            "currentDrawdownPct": cc.currentDrawdownPct if cc else None,
            "phase": r["phase"],
            "severity": r["severity"],
            "oppdatert": naa,
        }

        # §3: eventets tilstand bæres videre til neste skanning
        if cc is not None:
            korreksjoner[t] = {
                "correctionId": cid,
                "maxDrawdownPct": cc.maxDrawdownPct,
                "severity": cc.severity,
                "phase": cc.phase,
                "peakDate": cc.peakDate,
                "troughDate": cc.troughDate,
                "oppdatert": naa,
            }

    # Rydd bort varselhistorikk for korreksjoner som ikke lenger er aktive
    aktive_ider = {r["correctionId"] for r in resultater}
    for cid in list(varslet):
        if cid not in aktive_ider:
            varslet.pop(cid, None)

    if nye:
        logg = nye + state.get("alerts", [])
        state["alerts"] = logg[: cfg["alerts"]["maxLogEntries"]]
    return nye


# ══════════════════════════════════════════════════════════════
# HANDELSKALENDER
# Alle scores skal beregnes på siste AVSLUTTEDE handelsdag på Oslo Børs.
# Ikke gårsdagen i kalenderen — helger og børshelligdager skal hoppes over.
# ══════════════════════════════════════════════════════════════

def paaskedag(aar: int) -> date:
    """Første påskedag etter den gregorianske algoritmen."""
    a = aar % 19
    b, c = aar // 100, aar % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    maaned = (h + l - 7 * m + 114) // 31
    dag = ((h + l - 7 * m + 114) % 31) + 1
    return date(aar, maaned, dag)


def bors_helligdager(aar: int) -> set:
    """
    Dager Euronext Oslo holder stengt.

    De bevegelige er knyttet til påsken. Julaften og nyttårsaften er også
    stengt, ikke bare halv dag.
    """
    p = paaskedag(aar)
    return {
        date(aar, 1, 1),                    # Nyttårsdag
        p - timedelta(days=3),              # Skjærtorsdag
        p - timedelta(days=2),              # Langfredag
        p + timedelta(days=1),              # 2. påskedag
        date(aar, 5, 1),                    # Arbeidernes dag
        date(aar, 5, 17),                   # Grunnlovsdag
        p + timedelta(days=39),             # Kristi himmelfartsdag
        p + timedelta(days=50),             # 2. pinsedag
        date(aar, 12, 24),                  # Julaften
        date(aar, 12, 25),
        date(aar, 12, 26),
        date(aar, 12, 31),                  # Nyttårsaften
    }


def er_handelsdag(d: date) -> bool:
    """Hverdag som ikke er børshelligdag."""
    return d.weekday() < 5 and d not in bors_helligdager(d.year)


def forrige_handelsdag(d: date) -> date:
    d -= timedelta(days=1)
    while not er_handelsdag(d):
        d -= timedelta(days=1)
    return d


def siste_avsluttede_handelsdag(naa: Optional[datetime] = None,
                                cfg: dict = SCANNER_CONFIG) -> date:
    """
    Hvilken handelsdag analysen skal bygge på akkurat nå.

    Er dagens sesjon ferdig og sluttdataene rukket å lande, er det i dag.
    Ellers er det forrige handelsdag — som etter en helg er fredag, og etter
    en helligdag den siste virkedagen før den.
    """
    d_cfg = cfg["data"]
    tz = ZoneInfo(d_cfg["exchangeTimezone"])
    oslo = (naa or datetime.now(tz)).astimezone(tz)

    stengt = dtime(d_cfg["marketCloseHour"], d_cfg["marketCloseMinute"])
    frist = (datetime.combine(oslo.date(), stengt)
             + timedelta(minutes=d_cfg["dataLagMinutes"])).time()

    if er_handelsdag(oslo.date()) and oslo.time() >= frist:
        return oslo.date()
    return forrige_handelsdag(oslo.date())


def _bar_dato(df: pd.DataFrame) -> Optional[date]:
    """Datoen på siste bar i en kursserie."""
    if df is None or len(df) == 0:
        return None
    return pd.Timestamp(df.index[-1]).date()


def manglende_handelsdager(df: pd.DataFrame, forventet: date,
                           maks: int = 5) -> list:
    """
    Handelsdager som mangler i serien, fram til og med forventet dag.

    Etterslepssjekken ellers i koden spør «er siste bar eldre enn
    forventet?». Den er blind for et hull som ligger BAK en nyere bar, og
    det er nettopp slik feilen 07.09.2026 artet seg: mandagen borte fra
    Yahoo, tirsdagens uferdige bar på plass. Serien så fersk ut, og hele
    fallback-kjeden ble hoppet over. Her ses det derfor på hvilke
    handelsdager som faktisk finnes i indeksen.
    """
    if df is None or len(df) == 0:
        return []
    har = {pd.Timestamp(t).date() for t in df.index}
    forste = min(har)
    ut, d = [], forventet
    for _ in range(maks):
        if d <= forste:
            break
        if d not in har and er_handelsdag(d):
            ut.append(d)
        d = forrige_handelsdag(d)
    return sorted(ut)


def _juster_serie(df: pd.DataFrame, hendelser: list) -> pd.DataFrame:
    """Skaler barer før hver ex-dato. Flere hendelser komponeres."""
    d = df.copy()
    brukt = []
    for h in hendelser:
        faktor = _num(h.get("faktor"))
        if faktor is None or faktor <= 0 or faktor == 1.0:
            continue
        ex = pd.Timestamp(h["exDato"])
        maske = d.index < ex
        if not maske.any():
            continue
        for kol in ("Open", "High", "Low", "Close"):
            if kol in d.columns:
                d.loc[maske, kol] = d.loc[maske, kol] * faktor
        brukt.append({"exDato": h["exDato"], "faktor": faktor,
                      "kort": h.get("kort", "corporate action"),
                      "note": h.get("note", ""), "barer": int(maske.sum())})

    d.attrs = dict(df.attrs)
    if brukt:
        d.attrs["corporateActions"] = brukt
    return d


def juster_corporate_actions(prisdata: dict, cfg: dict = SCANNER_CONFIG) -> dict:
    """
    Sett historikken på samme grunnlag som dagens kurs over corporate actions.

    Yahoo justerer for utbytte og splitt, men ikke for fisjon. Da sammenlignes
    en kurs som inneholdt et utskilt selskap med en kurs som ikke gjør det, og
    differansen leses som et markedsfall. For KOG betyr det 34.8 % drawdown der
    det reelle er 22.2 %, en oppdiktet korreksjon på 27 % i historikken
    percentilen måles mot, og en SMA200 så høy at Trend Score aldri kommer over
    REVERSAL-terskelen.

    Kun historiske barer flyttes. Dagens kurs står urørt, slik at UI-et viser
    faktisk markedskurs mens sammenligninger går på justert grunnlag.
    """
    tabell = cfg.get("corporateActions") or {}
    if not tabell:
        return prisdata

    ut = {}
    for t, df in prisdata.items():
        hendelser = tabell.get(t)
        if not hendelser or df is None or len(df) == 0:
            ut[t] = df
            continue
        ut[t] = _juster_serie(df, hendelser)
        for h in ut[t].attrs.get("corporateActions", []):
            log.info(f"[{t}] justerte {h['barer']} barer før {h['exDato']} "
                     f"med faktor {h['faktor']}")
    return ut


def rens_prisdata(prisdata: dict, naa: Optional[datetime] = None,
                  cfg: dict = SCANNER_CONFIG) -> dict:
    """
    Forkast en uferdig candle før noe beregnes.

    Skanner du midt i sesjonen returnerer datakilden dagens halvferdige bar.
    Den ville gått rett inn i RSI, ATR, volumratio og dermed alle scorene.
    """
    if not cfg["data"]["dropIncompleteSession"]:
        return prisdata

    forventet = siste_avsluttede_handelsdag(naa, cfg)
    ut = {}
    for t, df in prisdata.items():
        d = _bar_dato(df)
        if d is not None and d > forventet and len(df) > 1:
            log.info(f"[{t}] forkaster uferdig bar {d} (siste avsluttede {forventet})")
            df = df.iloc[:-1]
        ut[t] = df
    return ut


def datastatus(prisdata: dict, naa: Optional[datetime] = None,
               cfg: dict = SCANNER_CONFIG) -> dict:
    """
    Kontrollerer at dataene faktisk går til siste avsluttede handelsdag.

    Mangler den, skal ikke analysen presenteres som oppdatert — den er da
    regnet på foreldede kurser, og det gjelder ikke bare KURS og % I DAG,
    men RSI, SMA-er, correction-data, fase og hele prioriteringen.
    """
    forventet = siste_avsluttede_handelsdag(naa, cfg)
    per_ticker = {t: _bar_dato(df) for t, df in prisdata.items()}
    gyldige = [d for d in per_ticker.values() if d is not None]
    etterslep = {t: d for t, d in per_ticker.items() if d is not None and d < forventet}
    mangler_helt = [t for t, d in per_ticker.items() if d is None]

    # Svakeste ledd, ikke det ferskeste. Med max() holdt én oppdatert ticker
    # stale=False for hele skanningen, og banneret meldte 08.09 mens NOD, KIT
    # og KOG ble beregnet på 07.09-closes. Headeren skal vise den candlen
    # analysen faktisk hviler på.
    faktisk = min(gyldige) if gyldige else None
    nyeste = max(gyldige) if gyldige else None

    return {
        "forventet": forventet,
        "faktisk": faktisk,
        "nyeste": nyeste,
        "spriker": bool(faktisk is not None and faktisk != nyeste),
        "stale": bool(not gyldige or etterslep or mangler_helt),
        "manglerHelt": mangler_helt,
        "etterslep": etterslep,
        "perTicker": per_ticker,
        "kilder": {t: df.attrs.get("sisteKilde", "yahoo")
                   for t, df in prisdata.items()},
        "handelsdagerBak": _handelsdager_mellom(faktisk, forventet) if faktisk else None,
    }


def _handelsdager_mellom(fra: date, til: date) -> int:
    """Antall handelsdager datagrunnlaget ligger bak."""
    if fra >= til:
        return 0
    n, d = 0, fra
    while d < til:
        d += timedelta(days=1)
        if er_handelsdag(d):
            n += 1
    return n


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


# ── Andrekilde: Stooq ─────────────────────────────────────────
# Gratis CSV uten nøkkel. Brukes kun når Yahoo mangler siste
# avsluttede handelsdag, aldri som primærkilde.

STOOQ_URL = "https://stooq.com/q/d/l/?s={symbol}&i=d"


def stooq_symbol(ticker: str) -> str:
    """KOG.OL → kog.ol. Tickere uten suffiks antas amerikanske."""
    t = ticker.strip().lower()
    if "." in t:
        return t
    return f"{t}.us"


def _hent_url(url: str, session, timeout: int) -> Optional[str]:
    """Hent tekst med curl_cffi-sesjonen om den finnes, ellers urllib."""
    try:
        if session is not None:
            svar = session.get(url, timeout=timeout)
            if getattr(svar, "status_code", 200) != 200:
                return None
            return svar.text
        with urlrequest.urlopen(url, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="replace")
    except Exception as e:
        log.debug(f"Henting av {url} feilet: {type(e).__name__}: {e}")
        return None


def parse_stooq_csv(tekst: str) -> Optional[pd.DataFrame]:
    """
    Tolk Stooq sin CSV. Returnerer None ved feilsvar, som ved rate limit
    kommer som vanlig tekst og ikke som en HTTP-feil.
    """
    if not tekst or "Date,Open" not in tekst.split("\n")[0]:
        return None
    try:
        df = pd.read_csv(io.StringIO(tekst))
    except Exception as e:
        log.debug(f"Stooq-CSV kunne ikke tolkes: {type(e).__name__}: {e}")
        return None
    if df.empty or "Close" not in df.columns:
        return None
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date"]).set_index("Date").sort_index()
    for kol in ("Open", "High", "Low", "Close", "Volume"):
        if kol not in df.columns:
            return None
        df[kol] = pd.to_numeric(df[kol], errors="coerce")
    return df[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])


def hent_stooq(ticker: str, session=None, cfg: dict = SCANNER_CONFIG) -> Optional[pd.DataFrame]:
    url = STOOQ_URL.format(symbol=stooq_symbol(ticker))
    tekst = _hent_url(url, session, cfg["data"]["stooqTimeout"])
    return parse_stooq_csv(tekst) if tekst else None


def flett_inn_kilde(basis: pd.DataFrame, ny: pd.DataFrame,
                    cfg: dict = SCANNER_CONFIG) -> tuple:
    """
    Flett nyere barer fra en annen kilde inn i en eksisterende serie.

    Kildene bruker ulikt justeringsgrunnlag — Yahoo justerer for utbytte,
    Stooq gjør det ikke. Limes de sammen rått, får siste bar et falskt hopp
    som slår rett inn i % i dag, RSI, ATR og korreksjonens drawdown. Derfor
    skaleres de nye barene til basisseriens nivå ved hjelp av forholdet på
    siste felles dato.
    """
    d = cfg["data"]
    if basis is None or basis.empty or ny is None or ny.empty:
        return basis, None

    felles = basis.index.intersection(ny.index)
    if len(felles) == 0:
        return basis, "ingen overlappende datoer"

    ankerdato = felles[-1]
    b = float(basis.loc[ankerdato, "Close"])
    n = float(ny.loc[ankerdato, "Close"])
    if not (math.isfinite(b) and math.isfinite(n)) or n <= 0:
        return basis, "ugyldig ankerkurs"

    faktor = b / n
    if abs(faktor - 1.0) > d["stooqMaxScaleDeviation"]:
        return basis, f"skaleringsfaktor {faktor:.3f} avviker for mye"

    nye_rader = ny[ny.index > basis.index[-1]].copy()
    if nye_rader.empty:
        return basis, "ingen nyere barer"

    for kol in ("Open", "High", "Low", "Close"):
        nye_rader[kol] = nye_rader[kol] * faktor

    slaatt = pd.concat([basis, nye_rader]).sort_index()
    slaatt = slaatt[~slaatt.index.duplicated(keep="first")]
    slaatt.attrs = dict(basis.attrs)
    slaatt.attrs["sisteKilde"] = "stooq"
    slaatt.attrs["skalering"] = round(faktor, 5)
    return slaatt, None


def topp_opp_fra_stooq(alle: dict, forventet: date, session,
                       diag: Optional[dict] = None,
                       cfg: dict = SCANNER_CONFIG) -> tuple:
    """Siste utvei når Yahoo ikke har siste avsluttede handelsdag."""
    if not cfg["data"]["stooqEnabled"]:
        return alle, []

    mangler = [t for t, df in alle.items()
               if _bar_dato(df) is not None and _bar_dato(df) < forventet]
    if not mangler:
        return alle, []

    log.info(f"Prøver Stooq for {len(mangler)} tickere som mangler {forventet}")
    fikset = []
    for t in mangler:
        d = diag.setdefault(t, {}) if diag is not None else {}
        try:
            url = STOOQ_URL.format(symbol=stooq_symbol(t))
            tekst = _hent_url(url, session, cfg["data"]["stooqTimeout"])
            if not tekst:
                d["stooq"] = "ingen respons"
                log.warning(f"[{t}] Stooq: ingen respons")
                continue
            d["stooqSvar"] = tekst.split("\n")[0][:60]
            ny = parse_stooq_csv(tekst)
            if ny is None:
                d["stooq"] = f"ikke CSV: {d['stooqSvar']}"
                log.warning(f"[{t}] Stooq ga ikke brukbare data")
                continue
            d["stooq"] = f"siste {_bar_dato(ny)}"
            slaatt, grunn = flett_inn_kilde(alle[t], ny, cfg)
            if grunn:
                d["stooq"] += f" — ikke flettet: {grunn}"
                log.warning(f"[{t}] Stooq ikke flettet inn: {grunn}")
                continue
            alle[t] = slaatt
            fikset.append(t)
            d["stooq"] += f" — flettet, skalering {slaatt.attrs.get('skalering')}"
            log.info(f"[{t}] Stooq toppet opp til {_bar_dato(slaatt)} "
                     f"(skalering {slaatt.attrs.get('skalering')})")
        except Exception as e:
            d["stooq"] = f"feil: {type(e).__name__}: {e}"[:120]
            log.warning(f"[{t}] Stooq feilet: {type(e).__name__}: {e}")
    return alle, fikset


# ══════════════════════════════════════════════════════════════
# INTRADAG-BACKFILL
# Yahoo leverte 07.09.2026 mandagen som null-bar på dagsoppløsning
# for hele Oslo Børs, mens 5-minuttersserien hadde dagen. Samme
# kilde, annet endepunkt — derfor ingen skalering: justeringsnivået
# er det samme.
# ══════════════════════════════════════════════════════════════

YAHOO_CHART_URL = ("https://query1.finance.yahoo.com/v8/finance/chart/"
                   "{ticker}?range={rekkevidde}&interval={intervall}")


def _hent_chart(ticker: str, session, rekkevidde: str, intervall: str,
                cfg: dict = SCANNER_CONFIG) -> Optional[dict]:
    """Rått svar fra Yahoos chart-API."""
    url = YAHOO_CHART_URL.format(ticker=ticker, rekkevidde=rekkevidde,
                                 intervall=intervall)
    tekst = _hent_url(url, session, cfg["data"]["backfillTimeout"])
    if not tekst:
        return None
    try:
        res = json.loads(tekst)["chart"]["result"][0]
    except Exception as e:
        log.debug(f"[{ticker}] chart-svar ({rekkevidde}/{intervall}) kunne ikke "
                  f"tolkes: {type(e).__name__}: {e}")
        return None
    return res if res.get("timestamp") else None


def _hent_intradag(ticker: str, session, cfg: dict = SCANNER_CONFIG) -> Optional[dict]:
    """Intradag-serien som dagsbarene rekonstrueres fra."""
    d = cfg["data"]
    return _hent_chart(ticker, session, d["backfillRange"],
                       d["backfillInterval"], cfg)


def _hent_dagsmeta(ticker: str, session, cfg: dict = SCANNER_CONFIG) -> Optional[dict]:
    """
    Egen range=1d-forespørsel, kun for den offisielle sluttkursen.

    chartPreviousClose er relativ til chartens REKKEVIDDE, ikke til siste
    sesjon. Fra intradag-svaret (range=1mo) pekte den en måned tilbake og ga
    KIT 88.90 mot riktige 98.40. Kun range=1d gir «sesjonen før dagens».
    """
    return _hent_chart(ticker, session, "1d", "1d", cfg)


def aggreger_intradag(tidsstempler: list, kvote: dict, tz: str) -> dict:
    """
    Slå intradag-barer sammen til dagsbarer, gruppert på børsens lokale dato.

    Tidsstemplene er epoch i UTC mens dagen defineres av børsens tidssone.
    Streamlit Cloud kjører i UTC, så konverteringen må være eksplisitt —
    ellers havner morgenbarene på feil dato.
    """
    sone, utc = ZoneInfo(tz), ZoneInfo("UTC")
    o, h = kvote.get("open") or [], kvote.get("high") or []
    l, c = kvote.get("low") or [], kvote.get("close") or []
    v = kvote.get("volume") or []
    ut: dict = {}

    for i, stempel in enumerate(tidsstempler or []):
        felt = [(_num(kol[i]) if i < len(kol) else None) for kol in (o, h, l, c)]
        if any(x is None for x in felt):
            continue                    # Yahoo har null-barer også intradag
        aapne, hoy, lav, lukk = felt
        vol = _num(v[i]) if i < len(v) else None
        dag = datetime.fromtimestamp(stempel, tz=utc).astimezone(sone).date()

        rad = ut.get(dag)
        if rad is None:
            ut[dag] = {"Open": aapne, "High": hoy, "Low": lav, "Close": lukk,
                       "Volume": vol or 0.0, "_barer": 1}
        else:
            rad["High"] = max(rad["High"], hoy)
            rad["Low"] = min(rad["Low"], lav)
            rad["Close"] = lukk
            rad["Volume"] += vol or 0.0
            rad["_barer"] += 1
    return ut


def offisiell_close(res: Optional[dict], dag: date,
                    tz_standard: str) -> Optional[float]:
    """
    Offisiell sluttkurs for `dag` fra et range=1d chart-svar, ellers None.

    Sluttauksjonen 16:20-16:25 ligger ikke i den kontinuerlige intradag-feeden,
    så et rent aggregat bommer litt: KOG ga 298.90 mot offisielle 298.00 den
    07.09.2026 — 0.30 % rett inn i % i dag, RSI og drawdown.

    chartPreviousClose tilhører sesjonen FØR den svaret selv gjelder, og
    dekker derfor nøyaktig én dag. Den datoen utledes her i stedet for å antas.
    """
    if not res:
        return None
    meta = res.get("meta") or {}
    kurs = _num(meta.get("chartPreviousClose"))
    stempler = res.get("timestamp") or []
    if kurs is None or not stempler:
        return None
    tz = meta.get("exchangeTimezoneName") or tz_standard
    sesjon = (datetime.fromtimestamp(stempler[0], tz=ZoneInfo("UTC"))
              .astimezone(ZoneInfo(tz)).date())
    return kurs if forrige_handelsdag(sesjon) == dag else None


def offisiell_close_i_dag(res: Optional[dict], dag: date, tz_standard: str,
                          cfg: dict = SCANNER_CONFIG) -> Optional[float]:
    """
    Offisiell sluttkurs for dagens egen sesjon, etter at børsen har stengt.

    Yahoo publiserer den ferdige dagsbaren først neste handelsmorgen. Det er
    et problem fordi radaren primært brukes etter børsslutt, til å planlegge
    neste dag. regularMarketPrice står derimot stille på sluttkursen så snart
    sesjonen er over.

    Mens børsen er åpen er det samme feltet en levende intradagkurs, og den må
    aldri lagres som sluttkurs. Derfor godtas den kun når regularMarketTime
    ligger på `dag` og er etter stengetid.
    """
    if not res:
        return None
    meta = res.get("meta") or {}
    kurs = _num(meta.get("regularMarketPrice"))
    stempel = meta.get("regularMarketTime")
    if kurs is None or not stempel:
        return None

    d = cfg["data"]
    tz = meta.get("exchangeTimezoneName") or tz_standard
    tidspunkt = (datetime.fromtimestamp(int(stempel), tz=ZoneInfo("UTC"))
                 .astimezone(ZoneInfo(tz)))
    if tidspunkt.date() != dag:
        return None
    stenger = dtime(d["marketCloseHour"], d["marketCloseMinute"])
    return kurs if tidspunkt.time() >= stenger else None


def kjent_sluttkurs(ticker: str, dag: date,
                    cfg: dict = SCANNER_CONFIG) -> Optional[float]:
    """
    Manuelt registrert offisiell sluttkurs for dager kilden har mistet.

    Siste utvei, etter chartPreviousClose og regularMarketPrice. Se
    kommentaren i SCANNER_CONFIG for hvor kursene kommer fra.
    """
    tabell = cfg["data"].get("kjenteSluttkurser") or {}
    return _num((tabell.get(dag.isoformat()) or {}).get(ticker))


def flett_inn_dagsbar(df: pd.DataFrame, dag: date, bar: dict) -> pd.DataFrame:
    """
    Sett en rekonstruert dagsbar inn i serien uten å røre eksisterende rader.

    Baren havner på sin egen dato i en sortert indeks, slik at RSI, SMA-er og
    ATR ser dagene i riktig rekkefølge.
    """
    if df is None or len(df) == 0:
        return df
    stempel = pd.Timestamp(dag)
    if stempel in df.index:
        return df
    ny = pd.DataFrame([{k: bar.get(k) for k in df.columns}], index=[stempel])
    ut = pd.concat([df, ny]).sort_index()
    ut.attrs = dict(df.attrs)
    return ut


def backfill_manglende_dager(alle: dict, forventet: date, session,
                             diag: Optional[dict] = None,
                             cfg: dict = SCANNER_CONFIG) -> tuple:
    """
    Tett hull i dagsserien med barer rekonstruert fra intradag.

    Uten dette forsvinner dagen ikke bare fra KURS og % I DAG, men fra RSI,
    SMA-ene, ATR og volumratio — altså fra hele signalmotoren.

    5-minutters historikk hos Yahoo rekker bare rundt en måned tilbake, så
    dette tetter ferske hull. Eldre hull står igjen, og da blir STALE stående,
    som er riktig: da er tallene faktisk ikke til å stole på.
    """
    d = cfg["data"]
    if not d["backfillEnabled"]:
        return alle, []

    trengs = {t: manglende_handelsdager(df, forventet, d["backfillMaxDays"])
              for t, df in alle.items()}
    trengs = {t: dager for t, dager in trengs.items() if dager}
    if not trengs:
        return alle, []

    log.info(f"Hull i dagsserien for {len(trengs)} tickere, "
             f"rekonstruerer fra intradag")
    fikset = []
    for t, dager in trengs.items():
        dd = diag.setdefault(t, {}) if diag is not None else {}
        try:
            res = _hent_intradag(t, session, cfg)
            if not res:
                dd["intradag"] = "ingen respons"
                log.warning(f"[{t}] intradag: ingen respons")
                continue

            meta = res.get("meta") or {}
            tz = meta.get("exchangeTimezoneName") or d["exchangeTimezone"]
            kvote = (res.get("indicators", {}).get("quote") or [{}])[0]
            per_dag = aggreger_intradag(res.get("timestamp"), kvote, tz)
            tilgjengelig = sorted(per_dag)
            dagsmeta = _hent_dagsmeta(t, session, cfg)

            lagt_inn = []
            for dag in dager:
                bar = per_dag.get(dag)
                if bar is None:
                    continue
                if bar["_barer"] < d["backfillMinBars"]:
                    log.warning(f"[{t}] {dag}: kun {bar['_barer']} intradag-barer, "
                                f"hopper over")
                    continue
                # chartPreviousClose dekker gårsdagen, regularMarketPrice
                # dagens egen sesjon når den er ferdig, og tabellen dager
                # kilden har mistet helt.
                off = (offisiell_close(dagsmeta, dag, tz)
                       or offisiell_close_i_dag(dagsmeta, dag, tz, cfg)
                       or kjent_sluttkurs(t, dag, cfg))
                if off is not None:
                    bar = dict(bar, Close=off)
                alle[t] = flett_inn_dagsbar(alle[t], dag, bar)
                alle[t].attrs["sisteKilde"] = "yahoo-intradag"
                lagt_inn.append(
                    f"{dag} ({'offisiell close' if off is not None else 'omtrentlig close'})")

            dd["intradag"] = (f"tettet {', '.join(lagt_inn)}" if lagt_inn
                              else f"manglet {dager}, intradag hadde "
                                   f"{tilgjengelig[-3:] if tilgjengelig else 'ingenting'}")
            if lagt_inn:
                fikset.append(t)
                log.info(f"[{t}] tettet {', '.join(lagt_inn)} fra intradag")
        except Exception as e:
            dd["intradag"] = f"feil: {type(e).__name__}: {e}"[:120]
            log.warning(f"[{t}] intradag-backfill feilet: {type(e).__name__}: {e}")
    return alle, fikset


def _hent_siste_dager(ticker: str, session, dager: int = 10) -> Optional[pd.DataFrame]:
    """
    Hent kun de siste dagene med period i stedet for et datointervall.

    Yahoo svarer på de to forespørslene fra hver sin cache, og den korte er
    den som faktisk er fersk. Dette er derfor et reelt forsøk, ikke bare det
    samme kallet på nytt.
    """
    raw = yf.download(ticker, period=f"{dager}d", interval="1d", progress=False,
                      auto_adjust=True, timeout=20, threads=False, session=session)
    if raw is None or raw.empty:
        return None
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    return raw.dropna(how="all")


def topp_opp_siste_dager(alle: dict, forventet: date, session,
                         diag: Optional[dict] = None,
                         cfg: dict = SCANNER_CONFIG) -> tuple:
    """
    Hovednedlastingen kan komme tilbake uten siste avsluttede handelsdag.
    Da hentes de siste dagene separat per ticker og flettes inn.

    Bare barer som er NYERE enn det vi allerede har legges til. Eksisterende
    rader røres ikke, slik at justeringsgrunnlaget i historikken holdes
    uendret og ikke blandes med et nytt fra en kortere forespørsel.
    """
    mangler = [t for t, df in alle.items()
               if _bar_dato(df) is not None and _bar_dato(df) < forventet]
    if not mangler:
        return alle, []

    log.info(f"Mangler siste handelsdag ({forventet}) for {len(mangler)} tickere, "
             f"henter siste dager separat")
    fikset = []
    for t in mangler:
        d = diag.setdefault(t, {}) if diag is not None else {}
        try:
            ny = _hent_siste_dager(t, session)
            if ny is None or ny.empty:
                d["yahooPeriod"] = "tomt svar"
                continue
            d["yahooPeriod"] = f"siste {_bar_dato(ny)}"
            nye_rader = ny[ny.index > alle[t].index[-1]]
            if nye_rader.empty:
                d["yahooPeriod"] += " (ingen nyere barer)"
                continue
            slaatt = pd.concat([alle[t], nye_rader]).sort_index()
            slaatt = slaatt[~slaatt.index.duplicated(keep="first")]
            alle[t] = slaatt
            fikset.append(t)
            log.info(f"[{t}] toppet opp til {_bar_dato(slaatt)}")
            time.sleep(RETRY_DELAY_PER_TICKER)
        except Exception as e:
            d["yahooPeriod"] = f"feil: {type(e).__name__}: {e}"[:120]
            log.warning(f"[{t}] topp-opp feilet: {type(e).__name__}: {e}")
    return alle, fikset


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
def hent_prisdata(tickers: tuple, handelsdag: Optional[date] = None) -> tuple:
    """
    Last ned daglig OHLCV for watchlisten. Kun rådata caches – scoringen
    kjøres på nytt ved hver rerun, slik at fundamental-avkrysning slår
    gjennom umiddelbart uten ny nedlasting.

    handelsdag inngår i cache-nøkkelen. Uten den kunne en container som lever
    over et døgnskifte servere gårsdagens nedlasting videre.
    """
    liste = list(tickers)
    diag: dict = {"_sesjon": "curl_cffi" if _lag_session() is not None else "urllib"}
    if not liste:
        return {}, diag

    # end er eksklusiv hos yfinance, og Streamlit Cloud kjører i UTC. Med
    # end = now falt siste avsluttede handelsdag utenfor vinduet i døgnskiftet.
    # To dager fram fjerner tvetydigheten; dager som ikke finnes gir ingen data.
    now = datetime.now()
    start = now - timedelta(days=HISTORY_DAYS)
    end = now + timedelta(days=2)
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

    for t, df in alle.items():
        diag.setdefault(t, {})["yahooBatch"] = str(_bar_dato(df))

    # Mangler siste avsluttede handelsdag, prøv en kort period-forespørsel.
    forventet = handelsdag or siste_avsluttede_handelsdag()
    bak = [t for t, df in alle.items()
           if _bar_dato(df) is not None and _bar_dato(df) < forventet]
    if bak:
        progress.progress(0.97, text=f"Henter siste handelsdag for {len(bak)}...")
        alle, fikset = topp_opp_siste_dager(alle, forventet, session, diag)
        if fikset:
            log.info(f"Toppet opp {len(fikset)} tickere til {forventet}")

        # Fortsatt bak? Da leverer ikke Yahoo dagen i det hele tatt.
        fortsatt = [t for t, df in alle.items()
                    if _bar_dato(df) is not None and _bar_dato(df) < forventet]
        if fortsatt:
            progress.progress(0.99, text=f"Stooq for {len(fortsatt)}...")
            alle, fra_stooq = topp_opp_fra_stooq(alle, forventet, session, diag)
            if fra_stooq:
                log.info(f"Stooq dekket {len(fra_stooq)} tickere")

    # Serien kan se fersk ut og likevel ha hull: Yahoo leverer dager som
    # null-barer, og ligger det en uferdig bar etter hullet, fanger ikke
    # etterslepssjekken over det opp. Derfor sjekkes hull for seg.
    progress.progress(0.99, text="Sjekker hull i serien...")
    alle, tettet = backfill_manglende_dager(alle, forventet, session, diag)
    if tettet:
        log.info(f"Tettet hull fra intradag for {len(tettet)} tickere")

    for t in liste:
        d = diag.setdefault(t, {})
        d["endelig"] = str(_bar_dato(alle[t])) if t in alle else "ingen data"
        d.setdefault("yahooBatch", "ingen data")
        d.setdefault("yahooPeriod", "ikke forsøkt")
        d.setdefault("stooq", "ikke forsøkt")
        d.setdefault("intradag", "ingen hull")

    progress.empty()
    log.info(f"Lastet ned {len(alle)}/{len(liste)} aksjer")
    return alle, diag


def kjor_scan(prisdata: dict, fund_store: dict, state: Optional[dict] = None,
              cfg: dict = SCANNER_CONFIG) -> list:
    """
    Kjør motoren på alle nedlastede aksjer, med forrige tilstand som input.

    Uferdige candles er allerede forkastet av rens_prisdata(), så alt her
    beregnes på siste avsluttede handelsdag.
    """
    lagrede = (state or {}).get("corrections", {})
    lyttepost = (state or {}).setdefault("lyttepost", {}) if state is not None else {}
    ut = []
    for ticker, df in prisdata.items():
        try:
            r = scan_stock(ticker, df, fund_store, lagrede.get(ticker), cfg,
                           lyttepost.get(ticker))
            if r is not None:
                # Signalet må overleve til neste skanning, ellers kan brudd
                # ikke måles mot der hypotesen faktisk startet.
                tilstand = (r.get("early") or {}).get("tilstand")
                if state is not None:
                    if tilstand:
                        lyttepost[ticker] = tilstand
                    else:
                        lyttepost.pop(ticker, None)
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
    "lilla": "#A98BFF",     # tidlig lag: LYTTEPOST
}

MONO = "'IBM Plex Mono', ui-monospace, SFMono-Regular, Menlo, monospace"
SANS = "'IBM Plex Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"

STATUS_FARGE = {
    STATUS_EVENT_RISK: DC["roed"],
    STATUS_REVERSAL: DC["gronn"],
    STATUS_STABILIZING: DC["blaa"],
    STATUS_LYTTEPOST: DC["lilla"],
    STATUS_LYTTEPOST_BRUTT: "#8C5560",
    STATUS_STRONG_CORRECTION: DC["orange"],
    STATUS_CORRECTION: DC["gul"],
    STATUS_BOTTOM_WATCH: "#7E8AA0",
    STATUS_FOLLOW: "#A5AFBF",
    STATUS_WAIT: "#6B7686",
}

STATUS_TEKST = {
    STATUS_EVENT_RISK: "EVENT RISK",
    STATUS_REVERSAL: "REVERSAL",
    STATUS_STABILIZING: "STABILIZING",
    STATUS_LYTTEPOST: "LYTTEPOST",
    STATUS_LYTTEPOST_BRUTT: "LYTTEPOST BRUTT",
    STATUS_STRONG_CORRECTION: "STRONG CORRECTION",
    STATUS_CORRECTION: "CORRECTION",
    STATUS_BOTTOM_WATCH: "BOTTOM WATCH",
    STATUS_FOLLOW: "FOLLOW",
    STATUS_WAIT: "WAIT",
}

STATUS_KORT = {**STATUS_TEKST, STATUS_STRONG_CORRECTION: "STRONG CORR",
               STATUS_LYTTEPOST_BRUTT: "LP BRUTT"}

# Rekkefølgen på statustellerne i toppen
TELLER_REKKEFOLGE = [
    STATUS_EVENT_RISK, STATUS_REVERSAL, STATUS_STABILIZING, STATUS_LYTTEPOST,
    STATUS_STRONG_CORRECTION, STATUS_CORRECTION, STATUS_BOTTOM_WATCH,
    STATUS_FOLLOW, STATUS_WAIT,
]

# Sonene brukes i KORT-visningen
SONER = [
    {"navn": "KREVER GJENNOMGANG", "farge": DC["roed"], "form": "stor",
     "statuser": [STATUS_EVENT_RISK, STATUS_REVERSAL]},
    {"navn": "FØLG", "farge": DC["blaa"], "form": "medium",
     "statuser": [STATUS_STABILIZING, STATUS_LYTTEPOST, STATUS_LYTTEPOST_BRUTT,
                  STATUS_STRONG_CORRECTION, STATUS_CORRECTION]},
    {"navn": "ROLIG", "farge": DC["svak"], "form": "kompakt",
     "statuser": [STATUS_BOTTOM_WATCH, STATUS_FOLLOW, STATUS_WAIT]},
]

SORTERINGSVALG = {
    "Prioritet": None,
    "Opportunity Score": "opportunity",
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


def datobanner(status: dict) -> str:
    """
    Hvilken handelsdag analysen faktisk bygger på. Vises alltid, slik at det
    er mulig å verifisere datagrunnlaget uten å gjette.
    """
    forventet = status["forventet"].strftime("%d.%m.%Y")
    faktisk = status["faktisk"].strftime("%d.%m.%Y") if status["faktisk"] else "—"

    if not status["stale"]:
        kilder = status.get("kilder", {})
        fra_stooq = sorted(t.replace(".OL", "") for t, k in kilder.items() if k == "stooq")
        rekonstruert = sorted(t.replace(".OL", "") for t, k in kilder.items()
                              if k == "yahoo-intradag")
        merke = (f' · SISTE DAG FRA STOOQ: {", ".join(fra_stooq)}' if fra_stooq else "")
        if rekonstruert:
            merke += (f' · EOD REKONSTRUERT FRA INTRADAG: '
                      f'{", ".join(rekonstruert)}')
        return (f'<div style="font-family:{MONO};font-size:10px;letter-spacing:0.1em;'
                f'color:{DC["svak"]};padding:8px 0 0;">'
                f'<span style="color:{DC["gronn"]};">●</span> MARKEDSDATA T.O.M. '
                f'{faktisk}{merke}</div>')

    bak = status["handelsdagerBak"]
    mangler = ", ".join(sorted(t.replace(".OL", "") for t in status["etterslep"]))
    return (
        f'<div style="background:{_rgba(DC["orange"], 0.1)};'
        f'border:1px solid {_rgba(DC["orange"], 0.45)};border-left:3px solid {DC["orange"]};'
        f'border-radius:6px;padding:11px 14px;margin:8px 0;">'
        f'<div style="font-family:{MONO};font-size:11px;letter-spacing:0.1em;'
        f'color:{DC["orange"]};font-weight:600;">⚠ DATA STALE — SISTE DATA {faktisk}</div>'
        f'<div style="font-size:12px;color:{DC["dempet"]};margin-top:5px;line-height:1.6;">'
        f'Siste avsluttede handelsdag på Oslo Børs er <b style="color:{DC["tekst"]};">'
        f'{forventet}</b>'
        + (f', altså {bak} handelsdag{"er" if bak != 1 else ""} foran datagrunnlaget'
           if bak else "")
        + '. Analysen under er regnet på foreldede kurser — det gjelder ikke bare '
        'KURS og % I DAG, men RSI, SMA-er, correction-data, fase og prioriteringen.'
        + (f'<br>Mangler siste dag: <span style="font-family:{MONO};">{mangler}</span>'
           if mangler else "")
        + '</div></div>')


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

    return (f'<div style="display:grid;'
            f'grid-template-columns:repeat(auto-fit,minmax(112px,1fr));'
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
        tidlig = r.get("early") or {}
        status = STATUS_KORT[r["status"]]
        if r["status"] == STATUS_LYTTEPOST and tidlig.get("styrke"):
            status += f" – {tidlig['styrke']}"
        if reversal_blokkert(r):
            status += " · GATE"
        fase = {PHASE_FALLING: "↓", PHASE_BASE_BUILDING: "=",
                PHASE_RECOVERING: "↑", PHASE_CLOSED: "•",
                PHASE_EVENT_RISK: "!", PHASE_NORMAL: ""}.get(r["phase"], "")
        rader.append({
            "": "▌",                       # statusspine
            "TICKER": r["Ticker"],
            "FASE": fase,
            "STATUS": status,
            "CORR": r["correctionScore"],
            "TREND": r["trendScore"],
            "RECOV": r["recoveryScore"],
            "TURN": tidlig.get("turnScore"),
            "ENTRY": tidlig.get("entryValue"),
            "KORR %": -cc.currentDrawdownPct if cc else None,
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
                    subset=["", "FASE", "STATUS", "CORR"])
    sty = sty.apply(lambda k: per_rad(
        k, lambda i: f"color: {DC['svak'] if dempet[i] else DC['tekst']}"),
        subset=["TICKER", "TREND", "RECOV", "PCTL", "DAGER", "KURS", "RSI"])
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
    "FASE": st.column_config.TextColumn("F", width=26,
                                        help="↓ faller · = bygger base · ↑ henter seg inn"),
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
            ("NÅ", d["Dato"].iloc[-1].strftime("%Y-%m-%d"), cc.currentPrice,
             STATUS_FARGE[r["status"]]),
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
      {f'-{f(cc.currentDrawdownPct, 1)} % fra topp' if cc else '—'}</span>
  </div>
  {bar('CORRECTION', r['correctionScore'],
       f"{r['correctionScore']:.0f} · PCTL {r['correctionPercentile']:.0f}"
       + ('*' if r['tynnHistorikk'] else ''), DC['gul'])}
  {bar('TREND', r['trendScore'], f"{r['trendScore']:.0f} · {r['trendBand']}", DC['blaa'])}
  {bar('RECOVERY', r['recoveryScore'],
       f"{r['recoveryScore']:.0f} · {r['recoveryBand'].replace('RECOVERY', '').strip() or 'NONE'}",
       DC['gronn'])}
  <div style="display:flex;gap:16px;margin-top:12px;font-family:{MONO};
       font-size:10px;letter-spacing:0.08em;color:{DC['svak']};">
    <span>FASE <span style="color:{DC['tekst']};">{cc.phase if cc else '—'}</span></span>
    <span>SEVERITY <span style="color:{STATUS_FARGE[r['status']]};">{(cc.severity if cc else '—').replace('_', ' ')}</span></span>
    <span>FART <span style="color:{DC['tekst']};">{f(cc.correctionVelocity, 2) if cc else '—'} %/d</span></span>
  </div>
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


# §9: de seks tegnene brukeren skal kunne lese av. Fire av dem er hele
# grupper fra Turn Score, to er enkeltkriterier som er lette å kjenne igjen
# på grafen. Scanneren skal være forklarbar.
TIDLIG_ETIKETTER = [
    ("gruppe", "A", "Fallmomentum avtar", "Fallmomentum avtar ikke"),
    ("gruppe", "B", "Bunnreaksjon", "Ingen bunnreaksjon"),
    ("gruppe", "C", "Kort momentum positivt", "Kort momentum ikke positivt"),
    ("gruppe", "D", "Volumstøtte", "Ingen volumstøtte"),
    ("kriterium", "higherLow", "Higher low etablert", "Higher low ikke etablert"),
    ("kriterium", "sma20Reclaim", "SMA20 reclaimet", "SMA20 ikke reclaimet"),
]


def tidlig_html(r: dict) -> str:
    """Hvorfor det tidlige laget mener risikoen er interessant."""
    e = r.get("early") or {}
    if not e.get("turnScore") and not e.get("aktiv"):
        return ""

    grupper, kriterier = e.get("grupper", {}), e.get("kriterier", {})
    rader, aktive = [], 0
    for slag, nokkel, ja, nei in TIDLIG_ETIKETTER:
        truffet = (grupper.get(nokkel, 0) > 0 if slag == "gruppe"
                   else bool(kriterier.get(nokkel)))
        aktive += int(truffet)
        rader.append(
            f'<div style="display:flex;gap:8px;align-items:center;padding:4px 0;'
            f'font-size:12px;color:{DC["dempet"] if truffet else DC["svakest"]};">'
            f'<span style="color:{DC["gronn"] if truffet else DC["kant"]};">'
            f'{"✓" if truffet else "○"}</span><span>{ja if truffet else nei}</span>'
            f'</div>')

    if e.get("fallingKnife"):
        sperre = (f'<div style="margin-top:8px;font-size:12px;color:{DC["orange"]};">'
                  f'⚠ Falling Knife Guard aktiv — fallet akselererer fortsatt. '
                  f'Ingen LYTTEPOST.</div>')
    elif e.get("bruddGrunner"):
        sperre = (f'<div style="margin-top:8px;font-size:12px;color:{DC["roed"]};">'
                  f'⛔ Brutt: {", ".join(e["bruddGrunner"])}.</div>')
    else:
        sperre = ""

    tittel = STATUS_TEKST.get(e.get("status"), "INGEN TIDLIG STATUS")
    if e.get("styrke"):
        tittel += f" – {e['styrke']}"

    return (
        f'<div style="margin-bottom:14px;">'
        f'<div style="font-family:{MONO};font-size:10px;letter-spacing:0.1em;'
        f'color:{DC["svak"]};margin-bottom:4px;">TIDLIG LAG · {tittel}</div>'
        f'<div style="display:flex;gap:18px;margin-bottom:8px;">'
        f'<span style="font-family:{MONO};font-size:12px;color:{DC["dempet"]};">'
        f'Turn <b style="color:{DC["tekst"]};font-size:16px;">'
        f'{e.get("turnScore", 0)}</b></span>'
        f'<span style="font-family:{MONO};font-size:12px;color:{DC["dempet"]};">'
        f'Entry <b style="color:{DC["tekst"]};font-size:16px;">'
        f'{e.get("entryValue", 0)}</b></span>'
        f'<span style="font-family:{MONO};font-size:12px;color:{DC["dempet"]};">'
        f'Opportunity <b style="color:{DC["tekst"]};font-size:16px;">'
        f'{e.get("opportunityScore", 0):.0f}</b></span></div>'
        + "".join(rader)
        + f'<div style="margin-top:6px;font-family:{MONO};font-size:11px;'
          f'color:{DC["svak"]};">{aktive}/6 tidlige signaler aktive</div>'
        + sperre
        + f'<div style="margin-top:8px;font-size:11px;color:{DC["svakest"]};'
          f'line-height:1.5;">Det tidlige laget kommer før full '
          f'reversal-bekreftelse og tåler høyere feilrate. Det er ingen '
          f'kjøpsanbefaling.</div></div>')


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
    # Ligger toppen før en corporate action, er den vist på justert grunnlag
    ca = next((h for h in r.get("corporateActions", [])
               if str(cc.peakDate) < h["exDato"]), None)
    z = r.get("supportSone")
    if z:
        stotte = (f'<span style="font-family:{MONO};">Støttesone {f(z["nivå"])} '
                  f'({f(z["avstandPct"], 1)} % under kurs, {z["treff"]} '
                  f'reaksjon{"er" if z["treff"] > 1 else ""}: '
                  f'{", ".join(z["kilder"])})</span>')
    else:
        stotte = (f'<span style="color:{DC["orange"]};">Ingen relevant støtte innen '
                  f'{f(r["supportVindu"], 1)} % — støttekomponenten er 0.</span>')

    return (
        f'<div style="display:flex;gap:16px;flex-wrap:wrap;">'
        f'{punkt("TOPP", f(cc.peakPrice), f"{cc.peakDate} · {cc.daysSincePeak} d siden")}{pil}'
        f'{punkt("BUNN", f(cc.troughPrice), f"{cc.troughDate} · {cc.daysPeakToTrough} d fall", DC["roed"])}{pil}'
        f'{punkt("NÅ", f(cc.currentPrice), f"{cc.daysSinceTrough} d siden bunn")}</div>'
        f'<div style="margin-top:12px;font-size:12px;color:{DC["dempet"]};'
        f'line-height:1.6;">Dybde topp→bunn <b>-{f(cc.maxDrawdownPct, 1)} %</b>, '
        f'nå <b>-{f(cc.currentDrawdownPct, 1)} %</b> fra topp. '
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
        + (f'<div style="margin-top:10px;font-size:11px;color:{DC["svak"]};'
           f'line-height:1.5;">Kurser før {ca["exDato"]} er skalert med '
           f'{ca["faktor"]} — {_esc(ca.get("kort", "corporate action"))}. Toppen vises '
           f'derfor på justert grunnlag, sammenlignbart med dagens kurs. '
           f'NÅ er faktisk markedskurs.</div>' if ca else "")
    )


def historikk_html(r: dict) -> str:
    h = r["historiskeKorreksjoner"]
    if not h:
        return (f'<div style="color:{DC["svak"]};font-size:12px;">'
                f'Ingen avsluttede korreksjoner funnet i historikken.</div>')
    cc = r.get("currentCorrection")
    naa = cc.maxDrawdownPct if cc else None
    rader = []
    if cc is not None:
        # §26: den aktive korreksjonen vises med sin maksimale dybde, ikke
        # med dagens drawdown — ellers krymper historikken når kursen henter
        # seg inn og sammenligningen blir feil.
        rader.append(
            f'<tr style="color:{DC["roed"]};">'
            f'<td style="padding:6px 8px 6px 0;font-family:{MONO};font-size:11px;">'
            f'{cc.peakDate}</td>'
            f'<td style="padding:6px 8px 6px 0;font-family:{MONO};font-size:11px;">'
            f'{cc.troughDate}{"" if cc.barsSinceTrough > 0 else " (i dag)"}</td>'
            f'<td style="padding:6px 8px 6px 0;font-family:{MONO};font-size:12px;'
            f'text-align:right;">-{cc.maxDrawdownPct:.1f} %</td>'
            f'<td style="padding:6px 8px 6px 0;font-family:{MONO};font-size:11px;'
            f'text-align:right;">{cc.daysPeakToTrough} d</td>'
            f'<td style="padding:6px 0;font-family:{MONO};font-size:11px;'
            f'text-align:right;">NÅ</td></tr>'
            f'<tr style="color:{DC["svak"]};"><td colspan="2" style="padding:0 8px 8px 0;'
            f'font-size:11px;">Nå -{cc.currentDrawdownPct:.1f} % fra topp · '
            f'{cc.daysSinceTrough} dager siden bunn</td>'
            f'<td colspan="3"></td></tr>')
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
                f'text-align:right;">{f"-{f(cc.currentDrawdownPct, 1)} %" if cc else "—"}</div>'
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
            + (f'{cc.daysSincePeak} dager siden topp · korreksjon {_esc(cc.correctionId)}' if cc else '—')
            + f'</div></div><div style="text-align:right;">{badge(r["status"])}{gate}'
            + (f'<div style="font-family:{MONO};font-size:11px;color:{DC["svak"]};'
               f'margin-top:6px;">{detalj}</div>' if detalj else '')
            + f'</div></div>'
            f'<div style="display:grid;grid-template-columns:repeat(4,minmax(0,1fr));'
            f'gap:12px;margin-top:16px;">'
            + maaler("DRAWDOWN NÅ", f"-{f(cc.currentDrawdownPct, 1)} %" if cc else "—",
                     f"max -{f(cc.maxDrawdownPct, 1)} %" if cc else "",
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
        return sorted(resultater, key=lambda r: -(r["currentCorrection"].currentDrawdownPct
                                                  if r.get("currentCorrection") else -999))
    if valg == "Opportunity Score":
        return sorted(resultater,
                      key=lambda r: -((r.get("early") or {}).get("opportunityScore") or -1))
    felt = SORTERINGSVALG.get(valg)
    if felt:
        return sorted(resultater, key=lambda r: -r[felt])
    # §10: LYTTEPOST rangeres på Opportunity Score, ikke Turn Score alene.
    # Ellers ville den bekreftede, men utstrakte aksjen alltid ligge over den
    # tidlige — som er stikk i strid med hensikten med laget.
    def nokkel(r):
        tidlig = r.get("early") or {}
        opp = tidlig.get("opportunityScore")
        return (STATUS_PRIORITY.get(r["status"], 99),
                -(opp if opp is not None and r["status"] in
                  (STATUS_LYTTEPOST, STATUS_BOTTOM_WATCH) else r["correctionScore"]))

    return sorted(resultater, key=nokkel)


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


def _hoyrepanel(r: dict, fund_store: dict, dstatus: dict = None) -> bool:
    """Detaljpanelet. Returnerer True hvis fundamental-sjekken ble endret."""
    with st.container(key="panel"):
        st.html(panel_topp_html(r))
        bak = (dstatus or {}).get("etterslep", {}).get(r["ticker"])
        if bak:
            st.html(f'<div style="background:{_rgba(DC["orange"], 0.1)};'
                    f'border:1px solid {_rgba(DC["orange"], 0.4)};border-radius:6px;'
                    f'padding:8px 11px;margin-top:10px;font-size:12px;'
                    f'color:{DC["orange"]};">⚠ Siste data for {_esc(r["Ticker"])} er '
                    f'{bak.strftime("%d.%m.%Y")} — tallene under er ikke oppdaterte.</div>')

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
            st.html(tidlig_html(r)
                    + kriterieliste_html("TREND SCORE", r["trendScore"], r["trendDeler"],
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


def _kildediagnose(diag: dict, dstatus: dict) -> None:
    """
    Hva hver kilde faktisk svarte, per ticker.

    Uten dette svelges feilene i logger som ikke er synlige fra appen, og
    feilsøking blir gjetting.
    """
    rader = []
    for t in sorted(k for k in diag if not k.startswith("_")):
        d = diag[t]
        rader.append({
            "Ticker": t.replace(".OL", ""),
            "Yahoo batch": d.get("yahooBatch", "—"),
            "Yahoo period": d.get("yahooPeriod", "—"),
            "Stooq": d.get("stooq", "—"),
            "Intradag": d.get("intradag", "—"),
            "Brukt": d.get("endelig", "—"),
        })
    if not rader:
        return

    with st.expander("DATAKILDER — DIAGNOSE"):
        st.caption(
            f"Forventet siste avsluttede handelsdag: {dstatus['forventet']} · "
            f"HTTP-klient: {diag.get('_sesjon', '?')}. "
            "«Yahoo batch» er den lange datointervall-forespørselen, "
            "«Yahoo period» den korte, «Stooq» andrekilden (av som "
            "standard), «Intradag» rekonstruksjon av dager Yahoo leverte "
            "som null-barer."
        )
        st.dataframe(pd.DataFrame(rader), width="stretch", hide_index=True,
                     height=min(len(rader) * 36 + 40, 400))

        st.caption("Test én ticker direkte mot begge kilder:")
        c = st.columns([2, 1])
        valgt = c[0].selectbox("Ticker", sorted(k for k in diag if not k.startswith("_")),
                               label_visibility="collapsed")
        if c[1].button("TEST KILDER", width="stretch"):
            _kildetest(valgt)


def _kildetest(ticker: str) -> None:
    """Kjør ticker mot hver kilde og vis råsvaret. Ren feilsøking."""
    session = _lag_session()
    st.write(f"**{ticker}** — curl_cffi: {session is not None}")

    try:
        y = _hent_siste_dager(ticker, session)
        st.write(f"Yahoo period=10d: "
                 + (f"{len(y)} rader, siste {_bar_dato(y)}" if y is not None and not y.empty
                    else "tomt svar"))
    except Exception as e:
        st.write(f"Yahoo period=10d feilet: `{type(e).__name__}: {e}`")

    url = STOOQ_URL.format(symbol=stooq_symbol(ticker))
    st.write(f"Stooq URL: `{url}`")
    try:
        tekst = _hent_url(url, session, SCANNER_CONFIG["data"]["stooqTimeout"])
        if not tekst:
            st.write("Stooq: ingen respons")
        else:
            st.code("\n".join(tekst.split("\n")[:4]), language="text")
            s_df = parse_stooq_csv(tekst)
            st.write(f"Tolket: "
                     + (f"{len(s_df)} rader, siste {_bar_dato(s_df)}"
                        if s_df is not None else "kunne ikke tolkes som CSV"))
    except Exception as e:
        st.write(f"Stooq feilet: `{type(e).__name__}: {e}`")


def _fotnote() -> None:
    c = SCANNER_CONFIG
    with st.expander("SLIK VIRKER RADAREN"):
        st.markdown(f"""
**Kjerneprinsippet:** ingen felles prosentgrense. Hver aksje sammenlignes med sin egen
historikk av korreksjoner, funnet med ATR-normalisert ZigZag
(terskel {c['swing']['atrMultiplier']} × ATR14). Et fall på 7 % kan være en stor DNB-korreksjon
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

    forventet_dag = siste_avsluttede_handelsdag()
    prisdata, kildediag = hent_prisdata(tuple(sorted(aktive)), forventet_dag)
    prisdata = rens_prisdata(prisdata)
    # Historikken må stå på samme grunnlag som dagens kurs før noe sammenlignes
    prisdata = juster_corporate_actions(prisdata)
    dstatus = datastatus(prisdata)
    resultater = (kjor_scan(prisdata, st.session_state.fundamentals,
                            st.session_state.radar_state) if prisdata else [])

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
                  f'padding-top:9px;text-align:right;">SKANNET {oslo} OSLO</div>')
        if c[2].button("SCAN NÅ", key="scan", width="stretch"):
            st.cache_data.clear()
            st.rerun()

        st.html(datobanner(dstatus))
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
        if dstatus["stale"]:
            _kildediagnose(kildediag, dstatus)
        _fotnote()

    with panel:
        if _hoyrepanel(valgt_r, st.session_state.fundamentals, dstatus):
            st.rerun()


if __name__ == "__main__":
    main()
