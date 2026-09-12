import json, math, os, urllib.parse
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET
import yfinance as yf
import re

OUT='data.json'; MULT=100.0
UA='market-intelligence-dashboard/1.0'


def sf(x):
    try:
        x=float(x); return x if math.isfinite(x) else None
    except: return None


def snap(t):
    try:
        h=yf.Ticker(t).history(period='5d', interval='1d', auto_adjust=False)
        if h.empty:return None,None
        c=h['Close'].dropna(); v=sf(c.iloc[-1]); p=sf(c.iloc[-2]) if len(c)>1 else None
        return v,((v/p)-1)*100 if v is not None and p else None
    except:return None,None


def iv(x):
    x=sf(x)
    return x if x and x>0 and x<3 else (x/100 if x and x>0 else None)


def gamma(S,K,sig,T,r=0):
    if not all(x and x>0 for x in (S,K,sig,T)): return 0.0
    d1=(math.log(S/K)+(r+.5*sig*sig)*T)/(sig*math.sqrt(T))
    return math.exp(-.5*d1*d1)/(math.sqrt(2*math.pi)*S*sig*math.sqrt(T))


def treasury_rates():
    url=("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml"
         "?data=daily_treasury_yield_curve&field_tdr_date_value="+str(datetime.now(timezone.utc).year))
    try:
        req=Request(url,headers={'User-Agent':UA})
        with urlopen(req,timeout=20) as r: root=ET.fromstring(r.read())
        records=[]
        for entry in root.iter():
            if entry.tag.rsplit('}',1)[-1] != 'entry': continue
            vals={}
            for node in entry.iter():
                tag=node.tag.rsplit('}',1)[-1]; text=(node.text or '').strip()
                if text: vals[tag]=text
            date=vals.get('NEW_DATE')
            if date:
                rec={'date':date}
                for key,field in [('y1m','BC_1MONTH'),('y3m','BC_3MONTH'),('y6m','BC_6MONTH'),('y1','BC_1YEAR'),('y2','BC_2YEAR'),('y5','BC_5YEAR'),('y10','BC_10YEAR'),('y20','BC_20YEAR'),('y30','BC_30YEAR')]: rec[key]=sf(vals.get(field))
                if any(rec[k] is not None for k in rec if k!='date'): records.append(rec)
        if not records:return None
        records.sort(key=lambda x:x['date']); return records[-1]
    except:return None


def fred_series(series_id, api_key=None):
    # Prefer the authenticated FRED API when a key is configured. Otherwise
    # use FRED's public graph CSV endpoint, which is sufficient for the
    # latest observations used by this dashboard and avoids a hard dependency
    # on a secret for GitHub Actions.
    try:
        if api_key:
            params={'series_id':series_id,'api_key':api_key,'file_type':'json','sort_order':'desc','limit':'3'}
            url='https://api.stlouisfed.org/fred/series/observations?'+urllib.parse.urlencode(params)
            req=Request(url,headers={'User-Agent':UA})
            with urlopen(req,timeout=20) as r: data=json.loads(r.read().decode('utf-8'))
            obs=[]
            for x in data.get('observations',[]):
                v=sf(x.get('value'))
                if v is not None: obs.append({'date':x.get('date'),'value':v})
        else:
            url='https://fred.stlouisfed.org/graph/fredgraph.csv?'+urllib.parse.urlencode({'id':series_id})
            req=Request(url,headers={'User-Agent':UA})
            with urlopen(req,timeout=20) as r: text=r.read().decode('utf-8',errors='replace')
            lines=text.strip().splitlines()
            obs=[]
            for line in reversed(lines[1:]):
                parts=line.split(',',1)
                if len(parts)!=2: continue
                v=sf(parts[1])
                if v is not None: obs.append({'date':parts[0],'value':v})
                if len(obs)>=3: break
        if not obs:return None
        latest=obs[0]; prev=obs[1] if len(obs)>1 else None
        return {'value':latest['value'],'date':latest['date'],'previous':prev['value'] if prev else None,'previous_date':prev['date'] if prev else None}
    except:return None


