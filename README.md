# Correction Radar

Personlig radar som overvåker en liten, forhåndsgodkjent watchlist og finner
fall som er **uvanlig store for akkurat den aksjen**.

Radaren gir aldri kjøps- eller salgssignaler. Den sier: *her skjer det noe –
undersøk denne.*

## Kjerneprinsippet

Ingen felles prosentgrense. Hver aksje sammenlignes med sin egen historikk av
korreksjoner, funnet med ATR-normalisert ZigZag. Et fall på 7 % kan samtidig
være en stor DNB-korreksjon og helt normal NAS-støy.

```
DNB -7 %   → historisk percentil 91   → interessant
NAS -7 %   → historisk percentil 24   → støy
```

## Tre separate scorer

De slås bevisst ikke sammen til én opportunity score.

| Score | Spørsmål | Sammensetning |
|---|---|---|
| **Correction** 0–100 | Hvor uvanlig er dagens fall for denne aksjen? | Percentil 60 % + teknisk stretch 20 % + støtte 20 % |
| **Trend** 0–100 | Er kursstrukturen fortsatt frisk? | SMA-struktur, helning, higher lows |
| **Recovery** 0–100 | Er fallet i ferd med å ta slutt? | Higher low, RSI, SMA20, motstandsbrudd, volum |

## Statuser

| Status | Krav |
|---|---|
| 🔴 EVENT RISK | Unormalt raskt fall og fundamental sjekk ikke fullført — overstyrer alt |
| ⚫ WAIT | Correction Score < 60 |
| ⚪ FOLLOW | Correction Score 60–74 |
| 🟡 CORRECTION | Corr ≥ 75, Recovery < 50 |
| 🟠 STRONG CORRECTION | Corr ≥ 85, Recovery < 50 |
| 🔵 STABILIZING | Corr ≥ 75, Recovery 50–69 |
| 🟢 REVERSAL | Corr ≥ 75, Recovery ≥ 70, Trend ≥ 55, fundamental sjekk fullført **og** case intakt |

Kurs under SMA200 fjerner ikke aksjen — det trekker bare Trend Score.

**REVERSAL betyr ikke kjøp.** Det betyr at det tekniske oppsettet nå er
interessant nok til at traden bør vurderes manuelt.

## Én korreksjon = én hendelse

Samme `correctionId` beholdes selv om fallet utdypes, slik at radaren ikke
spammer nye varsler hver dag. Banen 190 → 175 → 180 → 165 → 170 → 155 er
**én** correction event, ikke tre.

## Filstruktur

```
oslo-scanner/
├── scanner.py          # Hele appen. Config, indikatorer, swings, scorer, motor, UI
├── test_scanner.py     # Akseptansetester, kjører uten nettverk
├── requirements.txt
├── watchlist.json      # Watchlist (opprettes automatisk, gitignorert)
├── fundamentals.json   # Manuell fundamental sjekk (gitignorert)
└── radar_state.json    # Statushistorikk for varsler (gitignorert)
```

## Config

Alle terskler ligger i `SCANNER_CONFIG` øverst i `scanner.py`. Ingen viktige
tall er hardkodet nedover i motoren. Watchlisten endres i UI-et (legg til /
fjern / deaktiver) uten kodeendring; standardlisten står i `DEFAULT_WATCHLIST`.

## Installasjon

```bash
pip install -r requirements.txt
streamlit run scanner.py
```

## Tester

```bash
python test_scanner.py
```

Kjører akseptansekravene mot syntetiske kursserier med bevisst ulik
volatilitet. Ingen nettverkstilgang kreves.

## Merk om lagring

På Streamlit Cloud er disken flyktig. Watchlist, fundamental-avkryssing og
varselhistorikk nullstilles når appen starter på nytt — da faller watchlisten
tilbake til `DEFAULT_WATCHLIST` i koden.
