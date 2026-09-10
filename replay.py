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
import copy
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


SIGNALNAVN = {
    "A": "fallmomentum avtar", "B": "bunnreaksjon",
    "C": "kort momentum", "D": "volumstøtte",
    "higherLow": "higher low", "sma20Reclaim": "SMA20 reclaim",
}


def over_bunn(kurs: float, bunn: float) -> float:
    return (kurs / bunn - 1) * 100


def aktive_navn(r: dict) -> str:
    return ", ".join(SIGNALNAVN[k] for k, v in (r.get("signaler") or {}).items()
                     if v) or "ingen"


def analyser_episode(rader: list, ep: dict, dager_etter: int = 60) -> dict:
    """
    Recoveryfasen etter en registrert bunn, med milepæler og etterpåutvikling.

    Bunnen er funnet på hele serien, altså med etterpåklokskap — den er fasit,
    ikke input. Modellen som replayes har kun sett data til og med hver enkelt
    dag.
    """
    bunn = ep["troughPrice"]
    etter = [r for r in rader if str(r["dato"]) >= ep["troughDate"]][:dager_etter + 1]
    if not etter:
        return {"episode": ep, "dager": [], "lyttepost": None, "reversal": None,
                "bottomWatch": None, "falskeStarter": [], "brutt": []}

    def forste(pred):
        return next((r for r in etter if pred(r)), None)

    lp = forste(lambda r: r["status"] == S.STATUS_LYTTEPOST)
    bw = forste(lambda r: r["status"] == S.STATUS_BOTTOM_WATCH)
    # Lagets egen dom, før presedensregelen. STABILIZING vinner over
    # LYTTEPOST, så laget kan ha sagt LYTTEPOST uten at det ble vist.
    lp_eget = forste(lambda r: r["tidligStatus"] == S.STATUS_LYTTEPOST)
    rev = forste(lambda r: r["status"] == S.STATUS_REVERSAL)
    rev_tek = forste(lambda r: r["reversalTeknisk"])
    stab = forste(lambda r: r["status"] == S.STATUS_STABILIZING)

    # Falske starter: en LYTTEPOST som senere brytes, eller der bunnen ryker
    starter, falske = [], []
    forrige = None
    for i, r in enumerate(etter):
        if r["status"] == S.STATUS_LYTTEPOST and forrige != S.STATUS_LYTTEPOST:
            starter.append((i, r))
        forrige = r["status"]

    for i, r in starter:
        senere = etter[i + 1:]
        brutt = next((x for x in senere
                      if x["status"] == S.STATUS_LYTTEPOST_BRUTT), None)
        under_bunn = next((x for x in senere if x["kurs"] < bunn), None)
        if brutt or under_bunn:
            falske.append({"signal": r, "brutt": brutt, "underBunn": under_bunn})

    return {
        "episode": ep, "dager": etter,
        "bottomWatch": bw, "lyttepost": lp, "lytteposEget": lp_eget,
        "reversal": rev,
        "reversalTeknisk": rev_tek, "stabilizing": stab,
        "starter": [r for _, r in starter], "falskeStarter": falske,
    }


def etterpaa(etter: list, fra: dict, horisonter=(5, 10, 20)) -> str:
    """Hva skjedde med kursen etter signalet."""
    if fra is None:
        return "—"
    i = next((k for k, r in enumerate(etter) if r["dato"] == fra["dato"]), None)
    if i is None:
        return "—"
    biter = []
    for h in horisonter:
        if i + h < len(etter):
            biter.append(f"+{h}d {over_bunn(etter[i + h]['kurs'], fra['kurs']):+.1f} %")
    resten = etter[i + 1:]
    if resten:
        laveste = min(r["kurs"] for r in resten)
        biter.append(f"laveste etterpå {laveste:.2f} "
                     f"({over_bunn(laveste, fra['kurs']):+.1f} %)")
    return " · ".join(biter) if biter else "—"


