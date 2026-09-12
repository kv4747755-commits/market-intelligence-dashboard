# Market Intelligence Dashboard V3

V3 keeps the V2 UI but adds a data layer. The dashboard reads `data.json`, so the front-end can stay static while GitHub Actions refreshes market/macro snapshots.

## Current data foundation
- Market snapshot adapter: yfinance for NDX, DXY, EURUSD, VIX and Treasury proxies.
- Options adapter: nearest available Yahoo/yfinance option chain; calculates ATM IV, expected move, PCR OI and a modeled gamma profile when chain data is available.
- `data.json` is a sample fallback so the UI works immediately.

## Important
Yahoo Finance/yfinance is used here as a prototype adapter, not as a promise of exchange-grade real-time data. Before selling/distributing the terminal, replace this with a properly licensed market/options feed.

CFTC COT is a weekly official source and Treasury/FRED are official macro sources; their adapters are planned next. CFTC publishes COT weekly with positions as of the prior Tuesday, while FRED API access requires an API key.

## GitHub Actions
The included workflow runs the updater on a schedule and on manual dispatch. It commits the refreshed `data.json` back to the repository.