# FRED IDs chosen for broad macro coverage. Values remain clearly labeled by frequency/source.
FRED_SERIES={
    'fed_funds':'DFF','sofr':'SOFR',
    'real_10y':'DFII10','breakeven_10y':'T10YIE',
    'cpi':'CPIAUCSL','core_cpi':'CPILFESL','pce':'PCEPI','core_pce':'PCEPILFE','ppi':'PPIACO',
    'unemployment':'UNRATE','payrolls':'PAYEMS','avg_hourly_earnings':'CES0500000003','initial_claims':'ICSA','continuing_claims':'CCSA','jolts':'JTSJOL','labor_participation':'CIVPART',
    'gdp':'GDPC1','gdp_growth':'A191RL1Q225SBEA','industrial_production':'INDPRO','retail_sales':'RSAFS','housing_starts':'HOUST','building_permits':'PERMIT','consumer_sentiment':'UMCSENT',
    'fed_balance_sheet':'WALCL','m2':'M2SL','rrp':'RRPONTSYD','tga':'WTREGEN','financial_conditions':'NFCI',
    'ig_spread':'BAMLC0A0CM','hy_spread':'BAMLH0A0HYM2',
    'dollar_broad':'DTWEXBGS','wti':'DCOILWTICO','gold':'GOLDAMGBD228NLBM','copper':'PCOPPUSDM','natgas':'DHHNGSP','vix':'VIXCLS',
    'curve_2s10s':'T10Y2Y','curve_5s30s':'T5Y30Y','curve_3m10y':'T10Y3M'
}


def build_macro(fred_key, rates):
    out={'status':'partial' if not fred_key else 'live','source':'FRED + U.S. Treasury official feeds','updated_at':datetime.now(timezone.utc).isoformat(),'frequency_note':'Frequency varies by series: daily, weekly, monthly or quarterly.','series':{}}
    if rates:
        out['rates']={k:v for k,v in rates.items() if k!='date'}; out['rates_date']=rates.get('date')
    if fred_key:
        for name,sid in FRED_SERIES.items():
            v=fred_series(sid,fred_key)
            if v: out['series'][name]={'series_id':sid,**v}
    # Add explicit curve metrics from Treasury where possible.
    r=out.get('rates',{})
    for a,b,name in [('y2','y10','2s10s'),('y5','y30','5s30s'),('y3m','y10','3m10y')]:
        if r.get(a) is not None and r.get(b) is not None: out[name]=r[b]-r[a]
    return out


def fetch_news():
    queries=['"Federal Reserve" OR "Fed" OR "interest rates" OR CPI OR inflation OR jobs','"Treasury yields" OR "10-year yield" OR bonds OR "yield curve"','Nasdaq OR "S&P 500" OR "Wall Street" OR stocks OR equities','dollar OR DXY OR EURUSD OR euro OR ECB OR forex','oil OR crude OR OPEC OR commodities OR gold','tariffs OR sanctions OR Iran OR Ukraine OR geopolitics markets','options OR volatility OR VIX OR futures']
    positive={'fed':5,'federal reserve':5,'fomc':6,'interest rate':5,'inflation':5,'cpi':6,'ppi':4,'payroll':5,'jobs':4,'unemployment':4,'gdp':4,'treasury':5,'yield':5,'bond':4,'yield curve':5,'nasdaq':5,'s&p 500':5,'wall street':4,'stocks':3,'equities':3,'futures':4,'dollar':5,'dxy':6,'eurusd':6,'euro':3,'ecb':5,'forex':4,'currency':3,'oil':4,'crude':5,'opec':5,'gold':3,'commodity':3,'vix':5,'volatility':4,'options':4,'tariff':4,'sanction':4,'iran':4,'ukraine':3,'russia':3,'china':2,'geopolit':4,'central bank':5,'rate hike':6,'rate cut':6,'earnings':3,'forecast':3,'recession':5,'liquidity':4}
    high={'fomc','federal reserve','fed','cpi','inflation','rate hike','rate cut','interest rate','payroll','nonfarm','treasury yield','yield curve','oil','crude','opec','ecb','central bank','geopolit','sanction','tariff','vix'}
    exclude={'lottery','casino','sports','celebrity','entertainment','movie','tv show','reality tv','recipe','restaurant','shopping','coupon','dollar tree','fashion','horoscope','obituary','wedding','crime','local police'}
    source_bonus={'reuters':3,'cnbc':3,'bloomberg':3,'financial times':3,'marketwatch':2,'nasdaq':2,'associated press':2,'yahoo finance':2,'investing.com':2,"barron's":2}
    items=[];seen=set()
    for query in queries:
        url='https://news.google.com/rss/search?q='+urllib.parse.quote(query)+'&hl=en-US&gl=US&ceid=US:en'
        try:
            req=Request(url,headers={'User-Agent':UA})
            with urlopen(req,timeout=20) as r: root=ET.fromstring(r.read())
            for item in root.findall('.//item'):
                title=(item.findtext('title') or '').strip(); link=(item.findtext('link') or '').strip(); src=item.find('source'); source=(src.text or '').strip() if src is not None else 'Unknown'; pub=(item.findtext('pubDate') or '').strip()
                if not title or not link or link in seen: continue
                seen.add(link); text=f'{title} {source}'.lower()
                if any(term in text for term in exclude): continue
                score=sum(w for term,w in positive.items() if term in text)
                if score<4: continue
                score+=max((b for n,b in source_bonus.items() if n in source.lower()),default=0)
                try: published_at=parsedate_to_datetime(pub).astimezone(timezone.utc).isoformat() if pub else None
                except: published_at=pub
                hit=any(term in text for term in high); impact='HIGH' if hit and score>=8 else ('MEDIUM' if score>=6 else 'LOW')
                items.append({'title':title,'source':source or 'Unknown','published_at':published_at,'url':link,'relevance_score':score,'impact':impact})
        except: continue
    unique=[];keys=set()
    for x in sorted(items,key=lambda z:(z.get('relevance_score',0),z.get('published_at') or ''),reverse=True):
        k=' '.join(''.join(ch.lower() if ch.isalnum() else ' ' for ch in x['title']).split()[:12])
        if k in keys: continue
        keys.add(k); unique.append(x)
    unique=unique[:12]; unique.sort(key=lambda z:z.get('published_at') or '',reverse=True)
    return {'status':'live' if unique else 'unavailable','source':'Filtered Google News RSS aggregation','updated_at':datetime.now(timezone.utc).isoformat(),'items':unique}


