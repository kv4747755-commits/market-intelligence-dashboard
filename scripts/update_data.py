import json, math, re, os
from datetime import datetime, timezone, time as dt_time
from zoneinfo import ZoneInfo
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
        h = yf.Ticker(ticker).history(period="10d", interval="1d", auto_adjust=False)
        if h.empty:
            return None, None
        c = h["Close"].dropna()
        v = sf(c.iloc[-1])
        p1 = sf(c.iloc[-2]) if len(c) >= 2 else None
        p5 = sf(c.iloc[-6]) if len(c) >= 6 else (sf(c.iloc[0]) if len(c) >= 2 else None)
        ch1 = ((v / p1) - 1) * 100 if v is not None and p1 else None
        ch5 = ((v / p5) - 1) * 100 if v is not None and p5 else None
        return v, ch1, ch5
    except Exception:
        return None, None, None


def fetch_fx_snapshot(previous_fx=None):
    """Build the FX market data layer used by the Forex command center.

    Rates are indicative daily snapshots from Yahoo Finance/yfinance.
    Currency strength is a relative, model-derived score from the tracked
    major and cross pairs; it is not a forecast.
    """
    pair_tickers = {
        "EURUSD": "EURUSD=X",
        "GBPUSD": "GBPUSD=X",
        "USDJPY": "JPY=X",
        "AUDUSD": "AUDUSD=X",
        "NZDUSD": "NZDUSD=X",
        "USDCHF": "CHF=X",
        "USDCAD": "CAD=X",
        "EURJPY": "EURJPY=X",
        "GBPJPY": "GBPJPY=X",
        "EURGBP": "EURGBP=X",
        "USDINR": "INR=X",
        "USDCNY": "CNY=X",
    }

    pairs = {}
    for pair, ticker in pair_tickers.items():
        value, change, change5 = snap(ticker)
        pairs[pair] = {
            "ticker": ticker,
            "value": value,
            "change_pct": change,
            "change_5d_pct": change5,
        }

    # Each pair contributes its percentage move to the base currency and the
    # inverse move to the quote currency. This gives a transparent relative
    # strength reading without pretending to be an institutional index.
    pair_currencies = {
        "EURUSD": ("EUR", "USD"), "GBPUSD": ("GBP", "USD"),
        "USDJPY": ("USD", "JPY"), "AUDUSD": ("AUD", "USD"),
        "NZDUSD": ("NZD", "USD"), "USDCHF": ("USD", "CHF"),
        "USDCAD": ("USD", "CAD"), "EURJPY": ("EUR", "JPY"),
        "GBPJPY": ("GBP", "JPY"), "EURGBP": ("EUR", "GBP"),
        "USDINR": ("USD", "INR"), "USDCNY": ("USD", "CNY"),
    }
    contributions = {}
    for pair, (base, quote) in pair_currencies.items():
        ch = sf(pairs.get(pair, {}).get("change_pct"))
        if ch is None:
            continue
        contributions.setdefault(base, []).append(ch)
        contributions.setdefault(quote, []).append(-ch)

    raw_strength = {
        c: (sum(vals) / len(vals) if vals else None)
        for c, vals in contributions.items()
    }
    valid = [v for v in raw_strength.values() if v is not None]
    center = sum(valid) / len(valid) if valid else 0.0
    strength = {
        c: (round(v - center, 3) if v is not None else None)
        for c, v in raw_strength.items()
    }
    ranked = sorted(
        ((c, v) for c, v in strength.items() if v is not None),
        key=lambda x: x[1], reverse=True
    )

    # Cross-market drivers. These are context variables, not predictive signals.
    drivers = {}
    for key, ticker in {
        "WTI": "CL=F",
        "GOLD": "GC=F",
    }.items():
        value, change, change5 = snap(ticker)
        drivers[key] = {"value": value, "change_pct": change, "change_5d_pct": change5, "ticker": ticker}

    # Five-day currency strength uses the same transparent pair-contribution model,
    # but over a slower horizon. This prevents the dashboard from treating one
    # noisy session as a full regime change.
    contributions_5d = {}
    for pair, (base, quote) in pair_currencies.items():
        ch5 = sf(pairs.get(pair, {}).get("change_5d_pct"))
        if ch5 is None:
            continue
        contributions_5d.setdefault(base, []).append(ch5)
        contributions_5d.setdefault(quote, []).append(-ch5)
    raw_strength_5d = {c: (sum(vals) / len(vals) if vals else None) for c, vals in contributions_5d.items()}
    valid5 = [v for v in raw_strength_5d.values() if v is not None]
    center5 = sum(valid5) / len(valid5) if valid5 else 0.0
    strength_5d = {c: (round(v - center5, 3) if v is not None else None) for c, v in raw_strength_5d.items()}

    # Rank change is compared with the previous saved FX snapshot when available.
    previous_fx = previous_fx if isinstance(previous_fx, dict) else None
    previous_rank = {}
    if isinstance(previous_fx, dict):
        for i, item in enumerate(previous_fx.get("strength_rank") or []):
            if isinstance(item, dict) and item.get("currency"):
                previous_rank[str(item["currency"])] = i + 1
    rank_change = {}
    for i, (c, _) in enumerate(ranked, 1):
        rank_change[c] = (previous_rank[c] - i) if c in previous_rank else 0

    leader = ranked[0] if ranked else (None, None)
    laggard = ranked[-1] if ranked else (None, None)
    spread = (leader[1] - laggard[1]) if leader[1] is not None and laggard[1] is not None else None
    leader5 = max(((c, v) for c, v in strength_5d.items() if v is not None), key=lambda x: x[1], default=(None, None))
    laggard5 = min(((c, v) for c, v in strength_5d.items() if v is not None), key=lambda x: x[1], default=(None, None))

    # Pair-level relative edge plus a simple momentum regime. The edge is the
    # current currency-strength spread; acceleration is 1D minus 1/5 of 5D.
    for pair, payload in pairs.items():
        base, quote = pair_currencies.get(pair, (pair[:3], pair[3:]))
        b1, q1 = strength.get(base), strength.get(quote)
        b5, q5 = strength_5d.get(base), strength_5d.get(quote)
        edge = (b1 - q1) if b1 is not None and q1 is not None else None
        edge5 = (b5 - q5) if b5 is not None and q5 is not None else None
        acceleration = (edge - edge5 / 5.0) if edge is not None and edge5 is not None else None
        payload["relative_edge_pct"] = round(edge, 3) if edge is not None else None
        payload["relative_edge_5d_pct"] = round(edge5, 3) if edge5 is not None else None
        payload["momentum_acceleration"] = round(acceleration, 3) if acceleration is not None else None
        payload["regime"] = ("BULLISH" if edge is not None and edge > 0.05 and (acceleration is None or acceleration >= 0)
                              else "BEARISH" if edge is not None and edge < -0.05 and (acceleration is None or acceleration <= 0)
                              else "MIXED")

    return {
        "status": "live" if any(x.get("value") is not None for x in pairs.values()) else "unavailable",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "pairs": pairs,
        "currency_strength": strength,
        "currency_strength_5d": strength_5d,
        "strength_rank": [{"currency": c, "score": v, "score_5d": strength_5d.get(c), "rank_change": rank_change.get(c, 0)} for c, v in ranked],
        "strongest_currency": leader[0],
        "weakest_currency": laggard[0],
        "strength_summary": {
            "leader": leader[0], "leader_score": leader[1],
            "laggard": laggard[0], "laggard_score": laggard[1],
            "spread": round(spread, 3) if spread is not None else None,
            "leader_5d": leader5[0], "laggard_5d": laggard5[0],
        },
        "drivers": drivers,
        "method": "Relative currency strength is model-derived from 1D and 5D percentage changes across tracked FX pairs; not a forecast or trade signal.",
    }



