import json, math
from datetime import datetime
import pandas as pd
import yfinance as yf
ASSETS=[
{"name":"S&P 500","short":"SP","isin":"IE00B5BMR087","symbol":"CSPX.L","category":"Aktien"},
{"name":"STOXX Europe 600","short":"EU","isin":"DE0002635307","symbol":"EXSA.DE","category":"Aktien"},
{"name":"Emerging Markets","short":"EM","isin":"IE00BKM4GZ66","symbol":"EIMI.L","category":"Aktien"},
{"name":"China","short":"CN","isin":"A2PGQN","symbol":"XCHA.DE","category":"Aktien"},
{"name":"Indien","short":"IN","isin":"IE00BZCQB185","symbol":"IIND.L","category":"Aktien"},
{"name":"Japan", "short":"JP", "isin":"IE00B4L5YX21", "symbol":"SJPA.L", "category":"Aktien"},
{"name":"Bitcoin","short":"₿","isin":"GB00BJYDH287","symbol":"BITC.SW","category":"Bitcoin"},
{"name":"Gold","short":"Au","isin":"DE000A0S9GB0","symbol":"4GLD.DE","category":"Rohstoffe"},
{"name":"Silber","short":"Ag","isin":"JE00B1VS3333","symbol":"PHAG.L","category":"Rohstoffe"},
{"name":"Brent Öl","short":"Oil","isin":"BZ=F","symbol":"BZ=F","category":"Rohstoffe"},
{"name":"Kaffee","short":"☕","isin":"KC=F","symbol":"KC=F","category":"Rohstoffe"}]
def clean(x):
    try:
        x=float(x)
        return None if math.isnan(x) else round(x,4)
    except Exception:return None
def pct(a,b): return None if a is None or b in (None,0) else round((a/b-1)*100,2)
def get(a):
    hist=yf.download(a['symbol'],period='1y',interval='1d',auto_adjust=False,progress=False,threads=False)
    if hist.empty:
        o=dict(a); o.update(price=None,currency='',day_pct=None,ytd_pct=None,ma200_diff_pct=None,history_30d=[]); return o
    close=hist['Close'].dropna()
    if isinstance(close,pd.DataFrame): close=close.iloc[:,0]
    price=clean(close.iloc[-1]); prev=clean(close.iloc[-2]) if len(close)>=2 else None
    ytd=close[close.index>=pd.Timestamp('2026-01-01')]
    ytd_base=clean(ytd.iloc[0]) if not ytd.empty else None
    ma200=clean(close.tail(200).mean()) if len(close)>=200 else None
    try: currency=(yf.Ticker(a['symbol']).fast_info or {}).get('currency') or ''
    except Exception: currency=''
    o=dict(a); o.update(price=price,currency=currency,day_pct=pct(price,prev),ytd_pct=pct(price,ytd_base),ma200_diff_pct=pct(price,ma200),history_30d=[clean(v) for v in close.tail(30).tolist()]); return o
assets=[get(a) for a in ASSETS]
def avg(cat,field):
    vals=[x[field] for x in assets if x['category']==cat and x[field] is not None]
    return round(sum(vals)/len(vals),2) if vals else None
summary=[{'name':c,'day':avg(c,'day_pct'),'ytd':avg(c,'ytd_pct')} for c in ['Aktien','Rohstoffe','Bitcoin']]
with open('data/market_data.json','w',encoding='utf-8') as f: json.dump({'updated':datetime.now().strftime('%d.%m.%Y, %H:%M'),'assets':assets,'summary':summary},f,ensure_ascii=False,indent=2)
