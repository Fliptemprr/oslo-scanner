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
| `test_scanner.py` | 30 akseptansetester. Kjøres uten nett, med syntetiske kursserier |
| `sjekk_data.py` | Frittstående diagnose av datakildene. Krever nett |
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
2. **`python test_scanner.py` skal være 30/30 før hver push.** Feiler noe,
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

## 3. BLOKKERENDE PROBLEM (P0)

**Datafeeden leverer ikke siste avsluttede handelsdag.**

Ved skanning tirsdag 08.09.2026 kl 13:15 viste radaren fortsatt fredagens data
(04.09). Siste avsluttede handelsdag var mandag 07.09. Det gjaldt **alle**
tickere, ikke én.

Brukeren har bekreftet at mandagen finnes i markedet: KOG stengte 298,00
(−3,37 %), KIT 98,40 (+2,07 %). Radaren viste KOG 308,40 — fredagens tall.

Dette er blokkerende fordi det ikke bare gjelder KURS og % I DAG. Verifisert i
test: én manglende candle endrer kurs, RSI14, SMA20, SMA50, ATR14, Vol Ratio
og Correction Score. Altså hele signalmotoren.

**Brukeren har stanset all videre testing av score- og statuslogikken til dette
er løst. Ikke rør den logikken før datagrunnlaget er riktig.**

### Hva som allerede er gjort og utelukket

| Forsøk | Commit | Resultat |
|---|---|---|
| `end = datetime.now()` var eksklusiv og tvetydig i UTC-døgnskiftet. Endret til `now + 2 dager` | `372b228` | Løste det ikke |
| Kort `period="10d"`-forespørsel som fallback når siste dag mangler | `688bbc4` | Løste det ikke |
| Stooq som andrekilde, med skalering mot Yahoo-nivå | `312f148` | Ukjent — aldri verifisert mot ekte Stooq |
| Diagnosepanel i UI + `TEST KILDER`-knapp | `005f033` | Venter på observasjon |
| `sjekk_data.py` for lokal diagnose | `c961028` | **Ikke kjørt ennå** |

### Hva som IKKE er utelukket

- Om Stooq i det hele tatt svarer fra Streamlit Cloud (delte IP-er, rate limit)
- Om Yahoo faktisk mangler dagen, eller om noe i koden forkaster den
- Om `curl_cffi`-sesjonen fungerer mot Stooq

### FØRSTE OPPGAVE

```bash
python sjekk_data.py
```

Skriptet skriver ut hvilken dato hver kilde faktisk leverer som siste bar, per
ticker, for tre kilder: Yahoo med datointervall, Yahoo med `period`, og Stooq.
Det krever ikke Streamlit.

Tolkning:

- **Yahoo `period` har dagen, `intervall` ikke** → fallbacken i `hent_prisdata`
  virker, men noe i produksjonskjeden hopper over den. Se `topp_opp_siste_dager`.
- **Stooq har dagen, Yahoo ikke** → Stooq-kjeden er riktig, men noe feiler i
  produksjon. Sjekk `_hent_url`, `parse_stooq_csv` og skaleringsgrensen
  `data.stooqMaxScaleDeviation` (0.20).
- **Ingen kilder har dagen** → problemet er ikke koden. Da må en annen kilde
  velges. Brukeren har tilgang til alle Google-API-er; `GOOGLEFINANCE("OSL:KOG")`
  via Sheets API er da det mest realistiske alternativet.

Kjør deretter appen lokalt og se på den:

```bash
streamlit run scanner.py
```

---

## 4. Datahentingens arkitektur

Kjeden i `hent_prisdata()`:

```
yf.download(start, end)  i batcher på 15, 5 s pause, curl_cffi, threads=False
        ↓  mangler siste avsluttede handelsdag?
yf.download(period="10d")  per ticker
        ↓  fortsatt?
Stooq CSV  per ticker, skalert til Yahoo-nivå
        ↓
rens_prisdata()   forkaster uferdig candle hvis vi står midt i sesjonen
        ↓
datastatus()      sammenligner mot siste_avsluttede_handelsdag()
```

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

## 6. Åpne spørsmål som krever ekte data

Disse har stått ubesvart hele veien fordi utviklingsmiljøet manglet nett.
**Løs P0 først**, deretter disse:

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
python test_scanner.py          # skal være 30/30
streamlit run scanner.py        # se på den
git add -A && git commit -m "..." && git push
```

Commit-meldinger på norsk, som forklarer *hvorfor* endringen ble gjort og hva
som ble funnet underveis. Se `git log` for tonen.
