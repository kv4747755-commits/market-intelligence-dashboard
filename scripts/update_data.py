import json, math
from datetime import datetime, timezone
import yfinance as yf
OUT='data.json'
def sf(x):
    try:
        x=float(x); return x if math.isfinite(x) else None
    except: return None
def snap(ticker):
    h=yf.Ticker(ticker).history(period='5d',interval='1d',auto_adjust=False)
    if h.empty:return None,None
    c=h['Close'].dropna(); v=sf(c.iloc[-1]); p=sf(c.iloc[-2]) if len(c)>=2 else None
    return v,((v/p)-1)*100 if v is not None and p else None
def main():
    with open(OUT,encoding='utf-8') as f:d=json.load(f)
    for k,t in {'ndx':'^NDX','dxy':'DX-Y.NYB','eurusd':'EURUSD=X','vix':'^VIX'}.items():
        v,ch=snap(t)
        if v is not None:d['market'][k].update(value=v,change_pct=ch,source='yfinance',status='live-snapshot')
    d['options']={'status':'unavailable','ticker':'^NDX','expiry':None,'spot':d['market']['ndx']['value'],'atm_iv_pct':None,'expected_move_pct':None,'expected_move_points':None,'pcr_oi':None,'gamma_flip':None,'put_wall':None,'call_wall':None,'net_gex':None,'model':'Estimated GEX only when a valid option chain is available.'}
    try:
        t=yf.Ticker('^NDX'); ex=list(t.options or [])
        if ex:
            ch=t.option_chain(ex[0]); c=ch.calls.copy();p=ch.puts.copy();spot=d['market']['ndx']['value']
            if spot and not c.empty and not p.empty:
                c['dist']=(c.strike-spot).abs();p['dist']=(p.strike-spot).abs();a=[sf(c.sort_values('dist').iloc[0].get('impliedVolatility')),sf(p.sort_values('dist').iloc[0].get('impliedVolatility'))];a=[x for x in a if x is not None]
                if a:
                    iv=sum(a)/len(a); days=max((datetime.fromisoformat(ex[0]).date()-datetime.now(timezone.utc).date()).days,1); move=spot*iv*math.sqrt(days/365);d['options'].update(status='live',expiry=ex[0],atm_iv_pct=iv*100,expected_move_pct=move/spot*100,expected_move_points=move)
    except Exception:pass
    d['generated_at']=datetime.now(timezone.utc).isoformat()
    with open(OUT,'w',encoding='utf-8') as f:json.dump(d,f,indent=2)
if __name__=='__main__':main()
