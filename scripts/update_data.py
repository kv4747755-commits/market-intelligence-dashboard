import json, math
from datetime import datetime, timezone
import yfinance as yf
OUT='data.json'; MULT=100.0

def sf(x):
    try:
        x=float(x); return x if math.isfinite(x) else None
    except: return None

def snap(t):
    try:
        h=yf.Ticker(t).history(period='5d',interval='1d',auto_adjust=False)
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

def main():
    with open(OUT,encoding='utf-8') as f:d=json.load(f)
    d.setdefault('prices',{}); d.setdefault('rates',{})
    for k,t in {'ndx':'^NDX','dxy':'DX-Y.NYB','eurusd':'EURUSD=X','vix':'^VIX'}.items():
        v,ch=snap(t)
        if v is not None:d['prices'][k]=v; d['prices'][k+'_change']=ch
    for k,t in {'y3m':'^IRX','y10':'^TNX','y30':'^TYX'}.items():
        v,_=snap(t)
        if v is not None:d['rates'][k]=v
    S=sf(d['prices'].get('ndx'))
    o={'status':'unavailable','ticker':'^NDX','expiry':None,'spot':S,'atm_iv':None,'expected_move_pct':None,'expected_move_points':None,'pcr_oi':None,'gamma_flip':None,'put_wall':None,'call_wall':None,'net_gex':None,'oi_heatmap':[],'dealer_positioning':{'status':'unavailable','regime':None,'net_gex':None,'gamma_flip':None,'model':'Modeled from listed OI, IV and Black-Scholes gamma; not direct dealer inventory.'},'model':'Estimated GEX using listed option OI and modeled gamma; not direct dealer book.'}
    try:
        Tkr=yf.Ticker('^NDX'); ex=list(Tkr.options or [])
        if not ex or S is None: raise ValueError()
        today=datetime.now(timezone.utc).date(); fut=[e for e in ex if datetime.fromisoformat(e).date()>=today]; expiry=fut[0] if fut else ex[0]
        c=Tkr.option_chain(expiry).calls.copy(); p=Tkr.option_chain(expiry).puts.copy()
        if c.empty or p.empty: raise ValueError()
        days=max((datetime.fromisoformat(expiry).date()-today).days,1); T=days/365
        r=sf(d['rates'].get('y10')); r=(r/100 if r and abs(r)>1.5 else (r or 0))
        rows=[]
        for K in sorted(set(c.strike.dropna())|set(p.strike.dropna())):
            cr=c[c.strike==K]; pr=p[p.strike==K]
            coi=float(cr.iloc[0].get('openInterest') or 0) if not cr.empty else 0
            poi=float(pr.iloc[0].get('openInterest') or 0) if not pr.empty else 0
            civ=iv(cr.iloc[0].get('impliedVolatility')) if not cr.empty else None
            piv=iv(pr.iloc[0].get('impliedVolatility')) if not pr.empty else None
            rows.append((float(K),coi,poi,civ,piv))
        atm=min([(abs(K-S),sum(x for x in (ci,pi) if x)/len([x for x in (ci,pi) if x])) for K,co,po,ci,pi in rows if ci or pi],default=None)
        if atm:
            aiv=atm[1]; move=S*aiv*math.sqrt(T); o.update(status='live',expiry=expiry,atm_iv=aiv*100,expected_move_pct=move/S*100,expected_move_points=move)
        coi=sum(x[1] for x in rows); poi=sum(x[2] for x in rows); o['pcr_oi']=poi/coi if coi>0 else None
        heat=[]
        for K,co,po,ci,pi in rows:
            cg=gamma(S,K,ci,T,r) if ci else 0; pg=gamma(S,K,pi,T,r) if pi else 0
            cge=cg*co*MULT*S*S*.01; pge=-pg*po*MULT*S*S*.01
            if abs(K-S)<=S*.15: heat.append({'strike':K,'call_oi':co,'put_oi':po,'call_gex':cge,'put_gex':pge,'net_gex':cge+pge})
        o['oi_heatmap']=heat; total=sum(x['net_gex'] for x in heat); o['net_gex']=total
        if heat:
            o['call_wall']=max(heat,key=lambda x:x['call_gex'])['strike']; o['put_wall']=min(heat,key=lambda x:x['put_gex'])['strike']
        def total_at(s):
            return sum(((gamma(s,K,ci,T,r) if ci else 0)*co-(gamma(s,K,pi,T,r) if pi else 0)*po)*MULT*s*s*.01 for K,co,po,ci,pi in rows if abs(K-s)<=S*.15)
        xs=[S*.85+(S*.30)*i/120 for i in range(121)]; ys=[total_at(x) for x in xs]; flips=[]
        for i in range(120):
            if ys[i]==0:flips.append(xs[i])
            elif ys[i]*ys[i+1]<0:flips.append(xs[i]-ys[i]*(xs[i+1]-xs[i])/(ys[i+1]-ys[i]))
        o['gamma_flip']=min(flips,key=lambda x:abs(x-S)) if flips else None
        o['dealer_positioning']={'status':'modeled','regime':'Positive gamma' if total>0 else 'Negative gamma' if total<0 else 'Neutral gamma','net_gex':total,'gamma_flip':o['gamma_flip'],'model':'Modeled from listed OI, IV and Black-Scholes gamma; not direct dealer inventory.'}
    except Exception: pass
    d['options']=o; d['generated_at']=datetime.now(timezone.utc).isoformat(); d['sources']=['yfinance prototype adapter']
    with open(OUT,'w',encoding='utf-8') as f:json.dump(d,f,indent=2)
if __name__=='__main__':main()
