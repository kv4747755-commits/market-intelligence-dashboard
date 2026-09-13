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
        # Select the correct expiry for the current session:
        # 0DTE when an NDX expiry exists today and it is still before the
        # regular 4:00 p.m. ET expiry; otherwise use the nearest future expiry.
        t = yf.Ticker("^NDX")
        expiries = list(t.options or [])
        spot = sf(d["prices"].get("ndx"))
        if expiries and spot is not None:
            ny = ZoneInfo("America/New_York")
            now_ny = datetime.now(ny)
            today_ny = now_ny.date()
            parsed = sorted((e, datetime.fromisoformat(e).date()) for e in expiries)
            same_day = [e for e, ed in parsed if ed == today_ny]
            future = [(e, ed) for e, ed in parsed if ed > today_ny]

            if same_day and now_ny.time() < dt_time(16, 0):
                expiry = same_day[0]
                expiry_mode = "0DTE"
                expiry_label = "0DTE"
                expiry_time = datetime.combine(today_ny, dt_time(16, 0), tzinfo=ny)
                seconds_to_expiry = max((expiry_time - now_ny).total_seconds(), 15 * 60)
                T = seconds_to_expiry / (365 * 24 * 3600)
            elif future:
                expiry, expiry_date = future[0]
                days_to_expiry = (expiry_date - today_ny).days
                expiry_mode = "1DTE" if days_to_expiry == 1 else "NEAREST"
                expiry_label = expiry_mode
                T = max(days_to_expiry, 1) / 365
            else:
                raise ValueError("No valid future NDX expiry")

            ch = t.option_chain(expiry)
            c, p = ch.calls.copy(), ch.puts.copy()
            if not c.empty and not p.empty:
                c["dist"] = (c["strike"] - spot).abs()
                p["dist"] = (p["strike"] - spot).abs()

                civ = sf(c.sort_values("dist").iloc[0].get("impliedVolatility"))
                piv = sf(p.sort_values("dist").iloc[0].get("impliedVolatility"))
                ivs = [x for x in (civ, piv) if x is not None and x > 0]
                iv = sum(ivs) / len(ivs) if ivs else None

                if iv is not None:
                    move = spot * iv * math.sqrt(T)
                    options.update({
                        "status": "live",
                        "expiry": expiry,
                        "expiry_mode": expiry_mode,
                        "expiry_label": expiry_label,
                        "spot": spot,
                        "atm_iv": iv * 100,
                        "expected_move_pct": (move / spot) * 100,
                        "expected_move_points": move,
                        "time_to_expiry_hours": T * 365 * 24,
                    })

                call_oi = sf(c["openInterest"].fillna(0).sum())
                put_oi = sf(p["openInterest"].fillna(0).sum())
                if call_oi is not None and put_oi is not None and call_oi > 0:
                    options["pcr_oi"] = put_oi / call_oi

                # Rebuild the strike-level modeled GEX profile for the SAME
                # expiry selected above, so 0DTE never displays stale 1DTE data.
                def clean_iv(v):
                    v = sf(v)
                    return v if v is not None and v > 0 else None

                rows = []
                for K in sorted(set(c["strike"].dropna()) | set(p["strike"].dropna())):
                    cr = c[c["strike"] == K]
                    pr = p[p["strike"] == K]
                    coi = float(cr.iloc[0].get("openInterest") or 0) if not cr.empty else 0.0
                    poi = float(pr.iloc[0].get("openInterest") or 0) if not pr.empty else 0.0
                    civ_k = clean_iv(cr.iloc[0].get("impliedVolatility")) if not cr.empty else None
                    piv_k = clean_iv(pr.iloc[0].get("impliedVolatility")) if not pr.empty else None
                    rows.append((float(K), coi, poi, civ_k, piv_k))

                # Black-Scholes gamma approximation from IV when the chain
                # does not provide a reliable gamma field.
                def bs_gamma(S, K, vol):
                    if not vol or vol <= 0 or S <= 0 or K <= 0 or T <= 0:
                        return 0.0
                    try:
                        d1 = (math.log(S / K) + (r * T) + 0.5 * vol * vol * T) / (vol * math.sqrt(T))
                        return math.exp(-0.5 * d1 * d1) / (S * vol * math.sqrt(T) * math.sqrt(2 * math.pi))
                    except Exception:
                        return 0.0

                r = sf(d["rates"].get("y10"))
                r = (r / 100.0 if r is not None and abs(r) > 1.5 else (r or 0.0))
                MULT = 100.0
                heat = []
                for K, coi, poi, civ_k, piv_k in rows:
                    cg = bs_gamma(spot, K, civ_k)
                    pg = bs_gamma(spot, K, piv_k)
                    cge = cg * coi * MULT * spot * spot * 0.01
                    pge = -pg * poi * MULT * spot * spot * 0.01
                    if abs(K - spot) <= spot * 0.15:
                        heat.append({
                            "strike": K, "call_oi": coi, "put_oi": poi,
                            "call_gex": cge, "put_gex": pge, "net_gex": cge + pge
                        })

                options["oi_heatmap"] = heat
                total = sum(x["net_gex"] for x in heat)
                options["net_gex"] = total
                total_call_oi = sum(x["call_oi"] for x in heat)
                total_put_oi = sum(x["put_oi"] for x in heat)
                nonzero = sum(1 for x in heat if x["call_oi"] > 0 or x["put_oi"] > 0)
                near_atm_nonzero = sum(1 for x in heat if abs(x["strike"] - spot) <= spot * 0.05 and (x["call_oi"] > 0 or x["put_oi"] > 0))
                nonzero_ratio = nonzero / len(heat) if heat else 0
                reasons = []
                if len(heat) < 50: reasons.append("fewer than 50 strikes in the modeled window")
                if nonzero < 20: reasons.append("too few strikes with non-zero open interest")
                if near_atm_nonzero < 6: reasons.append("thin open interest near spot")
                if total_call_oi + total_put_oi < 500: reasons.append("low total open interest")
                if nonzero_ratio < 0.20: reasons.append("sparse open-interest coverage")
                options["data_quality"] = {
                    "status": "LIMITED" if reasons else "GOOD",
                    "reason": "; ".join(reasons) if reasons else "Sufficient strike and open-interest coverage for this modeled snapshot",
                    "strikes": len(heat), "nonzero_oi_strikes": nonzero,
                    "nonzero_oi_ratio": nonzero_ratio,
                    "near_atm_nonzero_strikes": near_atm_nonzero,
                    "total_call_oi": total_call_oi, "total_put_oi": total_put_oi,
                }

                if heat:
                    options["call_wall"] = max(heat, key=lambda x: x["call_gex"])["strike"]
                    options["put_wall"] = min(heat, key=lambda x: x["put_gex"])["strike"]

                def total_at(s):
                    total_g = 0.0
                    for K, coi, poi, civ_k, piv_k in rows:
                        if abs(K - s) <= spot * 0.15:
                            total_g += (bs_gamma(s, K, civ_k) * coi - bs_gamma(s, K, piv_k) * poi) * MULT * s * s * 0.01
                    return total_g

                xs = [spot * 0.85 + (spot * 0.30) * i / 120 for i in range(121)]
                ys = [total_at(x) for x in xs]
                flips = []
                for i in range(120):
                    if ys[i] == 0:
                        flips.append(xs[i])
                    elif ys[i] * ys[i + 1] < 0:
                        flips.append(xs[i] - ys[i] * (xs[i + 1] - xs[i]) / (ys[i + 1] - ys[i]))
                options["gamma_flip"] = min(flips, key=lambda x: abs(x - spot)) if flips else None
                options["dealer_positioning"] = {
                    "status": "modeled",
                    "regime": "Positive gamma" if total > 0 else "Negative gamma" if total < 0 else "Neutral gamma",
                    "net_gex": total,
                    "gamma_flip": options["gamma_flip"],
                    "model": "Modeled from listed OI, IV and Black-Scholes gamma; not direct dealer inventory."
                }

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
