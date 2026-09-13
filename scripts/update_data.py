import json, math, re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.request import Request, urlopen
import urllib.parse
import xml.etree.ElementTree as ET
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


def fetch_news():
    """Fetch and rank recent market-relevant headlines.

    Only headline metadata is stored. If the relevance filter removes
    everything, keep a small fallback set of recent feed items so the
    dashboard does not incorrectly report an empty news feed.
    """
    queries = [
        '"Federal Reserve" OR "Fed" OR "interest rates" OR CPI OR inflation OR jobs',
        '"Treasury yields" OR "10-year yield" OR bonds OR "yield curve"',
        'Nasdaq OR "S&P 500" OR "Wall Street" OR stocks OR equities',
        'dollar OR DXY OR EURUSD OR euro OR ECB OR forex',
        'oil OR crude OR OPEC OR commodities OR gold',
        'tariffs OR sanctions OR Iran OR Ukraine OR geopolitics markets',
        'options OR volatility OR VIX OR futures',
    ]

    positive = {
        "fed": 5, "federal reserve": 5, "fomc": 6, "interest rate": 5,
        "inflation": 5, "cpi": 6, "ppi": 4, "payroll": 5, "jobs": 4,
        "unemployment": 4, "gdp": 4, "treasury": 5, "yield": 5,
        "bond": 4, "yield curve": 5, "nasdaq": 5, "s&p 500": 5,
        "wall street": 4, "stocks": 3, "equities": 3, "futures": 4,
        "dollar": 5, "dxy": 6, "eurusd": 6, "euro": 3, "ecb": 5,
        "forex": 4, "currency": 3, "oil": 4, "crude": 5, "opec": 5,
        "gold": 3, "commodity": 3, "vix": 5, "volatility": 4,
        "options": 4, "tariff": 4, "sanction": 4, "iran": 4,
        "ukraine": 3, "russia": 3, "china": 2, "geopolit": 4,
        "central bank": 5, "rate hike": 6, "rate cut": 6,
        "earnings": 3, "forecast": 3, "recession": 5, "liquidity": 4,
    }
    high = {
        "fomc", "federal reserve", "fed", "cpi", "inflation", "rate hike",
        "rate cut", "interest rate", "payroll", "nonfarm", "treasury yield",
        "yield curve", "oil", "crude", "opec", "ecb", "central bank",
        "geopolit", "sanction", "tariff", "vix"
    }
    exclude = {
        "lottery", "casino", "sports", "celebrity", "entertainment",
        "movie", "tv show", "reality tv", "recipe", "restaurant",
        "shopping", "coupon", "dollar tree", "fashion", "horoscope",
        "obituary", "wedding", "crime", "local police"
    }
    source_bonus = {
        "reuters": 3, "cnbc": 3, "bloomberg": 3, "financial times": 3,
        "marketwatch": 2, "nasdaq": 2, "associated press": 2,
        "yahoo finance": 2, "investing.com": 2, "barron's": 2
    }

    ranked = []
    fallback = []
    seen = set()

    for query in queries:
        url = (
            "https://news.google.com/rss/search?q="
            + urllib.parse.quote(query)
            + "&hl=en-US&gl=US&ceid=US:en"
        )
        try:
            req = Request(url, headers={"User-Agent": "market-intelligence-dashboard/1.0"})
            with urlopen(req, timeout=20) as r:
                root = ET.fromstring(r.read())

            for item in root.findall(".//item"):
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                source_node = item.find("source")
                source = (source_node.text or "").strip() if source_node is not None else "Unknown"
                pub = (item.findtext("pubDate") or "").strip()
                if not title or not link:
                    continue

                key = link or title
                if key in seen:
                    continue
                seen.add(key)

                published_at = None
                if pub:
                    try:
                        published_at = parsedate_to_datetime(pub).astimezone(timezone.utc).isoformat()
                    except Exception:
                        published_at = pub

                base = {
                    "title": title,
                    "source": source or "Unknown",
                    "published_at": published_at,
                    "url": link,
                }
                fallback.append(base)

                text = f"{title} {source}".lower()
                if any(term in text for term in exclude):
                    continue

                score = sum(weight for term, weight in positive.items() if term in text)
                score += max(
                    (bonus for name, bonus in source_bonus.items() if name in source.lower()),
                    default=0
                )
                if score < 4:
                    continue

                high_hit = any(term in text for term in high)
                impact = "HIGH" if high_hit and score >= 8 else ("MEDIUM" if score >= 6 else "LOW")
                base.update({"relevance_score": score, "impact": impact})
                ranked.append(base)
        except Exception:
            continue

    # Prefer high-relevance stories, then recent ones.
    unique = []
    title_keys = set()
    for item in sorted(
        ranked,
        key=lambda x: (x.get("relevance_score", 0), x.get("published_at") or ""),
        reverse=True,
    ):
        normalized = re.sub(r"[^a-z0-9]+", " ", item["title"].lower()).strip()
        title_key = " ".join(normalized.split()[:12])
        if title_key in title_keys:
            continue
        title_keys.add(title_key)
        unique.append(item)
        if len(unique) >= 12:
            break

    # Safety fallback: Google RSS returned stories, but the scoring rules
    # rejected all of them. Keep recent items rather than showing false
    # "unavailable" status. These are still labeled as aggregated RSS news.
    if not unique:
        for item in sorted(fallback, key=lambda x: x.get("published_at") or "", reverse=True):
            normalized = re.sub(r"[^a-z0-9]+", " ", item["title"].lower()).strip()
            title_key = " ".join(normalized.split()[:12])
            if title_key in title_keys:
                continue
            title_keys.add(title_key)
            item["relevance_score"] = 0
            item["impact"] = "LOW"
            unique.append(item)
            if len(unique) >= 8:
                break

    unique.sort(key=lambda x: x.get("published_at") or "", reverse=True)
    return {
        "status": "live" if unique else "unavailable",
        "source": "Filtered Google News RSS aggregation",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "items": unique[:12],
    }


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

    # Preserve the existing advanced options/macro/COT fields in data.json.
    # This news patch only refreshes fields that this lightweight adapter can
    # safely update, so the interactive GEX profile is not downgraded.
    options = d.setdefault("options", {})
    options.setdefault("status", "unavailable")
    options.setdefault("ticker", "^NDX")
    options.setdefault("expiry", None)
    options.setdefault("spot", d["prices"].get("ndx"))
    options.setdefault("atm_iv", None)
    options.setdefault("expected_move_pct", None)
    options.setdefault("expected_move_points", None)
    options.setdefault("pcr_oi", None)
    options.setdefault("gamma_flip", None)
    options.setdefault("put_wall", None)
    options.setdefault("call_wall", None)
    options.setdefault("net_gex", None)
    options.setdefault("model", "Estimated GEX; dealer-sign assumption, not direct dealer book.")

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
                    (datetime.fromisoformat(expiry).date() - datetime.now(timezone.utc).date()).days,
                    1,
                )
                if iv is not None:
                    move = spot * iv * math.sqrt(days / 365)
                    options.update({
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
                    options["pcr_oi"] = put_oi / call_oi

                c_oi = c.dropna(subset=["openInterest"])
                p_oi = p.dropna(subset=["openInterest"])
                # Preserve modeled wall levels if the canonical data layer already has them.
                if options.get("call_wall") is None and not c_oi.empty:
                    options["call_wall"] = sf(c_oi.loc[c_oi["openInterest"].idxmax(), "strike"])
                if options.get("put_wall") is None and not p_oi.empty:
                    options["put_wall"] = sf(p_oi.loc[p_oi["openInterest"].idxmax(), "strike"])

                if "gamma" in c.columns and "gamma" in p.columns:
                    cg = c[["openInterest", "gamma"]].copy()
                    pg = p[["openInterest", "gamma"]].copy()
                    cg["openInterest"] = cg["openInterest"].fillna(0)
                    pg["openInterest"] = pg["openInterest"].fillna(0)
                    cg["gamma"] = cg["gamma"].fillna(0)
                    pg["gamma"] = pg["gamma"].fillna(0)
                    c_gex = (cg["gamma"] * cg["openInterest"] * 100 * spot * spot * 0.01).sum()
                    p_gex = (pg["gamma"] * pg["openInterest"] * 100 * spot * spot * 0.01).sum()
                    net_gex = float(c_gex - p_gex)
                    if math.isfinite(net_gex) and options.get("net_gex") is None:
                        options["net_gex"] = net_gex
    except Exception:
        pass

    # News is additive: it does not delete the existing macro/COT fields
    # already present in data.json.
    d["news"] = fetch_news()
    d["generated_at"] = datetime.now(timezone.utc).isoformat()
    d["sources"] = list(dict.fromkeys((d.get("sources") or []) + ["Filtered Google News RSS aggregation"]))

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2)


if __name__ == "__main__":
    main()
