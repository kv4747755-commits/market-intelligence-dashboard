# Market Intelligence Dashboard — Fixed Data Layer

This version fixes the V3 consistency problem by making `data.json` the single source of truth. All dashboard sections use the same NDX, DXY, EURUSD and VIX values.

Options/dealer analytics are never fabricated. If a valid options chain is unavailable, the UI says UNAVAILABLE. Expected move is calculated only from a valid ATM implied-volatility snapshot.

`update-data.yml` updates the market snapshot every 15 minutes and can be manually dispatched. The yfinance layer is a prototype/delayed-data adapter, not a licensed real-time feed. COT, Treasury/FRED and news remain separate adapter stages.
