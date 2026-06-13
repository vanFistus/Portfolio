import json, math
from datetime import datetime
import pandas as pd
import yfinance as yf

ASSETS=[
{"name":"S&P 500","short":"SP","isin":"IE00B5BMR087","symbol":"SXR8.DE","category":"Aktien"},
{"name":"Nasdaq 100","short":"ND","isin":"IE0032077012","symbol":"SXRV.DE","category":"Aktien"},
{"name":"STOXX Europe 600","short":"EU","isin":"DE0002635307","symbol":"EXSA.DE","category":"Aktien"},
{"name":"Emerging Markets","short":"EM","isin":"IE00BKM4GZ66","symbol":"EUNM.DE","category":"Aktien"},
{"name":"China","short":"CN","isin":"A2PGQN","symbol":"IQQC.DE","category":"Aktien"},
{"name":"Indien","short":"IN","isin":"IE00BZCQB185","symbol":"QDV5.DE","category":"Aktien"},
{"name":"Japan","short":"JP","isin":"IE00B4L5YX21","symbol":"XDJP.DE","category":"Aktien"},
{"name":"Bitcoin","short":"₿","isin":"GB00BJYDH287","symbol":"BTC-EUR","category":"Bitcoin"},
{"name":"Gold","short":"Au","isin":"DE000A0S9GB0","symbol":"4GLD.DE","category":"Rohstoffe"},
{"name":"Silber","short":"Ag","isin":"JE00B1VS3333","symbol":"XAAG.DE","category":"Rohstoffe"},
{"name":"Brent Öl","short":"Oil","isin":"BZ=F","symbol":"BZ=F","category":"Rohstoffe"},
{"name":"Kaffee","short":"☕","isin":"KC=F","symbol":"KC=F","category":"Rohstoffe"}]

def clean(x):
    try:
        x=float(x)
        return None if math.isnan(x) else round(x,4)
    except Exception:
        return None

def pct(a,b):
    return None if a is None or b in (None,0) else round((a/b-1)*100,2)

def get_close(symbol, period="1y", interval="1d"):
    hist=yf.download(symbol,period=period,interval=interval,auto_adjust=False,progress=False,threads=False)
    if hist.empty: return pd.Series(dtype=float)
    close=hist["Close"].dropna()
    if isinstance(close,pd.DataFrame): close=close.iloc[:,0]
    return close

def hist_list(series):
    return [clean(x) for x in series.dropna().tolist()]

def get(asset):
    a=dict(asset)
    close=get_close(a["symbol"],"1y","1d")
    intra=get_close(a["symbol"],"1d","5m")

    if close.empty:
        a.update(price=None,currency="",day_pct=None,ytd_pct=None,ma200_diff_pct=None,
                 history_intraday=[],history_1w=[],history_1m=[],history_ytd=[],history_1y=[],history_30d=[])
        return a

    price=clean(close.iloc[-1])
    prev=clean(close.iloc[-2]) if len(close)>1 else None
    ytd=close[close.index>=pd.Timestamp("2026-01-01",tz=close.index.tz if close.index.tz else None)]
    ytd_base=clean(ytd.iloc[0]) if not ytd.empty else None
    ma200=clean(close.tail(200).mean()) if len(close)>=200 else None

    try:
        currency=yf.Ticker(a["symbol"]).fast_info.get("currency","")
    except Exception:
        currency=""

    a.update(
        price=price,
        currency=currency,
        day_pct=pct(price,prev),
        ytd_pct=pct(price,ytd_base),
        ma200_diff_pct=pct(price,ma200),
        history_intraday=hist_list(intra),
        history_1w=hist_list(close.tail(7)),
        history_1m=hist_list(close.tail(31)),
        history_ytd=hist_list(ytd),
        history_1y=hist_list(close),
        history_30d=hist_list(close.tail(30))
    )
    return a

assets=[get(a) for a in ASSETS]

def avg(items,field):
    vals=[x[field] for x in items if x.get(field) is not None]
    return round(sum(vals)/len(vals),2) if vals else None

summary=[]
for cat in ["Aktien","Rohstoffe","Bitcoin"]:
    items=[a for a in assets if a["category"]==cat]
    summary.append({"name":cat,"day":avg(items,"day_pct"),"ytd":avg(items,"ytd_pct")})

with open("data/market_data.json","w",encoding="utf-8") as f:
    json.dump({"updated":datetime.now().strftime("%d.%m.%Y, %H:%M"),
               "assets":assets,"summary":summary},f,ensure_ascii=False,indent=2)
