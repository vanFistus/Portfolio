import json, math, os
from datetime import datetime
import pandas as pd
import yfinance as yf


FUNDAMENTAL_MARKETS=[
    # Proxy-ETFs/Indizes für die dynamischen Fundamentaldaten.
    # Yahoo Finance liefert die Kennzahlen je nach Ticker unterschiedlich vollständig.
    # Fehlende Werte werden im Dashboard als "—" angezeigt und fließen neutral in die Sterne ein.
    {"market":"USA","symbol":"SPY","label":"SPDR S&P 500 ETF Trust"},
    {"market":"Europa","symbol":"VGK","label":"Vanguard FTSE Europe ETF"},
    {"market":"Emerging Markets","symbol":"EEM","label":"iShares MSCI Emerging Markets ETF"},
    {"market":"China","symbol":"MCHI","label":"iShares MSCI China ETF"},
    {"market":"Indien","symbol":"INDA","label":"iShares MSCI India ETF"},
    {"market":"Japan","symbol":"EWJ","label":"iShares MSCI Japan ETF"}
]



ASSETS=[
{"name":"S&P 500","short":"SP","isin":"IE00B5BMR087","symbol":"SXR8.DE","category":"Aktien"},
{"name":"Nasdaq 100","short":"ND","isin":"IE0032077012","symbol":"SXRV.DE","category":"Aktien"},
{"name":"STOXX Europe 600","short":"EU","isin":"DE0002635307","symbol":"EXSA.DE","category":"Aktien"},
{"name":"Emerging Markets","short":"EM","isin":"IE00BKM4GZ66","symbol":"IQQE.DE","category":"Aktien"},
{"name":"China","short":"CN","isin":"A2PGQN","symbol":"IQQC.DE","category":"Aktien"},
{"name":"Indien","short":"IN","isin":"IE00BZCQB185","symbol":"QDV5.DE","category":"Aktien"},
{"name":"Japan","short":"JP","isin":"IE00B4L5YX21","symbol":"SJPA.L","category":"Aktien"},
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

def as_pct(x):
    x=clean(x)
    if x is None:
        return None
    # Yahoo liefert manche Kennzahlen als Dezimalwert (0.014), andere als Prozentwert (1.4).
    return round(x*100,2) if abs(x) <= 1 else round(x,2)

def pick(info, keys, percent=False):
    for k in keys:
        if k in info and info.get(k) not in (None, ""):
            return as_pct(info.get(k)) if percent else clean(info.get(k))
    return None

def get_fundamentals(row):
    market=row["market"]
    symbol=row["symbol"]
    try:
        t=yf.Ticker(symbol)
        info=t.get_info() or {}
    except Exception:
        info={}

    trailing_pe=pick(info,["trailingPE","trailingPe","peRatio","priceEpsCurrentYear"])
    forward_pe=pick(info,["forwardPE","forwardPe"])
    pb=pick(info,["priceToBook","priceToBookRatio"])
    dividend_yield=pick(info,["yield","dividendYield","trailingAnnualDividendYield"],percent=True)
    roe=pick(info,["returnOnEquity"],percent=True)
    earnings_growth=pick(info,["earningsGrowth","earningsQuarterlyGrowth"],percent=True)

    return {
        "market": market,
        "source_symbol": symbol,
        "source_label": row.get("label",""),
        "pe": trailing_pe,
        "forward_pe": forward_pe,
        "pb": pb,
        "dividend_yield": dividend_yield,
        "roe": roe,
        "earnings_growth": earnings_growth
    }


def pct(a,b):
    return None if a is None or b in (None,0) else round((a/b-1)*100,2)

def get_close(symbol, period="10y", interval="1d"):
    try:
        # Für Tagesdaten laden wir bewusst "max" statt nur "10y".
        # Dadurch sind history_3y, history_5y und history_10y stabil befüllt,
        # sofern Yahoo Finance für den jeweiligen Ticker genug Historie liefert.
        effective_period = "max" if interval == "1d" else period
        hist=yf.download(symbol,period=effective_period,interval=interval,auto_adjust=False,progress=False,threads=False)
    except Exception:
        return pd.Series(dtype=float)
    if hist.empty:
        return pd.Series(dtype=float)
    close=hist["Close"].dropna()
    if isinstance(close,pd.DataFrame):
        close=close.iloc[:,0]
    return close

def hist_list(series):
    return [clean(x) for x in series.dropna().tolist()]

def hist_count(series):
    return int(series.dropna().shape[0])

def from_date(series, years=None, months=None, days=None):
    if series.empty:
        return series
    end=series.index[-1]
    if years:
        start=end-pd.DateOffset(years=years)
    elif months:
        start=end-pd.DateOffset(months=months)
    elif days:
        start=end-pd.DateOffset(days=days)
    else:
        return series
    return series[series.index>=start]

def get(asset):
    a=dict(asset)
    close=get_close(a["symbol"],"10y","1d")
    intra=get_close(a["symbol"],"1d","5m")

    if close.empty:
        a.update(price=None,currency="",day_pct=None,ytd_pct=None,ma200_diff_pct=None,
                 history_intraday=[],history_1w=[],history_1m=[],history_ytd=[],history_1y=[],history_3y=[],history_5y=[],history_10y=[],history_30d=[])
        return a

    price=clean(close.iloc[-1])
    prev=clean(close.iloc[-2]) if len(close)>1 else None

    idx_tz = close.index.tz if getattr(close.index, "tz", None) else None
    ytd=close[close.index>=pd.Timestamp("2026-01-01",tz=idx_tz)]
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
        history_1w=hist_list(from_date(close,days=7)),
        history_1m=hist_list(from_date(close,months=1)),
        history_ytd=hist_list(ytd),
        history_1y=hist_list(from_date(close,years=1)),
        history_3y=hist_list(from_date(close,years=3)),
        history_5y=hist_list(from_date(close,years=5)),
        history_10y=hist_list(from_date(close,years=10)),
        history_30d=hist_list(from_date(close,days=30)),
        history_points={
            "1y": hist_count(from_date(close,years=1)),
            "3y": hist_count(from_date(close,years=3)),
            "5y": hist_count(from_date(close,years=5)),
            "10y": hist_count(from_date(close,years=10)),
            "all": hist_count(close)
        }
    )
    return a

assets=[get(a) for a in ASSETS]
fundamentals=[get_fundamentals(x) for x in FUNDAMENTAL_MARKETS]

def avg(items,field):
    vals=[x[field] for x in items if x.get(field) is not None]
    return round(sum(vals)/len(vals),2) if vals else None

summary=[]
for cat in ["Aktien","Rohstoffe","Bitcoin"]:
    items=[a for a in assets if a["category"]==cat]
    summary.append({"name":cat,"day":avg(items,"day_pct"),"ytd":avg(items,"ytd_pct")})

os.makedirs("data", exist_ok=True)
with open("data/market_data.json","w",encoding="utf-8") as f:
    json.dump({"updated":datetime.now().strftime("%d.%m.%Y, %H:%M"),"assets":assets,"summary":summary,"fundamentals":fundamentals},f,ensure_ascii=False,indent=2)
