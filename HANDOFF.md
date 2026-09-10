# Handoff — Correction Radar

Dette dokumentet er skrevet for en Claude Code-økt som kjører **lokalt** på
utviklerens maskin. Den forrige økten kjørte i skyen uten nettilgang til
markedsdata, og det er selve grunnen til at denne handoffen finnes.

---

## 1. Hva prosjektet er

Correction Radar overvåker en liten, forhåndsgodkjent watchlist og finner fall
som er **uvanlig store for akkurat den aksjen**. Den skanner ikke børsen for
kandidater, og den gir aldri kjøps- eller salgssignaler.

Kjerneprinsippet: ingen felles prosentgrense. Hver aksje sammenlignes med sin
egen historikk av korreksjoner, funnet med ATR-normalisert ZigZag. Et fall på
7 % kan samtidig være en stor DNB-korreksjon og helt normal NAS-støy.

- **Repo:** `Fliptemprr/oslo-scanner`, branch `main`
- **Deploy:** Streamlit Cloud, auto-deploy fra `main` på under et minutt
- **App:** https://oslo-scanner-5xwrrkjcsqh9g5sqystaht.streamlit.app
- **Stack:** Python, Streamlit, yfinance, curl_cffi, pandas, altair
- **Lokal sti (Windows):** `C:\Users\andre\Documents\erlend trading`

### Filer

| Fil | Innhold |
|---|---|
| `scanner.py` | Hele appen, ~3800 linjer. Config → typer → indikatorer → handelskalender → swings → scorer → motor → datahenting → UI |
| `test_scanner.py` | 67 akseptansetester. Kjøres uten nett, med syntetiske kursserier |
| `sjekk_data.py` | Frittstående diagnose av datakildene. Krever nett |
| `replay.py` | Historisk replay av det tidlige laget. Krever nett |
| `.streamlit/config.toml` | Mørkt tema |
| `README.md` | Kort produktbeskrivelse |

Kjøretidsstate (`watchlist.json`, `fundamentals.json`, `radar_state.json`) er
gitignorert. Streamlit Cloud har flyktig disk, så listen faller tilbake til
`DEFAULT_WATCHLIST` ved omstart.

---

## 2. Ufravikelige regler

1. **Ikke endre trading-logikken** — Correction Score, Trend Score, Recovery
   Score, phase, severity, statusmotoren, event risk — uten at brukeren
   eksplisitt gir nye regler. Masterspesifikasjonen styrer, ikke egne ideer.
2. **`python test_scanner.py` skal være 67/67 før hver push.** Feiler noe,
   er det enten en reell regresjon eller en dårlig test. Begge må undersøkes,
   ingen av dem ignoreres.
3. **`python -c "import ast; ast.parse(open('scanner.py').read())"` etter hver
   endring.**
4. **Én fil.** Ingen oppdeling av `scanner.py` i moduler.
5. **Norsk UI og norske kommentarer, engelske faguttrykk.** Kommentarer
   forklarer *hvorfor*, ikke *hva*.
6. **Alle terskler i `SCANNER_CONFIG`.** Ingen magiske tall spredt i koden.
7. **Aldri BUY/SELL noe sted.** REVERSAL betyr «verdt en manuell vurdering».
8. Brukeren vil ha direkte tilbakemelding. Si fra om noe er galt, ikke pynt.

---

## 3. P0 — LØST 08.09.2026

**Datafeeden leverte ikke siste avsluttede handelsdag.** Ved skanning tirsdag
08.09 kl 13:15 viste radaren fortsatt fredagens data (04.09) for alle tickere.

### Rotårsaken var to uavhengige feil

**1. Yahoo leverte mandag 07.09 som null-bar.** Dagen finnes som tidsstempel i
chart-APIet, men med `close=None`. `yfinance` sin `dropna` fjerner raden.

```
2026-09-04 09:00  close=308.3999938964844
2026-09-07 09:00  close=None          ← mandag
2026-09-08 09:00  close=298.5
```

Det gjaldt hele Oslo Børs, også EQNR og TEL utenfor watchlisten. AAPL var
upåvirket. På to år (500 barer) er dette den eneste null-dagen — altså et
engangstilfelle, ikke et løpende mønster.

