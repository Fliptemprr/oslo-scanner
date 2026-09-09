"""
Historisk replay av Early Entry-laget. Diagnostikk, ikke en del av appen.

Kjører samme motor dag for dag gjennom historikken, der hver dag kun ser
barer til og med den dagen. Ingen look-ahead, verken i indikatorer, swings
eller percentiler.

    python replay.py                       # KIT, NOD, KOG siste 12 mnd
    python replay.py --dager 126           # siste 6 mnd
    python replay.py --tickere KIT.OL      # én aksje
    python replay.py --alle                # hele tabellen, ikke bare utdrag
    python replay.py --csv replay          # skriver replay_KIT.OL.csv osv.

Krever nett. Rører verken scoring, terskler eller statuslogikk.
"""

import argparse
import sys
from datetime import datetime, timedelta

import pandas as pd

import scanner as S

STANDARD = ["KIT.OL", "NOD.OL", "KOG.OL"]


def hent(tickere: list) -> dict:
    """Samme datakjede som appen: nedlasting, hulltetting, rens, justering."""
    session = S._lag_session()
    now = datetime.now()
    alle = S._download_batch(list(tickere), session,
                             now - timedelta(days=S.HISTORY_DAYS),
                             now + timedelta(days=2))
    mangler = [t for t in tickere if t not in alle]
    if mangler:
        print(f"Ingen data for {', '.join(mangler)} — hoppes over.")
    forventet = S.siste_avsluttede_handelsdag()
    alle, _ = S.backfill_manglende_dager(alle, forventet, session, {})
    return S.juster_corporate_actions(S.rens_prisdata(alle))


def tabell(rader: list) -> pd.DataFrame:
    """Kolonnene slik de ble bestilt, i lesbar form."""
    df = pd.DataFrame([{
        "DATO": r["dato"],
        "KURS": round(r["kurs"], 2) if r["kurs"] is not None else None,
        "DD FRA TOPP": (round(-r["drawdownFraTopp"], 1)
                        if r["drawdownFraTopp"] is not None else None),
        "FRA BUNN": (round(r["fraLokalBunn"], 1)
                     if r["fraLokalBunn"] is not None else None),
        "TURN": r["turnScore"],
        "ENTRY": r["entryValue"],
        "OPP": r["opportunityScore"],
        "SIGNALER": r["aktiveSignaler"],
        "STATUS": S.STATUS_TEKST.get(r["status"], r["status"])
                  + (f" {r['styrke']}" if r["styrke"] else "")
                  + (" *KNIV" if r["fallingKnife"] else ""),
    } for r in rader])
    return df


def endringer(rader: list) -> list:
    """Kun dagene der statusen faktisk endret seg."""
    ut, forrige = [], None
    for r in rader:
        if r["status"] != forrige:
            ut.append(r)
            forrige = r["status"]
    return ut


def forsprang(rader: list, maks_tilbake: int = 30) -> list:
    """
    Kjernespørsmålet: kommer det tidlige laget faktisk før STABILIZING?

    For hver gang STABILIZING inntreffer, gå bakover i samme nedtur og finn
    første dag med BOTTOM WATCH og første dag med LYTTEPOST. Rapporter dager
    tidligere og kursforskjell. Negativ kursforskjell betyr at det tidlige
    signalet kom til en lavere kurs, altså en bedre inngang.
    """
    ut = []
    for i, r in enumerate(rader):
        if r["status"] != S.STATUS_STABILIZING:
            continue
        if i > 0 and rader[i - 1]["status"] in (S.STATUS_STABILIZING,
                                                S.STATUS_REVERSAL):
            continue

        # Første og siste treff skiller to ulike spørsmål: hvor tidlig laget
        # først ropte (og hvor mye kursen falt videre etterpå), mot hvilken
        # inngang det faktisk ga rett før vendingen ble bekreftet.
        forste, siste = {}, {}
        j = i - 1
        while j >= 0 and i - j <= maks_tilbake:
            if rader[j]["status"] in (S.STATUS_STABILIZING, S.STATUS_REVERSAL):
                break
            st = rader[j]["status"]
            if st in (S.STATUS_BOTTOM_WATCH, S.STATUS_LYTTEPOST):
                forste[st] = (i - j, rader[j])
                siste.setdefault(st, (i - j, rader[j]))
            j -= 1

        rad = {"stabDato": r["dato"], "stabKurs": r["kurs"]}
        for status, navn in ((S.STATUS_LYTTEPOST, "lp"),
                             (S.STATUS_BOTTOM_WATCH, "bw")):
            for kilde, suffiks in ((forste, "Forste"), (siste, "Siste")):
                if status in kilde:
                    dager, tidlig = kilde[status]
                    rad[navn + suffiks] = (dager, tidlig["dato"], tidlig["kurs"],
                                           (tidlig["kurs"] / r["kurs"] - 1) * 100)
                else:
                    rad[navn + suffiks] = None
        ut.append(rad)
    return ut


