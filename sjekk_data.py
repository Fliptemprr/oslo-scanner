"""
Diagnose av datakildene. Kjøres lokalt, der nettet ikke er blokkert.

    pip install yfinance curl_cffi pandas
    python sjekk_data.py

Skriver ut hva hver kilde faktisk svarer for hver ticker i watchlisten, slik
at vi vet om det er Yahoo, Stooq eller koden som svikter.
"""

import io
import sys
from datetime import datetime, timedelta

import pandas as pd

TICKERE = ["KOG.OL", "NOD.OL", "KIT.OL", "PROT.OL", "DNB.OL", "WAWI.OL", "NAS.OL"]
STOOQ = "https://stooq.com/q/d/l/?s={}&i=d"


def sesjon():
    try:
        from curl_cffi import requests as cr
        return cr.Session(impersonate="chrome"), "curl_cffi"
    except Exception as e:
        return None, f"ikke tilgjengelig ({type(e).__name__})"


def siste_dato(df):
    if df is None or len(df) == 0:
        return None
    return pd.Timestamp(df.index[-1]).date()


def flat(df):
    if df is not None and isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = df.columns.get_level_values(0)
    return df


def yahoo_intervall(t, s):
    import yfinance as yf
    now = datetime.now()
    df = yf.download(t, start=now - timedelta(days=1140), end=now + timedelta(days=2),
                     progress=False, auto_adjust=True, threads=False, session=s,
                     timeout=30)
    return flat(df.dropna(how="all")) if df is not None and not df.empty else None


def yahoo_period(t, s):
    import yfinance as yf
    df = yf.download(t, period="10d", interval="1d", progress=False,
                     auto_adjust=True, threads=False, session=s, timeout=20)
    return flat(df.dropna(how="all")) if df is not None and not df.empty else None


def stooq(t, s):
    url = STOOQ.format(t.lower())
    try:
        if s is not None:
            tekst = s.get(url, timeout=20).text
        else:
            from urllib import request
            with request.urlopen(url, timeout=20) as r:
                tekst = r.read().decode("utf-8", "replace")
    except Exception as e:
        return None, f"feil: {type(e).__name__}: {e}"[:90]

    forste = tekst.split("\n")[0][:70]
    if "Date,Open" not in forste:
        return None, f"ikke CSV: {forste}"
    df = pd.read_csv(io.StringIO(tekst))
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date"]).set_index("Date").sort_index()
    return df, "ok"


def main():
    s, klient = sesjon()
    print(f"HTTP-klient: {klient}")
    print(f"Lokal tid:   {datetime.now():%Y-%m-%d %H:%M}")
    print()
    print(f"{'TICKER':10s} {'YAHOO INTERVALL':17s} {'YAHOO PERIOD':15s} "
          f"{'STOOQ':13s} {'SISTE KURS':>11s}  MERKNAD")
    print("-" * 96)

    for t in TICKERE:
        rad, merknad = {}, ""
        for navn, fn in (("intervall", yahoo_intervall), ("period", yahoo_period)):
            try:
                df = fn(t, s)
                rad[navn] = siste_dato(df)
                if navn == "period" and df is not None:
                    rad["kurs"] = float(df["Close"].iloc[-1])
            except Exception as e:
                rad[navn] = None
                merknad += f"yahoo-{navn}: {type(e).__name__} "
        try:
            sdf, smerk = stooq(t, s)
            rad["stooq"] = siste_dato(sdf)
            if smerk != "ok":
                merknad += smerk
            elif sdf is not None:
                rad["stooq_kurs"] = float(sdf["Close"].iloc[-1])
        except Exception as e:
            rad["stooq"] = None
            merknad += f"stooq: {type(e).__name__} "

        kurs = rad.get("kurs") or rad.get("stooq_kurs")
        print(f"{t:10s} {str(rad.get('intervall')):17s} {str(rad.get('period')):15s} "
              f"{str(rad.get('stooq')):13s} {kurs if kurs else '—':>11} "
              f" {merknad.strip()}")

    print()
    print("Send hele denne utskriften tilbake. Kolonnene viser hvilken dato hver")
    print("kilde faktisk leverte som siste bar.")


if __name__ == "__main__":
    sys.exit(main())