**2. Hulldeteksjonen var blind for hull bak en nyere bar.** Fallback-kjeden
spurte «er siste bar eldre enn forventet?». Midt i sesjonen lå tirsdagens
uferdige bar sist i serien, så serien så fersk ut og hele kjeden ble hoppet
over. `rens_prisdata` forkastet deretter tirsdagsbaren, helt korrekt, og da
sto fredagen igjen. Selv en fungerende andrekilde ville aldri blitt kalt.

### Løsningen

Yahoo hadde mandagen hele tiden, bare ikke på dagsoppløsning. På 5-minutters
intervall lå dagen komplett. Samme kilde, annet endepunkt — derfor ingen
skalering, justeringsgrunnlaget er det samme.

- `manglende_handelsdager()` ser på hvilke handelsdager som faktisk finnes i
  indeksen, i stedet for bare å se på siste bar
- `backfill_manglende_dager()` rekonstruerer dagsbaren fra intradag-barene
- Sluttauksjonen 16:20-16:25 ligger ikke i den kontinuerlige intradag-feeden,
  så aggregatet ga KOG 298.90 mot offisielle 298.00. `meta.chartPreviousClose`
  har den eksakte sluttkursen og overstyrer aggregatets close

**Fallgruve, verifisert:** `chartPreviousClose` er relativ til chartens
REKKEVIDDE, ikke til siste sesjon. Fra `range=1mo` pekte den en måned tilbake
og ga KIT 88.90 mot riktige 98.40. Den må hentes fra en egen `range=1d`-
forespørsel. Det er `_hent_dagsmeta()`.

Verifisert mot brukerens bekreftede tall, null avvik:

```
TICKER        CLOSE   % I DAG       FASIT       %
KOG.OL       298.00     -3.37       298.0   -3.37
KIT.OL        98.40      2.07        98.4    2.07
```

### Stooq er død kode

`stooq.com` svarer HTTP 200 med en HTML-side, ikke CSV: et JavaScript
proof-of-work-challenge (`crypto.subtle.digest` i løkke til hashen starter med
fire nuller). Ingen ren HTTP-klient kommer forbi. Kjeden fra `312f148` har
aldri virket. `stooqEnabled` er satt til `False`; koden og testene står igjen
bak flagget i tilfelle det endrer seg.

### Begrensning

Yahoos 5-minutters historikk rekker bare rundt en måned tilbake. Backfillen
tetter derfor ferske hull, styrt av `backfillMaxDays` (5). Eldre hull står
igjen, og da blir STALE stående — som er riktig, for da er tallene faktisk
ikke til å stole på.

## 3b. Corporate actions — løst 08.09.2026

Yahoo justerer for utbytte og splitt, men **ikke for fisjon**. KOG skilte ut
Kongsberg Maritime (KMAR.OL) med ex-dato **15.04.2026** — ikke 22./23.04, som
er noteringsdatoen for KMAR.

```
14.04   close 398.50   low  394.50
15.04   open  328.38   high 328.63     ← gap -17.60 %, ingen overlapp
```

Hele bevegelsen lå mellom to sesjoner. Justeringsfaktoren fra Yahoo er
`1.000000` etter 17.04, og de 1.4 % før skyldes kun utbyttet på 5.70 kr.

Uten justering leste radaren fisjonen som et markedsfall:

| | Ujustert | Justert |
|---|---|---|
| Aktiv topp | 417.60 | **349.90** |
| Max drawdown | 34.79 % | **22.18 %** |
| Percentil | 100 | **94** |
| Trend Score | 30 | **75** |
| SMA200 | 316.16 | **290.00** |

Trend Score er den alvorligste: kurs 298.00 lå under SMA200 316.16, og siden
REVERSAL krever Trend ≥ 55 kunne KOG **aldri** nå REVERSAL før fisjonen falt ut
av SMA200-vinduet i februar 2027. Historikken var også forurenset — en
oppdiktet korreksjon på 27.3 % (reelt 13.3 %) i fordelingen percentilen måles
mot.