def skriv_episode(a: dict, dager_etter: int) -> None:
    ep, etter = a["episode"], a["dager"]
    bunn = ep["troughPrice"]
    merke = " (aktiv)" if ep["aktiv"] else ""
    print("\n" + "-" * 100)
    print(f"KORREKSJON {ep['peakDate']} → {ep['troughDate']}{merke} · "
          f"topp {ep['peakPrice']:.2f} → bunn {bunn:.2f} · "
          f"-{ep['drawdownPct']:.1f} % · {ep['dager']} dager")
    print("-" * 100)
    if not etter:
        print("  Ingen replayede dager etter bunnen.")
        return

    print(f"\n  {'DATO':12s} {'KURS':>8s} {'% O/BUNN':>9s} {'TURN':>5s} "
          f"{'ENTRY':>6s} {'SIG':>4s}  {'VIST STATUS':18s} {'LAGET SELV':14s} "
          f"AKTIVE SIGNALER")
    forrige = None
    for r in etter:
        if r["status"] == forrige and r["status"] not in (
                S.STATUS_LYTTEPOST, S.STATUS_LYTTEPOST_BRUTT):
            continue
        forrige = r["status"]
        eget = S.STATUS_TEKST.get(r["tidligStatus"], "—")
        print(f"  {str(r['dato']):12s} {r['kurs']:8.2f} "
              f"{over_bunn(r['kurs'], bunn):8.1f} % {r['turnScore']:5d} "
              f"{r['entryValue']:6d} {r['antallSignaler']:4d}  "
              f"{S.STATUS_TEKST.get(r['status'], r['status']):18s} "
              f"{eget:14s} {aktive_navn(r)}")

    print("\n  MILEPÆLER FRA BUNN "
          f"{ep['troughDate']} ({bunn:.2f}):")
    for navn, r in (("BOTTOM WATCH", a["bottomWatch"]),
                    ("LYTTEPOST vist", a["lyttepost"]),
                    ("LYTTEPOST laget", a["lytteposEget"]),
                    ("STABILIZING", a["stabilizing"]),
                    ("REVERSAL", a["reversal"]),
                    ("REVERSAL teknisk", a["reversalTeknisk"])):
        if r is None:
            print(f"    {navn:18s} — inntraff ikke")
            continue
        dager = next(k for k, x in enumerate(etter) if x["dato"] == r["dato"])
        print(f"    {navn:18s} {r['dato']} · {r['kurs']:8.2f} · "
              f"{over_bunn(r['kurs'], bunn):+6.1f} % over bunn · "
              f"{dager:2d} handelsdager etter bunn · Turn {r['turnScore']} · "
              f"Entry {r['entryValue']}")
        if navn.startswith("LYTTEPOST"):
            print(f"    {'':18s} aktive signaler: {aktive_navn(r)}")

    lp = a["lyttepost"] or a["lytteposEget"]
    rt = a["reversalTeknisk"]
    if lp and rt:
        d_lp, d_rt = over_bunn(lp["kurs"], bunn), over_bunn(rt["kurs"], bunn)
        i_lp = next(k for k, x in enumerate(etter) if x["dato"] == lp["dato"])
        i_rt = next(k for k, x in enumerate(etter) if x["dato"] == rt["dato"])
        print(f"\n    FORSPRANG: LYTTEPOST {d_lp:+.1f} % over bunn mot "
              f"REVERSAL {d_rt:+.1f} % → {d_rt - d_lp:+.1f} prosentpoeng "
              f"billigere, {i_rt - i_lp} handelsdager tidligere")

    print(f"\n  ETTERPÅ:")
    print(f"    etter LYTTEPOST   {etterpaa(etter, lp)}")
    print(f"    etter REVERSAL    {etterpaa(etter, rt)}")

    if a["falskeStarter"]:
        print(f"\n  FALSKE STARTER: {len(a['falskeStarter'])} av "
              f"{len(a['starter'])} LYTTEPOST-signaler")
        for f in a["falskeStarter"]:
            grunn = ("brutt " + str(f["brutt"]["dato"]) if f["brutt"]
                     else "kurs under bunn " + str(f["underBunn"]["dato"]))
            print(f"    {f['signal']['dato']} · {f['signal']['kurs']:.2f} "
                  f"({over_bunn(f['signal']['kurs'], bunn):+.1f} %) → {grunn}")
    elif a["starter"]:
        print(f"\n  FALSKE STARTER: ingen av {len(a['starter'])} "
              f"LYTTEPOST-signaler feilet")


