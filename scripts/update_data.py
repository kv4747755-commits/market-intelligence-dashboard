import json, math, os
from datetime import datetime, timezone
import yfinance as yf

OUT='data.json'

def quote(t):
    x=yf.Ticker(t)
    h=x.history(period='2d', interval='1d', auto_adjust=False)
    if h.empty: return None
    c=float(h['Close'].iloc[-1]); prev=float(h['Close'].iloc[-2]) if len(h)>1 else None
    return {'value':c,'change_pct':((c/prev)-1)*100 if prev else None}

def options_snapshot(ticker):
    try:
        t=yf.Ticker(ticker)
        exps=t.options
        if not exps: return {'status':'No option expiries returned'}
        # nearest non-expired expiry
        expiry=exps[0]
        chain=t.option_chain(expiry)
        calls=chain.calls.copy(); puts=chain.puts.copy()
        spot=float(t.history(period='1d')['Close'].iloc[-1])
        all_rows=[]
        for df,side in [(calls,'call'),(puts,'put')]:
            for _,r in df.iterrows():
                try:
                    all_rows.append({'side':side,'strike':float(r['strike']),'iv':float(r.get('impliedVolatility',0) or 0),'oi':float(r.get('openInterest',0) or 0),'vol':float(r.get('volume',0) or 0)})
                except Exception: pass
        if not all_rows: return {'status':'Option chain empty','expiry':expiry}
        atm=min(all_rows,key=lambda x:abs(x['strike']-spot))
        atm_iv=atm['iv']*100
        from datetime import date
        dte=max((date.fromisoformat(expiry)-datetime.now(timezone.utc).date()).days,0)
        T=max(dte/365,1/365)
        em=spot*atm['iv']*math.sqrt(T)*100
        calls_oi=sum(x['oi'] for x in all_rows if x['side']=='call')
        puts_oi=sum(x['oi'] for x in all_rows if x['side']=='put')
        # Simple modeled gamma exposure. Assumption: dealer is opposite the customer side; sign convention is call + / put -.
        def normpdf(x): return math.exp(-0.5*x*x)/math.sqrt(2*math.pi)
        r=0.04; sigma=max(atm['iv'],0.01)
        g=[]
        for x in all_rows:
            K=x['strike']; tau=T
            try:
                d1=(math.log(spot/K)+(r+0.5*sigma*sigma)*tau)/(sigma*math.sqrt(tau))
                gamma=normpdf(d1)/(spot*sigma*math.sqrt(tau))
                gex=gamma*x['oi']*100*spot*spot*0.01
                gex = gex if x['side']=='call' else -gex
                g.append((K,gex))
            except Exception: pass
        bystrike={}
        for K,v in g: bystrike[K]=bystrike.get(K,0)+v
        levels=sorted(bystrike.items())
        cum=0; flip=None
        for K,v in levels:
            prev=cum; cum+=v
            if prev==0 or cum==0 or (prev<0<cum) or (prev>0>cum): flip=K; break
        call_wall=max(((K,v) for K,v in levels if v>0),key=lambda z:z[1],default=(None,None))[0]
        put_wall=min(((K,v) for K,v in levels if v<0),key=lambda z:z[1],default=(None,None))[0]
        return {'status':f'Live chain: {ticker} {expiry}','ticker':ticker,'expiry':expiry,'spot':spot,'atm_iv':atm_iv,'expected_move_pct':(em/spot)*100,'expected_move_points':em,'pcr_oi':puts_oi/calls_oi if calls_oi else None,'gamma_flip':flip,'call_wall':call_wall,'put_wall':put_wall,'model':'Estimated GEX; dealer-sign assumption, not direct dealer book.'}
    except Exception as e:
        return {'status':'Options adapter error: '+str(e)[:120]}

def main():
    symbols={'ndx':'NQ=F','dxy':'DX-Y.NYB','eurusd':'EURUSD=X','vix':'^VIX'}
    prices={}
    for k,s in symbols.items():
        q=quote(s)
        if q: prices[k]=q['value']; prices[k+'_change']=q['change_pct']
    rates={}
    for k,s,m in [('y3m','^IRX',0.01),('y10','^TNX',0.1),('y30','^TYX',0.1)]:
        q=quote(s)
        if q: rates[k]=q['value']*m
    # Prefer NDX options, fall back to QQQ if NDX is unavailable.
    opt=options_snapshot('^NDX')
    if 'atm_iv' not in opt: opt=options_snapshot('QQQ'); opt['proxy_for']='NDX'
    data={'generated_at':datetime.now(timezone.utc).isoformat(),'prices':prices,'rates':rates,'options':opt,'sources':['yfinance prototype adapter']}
    with open(OUT,'w') as f: json.dump(data,f,indent=2)

if __name__=='__main__': main()