**Faktoren 0.83789 er utledet, ikke offisiell:** `(398.50 - 64.60) / 398.50`,
der 64.60 er KMARs første omsetning. Den gir topp 349.90, som stemmer med den
bakoverjusterte serien hos Nordnet og Finansavisen. Bytt til Oslo Børs'
offisielle faktor når den er bekreftet.

Merk at close-til-close-fallet var -18.19 %, mens den rene fisjonsdelen er
-16.21 %. De resterende ~2.4 % var ekte markedsbevegelse. Man kan derfor ikke
justere med det observerte gapet.

Tabellen `corporateActions` i `SCANNER_CONFIG` er **bevisst eksplisitt**.
Automatisk gap-deteksjon ble vurdert og valgt bort: watchlisten har 15 andre
gap uten overlapp mellom dagene, og de er resultatreaksjoner som skal telle som
korreksjoner. Automatikk ville visket ut ekte fall.

Volum røres ikke — ved fisjon endres ikke antall aksjer i selskapet det
fisjoneres fra. Dagens kurs står alltid urørt, så UI-et viser faktisk
markedskurs; detaljpanelet opplyser om justeringen der den historiske toppen
vises.

## 3c. EOD samme kveld, og en header som ikke lyver — løst 09.09.2026

To feil meldt av Erlend 08.-09.09.

**Headeren viste forventet dato, ikke brukt candle.** `datastatus` brukte
`max()` over tickerne:

```python
faktisk = max(gyldige)              # det ferskeste
stale   = faktisk < forventet
```

Én oppdatert ticker holdt `stale=False` for hele skanningen. Banneret meldte
«MARKEDSDATA T.O.M. 08.09» mens NOD, KIT og KOG ble beregnet på 07.09-closes.
Nå er `faktisk` **svakeste ledd** (`min`), `nyeste` er tatt vare på for
diagnose, og `stale` er sann så snart *én* ticker ligger bak.

**Yahoo publiserer dagsbaren først neste handelsmorgen.** Det er kritisk, siden
radaren primært brukes etter børsslutt for å planlegge neste dag. Rekkefølgen
for sluttkurs på en rekonstruert dag er nå:

| Kilde | Dekker |
|---|---|
| `chartPreviousClose` | gårsdagen (sesjonen før svarets egen) |
| `regularMarketPrice` | dagens sesjon, kun når `regularMarketTime` er etter 16:20 |
| `kjenteSluttkurser` | dager kilden har mistet helt |
| intradag-aggregatet | siste utvei, merkes «omtrentlig close» |

Klokkeslettsjekken er ufravikelig: mens børsen er åpen er `regularMarketPrice`
en levende intradagkurs, og den må aldri lagres som sluttkurs. Scores beregnes
fortsatt kun på avsluttede sesjoner.

**07.09.2026 er tapt hos Yahoo.** Dagen kom som null-bar og er nå helt borte
fra dagsserien. Den rekonstrueres fra intradag, men sluttauksjonen 16:20-16:25
ligger ikke i den kontinuerlige feeden, så aggregatet bommet med opptil 0.3 %
— nok til at `% I DAG` for 08.09 ble synlig feil. `kjenteSluttkurser` i
config holder de sju offisielle kursene, lest fra Yahoos egen
`meta.chartPreviousClose` den 08.09.

**Åpen begrensning:** 5-minutters historikk hos Yahoo rekker bare rundt en
måned. Rekonstruksjonen av 07.09 forsvinner derfor tidlig i oktober, og da vil
serien mangle dagen permanent. `kjenteSluttkurser` alene tetter ikke det —
den gir close, ikke OHLCV. Vurder å skrive rekonstruerte barer til disk, eller
å bytte til en kilde med ekte EOD-historikk, før det inntreffer.

## 4. Datahentingens arkitektur

Kjeden i `hent_prisdata()`:

```
yf.download(start, end)  i batcher på 15, 5 s pause, curl_cffi, threads=False
        ↓  siste bar eldre enn forventet?
yf.download(period="10d")  per ticker
        ↓  fortsatt?
Stooq CSV  per ticker, skalert til Yahoo-nivå   ← av som standard, se §3
        ↓  hull i serien, også bak en nyere bar?
backfill_manglende_dager()   5m-barer aggregert til dagsbar,
                             close fra range=1d chartPreviousClose
        ↓
rens_prisdata()   forkaster uferdig candle hvis vi står midt i sesjonen
        ↓
juster_corporate_actions()   skalerer historikken over fisjoner, se §3b
        ↓
datastatus()      sammenligner mot siste_avsluttede_handelsdag()
```

Merk rekkefølgen: hullsjekken kjører FØR `rens_prisdata`, mens den uferdige
baren fortsatt ligger i serien. Derfor kan den ikke basere seg på siste bar.

`threads=False` er påkrevd for curl_cffi — ikke fjern den.

**Handelskalenderen** (`siste_avsluttede_handelsdag`) tar hensyn til helger og
Oslo Børs' helligdager, inkludert de bevegelige påskedagene. Julaften og
nyttårsaften er helt stengt. Stengetid 16:20 pluss 40 minutters margin før
dagens bar regnes som tilgjengelig. Testet mot syv kanttilfeller.

**Skaleringen ved kildebytte:** Yahoo leverer utbyttejusterte kurser, Stooq
ujusterte. Limes de sammen rått får siste bar et falskt hopp som slår inn i
% i dag, RSI, ATR og drawdown. Nye barer skaleres derfor med forholdet mellom
kildene på siste felles dato. Avviker faktoren mer enn 20 % fra 1, flettes
ingenting inn og STALE-varselet blir stående.

---

## 5. Signalmotoren, kort

Tre separate scorer som bevisst **ikke** slås sammen:

| Score | Spørsmål | Sammensetning |
|---|---|---|
| Correction | Hvor uvanlig er dagens fall for denne aksjen? | percentil 60 % + teknisk stretch 20 % + støtte 20 % |
| Trend | Er kursstrukturen frisk? | SMA-struktur, helning, higher lows |
| Recovery | Er fallet i ferd med å ta slutt? | higher low, RSI, SMA20, motstandsbrudd, volum |

Korreksjonen er et **stateful event** med samme `correctionId` gjennom hele
forløpet. Intern `phase` (NORMAL/FALLING/BASE_BUILDING/RECOVERING/EVENT_RISK/
CLOSED) og `severity` (NONE/FOLLOW/CORRECTION/STRONG_CORRECTION) beregnes hver
for seg, og statusen utledes av de to. `maxDrawdownPct` krymper aldri;
`currentDrawdownPct` endres.

To designvalg som er lette å ødelegge ved et uhell:

- **`severityPercentileFloor`**: severity har et gulv fra percentilen, fordi
  stretch og støtte faller når kursen henter seg inn. Uten gulvet mister en
  aksje i recovery severity-historien sin så snart lagret tilstand går tapt —
  og Streamlit Cloud nullstiller disken ved hver omstart.
- **Recovery er gatet på `severity >= CORRECTION`**. Uten det ville en aksje
  på ATH fått recovery-poeng for «close over SMA20» og «positiv 5D-momentum».

---

## 5b. Tidlig lag — BOTTOM WATCH og LYTTEPOST (09.09.2026)

Bygget etter Erlends spesifikasjon, implementert bokstavelig. Stigen er
`WAIT → BOTTOM WATCH → LYTTEPOST → STABILIZING → REVERSAL`.

Laget kommer bevisst før full reversal-bekreftelse og tåler høyere feilrate.
**LYTTEPOST er ikke et kjøpssignal.**

| Del | Hvor |
|---|---|
| Terskler og poeng | `SCANNER_CONFIG["early"]` |
| Features fra OHLCV | `tidlige_features()` |
| §2 Falling Knife Guard | `falling_knife_guard()` |
| §3 Turn Score 0-100 | `turn_score()`, grupper A-F |
| §4 Entry Value 0-100 | `entry_value()` |
| §5/§7/§8 livssyklus | `vurder_tidlig_lag()` |
| §10 rangering | `opportunity_score()` |