def kjor_korreksjoner(t: str, df, dager: int, dager_etter: int,
                      min_dybde: float) -> None:
    episoder = [e for e in S.korreksjonsepisoder(t, df)
                if e["drawdownPct"] >= min_dybde]
    if not episoder:
        print(f"  Ingen registrerte korreksjoner dypere enn {min_dybde} %.")
        return

    rader = S.replay_ticker(t, df, dager)
    if not rader:
        print("  For kort historikk til replay.")
        return
    forste_replay = str(rader[0]["dato"])
    brukbare = [e for e in episoder if e["troughDate"] >= forste_replay]
    print(f"  {len(episoder)} registrerte korreksjoner, {len(brukbare)} med "
          f"bunn innenfor replayvinduet ({forste_replay} →).")

    analyser = [analyser_episode(rader, e, dager_etter) for e in brukbare]
    for a in analyser:
        skriv_episode(a, dager_etter)

    print("\n" + "=" * 100)
    print(f"OPPSUMMERING {t}")
    print("=" * 100)
    med_lp = [a for a in analyser if a["lyttepost"] or a["lytteposEget"]]
    vist_lp = [a for a in analyser if a["lyttepost"]]
    med_rt = [a for a in analyser if a["reversalTeknisk"]]
    begge = [a for a in analyser
             if (a["lyttepost"] or a["lytteposEget"]) and a["reversalTeknisk"]]

    def snitt(xs):
        return sum(xs) / len(xs) if xs else None

    lp_pct = [over_bunn((a["lyttepost"] or a["lytteposEget"])["kurs"],
                        a["episode"]["troughPrice"]) for a in med_lp]
    rt_pct = [over_bunn(a["reversalTeknisk"]["kurs"], a["episode"]["troughPrice"])
              for a in med_rt]
    diff = [over_bunn(a["reversalTeknisk"]["kurs"], a["episode"]["troughPrice"])
            - over_bunn((a["lyttepost"] or a["lytteposEget"])["kurs"],
                        a["episode"]["troughPrice"])
            for a in begge]
    falske = sum(len(a["falskeStarter"]) for a in analyser)
    starter = sum(len(a["starter"]) for a in analyser)

    print(f"  Korreksjoner analysert          {len(analyser)}")
    print(f"  LYTTEPOST etter lagets kriterier {len(med_lp)}/{len(analyser)}")
    print(f"  ...og faktisk VIST som status    {len(vist_lp)}/{len(analyser)}"
          + ("   ← STABILIZING vant presedensen" if len(vist_lp) < len(med_lp)
             else ""))
    print(f"  REVERSAL (teknisk) utløst i     {len(med_rt)}/{len(analyser)}")
    if lp_pct:
        print(f"  Snitt LYTTEPOST over bunn       {snitt(lp_pct):+.1f} %")
    if rt_pct:
        print(f"  Snitt REVERSAL over bunn        {snitt(rt_pct):+.1f} %")
    if diff:
        print(f"  Snitt forsprang                 {snitt(diff):+.1f} "
              f"prosentpoeng billigere ({len(diff)} korreksjoner)")
    if starter:
        print(f"  Falske starter                  {falske}/{starter} "
              f"LYTTEPOST-signaler ({falske / starter * 100:.0f} %)")
    print("\n  REVERSAL krever manuell fundamental godkjenning, som ikke finnes")
    print("  i historikk. «REVERSAL teknisk» er derfor sammenligningspunktet:")
    print("  alle tekniske krav oppfylt, kun gaten manglet.")