def fetch_cot():
    endpoint='https://publicreporting.cftc.gov/resource/yw9f-hn96.json'
    markets={'Nasdaq-100 E-mini':'209742','S&P 500 E-mini':'13874A','Euro FX':'099741','U.S. Dollar Index':'098662','10Y Treasury Note':'043602'}
    out={}
    try:
        codes=','.join("'%s'"%c for c in markets.values())
        params={'$limit':'100','$order':'report_date_as_yyyy_mm_dd DESC','$where':"futonly_or_combined='Combined' AND cftc_contract_market_code IN (%s)"%codes}
        req=Request(endpoint+'?'+urllib.parse.urlencode(params),headers={'User-Agent':UA,'Accept':'application/json'})
        with urlopen(req,timeout=25) as r: rows=json.loads(r.read().decode('utf-8'))
        code_to_name={v:k for k,v in markets.items()}
        for row in rows:
            name=code_to_name.get(str(row.get('cftc_contract_market_code') or ''))
            if not name or name in out: continue
            num=lambda key: sf(row.get(key))
            out[name]={'market':row.get('market_and_exchange_names') or name,'report_date':str(row.get('report_date_as_yyyy_mm_dd') or '')[:10],'open_interest':num('open_interest_all'),'dealer_net':(num('dealer_positions_long_all') or 0)-(num('dealer_positions_short_all') or 0),'asset_manager_net':(num('asset_mgr_positions_long') or 0)-(num('asset_mgr_positions_short') or 0),'leveraged_money_net':(num('lev_money_positions_long') or 0)-(num('lev_money_positions_short') or 0)}
        return {'status':'live' if out else 'unavailable','source':'CFTC TFF Combined','reporting_basis':'Tuesday positions, generally released Friday 3:30 p.m. ET','markets':out}
    except:return {'status':'unavailable','source':'CFTC TFF Combined','markets':{}}