def fetch_yfinance_fx_news():
    """Fetch FX headlines through yfinance's built-in Yahoo Finance news/search layer.

    This uses the same Yahoo access layer already used by the dashboard for prices,
    avoiding direct requests to Yahoo's search endpoint from GitHub Actions.
    Only headline metadata is stored.
    """
    items = []
    seen = set()
    currency_queries = {
        "USD": ["US dollar Federal Reserve DXY", "USD forex"],
        "EUR": ["EURUSD euro ECB", "euro forex"],
        "GBP": ["GBPUSD pound Bank England", "sterling forex"],
        "JPY": ["USDJPY yen Bank Japan", "Japanese yen forex"],
        "CHF": ["USDCHF Swiss franc SNB", "Swiss franc forex"],
        "AUD": ["AUDUSD Australian dollar RBA", "Australian dollar forex"],
        "CAD": ["USDCAD Canadian dollar Bank Canada", "Canadian dollar forex"],
        "NZD": ["NZDUSD New Zealand dollar RBNZ", "New Zealand dollar forex"],
        "INR": ["USDINR Indian rupee RBI", "Indian rupee forex"],
        "CNY": ["USDCNY yuan PBOC", "Chinese yuan forex"],
    }
    ticker_map = {
        "USD": "DX-Y.NYB",
        "EUR": "EURUSD=X",
        "GBP": "GBPUSD=X",
        "JPY": "JPY=X",
        "CHF": "CHF=X",
        "AUD": "AUDUSD=X",
        "CAD": "CAD=X",
        "NZD": "NZDUSD=X",
        "INR": "INR=X",
        "CNY": "CNY=X",
    }

    def add(story, currency):
        if not isinstance(story, dict):
            return
        title = str(story.get("title") or "").strip()
        link = str(story.get("link") or story.get("url") or "").strip()
        if not title:
            return
        key = re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()
        if key in seen:
            return
        seen.add(key)
        ts = story.get("providerPublishTime")
        published_at = None
        if isinstance(ts, (int, float)):
            try:
                published_at = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
            except Exception:
                pass
        elif isinstance(ts, str) and ts:
            published_at = ts
        source = story.get("publisher") or "Yahoo Finance"
        items.append({
            "title": title,
            "link": link,
            "source": str(source),
            "published_at": published_at,
            "currency": currency,
        })

    # First use ticker-native news because this shares the same yfinance path
    # already proven to work for the dashboard's market snapshots.
    for currency, ticker in ticker_map.items():
        try:
            stories = yf.Ticker(ticker).get_news(count=8)
            for story in stories or []:
                add(story, currency)
        except Exception:
            pass

    # Then use yfinance's supported Search API for broader currency headlines.
    for currency, queries in currency_queries.items():
        for query in queries:
            try:
                result = yf.Search(
                    query,
                    max_results=2,
                    news_count=6,
                    include_cb=False,
                    timeout=15,
                    raise_errors=False,
                )
                for story in getattr(result, "news", []) or []:
                    add(story, currency)
            except Exception:
                continue

    items.sort(key=lambda x: x.get("published_at") or "", reverse=True)
    return items[:30]

def fetch_fx_news():
    """Fetch and rank genuinely currency-relevant headlines.

    A feed can be tagged with a currency because of the query/ticker that produced
    it, but that tag is NOT trusted by itself.  The headline must contain explicit
    currency/central-bank/country evidence before it is shown under that currency.
    Generic company, China-tech, stock and commodity headlines are rejected.
    """
    currency_rules = {
        "USD": {
            "terms": [r"\bus dollar\b", r"\bdollar\b", r"\bdxy\b", r"\bfederal reserve\b", r"\bfed\b", r"\bfomc\b", r"\btreasury\b", r"\btreasuries\b", r"\bus yields?\b", r"\bamerican economy\b", r"\bus economy\b"],
            "countries": [r"\bunited states\b", r"\bus\b", r"\bu\.s\.\b", r"\bamerica\b", r"\bamerican\b"],
        },
        "EUR": {
            "terms": [r"\beuro\b", r"\beurozone\b", r"\beuro area\b", r"\becb\b", r"\beuropean central bank\b", r"\beurusd\b", r"\beuropean economy\b"],
            "countries": [r"\bgermany\b", r"\bfrance\b", r"\bitaly\b", r"\bspain\b", r"\bnetherlands\b", r"\beuropean union\b", r"\beu\b"],
        },
        "GBP": {
            "terms": [r"\bpound\b", r"\bsterling\b", r"\bgbpusd\b", r"\bbank of england\b", r"\bboe\b", r"\buk economy\b", r"\bbritish economy\b"],
            "countries": [r"\bunited kingdom\b", r"\buk\b", r"\bbritain\b", r"\bbritish\b", r"\bengland\b"],
        },
        "JPY": {
            "terms": [r"\byen\b", r"\busdjpy\b", r"\bbank of japan\b", r"\bboj\b", r"\bjapanese economy\b"],
            "countries": [r"\bjapan\b", r"\bjapanese\b"],
        },
        "CHF": {
            "terms": [r"\bswiss franc\b", r"\bchf\b", r"\bsnb\b", r"\bswiss national bank\b", r"\bswiss economy\b"],
            "countries": [r"\bswitzerland\b", r"\bswiss\b"],
        },
        "AUD": {
            "terms": [r"\baustralian dollar\b", r"\baussie\b", r"\baudusd\b", r"\brba\b", r"\breserve bank of australia\b", r"\baustralian economy\b"],
            "countries": [r"\baustralia\b", r"\baustralian\b"],
        },
        "CAD": {
            "terms": [r"\bcanadian dollar\b", r"\bloonie\b", r"\busdcad\b", r"\bbank of canada\b", r"\bboc\b", r"\bcanadian economy\b"],
            "countries": [r"\bcanada\b", r"\bcanadian\b"],
        },
        "NZD": {
            "terms": [r"\bnew zealand dollar\b", r"\bkiwi\b", r"\bnzdusd\b", r"\brbnz\b", r"\breserve bank of new zealand\b", r"\bnew zealand economy\b"],
            "countries": [r"\bnew zealand\b", r"\bnew zealanders?\b"],
        },
        "INR": {
            "terms": [r"\brupee\b", r"\bindian rupee\b", r"\busdinr\b", r"\brbi\b", r"\breserve bank of india\b", r"\bindian economy\b"],
            "countries": [r"\bindia\b", r"\bindian\b"],
        },
        "CNY": {
            "terms": [r"\byuan\b", r"\brenminbi\b", r"\busdcny\b", r"\bpboc\b", r"\bpeople's bank of china\b", r"\bchinese yuan\b", r"\bchinese economy\b"],
            "countries": [r"\bchina\b", r"\bchinese\b"],
        },
    }

    # Topics that are materially useful for FX decision-making.
    topic_rules = [
        ("MONETARY_POLICY", [r"central bank", r"interest rate", r"rate hike", r"rate cut", r"rate decision", r"policy rate", r"fomc", r"ecb", r"boe", r"boj", r"rba", r"rbnz", r"snb", r"rbi", r"pboc"]),
        ("INFLATION", [r"inflation", r"cpi", r"ppi", r"consumer prices?", r"producer prices?", r"price pressure"]),
        ("LABOR", [r"jobs", r"employment", r"payroll", r"nonfarm", r"unemployment", r"wages?", r"jobless claims"]),
        ("GROWTH", [r"gdp", r"growth", r"recession", r"manufacturing", r"services pmi", r"pmi", r"retail sales", r"industrial production"]),
        ("FX_MARKET", [r"forex", r"currency", r"exchange rate", r"fx market", r"dollar", r"euro", r"pound", r"yen", r"yuan", r"rupee", r"franc", r"sterling"]),
        ("INTERVENTION", [r"intervention", r"currency intervention", r"verbal intervention", r"defend.*currency", r"buying.*currency", r"selling.*currency"]),
        ("TRADE", [r"tariff", r"trade deficit", r"trade surplus", r"exports?", r"imports?", r"trade war", r"sanctions?"]),
        ("COMMODITIES", [r"oil", r"crude", r"opec", r"commodity", r"iron ore", r"copper"]),
        ("RISK", [r"risk[- ]off", r"risk[- ]on", r"safe haven", r"geopolit", r"war", r"sanctions?"]),
    ]
    high_topics = {"MONETARY_POLICY", "INFLATION", "LABOR", "INTERVENTION"}
    reject_terms = [
        r"earnings call", r"revenue", r"ipo", r"cash burn", r"shares rebound", r"stock jumps?", r"stock falls?",
        r"technology", r"ai company", r"artificial intelligence", r"quarterly results", r"company shares", r"investor relations",
        r"sports", r"celebrity", r"movie", r"entertainment", r"lottery", r"casino", r"recipe", r"restaurant",
    ]

    items = []
    seen_titles = set()

    def classify(title, provided=None):
        text = re.sub(r"\s+", " ", str(title or "").strip().lower())
        if not text:
            return None
        if any(re.search(pat, text) for pat in reject_terms):
            return None

        candidates = []
        for code, cfg in currency_rules.items():
            term_hits = [pat for pat in cfg["terms"] if re.search(pat, text)]
            country_hits = [pat for pat in cfg["countries"] if re.search(pat, text)]
            # Direct currency/central-bank/pair language is strong evidence.
            score = 0
            if term_hits:
                score += 5 + 2 * min(len(term_hits) - 1, 3)
            # Country-only evidence is accepted only when paired with FX/macro context.
            if country_hits:
                score += 2
            macro_context = any(re.search(pat, text) for pat in [
                r"forex", r"currency", r"exchange rate", r"central bank", r"interest rate", r"rate decision",
                r"inflation", r"cpi", r"ppi", r"gdp", r"jobs", r"employment", r"payroll", r"unemployment",
                r"yield", r"bond", r"tariff", r"trade", r"intervention", r"economy", r"economic",
            ])
            if country_hits and macro_context:
                score += 3
            if term_hits:
                candidates.append((score, code, term_hits, country_hits))

        if not candidates:
            return None
        candidates.sort(reverse=True, key=lambda x: x[0])
        score, code, term_hits, country_hits = candidates[0]
        if score < 5:
            return None

        topic = "OTHER"
        topic_score = 0
        for name, patterns in topic_rules:
            hits = sum(1 for pat in patterns if re.search(pat, text))
            if hits > topic_score:
                topic, topic_score = name, hits
        if topic_score == 0:
            return None

        # A provided feed tag is merely a fallback tie-breaker; never override
        # stronger headline evidence with it.
        if provided and str(provided).upper() == code:
            score += 1
        impact = "HIGH" if topic in high_topics or score >= 11 else ("MEDIUM" if score >= 7 else "LOW")
        return code, score, topic, impact

    def add_item(title, link, source, published_at, currency=None):
        title = (title or "").strip()
        link = (link or "").strip()
        source = (source or "News").strip()
        if not title:
            return
        normalized = re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()
        key = " ".join(normalized.split()[:20])
        if not key or key in seen_titles:
            return
        result = classify(title, currency)
        if not result:
            return
        code, score, topic, impact = result
        seen_titles.add(key)
        items.append({
            "title": title,
            "link": link,
            "source": source,
            "published_at": published_at,
            "currency": code,
            "relevance_score": score,
            "impact": impact,
            "topic": topic,
        })

    # 0) yfinance/Yahoo Finance layer.
    for story in fetch_yfinance_fx_news():
        add_item(story.get("title"), story.get("link"), story.get("source"), story.get("published_at"), story.get("currency"))

    # 1) Google News RSS: targeted macro/FX searches.
    google_queries = [
        'dollar DXY Federal Reserve forex', 'euro ECB EURUSD forex', 'pound sterling Bank of England forex',
        'yen Bank of Japan JPY forex', 'Swiss franc SNB forex', 'Australian dollar RBA forex',
        'Canadian dollar Bank of Canada oil forex', 'New Zealand dollar RBNZ forex',
        'rupee RBI USDINR forex', 'yuan PBOC USDCNY forex',
    ]
    for query in google_queries:
        url = "https://news.google.com/rss/search?q=" + urllib.parse.quote(query) + "&hl=en-US&gl=US&ceid=US:en"
        try:
            req = Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; MarketIntelligenceDashboard/2.0)"})
            with urlopen(req, timeout=12) as r:
                root = ET.fromstring(r.read())
            for item in root.findall(".//item")[:10]:
                title = item.findtext("title") or ""
                link = item.findtext("link") or ""
                pub = item.findtext("pubDate") or ""
                source_node = item.find("source")
                source = (source_node.text or "").strip() if source_node is not None else "Google News"
                published_at = pub
                if pub:
                    try:
                        published_at = parsedate_to_datetime(pub).astimezone(timezone.utc).isoformat()
                    except Exception:
                        pass
                add_item(title, link, source, published_at)
        except Exception:
            continue

    # 2) Investing.com Forex RSS fallback.
    for url in ["https://in.investing.com/rss/news_1.rss", "https://www.investing.com/rss/news_1.rss"]:
        try:
            req = Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; MarketIntelligenceDashboard/2.0)"})
            with urlopen(req, timeout=12) as r:
                root = ET.fromstring(r.read())
            for item in root.findall(".//item")[:40]:
                title = item.findtext("title") or ""
                link = item.findtext("link") or ""
                pub = item.findtext("pubDate") or ""
                published_at = pub
                if pub:
                    try:
                        published_at = parsedate_to_datetime(pub).astimezone(timezone.utc).isoformat()
                    except Exception:
                        pass
                add_item(title, link, "Investing.com", published_at)
            if items:
                break
        except Exception:
            continue

    # 3) Yahoo public search/news fallback.
    yahoo_queries = {
        "USD": "USD dollar DXY Fed", "EUR": "EURUSD euro ECB", "GBP": "GBPUSD pound Bank England",
        "JPY": "USDJPY yen Bank Japan", "CHF": "USDCHF Swiss franc SNB", "AUD": "AUDUSD Australian dollar RBA",
        "CAD": "USDCAD Canadian dollar Bank Canada oil", "NZD": "NZDUSD New Zealand dollar RBNZ",
        "INR": "USDINR rupee RBI", "CNY": "USDCNY yuan PBOC",
    }
    for currency, query in yahoo_queries.items():
        try:
            params = urllib.parse.urlencode({"q": query, "quotesCount": "0", "newsCount": "6"})
            url = "https://query1.finance.yahoo.com/v1/finance/search?" + params
            req = Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; MarketIntelligenceDashboard/2.0)"})
            with urlopen(req, timeout=8) as r:
                payload = json.loads(r.read().decode("utf-8", errors="replace"))
            for story in (payload.get("news") or [])[:6]:
                ts = story.get("providerPublishTime")
                published_at = None
                if isinstance(ts, (int, float)):
                    try:
                        published_at = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
                    except Exception:
                        pass
                add_item(story.get("title"), story.get("link") or story.get("url"), story.get("publisher") or "Yahoo Finance", published_at, currency)
        except Exception:
            continue

    # Highest decision value first, then freshness.  Hard cap keeps the UI compact.
    items.sort(key=lambda x: (x.get("relevance_score", 0), x.get("published_at") or ""), reverse=True)
    return {
        "status": "live" if items else "unavailable",
        "source": "Relevance-filtered yfinance/Yahoo + Google News RSS + Investing.com Forex RSS",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "items": items[:12],
    }

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



