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
        # PROFESSIONAL-STYLE MODELED GEX LAYER
        # Uses only the listed option chains available through yfinance.
        # It aggregates multiple expiries, models 0DTE with intraday time,
        # calculates Black-Scholes gamma, and derives gamma flip/walls from
        # the aggregate profile. This remains an estimate, not dealer inventory.
        t = yf.Ticker("^NDX")
        expiries = list(t.options or [])
        spot = sf(d["prices"].get("ndx"))
        if expiries and spot is not None:
            ny = ZoneInfo("America/New_York")
            now_ny = datetime.now(ny)
            today_ny = now_ny.date()
            parsed = sorted((e, datetime.fromisoformat(e).date()) for e in expiries)
            future = [(e, ed) for e, ed in parsed if ed >= today_ny]
            if not future:
                raise ValueError("No current/future NDX expiries")

            # Keep the model broad enough to capture the near-term surface,
            # while limiting API calls for a free-data GitHub Actions workflow.
            selected = future[:12]
            r = sf(d.get("rates", {}).get("y10"))
            r = (r / 100.0 if r is not None and abs(r) > 1.5 else (r or 0.0))
            MULT = 100.0

            def clean_iv(v):
                v = sf(v)
                return v if v is not None and 0.0001 < v < 5 else None

            def time_to_expiry(ed):
                if ed == today_ny and now_ny.time() < dt_time(16, 0):
                    expiry_time = datetime.combine(ed, dt_time(16, 0), tzinfo=ny)
                    sec = max((expiry_time - now_ny).total_seconds(), 15 * 60)
                    return sec / (365 * 24 * 3600), "0DTE"
                days = max((ed - today_ny).days, 1)
                return days / 365.0, ("1DTE" if days == 1 else "NEAREST")

            def bs_terms(S, K, vol, T):
                if not vol or vol <= 0 or S <= 0 or K <= 0 or T <= 0:
                    return (0.0, 0.0, 0.0)
                try:
                    root = math.sqrt(T)
                    d1 = (math.log(S / K) + (r + 0.5 * vol * vol) * T) / (vol * root)
                    d2 = d1 - vol * root
                    pdf = math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi)
                    gamma = pdf / (S * vol * root)
                    # Simplified spot-vanna and charm terms. These are
                    # analytical sensitivity estimates, not exchange fields.
                    vanna = -pdf * d2 / vol
                    charm = -pdf * (2 * r * T - d2 * vol * root) / (2 * T * vol * root)
                    return gamma, vanna, charm
                except Exception:
                    return (0.0, 0.0, 0.0)

            all_rows = []
            expiry_stats = []
            for expiry, expiry_date in selected:
                try:
                    T, mode = time_to_expiry(expiry_date)
                    ch = t.option_chain(expiry)
                    c, p = ch.calls.copy(), ch.puts.copy()
                    if c.empty and p.empty:
                        continue
                    strikes = sorted(set(c.get("strike", []).dropna()) | set(p.get("strike", []).dropna()))
                    exp_net = 0.0
                    exp_call_oi = 0.0
                    exp_put_oi = 0.0
                    exp_nonzero = 0
                    for K0 in strikes:
                        K = float(K0)
                        cr = c[c["strike"] == K0]
                        pr = p[p["strike"] == K0]
                        coi = float(sf(cr.iloc[0].get("openInterest")) or 0.0) if not cr.empty else 0.0
                        poi = float(sf(pr.iloc[0].get("openInterest")) or 0.0) if not pr.empty else 0.0
                        civ = clean_iv(cr.iloc[0].get("impliedVolatility")) if not cr.empty else None
                        piv = clean_iv(pr.iloc[0].get("impliedVolatility")) if not pr.empty else None
                        cg, cv, cc = bs_terms(spot, K, civ, T)
                        pg, pv, pc = bs_terms(spot, K, piv, T)
                        # Standard modeled dealer-sign convention: calls +, puts -.
                        call_gex = cg * coi * MULT * spot * spot * 0.01
                        put_gex = -pg * poi * MULT * spot * spot * 0.01
                        call_vanna = cv * coi * MULT * spot * 0.01
                        put_vanna = -pv * poi * MULT * spot * 0.01
                        call_charm = cc * coi * MULT * spot * 0.01
                        put_charm = -pc * poi * MULT * spot * 0.01
                        net = call_gex + put_gex
                        if coi > 0 or poi > 0:
                            exp_nonzero += 1
                        exp_call_oi += coi
                        exp_put_oi += poi
                        all_rows.append({
                            "strike": K, "expiry": expiry, "expiry_mode": mode,
                            "call_oi": coi, "put_oi": poi,
                            "call_gex": call_gex, "put_gex": put_gex, "net_gex": net,
                            "call_vanna": call_vanna, "put_vanna": put_vanna,
                            "call_charm": call_charm, "put_charm": put_charm,
                            "iv_call": civ, "iv_put": piv, "T": T,
                        })
                        exp_net += net
                    expiry_stats.append({
                        "expiry": expiry, "mode": mode, "days": max((expiry_date - today_ny).days, 0),
                        "net_gex": exp_net, "call_oi": exp_call_oi, "put_oi": exp_put_oi,
                        "nonzero_strikes": exp_nonzero,
                    })
                except Exception:
                    continue

            if all_rows:
                # Aggregate the multi-expiry strike profile.
                by_strike = {}
                for row in all_rows:
                    K = row["strike"]
                    a = by_strike.setdefault(K, {
                        "strike": K, "call_oi": 0.0, "put_oi": 0.0,
                        "call_gex": 0.0, "put_gex": 0.0, "net_gex": 0.0,
                        "vanna": 0.0, "charm": 0.0,
                    })
                    for key in ("call_oi", "put_oi", "call_gex", "put_gex", "net_gex"):
                        a[key] += row[key]
                    a["vanna"] += row["call_vanna"] + row["put_vanna"]
                    a["charm"] += row["call_charm"] + row["put_charm"]

                heat = [by_strike[k] for k in sorted(by_strike)]
                # Keep a broad but bounded profile around spot for rendering.
                heat = [x for x in heat if abs(x["strike"] - spot) <= spot * 0.15]
                total = sum(x["net_gex"] for x in heat)
                total_call_oi = sum(x["call_oi"] for x in heat)
                total_put_oi = sum(x["put_oi"] for x in heat)
                vanna_total = sum(x["vanna"] for x in heat)
                charm_total = sum(x["charm"] for x in heat)

                # Walls are concentration levels, not simply the single largest OI.
                # Use absolute exposure to find the strongest modeled call/put levels.
                call_candidates = [x for x in heat if x["call_gex"] > 0]
                put_candidates = [x for x in heat if x["put_gex"] < 0]
                call_wall = max(call_candidates, key=lambda x: x["call_gex"])["strike"] if call_candidates else None
                put_wall = min(put_candidates, key=lambda x: x["put_gex"])["strike"] if put_candidates else None

                def total_at(S):
                    value = 0.0
                    for row in all_rows:
                        if abs(row["strike"] - S) > spot * 0.15:
                            continue
                        cg, _, _ = bs_terms(S, row["strike"], row["iv_call"], row["T"])
                        pg, _, _ = bs_terms(S, row["strike"], row["iv_put"], row["T"])
                        value += (cg * row["call_oi"] - pg * row["put_oi"]) * MULT * S * S * 0.01
                    return value

                # Dense profile around spot; interpolate the nearest zero crossing.
                lo, hi = spot * 0.90, spot * 1.10
                xs = [lo + (hi - lo) * i / 240 for i in range(241)]
                ys = [total_at(x) for x in xs]
                flips = []
                for i in range(len(xs) - 1):
                    if ys[i] == 0:
                        flips.append(xs[i])
                    elif ys[i] * ys[i + 1] < 0:
                        flips.append(xs[i] - ys[i] * (xs[i + 1] - xs[i]) / (ys[i + 1] - ys[i]))
                gamma_flip = min(flips, key=lambda x: abs(x - spot)) if flips else None

                nonzero = sum(1 for x in heat if x["call_oi"] > 0 or x["put_oi"] > 0)
                near_atm_nonzero = sum(1 for x in heat if abs(x["strike"] - spot) <= spot * 0.05 and (x["call_oi"] > 0 or x["put_oi"] > 0))
                reasons = []
                if len(selected) < 6: reasons.append("limited expiry coverage")
                if len(heat) < 50: reasons.append("fewer than 50 strikes in modeled window")
                if nonzero < 40: reasons.append("sparse open interest")
                if near_atm_nonzero < 8: reasons.append("thin OI near spot")
                confidence = "HIGH" if len(selected) >= 8 and len(heat) >= 80 and near_atm_nonzero >= 10 else "MEDIUM" if len(selected) >= 4 and len(heat) >= 50 else "LOW"
                if reasons and confidence == "HIGH": confidence = "MEDIUM"

                modes = [x["mode"] for x in expiry_stats]
                expiry_mode = "0DTE" if "0DTE" in modes else ("1DTE" if "1DTE" in modes else "MULTI-EXPIRY")
                nearest_expiry = selected[0][0]
                # Build switchable near-term profiles so the frontend can compare
                # 0DTE, 1DTE and the full multi-expiry aggregate without fabricating data.
                def make_profile(profile_rows, label):
                    if not profile_rows:
                        return {"status":"unavailable","label":label,"heatmap":[],"expiry":None,"net_gex":None,"call_wall":None,"put_wall":None,"gamma_flip":None}
                    ps = {}
                    for row in profile_rows:
                        K = row["strike"]
                        a = ps.setdefault(K, {"strike":K,"call_oi":0.0,"put_oi":0.0,"call_gex":0.0,"put_gex":0.0,"net_gex":0.0})
                        for key in ("call_oi","put_oi","call_gex","put_gex","net_gex"):
                            a[key] += row[key]
                    ph = [ps[k] for k in sorted(ps) if abs(k-spot) <= spot*0.15]
                    if not ph:
                        return {"status":"unavailable","label":label,"heatmap":[]}
                    ptotal = sum(x["net_gex"] for x in ph)
                    cc = [x for x in ph if x["call_gex"] > 0]
                    pp = [x for x in ph if x["put_gex"] < 0]
                    pcw = max(cc,key=lambda x:x["call_gex"])["strike"] if cc else None
                    ppw = min(pp,key=lambda x:x["put_gex"])["strike"] if pp else None
                    def ptotal_at(S):
                        value=0.0
                        for row in profile_rows:
                            if abs(row["strike"]-S)>spot*0.15: continue
                            cg,_,_=bs_terms(S,row["strike"],row["iv_call"],row["T"])
                            pg,_,_=bs_terms(S,row["strike"],row["iv_put"],row["T"])
                            value += (cg*row["call_oi"]-pg*row["put_oi"])*MULT*S*S*0.01
                        return value
                    px=[spot*0.90+(spot*0.20)*i/120 for i in range(121)]
                    py=[ptotal_at(x) for x in px]
                    pflip=[]
                    for j in range(len(px)-1):
                        if py[j]==0: pflip.append(px[j])
                        elif py[j]*py[j+1]<0: pflip.append(px[j]-py[j]*(px[j+1]-px[j])/(py[j+1]-py[j]))
                    p_gamma=min(pflip,key=lambda x:abs(x-spot)) if pflip else None
                    return {
                        "status":"live","label":label,"expiry":profile_rows[0].get("expiry"),
                        "expiry_mode":profile_rows[0].get("expiry_mode"),"heatmap":ph,"net_gex":ptotal,
                        "call_wall":pcw,"put_wall":ppw,"gamma_flip":p_gamma,
                        "call_oi":sum(x["call_oi"] for x in ph),"put_oi":sum(x["put_oi"] for x in ph),
                    }

                by_mode = {}
                for mode in ("0DTE", "1DTE"):
                    matching = [x for x in all_rows if x.get("expiry_mode") == mode]
                    by_mode[mode] = make_profile(matching, mode)
                by_mode["ALL"] = make_profile(all_rows, "ALL EXPIRATIONS")

                options.update({
                    "status": "live",
                    "expiry": nearest_expiry,
                    "profile_mode": "ALL",
                    "profiles": by_mode,
                    "expiry_mode": expiry_mode,
                    "expiry_label": expiry_mode,
                    "spot": spot,
                    "oi_heatmap": heat,
                    "net_gex": total,
                    "call_wall": call_wall,
                    "put_wall": put_wall,
                    "gamma_flip": gamma_flip,
                    "gex_confidence": confidence,
                    "gex_methodology": "Multi-expiry Black-Scholes gamma model from listed OI/IV; calls positive, puts negative by dealer-sign assumption.",
                    "expiry_count": len(expiry_stats),
                    "expiry_dates": [x["expiry"] for x in expiry_stats],
                    "expiry_breakdown": expiry_stats,
                    "vanna": vanna_total,
                    "charm": charm_total,
                    "coverage": {
                        "strikes": len(heat), "nonzero_oi_strikes": nonzero,
                        "near_atm_nonzero_strikes": near_atm_nonzero,
                        "total_call_oi": total_call_oi, "total_put_oi": total_put_oi,
                    },
                    "data_quality": {
                        "status": "LIMITED" if reasons else "GOOD",
                        "reason": "; ".join(reasons) if reasons else "Broad multi-expiry strike and OI coverage",
                        "strikes": len(heat), "nonzero_oi_strikes": nonzero,
                        "near_atm_nonzero_strikes": near_atm_nonzero,
                        "expiry_count": len(expiry_stats),
                    },
                    "dealer_positioning": {
                        "status": "modeled",
                        "regime": "Positive gamma" if total > 0 else "Negative gamma" if total < 0 else "Neutral gamma",
                        "net_gex": total, "gamma_flip": gamma_flip,
                        "confidence": confidence,
                        "model": "Modeled from listed multi-expiry OI/IV and Black-Scholes sensitivities; not direct dealer inventory.",
                    },
                })
                options["model_notes"] = [
                    "Uses up to 12 nearest current/future expirations to improve structure versus single-expiry GEX.",
                    "0DTE uses time remaining to the modeled 4:00 p.m. ET expiry.",
                    "Gamma flip is interpolated from the aggregate price-response profile.",
                    "Vanna and Charm are analytical estimates from the same OI/IV snapshot.",
                    "Dealer side is inferred by a conventional sign assumption; actual dealer inventory is not observable here.",
                ]

    except Exception:
        pass

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
    d["news"] = fetch_news()
    d["generated_at"] = datetime.now(timezone.utc).isoformat()
    d["sources"] = list(dict.fromkeys((d.get("sources") or []) + ["Filtered Google News RSS aggregation"]))

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2)


if __name__ == "__main__":
    main()