def skriv_forsprang(rader: list) -> None:
    fs = forsprang(rader)
    if not fs:
        print("\n  FORSPRANG: ingen STABILIZING i perioden.")
        return

    print("\n  FORSPRANG FØR STABILIZING")
    print("    Negativ kursforskjell = signalet ga lavere inngang enn "
          "STABILIZING, altså bedre.")
    print("    «Første» er første rop i nedturen, «siste» er signalet rett "
          "før vendingen ble bekreftet.")
    print(f"\n    {'STABILIZING':12s} {'KURS':>8s}   "
          f"{'LP FØRSTE':>24s} {'LP SISTE':>24s}")
    for f in fs:
        biter = []
        for navn in ("lpForste", "lpSiste"):
            v = f[navn]
            biter.append(f"{'—':>24s}" if v is None else
                         f"{v[0]:2d} d · {v[2]:7.2f} · {v[3]:+6.1f} %")
        print(f"    {str(f['stabDato']):12s} {f['stabKurs']:8.2f}   "
              f"{biter[0]:>24s} {biter[1]:>24s}")

    for navn, etikett in (("lpForste", "LYTTEPOST første"),
                          ("lpSiste", "LYTTEPOST siste"),
                          ("bwForste", "BOTTOM WATCH første"),
                          ("bwSiste", "BOTTOM WATCH siste")):
        traff = [f[navn] for f in fs if f[navn]]
        if traff:
            snitt_d = sum(t[0] for t in traff) / len(traff)
            snitt_k = sum(t[3] for t in traff) / len(traff)
            bedre = sum(1 for t in traff if t[3] < 0)
            print(f"    {etikett:22s} {len(traff)}/{len(fs)} nedturer · "
                  f"snitt {snitt_d:4.1f} d før · snitt {snitt_k:+6.1f} % · "
                  f"bedre inngang i {bedre}/{len(traff)}")
        else:
            print(f"    {etikett:22s} kom aldri før STABILIZING")


def skriv_milepaeler(rader: list) -> None:
    m = S.replay_milepaeler(rader)
    print("\n  FØRSTE GANG:")
    for status, tekst in S.REPLAY_MILEPAELER:
        r = m.get(status)
        if r:
            print(f"    {tekst:24s} {r['dato']}  kurs {r['kurs']:.2f} · "
                  f"Turn {r['turnScore']} · Entry {r['entryValue']} · "
                  f"Opp {r['opportunityScore']}")
        else:
            print(f"    {tekst:24s} — inntraff aldri i perioden")

    tek = m.get("REVERSAL_TEKNISK")
    if tek and not m.get(S.STATUS_REVERSAL):
        print(f"\n    REVERSAL teknisk oppfylt {tek['dato']} (kurs "
              f"{tek['kurs']:.2f}), men blokkert av fundamental gate.")
        print("    Den gaten er en manuell avkryssing og finnes ikke i "
              "historikk, så REVERSAL\n    kan i praksis ikke utløses i et "
              "replay.")


def main() -> int:
    ap = argparse.ArgumentParser(description="Historisk replay av Early Entry")
    ap.add_argument("--tickere", nargs="+", default=STANDARD)
    ap.add_argument("--dager", type=int, default=252,
                    help="handelsdager tilbake (252 ≈ 12 mnd, 126 ≈ 6 mnd)")
    ap.add_argument("--alle", action="store_true",
                    help="vis hver dag, ikke bare statusendringer")
    ap.add_argument("--csv", metavar="PREFIKS",
                    help="skriv full tabell til PREFIKS_<ticker>.csv")
    a = ap.parse_args()

    pd.set_option("display.width", 200)
    pd.set_option("display.max_rows", 400)

    print(f"Henter data for {', '.join(a.tickere)} ...")
    data = hent(a.tickere)
    if not data:
        print("Ingen data. Avbryter.")
        return 1

    for t in a.tickere:
        df = data.get(t)
        if df is None or df.empty:
            continue
        print("\n" + "=" * 100)
        print(f"{t} · replay av {a.dager} handelsdager · "
              f"kun data t.o.m. hver enkelt dag")
        print("=" * 100)

        rader = S.replay_ticker(t, df, a.dager)
        if not rader:
            print("  For kort historikk til replay.")
            continue

        full = tabell(rader)
        if a.csv:
            filnavn = f"{a.csv}_{t}.csv"
            full.to_csv(filnavn, index=False)
            print(f"  Skrev {len(full)} rader til {filnavn}")

        vis = full if a.alle else tabell(endringer(rader))
        merknad = "alle dager" if a.alle else "kun dager der statusen endret seg"
        print(f"\n  {len(rader)} dager replayet, {rader[0]['dato']} → "
              f"{rader[-1]['dato']}. Viser {merknad}.\n")
        print(vis.to_string(index=False))

        fordeling = {}
        for r in rader:
            navn = S.STATUS_TEKST.get(r["status"], r["status"])
            fordeling[navn] = fordeling.get(navn, 0) + 1
        print("\n  DAGER PER STATUS: "
              + " · ".join(f"{k} {v}" for k, v in sorted(fordeling.items(),
                                                         key=lambda x: -x[1])))
        skriv_milepaeler(rader)
        skriv_forsprang(rader)

    print("\nKursene er justert for corporate actions, slik motoren ser dem.")
    print("For KOG betyr det at barer før fisjonen 15.04.2026 er skalert.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