VARIANTER = [
    ("dagens", {}),
    ("presedens", {"lyttepostForanStabilizing": True}),
    ("entry-decay", {"decayKreverEntry": True}),
    ("begge", {"lyttepostForanStabilizing": True, "decayKreverEntry": True}),
]


def variant_cfg(overstyr: dict) -> dict:
    cfg = copy.deepcopy(S.SCANNER_CONFIG)
    cfg["early"].update(overstyr)
    return cfg


def lp_starter(etter: list) -> list:
    """Hver gang LYTTEPOST slås PÅ, ikke hver dag den står på."""
    ut, forrige = [], None
    for r in etter:
        if r["status"] == S.STATUS_LYTTEPOST and forrige != S.STATUS_LYTTEPOST:
            ut.append(r)
        forrige = r["status"]
    return ut


def maal_variant(t: str, df, episoder: list, dager: int, dager_etter: int,
                 cfg: dict) -> dict:
    """Kjør replay med en gitt config og mål det vi bryr oss om."""
    rader = S.replay_ticker(t, df, dager, cfg=cfg)
    per_episode = []
    for ep in episoder:
        a = analyser_episode(rader, ep, dager_etter)
        etter = a["dager"]
        starter = lp_starter(etter)
        forste = starter[0] if starter else None
        dag_nr = (next(k for k, x in enumerate(etter) if x["dato"] == forste["dato"])
                  if forste else None)
        per_episode.append({
            "ep": ep, "etter": etter, "starter": starter, "forste": forste,
            "dagNr": dag_nr,
            "overBunn": (over_bunn(forste["kurs"], ep["troughPrice"])
                         if forste else None),
            "falske": a["falskeStarter"],
            "lpDager": sum(1 for r in etter if r["status"] == S.STATUS_LYTTEPOST),
        })
    return {"rader": rader, "episoder": per_episode}