# --- MACRO DATA LAYER -------------------------------------------------------
# FRED supplies economic releases; Treasury supplies the official daily
# par-yield curve. The workflow passes FRED_API_KEY from GitHub Actions.
FRED_SERIES = {
    'fed_funds':'DFF', 'sofr':'SOFR',
    'real_10y':'DFII10', 'breakeven_10y':'T10YIE',
    'cpi':'CPIAUCSL', 'core_cpi':'CPILFESL', 'pce':'PCEPI', 'core_pce':'PCEPILFE', 'ppi':'PPIACO',
    'unemployment':'UNRATE', 'payrolls':'PAYEMS', 'avg_hourly_earnings':'CES0500000003',
    'initial_claims':'ICSA', 'continuing_claims':'CCSA', 'jolts':'JTSJOL', 'labor_participation':'CIVPART',
    'gdp':'GDPC1', 'gdp_growth':'A191RL1Q225SBEA', 'industrial_production':'INDPRO',
    'retail_sales':'RSAFS', 'housing_starts':'HOUST', 'building_permits':'PERMIT',
    'consumer_sentiment':'UMCSENT',
    'fed_balance_sheet':'WALCL', 'm2':'M2SL', 'rrp':'RRPONTSYD', 'tga':'WTREGEN',
    'financial_conditions':'NFCI', 'ig_spread':'BAMLC0A0CM', 'hy_spread':'BAMLH0A0HYM2',
}


def fred_fetch_series(series_id, api_key, limit=100):
    """Return recent numeric FRED observations as [{date,value}]."""
    if not api_key:
        return []
    q = urllib.parse.urlencode({
        'series_id': series_id,
        'api_key': api_key,
        'file_type': 'json',
        'sort_order': 'desc',
        'limit': str(limit),
    })
    url = 'https://api.stlouisfed.org/fred/series/observations?' + q
    try:
        req = Request(url, headers={'User-Agent':'market-intelligence-dashboard/1.0'})
        with urlopen(req, timeout=20) as r:
            payload = json.loads(r.read().decode('utf-8'))
        out = []
        for row in payload.get('observations', []):
            v = sf(row.get('value'))
            if v is not None:
                out.append({'date': row.get('date'), 'value': v})
        return out
    except Exception:
        return []


def fetch_fred_macro():
    """Build the frontend-compatible macro.series structure."""
    api_key = (os.environ.get('FRED_API_KEY') or '').strip()
    if len(api_key) < 20:
        return {}, False

    from concurrent.futures import ThreadPoolExecutor, as_completed
    result = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        jobs = {pool.submit(fred_fetch_series, sid, api_key, 100): key for key, sid in FRED_SERIES.items()}
        for fut in as_completed(jobs):
            key = jobs[fut]
            try:
                obs = fut.result()
            except Exception:
                obs = []
            if not obs:
                continue
            latest = obs[0]
            previous = obs[1] if len(obs) > 1 else None
            result[key] = {
                'series_id': FRED_SERIES[key],
                'value': latest['value'],
                'date': latest.get('date'),
                'previous': previous['value'] if previous else None,
                'previous_date': previous.get('date') if previous else None,
            }

    return result, bool(result)