def options_model(d):
    S=sf(d['prices'].get('ndx'))
    o={'status':'unavailable','ticker':'^NDX','expiry':None,'spot':S,'atm_iv':None,'expected_move_pct':None,'expected_move_points':None,'pcr_oi':None,'gamma_flip':None,'put_wall':None,'call_wall':None,'net_gex':None,'oi_heatmap':[],'data_quality':{'status':'UNAVAILABLE','reason':'No valid option chain','strikes':0,'nonzero_oi_strikes':0,'nonzero_oi_ratio':0,'near_atm_nonzero_strikes':0,'total_call_oi':0,'total_put_oi':0},'dealer_positioning':{'status':'unavailable','regime':None,'net_gex':None,'gamma_flip':None,'model':'Modeled from listed OI, IV and Black-Scholes gamma; not direct dealer inventory.'},'model':'Estimated GEX using listed option OI and modeled gamma; not direct dealer book.'}
    try:
        tkr=yf.Ticker('^NDX'); ex=list(tkr.options or [])
        if not ex or S is None: raise ValueError()
        today=datetime.now(timezone.utc).date(); fut=[e for e in ex if datetime.fromisoformat(e).date()>=today]; expiry=fut[0] if fut else ex[0]
        c=tkr.option_chain(expiry).calls.copy(); p=tkr.option_chain(expiry).puts.copy()
        if c.empty or p.empty: raise ValueError()
        days=max((datetime.fromisoformat(expiry).date()-today).days,1); T=days/365; r=sf(d['rates'].get('y10')); r=(r/100 if r and abs(r)>1.5 else (r or 0))
        rows=[]
        for K in sorted(set(c.strike.dropna())|set(p.strike.dropna())):
            cr=c[c.strike==K];pr=p[p.strike==K]; coi=float(cr.iloc[0].get('openInterest') or 0) if not cr.empty else 0;poi=float(pr.iloc[0].get('openInterest') or 0) if not pr.empty else 0;civ=iv(cr.iloc[0].get('impliedVolatility')) if not cr.empty else None;piv=iv(pr.iloc[0].get('impliedVolatility')) if not pr.empty else None;rows.append((float(K),coi,poi,civ,piv))
        atm=min([(abs(K-S),sum(x for x in (ci,pi) if x)/len([x for x in (ci,pi) if x])) for K,co,po,ci,pi in rows if ci or pi],default=None)
        if atm:
            aiv=atm[1];move=S*aiv*math.sqrt(T);o.update(status='live',expiry=expiry,atm_iv=aiv*100,expected_move_pct=move/S*100,expected_move_points=move)
        coi=sum(x[1] for x in rows);poi=sum(x[2] for x in rows);o['pcr_oi']=poi/coi if coi>0 else None
        heat=[]
        for K,co,po,ci,pi in rows:
            cg=gamma(S,K,ci,T,r) if ci else 0;pg=gamma(S,K,pi,T,r) if pi else 0;cge=cg*co*MULT*S*S*.01;pge=-pg*po*MULT*S*S*.01
            if abs(K-S)<=S*.15:heat.append({'strike':K,'call_oi':co,'put_oi':po,'call_gex':cge,'put_gex':pge,'net_gex':cge+pge})
        o['oi_heatmap']=heat;o['net_gex']=sum(x['net_gex'] for x in heat);total_call_oi=sum(x['call_oi'] for x in heat);total_put_oi=sum(x['put_oi'] for x in heat);nonzero=sum(1 for x in heat if x['call_oi'] or x['put_oi']);near=sum(1 for x in heat if abs(x['strike']-S)<=S*.05 and (x['call_oi'] or x['put_oi']));ratio=nonzero/len(heat) if heat else 0;reasons=[]
        if len(heat)<50:reasons.append('fewer than 50 strikes in modeled window')
        if nonzero<20:reasons.append('too few strikes with non-zero open interest')
        if near<6:reasons.append('thin open interest near spot')
        if total_call_oi+total_put_oi<500:reasons.append('low total open interest')
        if ratio<.20:reasons.append('sparse open-interest coverage')
        o['data_quality']={'status':'LIMITED' if reasons else 'GOOD','reason':'; '.join(reasons) if reasons else 'Sufficient strike and open-interest coverage for this modeled snapshot','strikes':len(heat),'nonzero_oi_strikes':nonzero,'nonzero_oi_ratio':ratio,'near_atm_nonzero_strikes':near,'total_call_oi':total_call_oi,'total_put_oi':total_put_oi}
        if heat:o['call_wall']=max(heat,key=lambda x:x['call_gex'])['strike'];o['put_wall']=min(heat,key=lambda x:x['put_gex'])['strike']
        def total_at(s): return sum(((gamma(s,K,ci,T,r) if ci else 0)*co-(gamma(s,K,pi,T,r) if pi else 0)*po)*MULT*s*s*.01 for K,co,po,ci,pi in rows if abs(K-s)<=S*.15)
        xs=[S*.85+(S*.30)*i/120 for i in range(121)];ys=[total_at(x) for x in xs];flips=[]
        for i in range(120):
            if ys[i]==0:flips.append(xs[i])
            elif ys[i]*ys[i+1]<0:flips.append(xs[i]-ys[i]*(xs[i+1]-xs[i])/(ys[i+1]-ys[i]))
        o['gamma_flip']=min(flips,key=lambda x:abs(x-S)) if flips else None;o['dealer_positioning']={'status':'modeled','regime':'Positive gamma' if o['net_gex']>0 else 'Negative gamma' if o['net_gex']<0 else 'Neutral gamma','net_gex':o['net_gex'],'gamma_flip':o['gamma_flip'],'model':'Modeled from listed OI, IV and Black-Scholes gamma; not direct dealer inventory.'}
    except:pass
    return o


