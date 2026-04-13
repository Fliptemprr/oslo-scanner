# Oslo Børs Swing Trading Scanner – MVP

Enkel swing trading scanner for Oslo Børs (OBX-aksjer).  
Finner kandidater basert på SMA 200, SMA 50, RSI 14, volum og pris-avstander.

## Filstruktur

```
oslo-scanner/
├── scanner.py          # Hovedapp (Streamlit)
├── requirements.txt    # Python-avhengigheter
├── watchlist.json      # Watchlist (opprettes automatisk)
└── README.md           # Denne filen
```

## Installasjon

```bash
# 1. Klon/kopier mappen
cd oslo-scanner

# 2. (Valgfritt) Lag virtuelt miljø
python -m venv venv
venv\Scripts\activate      # Windows
# source venv/bin/activate  # Mac/Linux

# 3. Installer avhengigheter
pip install -r requirements.txt
```

## Kjør appen

```bash
streamlit run scanner.py
```

Åpner i nettleseren på `http://localhost:8501`.

## Bruk

1. Trykk **Scan nå** for å hente ferske data
2. Bruk filtrene øverst for å snevre inn kandidater
3. Klikk ☆-knappen for å legge aksjer til watchlist
4. Watchlisten lagres automatisk til `watchlist.json`
5. Åpne chart i Nordnet/Finansavisen for endelig vurdering

## Legg til flere aksjer

Rediger `OBX_TICKERS`-dicten øverst i `scanner.py`:

```python
OBX_TICKERS = {
    "EQNR.OL": "Equinor",
    "DNB.OL": "DNB Bank",
    # ... legg til flere her
    "NYAKSJE.OL": "Nytt Selskap",
}
```