def fetch_treasury_curve():
    """Fetch the current month's official Treasury par-yield curve."""
    now = datetime.now(timezone.utc)
    url = (
        'https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml'
        f'?data=daily_treasury_yield_curve&field_tdr_date_value_month={now.strftime("%Y%m")}'
    )
    try:
        req = Request(url, headers={'User-Agent':'market-intelligence-dashboard/1.0'})
        with urlopen(req, timeout=20) as r:
            root = ET.fromstring(r.read())
    except Exception:
        return {}

    wanted = {
        'BC_1MONTH':'y1m', 'BC_3MONTH':'y3m', 'BC_6MONTH':'y6m',
        'BC_1YEAR':'y1', 'BC_2YEAR':'y2', 'BC_5YEAR':'y5',
        'BC_10YEAR':'y10', 'BC_20YEAR':'y20', 'BC_30YEAR':'y30',
    }
    rows = []
    for entry in root.iter():
        if entry.tag.split('}')[-1].lower() != 'entry':
            continue
        row = {}
        for child in entry.iter():
            tag = child.tag.split('}')[-1].upper()
            if tag in wanted or tag == 'NEW_DATE':
                row[tag] = child.text
        if row.get('NEW_DATE'):
            rows.append(row)
    if not rows:
        return {}
    rows.sort(key=lambda x: x.get('NEW_DATE') or '', reverse=True)
    latest = rows[0]
    out = {'date': latest.get('NEW_DATE')}
    for src_key, out_key in wanted.items():
        out[out_key] = sf(latest.get(src_key))
    return out


def yoy(obs, months=12):
    if len(obs) <= months:
        return None
    a, b = obs[0].get('value'), obs[months].get('value')
    if a is None or b in (None, 0):
        return None
    return (a / b - 1.0) * 100.0