Presedens: det tidlige laget legges foran WAIT, FOLLOW, CORRECTION og STRONG
CORRECTION, men aldri foran EVENT RISK, STABILIZING eller REVERSAL. Den
klassiske statusen er uendret og ligger i `_klassisk_status()`.

Signalet lagres i `radar_state.json` under `lyttepost`, slik at brudd måles mot
der hypotesen startet og ikke mot dagens bunn. Streamlit Cloud nullstiller
disken, så et signal kan gå tapt ved omstart.

### Åpent: laget mangler en korreksjonsgate

Spesifikasjonen har ingen gate på severity, og det ble implementert som
spesifisert. Første kjøring på ekte data 09.09.2026:

| Ticker | Status | Drawdown | Severity | Turn | Entry |
|---|---|---|---|---|---|
| DNB | BOTTOM WATCH | 0.6 % | NONE | 42 | 90 |
| WAWI | BOTTOM WATCH | 0.1 % | NONE | 47 | 65 |

Begge står praktisk talt på topp. De får poeng for momentum, volum og
SMA20-reclaim — det samme problemet Recovery Score løser med
`requiresSeverity: CORRECTION` (se §5).

Samtidig var de fem aksjene som faktisk står i korreksjon alle STABILIZING,
som vinner over laget. Nettoresultatet den dagen var at laget kun lyste på de
to aksjene uten korreksjon.

Mulig fiks, én linje: `"krevSeverity": SEV_FOLLOW` i `early`, sjekket i
`vurder_tidlig_lag`. Ikke gjort — venter på Erlends beslutning.

## 6. Åpne spørsmål som krever ekte data

Disse har stått ubesvart hele veien fordi utviklingsmiljøet manglet nett.
P0 er nå løst, så disse er neste steg. **Endre én verdi om gangen.**

### `maxCorrectionLookbackDays` (nå 400)

Mot ekte Oslo-data forankret korreksjoner seg 108–265 dager tilbake. Et fall
over 265 dager er en nedtrend, ikke en korreksjon.

Se på `DAGER`-kolonnen. Ligger flere aksjer på 150–300, prøv **150**. Blir
tallene da 20–90 for de fleste, sitter det. Havner mange på `WAIT` med
drawdown nær null, er 150 for hardt — prøv 200.

### `severityPercentileFloor` (nå `True`)

Symptom på for løst gulv: fire eller flere av sju står på STRONG CORRECTION
samtidig med beskjedne drawdowns. Radaren skal skille, ikke rope om alt.

Fiks i prioritert rekkefølge: hev `correction.minDepthPct` fra 3.0 til 5–6,
eller sett gulvet til `False`.

**Endre én verdi om gangen.** Ellers vet du ikke hvilken som gjorde hva.

---

## 7. Kjente begrensninger

- **Streamlit Cloud har flyktig disk.** Watchlist, fundamental-avkryssing og
  varselhistorikk nullstilles ved omstart. Ekte persistens krever database.
- **Varsler er kun i appen.** Ingen push eller e-post.
- **Ingen AI-funksjoner.** FORKLAR/NYHETER er designet, men bevisst ikke bygget
  (§39 i masterspec). Ikke bygg dem uten at brukeren ber om det.
- **UI-et er 1a TERMINAL** fra en Claude Design-handoff. Streamlit-widgets
  beholder sin egen DOM, så det blir aldri pikselperfekt mot mockupen.
- **Mobil er ikke egenimplementert.** Streamlit stabler kolonner selv.

---

## 8. Arbeidsflyt

```bash
git pull
# endre scanner.py
python -c "import ast; ast.parse(open('scanner.py').read())"
python test_scanner.py          # skal være 67/67
streamlit run scanner.py        # se på den
git add -A && git commit -m "..." && git push
```

Lokalt oppsett på Windows: `.venv` med `pip install -r requirements.txt`.
Testsuiten skriver ✓ og ✗, som cp1252-konsollen ikke kan vise — kjør derfor
`set PYTHONIOENCODING=utf-8` først, ellers kræsjer den på utskriften og ikke
på logikken.

Commit-meldinger på norsk, som forklarer *hvorfor* endringen ble gjort og hva
som ble funnet underveis. Se `git log` for tonen.