def main():
    with open(OUT,encoding='utf-8') as f:d=json.load(f)
    d.setdefault('prices',{});d.setdefault('rates',{});d.setdefault('cot',{});d.setdefault('news',{});d.setdefault('macro',{})
    for k,t in {'ndx':'^NDX','dxy':'DX-Y.NYB','eurusd':'EURUSD=X','vix':'^VIX','usdjpy':'JPY=X','gbpusd':'GBPUSD=X','usdcn':'USDCNH=X','wti':'CL=F','gold':'GC=F','copper':'HG=F','natgas':'NG=F'}.items():
        v,ch=snap(t)
        if v is not None:d['prices'][k]=v;d['prices'][k+'_change']=ch
    treasury=treasury_rates()
    if treasury:
        for k,v in treasury.items():
            if k!='date' and v is not None:d['rates'][k]=v
        d['rates']['source']='U.S. Treasury Daily Treasury Par Yield Curve Rates';d['rates']['date']=treasury.get('date')
    else:d['rates']['source']=d['rates'].get('source','U.S. Treasury unavailable; previous snapshot retained')
    d['options']=options_model(d);d['cot']=fetch_cot();d['news']=fetch_news()
    fred_key=os.getenv('FRED_API_KEY','').strip();d['macro']=build_macro(fred_key,treasury)
    d['macro']['access']='Authenticated FRED API' if fred_key else 'Public FRED graph CSV endpoint'
    # Fed schedule is official/static calendar data; next meeting is calculated from the published 2026 schedule.
    meetings=[('2026-09-15','2026-09-16',True),('2026-10-27','2026-10-28',False),('2026-12-08','2026-12-09',True),('2027-01-26','2027-01-27',False)]
    today=datetime.now(timezone.utc).date(); next_m=next(((a,b,s) for a,b,s in meetings if datetime.fromisoformat(a).date()>=today),None)
    d['macro']['fomc']={'next_meeting':next_m[0]+' to '+next_m[1] if next_m else None,'is_sep':next_m[2] if next_m else None,'source':'Federal Reserve FOMC calendar'}
    # Regime engine: intentionally conservative; only emits a score when sufficient data exists.
    s=d['macro'].get('series',{});reg={}
    def val(k):return s.get(k,{}).get('value')
    def prev(k):return s.get(k,{}).get('previous')
    reg['inflation']='Hot / sticky' if val('cpi') is not None and prev('cpi') is not None and val('cpi')>prev('cpi') else 'Cooling / stable' if val('cpi') is not None else 'Unavailable'
    reg['labor']='Tight' if val('unemployment') is not None and val('unemployment')<4.5 else 'Looser' if val('unemployment') is not None else 'Unavailable'
    reg['growth']='Expanding' if val('gdp_growth') is not None and val('gdp_growth')>0 else 'Contracting' if val('gdp_growth') is not None else 'Unavailable'
    reg['liquidity']='Expanding' if val('fed_balance_sheet') is not None and prev('fed_balance_sheet') is not None and val('fed_balance_sheet')>prev('fed_balance_sheet') else 'Contracting' if val('fed_balance_sheet') is not None else 'Unavailable'
    reg['credit']='Stressed' if val('hy_spread') is not None and val('hy_spread')>5 else 'Contained' if val('hy_spread') is not None else 'Unavailable'
    reg['overall']='Mixed' if 'Unavailable' in reg.values() else ('Risk-off' if reg['credit']=='Stressed' or reg['growth']=='Contracting' else 'Mixed / risk-sensitive')
    d['macro']['regime']=reg
    d['generated_at']=datetime.now(timezone.utc).isoformat();d['sources']=['yfinance market/options adapter','U.S. Treasury Daily Treasury Par Yield Curve Rates','FRED economic data','Federal Reserve FOMC calendar','CFTC TFF Combined','Filtered Google News RSS aggregation']
    with open(OUT,'w',encoding='utf-8') as f:json.dump(d,f,indent=2)

if __name__=='__main__':main()