def build_macro(previous_macro=None):
    series, fred_live = fetch_fred_macro()
    curve = fetch_treasury_curve()
    if not series and not curve:
        return None

    # Re-query the few monthly series that need a 12-month comparison.
    api_key = (os.environ.get('FRED_API_KEY') or '').strip()
    yoy_map = {}
    if api_key:
        for key in ('cpi','core_cpi','pce','core_pce','m2','payrolls'):
            sid = FRED_SERIES[key]
            obs = fred_fetch_series(sid, api_key, 20)
            yoy_map[key] = yoy(obs, 12)

    cpi_yoy = yoy_map.get('cpi')
    core_cpi_yoy = yoy_map.get('core_cpi')
    pce_yoy = yoy_map.get('pce')
    core_pce_yoy = yoy_map.get('core_pce')
    gdp_growth = series.get('gdp_growth', {}).get('value')
    unemployment = series.get('unemployment', {}).get('value')
    hy = series.get('hy_spread', {}).get('value')
    nfci = series.get('financial_conditions', {}).get('value')
    walcl = series.get('fed_balance_sheet', {}).get('value')
    walcl_prev = series.get('fed_balance_sheet', {}).get('previous')
    m2_yoy = yoy_map.get('m2')

    inflation_score = 0
    if cpi_yoy is not None:
        inflation_score += 1 if cpi_yoy > 3.0 else -1 if cpi_yoy < 2.0 else 0
    if core_pce_yoy is not None:
        inflation_score += 1 if core_pce_yoy > 3.0 else -1 if core_pce_yoy < 2.0 else 0
    inflation = 'HOT' if inflation_score >= 1 else 'COOLING' if inflation_score <= -1 else 'STABLE'

    labor = 'TIGHT' if unemployment is not None and unemployment < 4.5 else 'SOFTENING' if unemployment is not None and unemployment >= 5.0 else 'BALANCED'

    if gdp_growth is not None:
        growth = 'EXPANDING' if gdp_growth >= 2.0 else 'SLOWING' if gdp_growth >= 0 else 'CONTRACTING'
    else:
        growth = 'UNKNOWN'

    liquidity_score = 0
    if walcl is not None and walcl_prev is not None:
        liquidity_score += 1 if walcl > walcl_prev else -1
    if m2_yoy is not None:
        liquidity_score += 1 if m2_yoy > 3 else -1 if m2_yoy < 0 else 0
    if nfci is not None:
        liquidity_score += 1 if nfci < 0 else -1 if nfci > 0.5 else 0
    liquidity = 'EXPANDING' if liquidity_score >= 1 else 'TIGHTENING' if liquidity_score <= -1 else 'NEUTRAL'

    if hy is not None:
        credit = 'STRESS' if hy >= 6.0 else 'ELEVATED' if hy >= 4.5 else 'BENIGN'
    else:
        credit = 'UNKNOWN'

    overall_score = 0
    overall_score += 1 if growth == 'EXPANDING' else -1 if growth == 'CONTRACTING' else 0
    overall_score += 1 if liquidity == 'EXPANDING' else -1 if liquidity == 'TIGHTENING' else 0
    overall_score -= 1 if inflation == 'HOT' else 0
    overall_score -= 1 if credit == 'STRESS' else 0
    overall = 'SUPPORTIVE' if overall_score >= 2 else 'CAUTIOUS' if overall_score <= -1 else 'MIXED'

    # Official 2026 FOMC meeting windows used for the dashboard's event card.
    fomc = [
        ('2026-01-27','2026-01-28'), ('2026-03-17','2026-03-18'),
        ('2026-04-28','2026-04-29'), ('2026-06-16','2026-06-17'),
        ('2026-07-28','2026-07-29'), ('2026-09-15','2026-09-16'),
        ('2026-10-27','2026-10-28'), ('2026-12-08','2026-12-09'),
    ]
    today = datetime.now(timezone.utc).date()
    next_meeting = None
    is_sep = False
    for start, end in fomc:
        if datetime.fromisoformat(end).date() >= today:
            next_meeting = f'{start} → {end}'
            is_sep = start.startswith('2026-09-')
            break

    macro = {
        'status': 'live' if fred_live and curve else ('partial' if fred_live or curve else 'unavailable'),
        'updated_at': datetime.now(timezone.utc).isoformat(),
        'series': series,
        'regime': {
            'inflation': inflation, 'labor': labor, 'growth': growth,
            'liquidity': liquidity, 'credit': credit, 'overall': overall,
        },
        'fomc': {'next_meeting': next_meeting, 'is_sep': is_sep},
        'derived': {
            'cpi_yoy': cpi_yoy, 'core_cpi_yoy': core_cpi_yoy,
            'pce_yoy': pce_yoy, 'core_pce_yoy': core_pce_yoy, 'm2_yoy': m2_yoy,
        },
    }
    if curve:
        macro['treasury_curve'] = curve
        # Treasury spread calculations in percentage points.  Keep these in
        # the macro payload so the frontend does not have to reconstruct them.
        def spread(long_key, short_key):
            a, b = curve.get(long_key), curve.get(short_key)
            return round(a - b, 4) if a is not None and b is not None else None
        macro['2s10s'] = spread('y10', 'y2')
        macro['5s30s'] = spread('y30', 'y5')
        macro['3m10y'] = spread('y10', 'y3m')

        # Curve direction is based on the change from the previous saved
        # dashboard snapshot. This is a descriptive regime signal, not a
        # trading prediction.
        prev = previous_macro or {}
        pairs = [('2s10s', macro['2s10s']), ('5s30s', macro['5s30s']), ('3m10y', macro['3m10y'])]
        changes = {}
        for key, cur in pairs:
            old = prev.get(key)
            changes[key] = round(cur - old, 4) if cur is not None and old is not None else None

        valid_changes = [v for v in changes.values() if v is not None]
        eps = 0.005
        up = sum(v > eps for v in valid_changes)
        down = sum(v < -eps for v in valid_changes)
        if not valid_changes:
            direction = 'AWAITING HISTORY'
        elif up >= 2 and down == 0:
            direction = 'STEEPENING'
        elif down >= 2 and up == 0:
            direction = 'FLATTENING'
        elif up == 0 and down == 0:
            direction = 'STABLE'
        else:
            direction = 'MIXED'

        spreads_now = [macro.get(k) for k in ('2s10s','5s30s','3m10y')]
        positive_count = sum(v is not None and v > 0 for v in spreads_now)
        inverted = any(v is not None and v < 0 for v in spreads_now)
        if inverted:
            nq_implication = 'CAUTION'
            interpretation = 'At least one tracked curve segment is inverted; treat this as a macro caution signal, not a timing signal.'
        elif direction == 'STEEPENING' and positive_count == 3:
            nq_implication = 'MODERATELY SUPPORTIVE'
            interpretation = 'All three tracked spreads are positive and the curve is broadly steepening versus the prior snapshot; this is generally more supportive for risk appetite, all else equal.'
        elif direction == 'FLATTENING':
            nq_implication = 'CAUTIOUS'
            interpretation = 'The curve is broadly flattening versus the prior snapshot; watch growth and policy expectations alongside real yields and credit.'
        elif direction == 'STABLE':
            nq_implication = 'NEUTRAL'
            interpretation = 'The curve is broadly stable; use inflation, real yields, liquidity and credit for the stronger macro signal.'
        else:
            nq_implication = 'MIXED'
            interpretation = 'Curve segments are sending mixed signals; avoid treating the curve alone as a directional NQ or FX trigger.'

        macro['curve_signal'] = {
            'direction': direction,
            'changes_pp': changes,
            'nq_implication': nq_implication,
            'interpretation': interpretation,
            'confidence': 'HIGH' if len(valid_changes) == 3 and (up >= 2 and down == 0 or down >= 2 and up == 0) else 'MEDIUM' if valid_changes else 'LOW',
            'method': 'Direction compares current spreads with the previous saved dashboard snapshot; interpretation is heuristic and not a forecast.'
        }
    return macro


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
        v, ch, _ = snap(ticker)
        if v is not None:
            d["prices"][key] = v
            d["prices"][key + "_change"] = ch

    # FX command-center layer: major pairs, relative currency strength and
    # cross-market commodity drivers. This is additive and does not alter GEX.
    d["fx"] = fetch_fx_snapshot(d.get("fx") if isinstance(d.get("fx"), dict) else None)
    fx_pairs = d["fx"].get("pairs", {})
    for pair, payload in fx_pairs.items():
        if payload.get("value") is not None:
            d["prices"][pair.lower()] = payload["value"]
            d["prices"][pair.lower() + "_change"] = payload.get("change_pct")
    for key, payload in d["fx"].get("drivers", {}).items():
        k = key.lower()
        if payload.get("value") is not None:
            d["prices"][k] = payload["value"]
            d["prices"][k + "_change"] = payload.get("change_pct")

    # Yahoo's ^IRX, ^TNX and ^TYX values are already percent values.
    for key, ticker in [
        ("y3m", "^IRX"), ("y10", "^TNX"), ("y30", "^TYX")
    ]:
        v, _, _ = snap(ticker)
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

    # Preserve the last completed live 0DTE snapshot so pre-market can display
    # the previous session's final state without pretending stale quotes are live.
    previous_live_0dte = None
    try:
        old_profile = (options.get("profiles") or {}).get("0DTE")
        if old_profile and old_profile.get("status") == "live" and old_profile.get("heatmap"):
            previous_live_0dte = json.loads(json.dumps(old_profile))
        elif options.get("last_closed_0dte") and options["last_closed_0dte"].get("heatmap"):
            previous_live_0dte = json.loads(json.dumps(options["last_closed_0dte"]))
    except Exception:
        previous_live_0dte = None

    try:
        # REFINED GEX ENGINE -------------------------------------------------
        # This is intentionally more than a single Black-Scholes calculation.
        # The model uses:
        #   1) multiple expiries,
        #   2) intraday time-to-expiry for 0DTE,
        #   3) forward estimation from put/call parity when possible,
        #   4) market-implied-volatility inputs with quote-based IV recovery,
        #   5) a smoothed strike smile for missing/noisy IVs,
        #   6) Black-76/BS-consistent gamma, and
        #   7) full repricing of gamma across a price grid for the flip.
        # It remains a MODEL of dealer positioning, not observed inventory.
        t = yf.Ticker("^NDX")
        expiries = list(t.options or [])
        spot = sf(d["prices"].get("ndx"))
        if not expiries or spot is None:
            raise ValueError("Missing NDX spot or option expiries")

        ny = ZoneInfo("America/New_York")
        now_ny = datetime.now(ny)
        today_ny = now_ny.date()
        market_open = dt_time(9, 30)
        market_close = dt_time(16, 0)
        now_clock = now_ny.time()
        is_weekday = today_ny.weekday() < 5
        today_expiry_exists = any(ed == today_ny for _, ed in parsed)
        if is_weekday and today_expiry_exists and now_clock < market_open:
            session_state = "PRE_MARKET"
        elif is_weekday and today_expiry_exists and market_open <= now_clock < market_close:
            session_state = "LIVE"
        elif is_weekday and today_expiry_exists and now_clock >= market_close:
            session_state = "CLOSED"
        else:
            session_state = "CLOSED"

        # A same-day expiry is a genuine 0DTE only while the regular U.S.
        # session is open. After 4:00 p.m. ET it is expired and must disappear
        # from the active curve. Before 9:30 a.m. ET we deliberately do not
        # model a moving 0DTE from stale pre-market quotes; the dashboard uses
        # the previous session's final 0DTE snapshot instead.
        if session_state == "LIVE":
            future = [(e, ed) for e, ed in parsed if ed >= today_ny]
        else:
            future = [(e, ed) for e, ed in parsed if ed > today_ny]
        selected = future[:12]
        if not selected:
            raise ValueError("No current/future NDX expiries")

        r = sf(d.get("rates", {}).get("y10"))
        r = (r / 100.0 if r is not None and abs(r) > 1.5 else (r or 0.0))
        MULT = 100.0

        def sane_iv(v):
            v = sf(v)
            return v if v is not None and 0.01 <= v <= 2.5 else None

        def mid_price(row):
            bid = sf(row.get("bid"))
            ask = sf(row.get("ask"))
            last = sf(row.get("lastPrice"))
            if bid is not None and ask is not None and ask > 0 and bid >= 0 and ask >= bid:
                m = (bid + ask) / 2.0
                if m > 0:
                    return m
            return last if last is not None and last > 0 else None

        def norm_cdf(x):
            return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

        def black_price(F, K, vol, T, disc, is_call):
            if F <= 0 or K <= 0 or vol <= 0 or T <= 0:
                return 0.0
            root = math.sqrt(T)
            d1 = (math.log(F / K) + 0.5 * vol * vol * T) / (vol * root)
            d2 = d1 - vol * root
            if is_call:
                return disc * (F * norm_cdf(d1) - K * norm_cdf(d2))
            return disc * (K * norm_cdf(-d2) - F * norm_cdf(-d1))

        def implied_vol_from_price(price, F, K, T, disc, is_call):
            # Robust bisection. We only use this as a recovery path when the
            # vendor IV is missing/unusable; it is not used blindly on bad quotes.
            if price is None or price <= 0 or F <= 0 or K <= 0 or T <= 0:
                return None
            intrinsic = disc * max((F - K) if is_call else (K - F), 0.0)
            upper = disc * (F if is_call else K)
            if price < intrinsic * 0.995 or price > upper * 1.005:
                return None
            lo, hi = 0.005, 2.5
            p_hi = black_price(F, K, hi, T, disc, is_call)
            if p_hi < price:
                return None
            for _ in range(48):
                mid = (lo + hi) / 2.0
                p = black_price(F, K, mid, T, disc, is_call)
                if p < price:
                    lo = mid
                else:
                    hi = mid
            iv = (lo + hi) / 2.0
            return iv if sane_iv(iv) else None

        def solve_linear3(A, b):
            # Small 3x3 Gaussian elimination; avoids adding scipy to the
            # GitHub Actions dependency footprint.
            M = [list(A[i]) + [b[i]] for i in range(3)]
            for col in range(3):
                pivot = max(range(col, 3), key=lambda i: abs(M[i][col]))
                if abs(M[pivot][col]) < 1e-12:
                    return None
                M[col], M[pivot] = M[pivot], M[col]
                div = M[col][col]
                for j in range(col, 4):
                    M[col][j] /= div
                for i in range(3):
                    if i == col:
                        continue
                    fac = M[i][col]
                    for j in range(col, 4):
                        M[i][j] -= fac * M[col][j]
            return [M[i][3] for i in range(3)]

        def fit_smile(points):
            # Quadratic IV smile in log-moneyness. Near-ATM observations get
            # more weight, while raw valid market IVs are retained as anchors.
            if len(points) < 5:
                return None
            A = [[0.0, 0.0, 0.0] for _ in range(3)]
            b = [0.0, 0.0, 0.0]
            for x, vol in points:
                w = math.exp(-min(abs(x), 0.30) ** 2 / (2 * 0.12 ** 2))
                z = [1.0, x, x * x]
                for i in range(3):
                    b[i] += w * z[i] * vol
                    for j in range(3):
                        A[i][j] += w * z[i] * z[j]
            coef = solve_linear3(A, b)
            if coef is None:
                return None
            return lambda x: max(0.01, min(2.5, coef[0] + coef[1] * x + coef[2] * x * x))

        def expiry_time(ed):
            if ed == today_ny and session_state == "LIVE":
                exp_dt = datetime.combine(ed, market_close, tzinfo=ny)
                seconds = max((exp_dt - now_ny).total_seconds(), 5 * 60)
                return seconds / (365 * 24 * 3600), "0DTE"
            exp_dt = datetime.combine(ed, market_close, tzinfo=ny)
            seconds = max((exp_dt - now_ny).total_seconds(), 3600)
            days = (ed - today_ny).days
            return seconds / (365 * 24 * 3600), ("1DTE" if days == 1 else "NEAREST")

        all_rows = []
        expiry_stats = []
        expiry_surfaces = []
        expiry_errors = []
        successful_expiries = []
        direct_iv_count = 0
        solved_iv_count = 0
        fitted_iv_count = 0

        for expiry, expiry_date in selected:
            try:
                T, mode = expiry_time(expiry_date)
                ch = None
                last_err = None
                for attempt in range(3):
                    try:
                        ch = t.option_chain(expiry)
                        break
                    except Exception as exc:  # retry transient Yahoo/yfinance failures
                        last_err = exc
                        if attempt < 2:
                            import time
                            time.sleep(1.5 * (attempt + 1))
                if ch is None:
                    expiry_errors.append({"expiry": expiry, "error": str(last_err)[:300]})
                    continue
                successful_expiries.append(expiry)
                c, p = ch.calls.copy(), ch.puts.copy()
                if c.empty and p.empty:
                    continue

                # Normalize the rows we need. OI is kept as-is; it is the
                # standing inventory input, not an intraday flow measure.
                for df in (c, p):
                    for col in ("strike", "openInterest", "impliedVolatility", "bid", "ask", "lastPrice"):
                        if col not in df.columns:
                            df[col] = None

                disc = math.exp(-r * T)

                # Estimate forward from put-call parity using near-ATM pairs.
                # For European index options this is more coherent than using
                # spot directly when computing the volatility surface.
                by_k_c = {float(row["strike"]): row for _, row in c.iterrows() if sf(row.get("strike")) is not None}
                by_k_p = {float(row["strike"]): row for _, row in p.iterrows() if sf(row.get("strike")) is not None}
                forward_samples = []
                for K in sorted(set(by_k_c) & set(by_k_p)):
                    if abs(K / spot - 1.0) > 0.08:
                        continue
                    cm = mid_price(by_k_c[K])
                    pm = mid_price(by_k_p[K])
                    if cm is None or pm is None:
                        continue
                    Fk = K + (cm - pm) / disc
                    if 0.90 * spot < Fk < 1.10 * spot:
                        weight = 1.0 / (1.0 + abs(math.log(K / spot)) * 50.0)
                        forward_samples.append((Fk, weight))
                if forward_samples:
                    forward_samples.sort(key=lambda x: x[0])
                    # Weighted mean is less jumpy than a single strike.
                    F = sum(x * w for x, w in forward_samples) / sum(w for _, w in forward_samples)
                else:
                    F = spot * math.exp(r * T)

                raw_points = []
                rows = []
                for side, df in (("call", c), ("put", p)):
                    for _, row in df.iterrows():
                        K = sf(row.get("strike"))
                        if K is None or K <= 0:
                            continue
                        oi = sf(row.get("openInterest")) or 0.0
                        vendor_iv = sane_iv(row.get("impliedVolatility"))
                        x = math.log(K / F)
                        iv = vendor_iv
                        source = "vendor_iv" if vendor_iv is not None else None
                        if iv is not None:
                            direct_iv_count += 1
                        if iv is None:
                            price = mid_price(row)
                            iv = implied_vol_from_price(price, F, K, T, disc, side == "call")
                            if iv is not None:
                                source = "quote_solved_iv"
                                solved_iv_count += 1
                        if iv is not None and abs(x) <= 0.35:
                            raw_points.append((x, iv))
                        rows.append({"side": side, "K": K, "oi": oi, "x": x, "iv": iv, "source": source})

                smile = fit_smile(raw_points)
                surface_points = 0
                for row in rows:
                    if row["iv"] is None and smile is not None and abs(row["x"]) <= 0.35:
                        row["iv"] = smile(row["x"])
                        row["source"] = "smoothed_surface"
                        fitted_iv_count += 1
                    if row["iv"] is not None:
                        surface_points += 1

                expiry_net = 0.0
                expiry_call_oi = 0.0
                expiry_put_oi = 0.0
                nonzero = 0
                for row in rows:
                    K = row["K"]
                    iv = row["iv"]
                    oi = row["oi"]
                    # Ignore very far OTM contracts in the live GEX surface;
                    # their listed OI is useful context but their numerical
                    # gamma is effectively noise for the near-term map.
                    if abs(K / spot - 1.0) > 0.15:
                        continue
                    if row["side"] == "call":
                        expiry_call_oi += oi
                    else:
                        expiry_put_oi += oi
                    if oi > 0:
                        nonzero += 1
                    if iv is None or oi <= 0:
                        continue

                    root = math.sqrt(T)
                    # Black-76 gamma converted back to spot gamma. This keeps
                    # the forward/discount relationship explicit and is more
                    # internally consistent for index options than assuming
                    # spot == forward at every expiry.
                    d1 = (math.log(F / K) + 0.5 * iv * iv * T) / (iv * root)
                    pdf = math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi)
                    gamma = disc * pdf * F / (spot * spot * iv * root)
                    signed = 1.0 if row["side"] == "call" else -1.0
                    gex = signed * gamma * oi * MULT * spot * spot * 0.01
                    all_rows.append({
                        "strike": K, "expiry": expiry, "expiry_mode": mode,
                        "call_oi": oi if row["side"] == "call" else 0.0,
                        "put_oi": oi if row["side"] == "put" else 0.0,
                        "call_gex": gex if row["side"] == "call" else 0.0,
                        "put_gex": gex if row["side"] == "put" else 0.0,
                        "net_gex": gex,
                        "iv": iv,
                    })
                    expiry_net += gex

                expiry_stats.append({
                    "expiry": expiry, "mode": mode,
                    "days": max((expiry_date - today_ny).days, 0),
                    "net_gex": expiry_net,
                    "call_oi": expiry_call_oi, "put_oi": expiry_put_oi,
                    "nonzero_contract_rows": nonzero,
                    "surface_points": surface_points,
                    "forward": F,
                    "atm_iv": (sum(v for x, v in raw_points if abs(x) <= 0.03) / max(1, sum(1 for x, v in raw_points if abs(x) <= 0.03)) * 100) if any(abs(x) <= 0.03 for x, v in raw_points) else None,
                })
            except Exception:
                continue

        if not all_rows:
            detail = "; ".join(f"{x.get('expiry')}: {x.get('error')}" for x in expiry_errors[:4])
            raise ValueError("No usable option rows" + (f" ({detail})" if detail else ""))

        by_strike = {}
        for row in all_rows:
            K = row["strike"]
            a = by_strike.setdefault(K, {
                "strike": K, "call_oi": 0.0, "put_oi": 0.0,
                "call_gex": 0.0, "put_gex": 0.0, "net_gex": 0.0,
            })
            for key in ("call_oi", "put_oi", "call_gex", "put_gex", "net_gex"):
                a[key] += row[key]

        heat = [by_strike[k] for k in sorted(by_strike)]
        heat = [x for x in heat if abs(x["strike"] - spot) <= spot * 0.15]
        total = sum(x["net_gex"] for x in heat)
        total_call_oi = sum(x["call_oi"] for x in heat)
        total_put_oi = sum(x["put_oi"] for x in heat)
        nonzero = sum(1 for x in heat if x["call_oi"] > 0 or x["put_oi"] > 0)
        near_atm_nonzero = sum(1 for x in heat if abs(x["strike"] - spot) <= spot * 0.05 and (x["call_oi"] > 0 or x["put_oi"] > 0))
        nonzero_ratio = nonzero / len(heat) if heat else 0.0

        # The headline ATM IV / expected move comes from the nearest expiry,
        # not from the aggregate multi-expiry GEX book.
        nearest = expiry_stats[0]
        nearest_expiry = nearest["expiry"]
        nearest_mode = nearest["mode"]
        nearest_iv = nearest.get("atm_iv")
        T_near, _ = expiry_time(selected[0][1])
        if nearest_iv is not None:
            iv_dec = nearest_iv / 100.0
            move = spot * iv_dec * math.sqrt(T_near)
            options.update({
                "atm_iv": nearest_iv,
                "expected_move_pct": (move / spot) * 100,
                "expected_move_points": move,
                "time_to_expiry_hours": T_near * 365 * 24,
            })

        # OI PCR should reflect the nearest expiry, while all-expiry OI is
        # retained in coverage for transparency.
        nearest_call_oi = nearest.get("call_oi") or 0.0
        nearest_put_oi = nearest.get("put_oi") or 0.0
        if nearest_call_oi > 0:
            options["pcr_oi"] = nearest_put_oi / nearest_call_oi

        # Reprice the whole modeled surface at many hypothetical spot levels.
        # This is more robust than interpolating the already-aggregated GEX bars.
        def gex_at(test_spot):
            total_g = 0.0
            for row in all_rows:
                K = row["strike"]
                iv = row["iv"]
                if iv is None or row["call_oi"] + row["put_oi"] <= 0:
                    continue
                # Recover expiry T and forward from the expiry table.
                stat = next((x for x in expiry_stats if x["expiry"] == row["expiry"]), None)
                if not stat:
                    continue
                exp_date = datetime.fromisoformat(row["expiry"]).date()
                T, _ = expiry_time(exp_date)
                F0 = stat.get("forward") or spot
                # Preserve the inferred carry from the expiry's parity forward.
                carry = math.log(max(F0, 1e-9) / max(spot, 1e-9)) / max(T, 1e-9)
                F_test = test_spot * math.exp(carry * T)
                root = math.sqrt(T)
                d1 = (math.log(F_test / K) + 0.5 * iv * iv * T) / (iv * root)
                pdf = math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi)
                disc = math.exp(-r * T)
                gamma = disc * pdf * F_test / (test_spot * test_spot * iv * root)
                signed_oi = row["call_oi"] - row["put_oi"]
                total_g += gamma * signed_oi * MULT * test_spot * test_spot * 0.01
            return total_g

        xs = [spot * 0.90 + spot * 0.20 * i / 240 for i in range(241)]
        ys = [gex_at(x) for x in xs]
        flips = []
        for i in range(len(xs) - 1):
            if ys[i] == 0:
                flips.append(xs[i])
            elif ys[i] * ys[i + 1] < 0:
                flips.append(xs[i] - ys[i] * (xs[i + 1] - xs[i]) / (ys[i + 1] - ys[i]))
        gamma_flip = min(flips, key=lambda x: abs(x - spot)) if flips else None

        reasons = []
        if len(expiry_stats) < 6:
            reasons.append("limited expiry coverage")
        if len(heat) < 80:
            reasons.append("fewer than 80 modeled strikes")
        if nonzero < 40:
            reasons.append("sparse open interest")
        if near_atm_nonzero < 10:
            reasons.append("thin OI near spot")
        if direct_iv_count + solved_iv_count < 30:
            reasons.append("limited volatility-surface observations")
        confidence = "HIGH" if len(expiry_stats) >= 8 and len(heat) >= 80 and near_atm_nonzero >= 10 and direct_iv_count + solved_iv_count >= 30 else "MEDIUM" if len(expiry_stats) >= 4 and len(heat) >= 50 else "LOW"
        if reasons and confidence == "HIGH":
            confidence = "MEDIUM"

        modes = [x["mode"] for x in expiry_stats]
        expiry_mode = "0DTE" if "0DTE" in modes else ("1DTE" if "1DTE" in modes else "MULTI-EXPIRY")
        # Build switchable profiles from the SAME refined rows used by the
        # headline GEX calculation. This prevents the frontend selector from
        # accidentally retaining an older/stale profile snapshot.
        stat_by_expiry = {x["expiry"]: x for x in expiry_stats}

        def profile_gex_at(profile_rows, test_spot):
            total_g = 0.0
            for row in profile_rows:
                K = row["strike"]
                iv = row.get("iv")
                oi = row.get("call_oi", 0.0) + row.get("put_oi", 0.0)
                if iv is None or oi <= 0 or K <= 0:
                    continue
                stat = stat_by_expiry.get(row["expiry"])
                if not stat:
                    continue
                exp_date = datetime.fromisoformat(row["expiry"]).date()
                T, _ = expiry_time(exp_date)
                F0 = stat.get("forward") or spot
                carry = math.log(max(F0, 1e-9) / max(spot, 1e-9)) / max(T, 1e-9)
                F_test = test_spot * math.exp(carry * T)
                root = math.sqrt(T)
                d1 = (math.log(F_test / K) + 0.5 * iv * iv * T) / (iv * root)
                pdf = math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi)
                disc = math.exp(-r * T)
                gamma = disc * pdf * F_test / (test_spot * test_spot * iv * root)
                total_g += gamma * (row.get("call_oi", 0.0) - row.get("put_oi", 0.0)) * MULT * test_spot * test_spot * 0.01
            return total_g

        def make_profile(profile_rows, label):
            if not profile_rows:
                return {"status": "unavailable", "label": label, "heatmap": [], "expiry": None, "net_gex": None, "call_wall": None, "put_wall": None, "gamma_flip": None}
            by_k = {}
            for row in profile_rows:
                K = row["strike"]
                a = by_k.setdefault(K, {"strike": K, "call_oi": 0.0, "put_oi": 0.0, "call_gex": 0.0, "put_gex": 0.0, "net_gex": 0.0})
                for key in ("call_oi", "put_oi", "call_gex", "put_gex", "net_gex"):
                    a[key] += row.get(key, 0.0)
            ph = [by_k[k] for k in sorted(by_k) if abs(k - spot) <= spot * 0.15]
            if not ph:
                return {"status": "unavailable", "label": label, "heatmap": []}
            ptotal = sum(x["net_gex"] for x in ph)
            call_rows = [x for x in ph if x["call_gex"] > 0]
            put_rows = [x for x in ph if x["put_gex"] < 0]
            call_wall = max(call_rows, key=lambda x: x["call_gex"])["strike"] if call_rows else None
            put_wall = min(put_rows, key=lambda x: x["put_gex"])["strike"] if put_rows else None
            px = [spot * 0.90 + spot * 0.20 * i / 120 for i in range(121)]
            py = [profile_gex_at(profile_rows, x) for x in px]
            flips = []
            for j in range(len(px) - 1):
                if py[j] == 0:
                    flips.append(px[j])
                elif py[j] * py[j + 1] < 0:
                    flips.append(px[j] - py[j] * (px[j + 1] - px[j]) / (py[j + 1] - py[j]))
            pflip = min(flips, key=lambda x: abs(x - spot)) if flips else None
            modes_here = sorted({x.get("expiry_mode") for x in profile_rows if x.get("expiry_mode")})
            return {
                "status": "live", "label": label,
                "expiry": profile_rows[0].get("expiry"),
                "expiry_mode": profile_rows[0].get("expiry_mode"),
                "expiry_count": len({x.get("expiry") for x in profile_rows}),
                "heatmap": ph, "net_gex": ptotal,
                "call_wall": call_wall, "put_wall": put_wall, "gamma_flip": pflip,
                "call_oi": sum(x["call_oi"] for x in ph),
                "put_oi": sum(x["put_oi"] for x in ph),
            }

        profiles = {}
        for mode in ("0DTE", "1DTE"):
            matching = [x for x in all_rows if x.get("expiry_mode") == mode]
            profiles[mode] = make_profile(matching, mode)
        profiles["ALL"] = make_profile(all_rows, "ALL EXPIRATIONS")

        # Session-state rules for the 0DTE selector:
        # PRE_MARKET: show the previous session's final 0DTE snapshot, frozen.
        # LIVE: show today's calculated 0DTE.
        # CLOSED: today's 0DTE is expired and is unavailable.
        if session_state == "PRE_MARKET":
            if previous_live_0dte:
                pre = previous_live_0dte
                pre["status"] = "premarket"
                pre["label"] = "PRE-MARKET • PREVIOUS SESSION"
                pre["display_expiry"] = pre.get("expiry")
                pre["snapshot_note"] = "Frozen at the previous U.S. regular-session close; not live 0DTE data."
                profiles["0DTE"] = pre
            else:
                profiles["0DTE"] = {
                    "status": "unavailable", "label": "PRE-MARKET • NO PRIOR SNAPSHOT",
                    "heatmap": [], "expiry": None, "net_gex": None,
                    "call_wall": None, "put_wall": None, "gamma_flip": None,
                    "snapshot_note": "No previous live 0DTE snapshot is available yet."
                }
        elif session_state == "CLOSED":
            if previous_live_0dte:
                closed_snapshot = json.loads(json.dumps(previous_live_0dte))
                closed_snapshot["status"] = "closed_snapshot"
                closed_snapshot["snapshot_note"] = "Final live 0DTE snapshot captured before the 4:00 p.m. ET close."
                options["last_closed_0dte"] = closed_snapshot
            profiles["0DTE"] = {
                "status": "closed", "label": "0DTE • CLOSED",
                "heatmap": [], "expiry": None, "net_gex": None,
                "call_wall": None, "put_wall": None, "gamma_flip": None,
                "snapshot_note": "Today's 0DTE has expired at 4:00 p.m. ET."
            }

        active_expiry_mode = "0DTE" if session_state == "LIVE" and profiles.get("0DTE", {}).get("status") == "live" else ("1DTE" if profiles.get("1DTE", {}).get("status") == "live" else "MULTI-EXPIRY")

        options.update({
            "status": "live",
            "gex_refresh": {
                "status": "LIVE",
                "attempted_at": datetime.now(timezone.utc).isoformat(),
                "source": "Yahoo Finance / yfinance NDX option chains",
                "requested_expiries": len(selected),
                "successful_expiries": len(successful_expiries),
                "failed_expiries": len(expiry_errors),
                "errors": expiry_errors[:6],
            },
            "market_session": session_state,
            "market_session_et": now_ny.strftime("%Y-%m-%d %H:%M:%S %Z"),
            "regular_session_open_et": market_open.strftime("%H:%M"),
            "regular_session_close_et": market_close.strftime("%H:%M"),
            "expiry": nearest_expiry,
            "expiry_mode": active_expiry_mode,
            "expiry_label": active_expiry_mode,
            "profile_mode": "ALL",
            "profiles": profiles,
            "spot": spot,
            "oi_heatmap": heat,
            "net_gex": total,
            "call_wall": max(heat, key=lambda x: x["call_gex"])["strike"] if heat else None,
            "put_wall": min(heat, key=lambda x: x["put_gex"])["strike"] if heat else None,
            "gamma_flip": gamma_flip,
            "gex_confidence": confidence,
            "gex_methodology": "Surface-aware multi-expiry GEX: parity-forward + market IV/quote-IV recovery + smoothed IV smile + Black-76/spot-gamma conversion; dealer sign is assumed, not observed.",
            "gex_model_version": "GEX v2 surface-aware",
            "expiry_count": len(expiry_stats),
            "expiry_dates": [x["expiry"] for x in expiry_stats],
            "expiry_breakdown": expiry_stats,
            "surface_quality": {
                "vendor_iv_points": direct_iv_count,
                "quote_solved_iv_points": solved_iv_count,
                "smoothed_iv_points": fitted_iv_count,
                "total_surface_points": direct_iv_count + solved_iv_count + fitted_iv_count,
            },
            "coverage": {
                "strikes": len(heat), "nonzero_oi_strikes": nonzero,
                "near_atm_nonzero_strikes": near_atm_nonzero,
                "total_call_oi": total_call_oi, "total_put_oi": total_put_oi,
            },
            "data_quality": {
                "status": "LIMITED" if reasons else "GOOD",
                "reason": "; ".join(reasons) if reasons else "Broad multi-expiry OI and volatility-surface coverage",
                "strikes": len(heat), "nonzero_oi_strikes": nonzero,
                "near_atm_nonzero_strikes": near_atm_nonzero,
                "expiry_count": len(expiry_stats),
            },
            "dealer_positioning": {
                "status": "modeled",
                "regime": "Positive gamma" if total > 0 else "Negative gamma" if total < 0 else "Neutral gamma",
                "net_gex": total, "gamma_flip": gamma_flip,
                "confidence": confidence,
                "model": "Modeled from listed multi-expiry OI and a market-implied volatility surface; not direct dealer inventory.",
            },
            "model_notes": [
                "Uses up to 12 nearest current/future NDX expirations.",
                "0DTE is active only during the regular 9:30 a.m.–4:00 p.m. ET session.",
                "Pre-market 0DTE is frozen to the previous session's final snapshot when available.",
                "After 4:00 p.m. ET the expiring 0DTE is removed from the active curve.",
                "0DTE uses actual time remaining to the 4 p.m. ET expiry while the regular session is open.",
                "Forward is estimated from near-ATM put/call parity when quotes permit.",
                "Vendor IV is used when sane; missing IV can be recovered from quoted option prices.",
                "A weighted quadratic smile fills gaps in the per-expiry volatility surface.",
                "Black-76 gamma is converted consistently to spot gamma using the inferred forward and discount factor.",
                "Gamma flip is found by revaluing gamma across a dense hypothetical spot grid.",
                "Call-positive / put-negative dealer sign is a conventional assumption; public OI cannot reveal actual dealer inventory.",
                "GEX magnitude is best compared within this dashboard's methodology, not treated as an absolute cross-provider number.",
            ],
        })

    except Exception as exc:
        # Preserve the last good GEX values, but explicitly mark the refresh as failed
        # so the dashboard can never mistake stale GEX for a fresh calculation.
        options["gex_refresh"] = {
            "status": "FAILED",
            "attempted_at": datetime.now(timezone.utc).isoformat(),
            "source": "Yahoo Finance / yfinance NDX option chains",
            "error": str(exc)[:500],
        }
        options["model_error"] = str(exc)[:500]

    # Refresh the macro layer without deleting an older good snapshot if a source is temporarily unavailable.
    try:
        macro = build_macro(d.get("macro") or {})
        if macro:
            d["macro"] = macro
            curve = macro.get("treasury_curve") or {}
            for k in ("y1m","y3m","y6m","y1","y2","y5","y10","y20","y30"):
                if curve.get(k) is not None:
                    d.setdefault("rates", {})[k] = curve[k]
    except Exception:
        pass

    # News is additive: it does not delete the existing macro/COT fields
    # already present in data.json.
    #
    # FX news gets a second-stage fallback from the general market-news feed.
    # This is important on GitHub Actions where dedicated RSS/search endpoints
    # can intermittently return an empty response even though the broader news
    # feed is working. We classify only headlines that contain explicit FX /
    # central-bank / currency terms, so the fallback does not invent stories.
    fx_news = fetch_fx_news()
    general_news = fetch_news()

    def classify_general_fx_news(items):
        currency_patterns = {
            "USD": ["dollar", "dxy", "federal reserve", "fed", "us dollar", "greenback"],
            "EUR": ["euro", "ecb", "eurusd", "euro zone", "eurozone"],
            "GBP": ["pound", "sterling", "bank of england", "boe", "gbp"],
            "JPY": ["yen", "bank of japan", "boj", "jpy", "japan currency"],
            "CHF": ["swiss franc", "snb", "chf", "switzerland currency"],
            "AUD": ["australian dollar", "rba", "aud", "australia currency"],
            "CAD": ["canadian dollar", "bank of canada", "boc", "cad", "canada currency"],
            "NZD": ["new zealand dollar", "rbnz", "nzd", "new zealand currency"],
            "INR": ["rupee", "rbi", "inr", "indian currency", "india currency"],
            "CNY": ["yuan", "renminbi", "pboc", "cny", "china currency", "chinese yuan"],
        }
        out = []
        seen = set()
        for item in items or []:
            # General-news feeds can return either dictionaries or plain
            # headline strings. Normalize both forms before reading fields.
            if isinstance(item, dict):
                title = str(item.get("title") or "").strip()
                link = item.get("link") or ""
                source = item.get("source") or "Market News"
                published_at = item.get("published_at")
            else:
                title = str(item or "").strip()
                link = ""
                source = "Market News"
                published_at = None

            text = title.lower()
            if not title:
                continue
            hits = []
            for code, terms in currency_patterns.items():
                if any(term in text for term in terms):
                    hits.append(code)
            if not hits:
                continue
            code = hits[0]
            key = re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "title": title,
                "link": link,
                "source": source,
                "published_at": published_at,
                "currency": code,
            })
            if len(out) >= 20:
                break
        return out

    # fetch_news() returns a structured object; classify its headline items, not the wrapper keys.
    general_items = general_news.get("items", []) if isinstance(general_news, dict) else general_news
    fx_fallback = classify_general_fx_news(general_items)
    if not fx_news.get("items"):
        fx_news = {
            "status": "fallback",
            "source": "Classified general market-news headlines (FX fallback)",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "items": fx_fallback[:20],
        }
    elif fx_fallback:
        # Keep dedicated FX headlines first, then use general headlines only
        # to fill gaps. Deduplicate by normalized title.
        existing = {re.sub(r"[^a-z0-9]+", " ", str(x.get("title") or "").lower()).strip() for x in fx_news.get("items", [])}
        for item in fx_fallback:
            key = re.sub(r"[^a-z0-9]+", " ", str(item.get("title") or "").lower()).strip()
            if key not in existing:
                fx_news.setdefault("items", []).append(item)
                existing.add(key)
            if len(fx_news.get("items", [])) >= 20:
                break

    d["fx_news"] = fx_news
    d["news"] = general_news
    d["generated_at"] = datetime.now(timezone.utc).isoformat()
    d["sources"] = list(dict.fromkeys((d.get("sources") or []) + ["Filtered Google News RSS aggregation"]))

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2)


if __name__ == "__main__":
    main()
