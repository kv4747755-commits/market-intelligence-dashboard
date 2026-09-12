import json, math
from datetime import datetime, timezone
import yfinance as yf

OUT = "data.json"

def sf(x):
    try:
        x = float(x)
        return x if math.isfinite(x) else None
    except Exception:
        return None

def snap(ticker):
    try:
        h = yf.Ticker(ticker).history(period="5d", interval="1d", auto_adjust=False)
        if h.empty:
            return None, None
        c = h["Close"].dropna()
        v = sf(c.iloc[-1])
        p = sf(c.iloc[-2]) if len(c) >= 2 else None
        ch = ((v / p) - 1) * 100 if v is not None and p else None
        return v, ch
    except Exception:
        return None, None

def main():
    try:
        with open(OUT, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        d = {
            "generated_at": None,
            "prices": {
                "ndx": None, "ndx_change": None,
                "dxy": None, "dxy_change": None,
                "eurusd": None, "eurusd_change": None,
                "vix": None, "vix_change": None
            },
            "rates": {"y3m": None, "y10": None, "y30": None},
            "options": {},
            "sources": []
        }

    for key, ticker in [
        ("ndx", "^NDX"), ("dxy", "DX-Y.NYB"),
        ("eurusd", "EURUSD=X"), ("vix", "^VIX")
    ]:
        v, ch = snap(ticker)
        if v is not None:
            d["prices"][key] = v
            d["prices"][key + "_change"] = ch

    # Yahoo's ^IRX, ^TNX and ^TYX values are already percent values.
    for key, ticker in [
        ("y3m", "^IRX"), ("y10", "^TNX"), ("y30", "^TYX")
    ]:
        v, _ = snap(ticker)
        if v is not None:
            d["rates"][key] = v

    d["options"] = {
        "status": "unavailable",
        "ticker": "^NDX",
        "expiry": None,
        "spot": d["prices"].get("ndx"),
        "atm_iv": None,
        "expected_move_pct": None,
        "expected_move_points": None,
        "pcr_oi": None,
        "gamma_flip": None,
        "put_wall": None,
        "call_wall": None,
        "net_gex": None,
        "model": "Estimated GEX; dealer-sign assumption, not direct dealer book."
    }

    try:
        t = yf.Ticker("^NDX")
        expiries = list(t.options or [])
        spot = sf(d["prices"].get("ndx"))
        if expiries and spot is not None:
            today = datetime.now(timezone.utc).date()
            future = [e for e in expiries if datetime.fromisoformat(e).date() >= today]
            expiry = future[0] if future else expiries[0]

            ch = t.option_chain(expiry)
            c, p = ch.calls.copy(), ch.puts.copy()

            if not c.empty and not p.empty:
                c["dist"] = (c["strike"] - spot).abs()
                p["dist"] = (p["strike"] - spot).abs()

                civ = sf(c.sort_values("dist").iloc[0].get("impliedVolatility"))
                piv = sf(p.sort_values("dist").iloc[0].get("impliedVolatility"))
                ivs = [x for x in (civ, piv) if x is not None and x > 0]
                iv = sum(ivs) / len(ivs) if ivs else None

                days = max(
                    (datetime.fromisoformat(expiry).date()
                     - datetime.now(timezone.utc).date()).days, 1
                )

                if iv is not None:
                    # Correct expected move: no extra *100 here.
                    move = spot * iv * math.sqrt(days / 365)
                    d["options"].update({
                        "status": "live",
                        "expiry": expiry,
                        "spot": spot,
                        "atm_iv": iv * 100,
                        "expected_move_pct": (move / spot) * 100,
                        "expected_move_points": move
                    })

                call_oi = sf(c["openInterest"].fillna(0).sum())
                put_oi = sf(p["openInterest"].fillna(0).sum())
                if call_oi is not None and put_oi is not None and call_oi > 0:
                    d["options"]["pcr_oi"] = put_oi / call_oi

                c_oi = c.dropna(subset=["openInterest"])
                p_oi = p.dropna(subset=["openInterest"])
                if not c_oi.empty:
                    d["options"]["call_wall"] = sf(
                        c_oi.loc[c_oi["openInterest"].idxmax(), "strike"])
                if not p_oi.empty:
                    d["options"]["put_wall"] = sf(
                        p_oi.loc[p_oi["openInterest"].idxmax(), "strike"])

                if "gamma" in c.columns and "gamma" in p.columns:
                    cg = c[["openInterest", "gamma"]].copy()
                    pg = p[["openInterest", "gamma"]].copy()
                    cg["openInterest"] = cg["openInterest"].fillna(0)
                    pg["openInterest"] = pg["openInterest"].fillna(0)
                    cg["gamma"] = cg["gamma"].fillna(0)
                    pg["gamma"] = pg["gamma"].fillna(0)

                    c_gex = (cg["gamma"] * cg["openInterest"] * 100 *
                             spot * spot * 0.01).sum()
                    p_gex = (pg["gamma"] * pg["openInterest"] * 100 *
                             spot * spot * 0.01).sum()
                    net_gex = float(c_gex - p_gex)
                    if math.isfinite(net_gex):
                        d["options"]["net_gex"] = net_gex

    except Exception:
        pass

    d["generated_at"] = datetime.now(timezone.utc).isoformat()
    d["sources"] = ["yfinance prototype adapter"]

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2)

if __name__ == "__main__":
    main()