def sammenlign(t: str, df, dager: int, dager_etter: int,
               min_dybde: float) -> None:
    episoder = [e for e in S.korreksjonsepisoder(t, df)
                if e["drawdownPct"] >= min_dybde]
    prov = S.replay_ticker(t, df, dager)
    if not prov or not episoder:
        print("  For lite data til sammenligning.")
        return
    forste_replay = str(prov[0]["dato"])
    episoder = [e for e in episoder if e["troughDate"] >= forste_replay]
    print(f"  {len(episoder)} korreksjoner med bunn innenfor replayvinduet.\n")

    kjort = {}
    for navn, overstyr in VARIANTER:
        kjort[navn] = maal_variant(t, df, episoder, dager, dager_etter,
                                   variant_cfg(overstyr))

    # ── Per korreksjon ──
    for i, ep in enumerate(episoder):
        print("-" * 100)
        print(f"KORREKSJON {ep['peakDate']} → {ep['troughDate']} · "
              f"bunn {ep['troughPrice']:.2f} · -{ep['drawdownPct']:.1f} %")
        print(f"  {'VARIANT':13s} {'SIGNALER':>9s} {'FØRSTE LP':>26s} "
              f"{'LP-DAGER':>9s} {'FALSKE':>7s}")
        for navn, _ in VARIANTER:
            e = kjort[navn]["episoder"][i]
            forste = (f"{e['forste']['dato']} {e['forste']['kurs']:7.2f} "
                      f"{e['overBunn']:+5.1f} % d{e['dagNr']}"
                      if e["forste"] else "—")
            print(f"  {navn:13s} {len(e['starter']):9d} {forste:>26s} "
                  f"{e['lpDager']:9d} {len(e['falske']):7d}")
        print()

    # ── Totalt ──
    print("=" * 100)
    print(f"OPPSUMMERING {t}")
    print("=" * 100)
    print(f"  {'VARIANT':13s} {'SIGNALER':>9s} {'KORR MED LP':>12s} "
          f"{'SNITT % O/BUNN':>15s} {'SNITT DAG':>10s} {'LP-DAGER':>9s} "
          f"{'FALSKE':>7s}")
    for navn, _ in VARIANTER:
        eps = kjort[navn]["episoder"]
        starter = sum(len(e["starter"]) for e in eps)
        med = [e for e in eps if e["forste"]]
        falske = sum(len(e["falske"]) for e in eps)
        lpdager = sum(e["lpDager"] for e in eps)
        snitt_pct = (sum(e["overBunn"] for e in med) / len(med)) if med else None
        snitt_dag = (sum(e["dagNr"] for e in med) / len(med)) if med else None
        print(f"  {navn:13s} {starter:9d} {len(med):>7d}/{len(eps):<4d} "
              f"{(f'{snitt_pct:+.1f} %' if med else '—'):>15s} "
              f"{(f'{snitt_dag:.1f}' if med else '—'):>10s} "
              f"{lpdager:9d} {falske:7d}")

    # ── Hvilke signaler forsvinner med entry-decay, og var de gode? ──
    for grunnlag, variant in (("dagens", "entry-decay"),
                              ("presedens", "begge")):
        print(f"\n  ENTRY-DECAY MOT «{grunnlag}»: hvilke signaler forsvinner?")
        forsvunnet, beholdt = [], 0
        for i, ep in enumerate(episoder):
            basis = kjort[grunnlag]["episoder"][i]
            ny = kjort[variant]["episoder"][i]
            ny_datoer = {r["dato"] for r in ny["starter"]}
            falske_datoer = {f["signal"]["dato"] for f in basis["falske"]}
            for r in basis["starter"]:
                if r["dato"] in ny_datoer:
                    beholdt += 1
                else:
                    forsvunnet.append((ep, r, r["dato"] in falske_datoer))
        if not forsvunnet:
            print(f"    Ingen. Alle {beholdt} signaler beholdt.")
            continue
        daarlige = sum(1 for _, _, falsk in forsvunnet if falsk)
        print(f"    {len(forsvunnet)} forsvant, {beholdt} beholdt.")
        for ep, r, falsk in forsvunnet:
            print(f"      {r['dato']} · {r['kurs']:7.2f} · "
                  f"{over_bunn(r['kurs'], ep['troughPrice']):+6.1f} % over bunn · "
                  f"Turn {r['turnScore']} · Entry {r['entryValue']} · "
                  f"{'VAR falsk start' if falsk else 'var IKKE falsk start'}")
        print(f"    → {daarlige} av {len(forsvunnet)} fjernede var falske "
              f"starter. {len(forsvunnet) - daarlige} var det ikke.")


def main() -> int:
    ap = argparse.ArgumentParser(description="Historisk replay av Early Entry")
    ap.add_argument("--tickere", nargs="+", default=STANDARD)
    ap.add_argument("--dager", type=int, default=252,
                    help="handelsdager tilbake (252 ≈ 12 mnd, 126 ≈ 6 mnd)")
    ap.add_argument("--alle", action="store_true",
                    help="vis hver dag, ikke bare statusendringer")
    ap.add_argument("--csv", metavar="PREFIKS",
                    help="skriv full tabell til PREFIKS_<ticker>.csv")
    ap.add_argument("--korreksjoner", action="store_true",
                    help="analyser hver registrerte korreksjon for seg")
    ap.add_argument("--etter", type=int, default=60,
                    help="handelsdager med recovery som analyseres etter bunnen")
    ap.add_argument("--min-dybde", type=float, default=10.0,
                    help="minste drawdown i prosent for å tas med")
    ap.add_argument("--varianter", action="store_true",
                    help="sammenlign dagens modell mot presedens- og "
                         "entry-decay-variantene")
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

        if a.varianter:
            sammenlign(t, df, a.dager, a.etter, a.min_dybde)
            continue

        if a.korreksjoner:
            kjor_korreksjoner(t, df, a.dager, a.etter, a.min_dybde)
            continue

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
