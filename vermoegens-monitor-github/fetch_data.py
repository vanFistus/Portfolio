import json
import math
import os
from datetime import datetime

import pandas as pd
import yfinance as yf


ASSETS = [
    {"name": "S&P 500", "short": "SP", "isin": "IE00B5BMR087", "symbol": "SXR8.DE", "category": "Aktien"},
    {"name": "Nasdaq 100", "short": "ND", "isin": "IE0032077012", "symbol": "SXRV.DE", "category": "Aktien"},
    {"name": "STOXX Europe 600", "short": "EU", "isin": "DE0002635307", "symbol": "EXSA.DE", "category": "Aktien"},
    {"name": "Emerging Markets", "short": "EM", "isin": "IE00BKM4GZ66", "symbol": "IQQE.DE", "category": "Aktien"},
    {"name": "China", "short": "CN", "isin": "A2PGQN", "symbol": "IQQC.DE", "category": "Aktien"},
    {"name": "Indien", "short": "IN", "isin": "IE00BZCQB185", "symbol": "QDV5.DE", "category": "Aktien"},
    {"name": "Japan", "short": "JP", "isin": "IE00B4L5YX21", "symbol": "SJPA.L", "category": "Aktien"},
    {"name": "Bitcoin", "short": "₿", "isin": "GB00BJYDH287", "symbol": "BTC-EUR", "category": "Bitcoin"},
    {"name": "Gold", "short": "Au", "isin": "DE000A0S9GB0", "symbol": "4GLD.DE", "category": "Rohstoffe"},
    {"name": "Silber", "short": "Ag", "isin": "JE00B1VS3333", "symbol": "XAAG.DE", "category": "Rohstoffe"},
    {"name": "Brent Öl", "short": "Oil", "isin": "BZ=F", "symbol": "BZ=F", "category": "Rohstoffe"},
    {"name": "Kaffee", "short": "☕", "isin": "KC=F", "symbol": "KC=F", "category": "Rohstoffe"},
]


def clean(value):
    try:
        value = float(value)
        return None if math.isnan(value) else round(value, 4)
    except Exception:
        return None


def pct(a, b):
    return None if a is None or b in (None, 0) else round((a / b - 1) * 100, 2)


def get_close(symbol, period="10y", interval="1d"):
    try:
        # Für Tagesdaten bewusst "max": dadurch bleiben 3/5/10 Jahre
        # stabil verfügbar, sofern Yahoo für den Ticker genügend Historie hat.
        effective_period = "max" if interval == "1d" else period
        hist = yf.download(
            symbol,
            period=effective_period,
            interval=interval,
            auto_adjust=False,
            progress=False,
            threads=False,
        )
    except Exception:
        return pd.Series(dtype=float)

    if hist.empty:
        return pd.Series(dtype=float)

    close = hist["Close"].dropna()

    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]

    return close


def hist_list(series):
    return [clean(x) for x in series.dropna().tolist()]


def hist_dates(series, intraday=False):
    """Datumsachse passend zu hist_list().

    Tagesdaten werden als YYYY-MM-DD gespeichert. Intraday-Zeitpunkte werden
    als ISO-8601 inklusive Zeitzonen-Offset gespeichert, sofern Yahoo einen
    timezone-aware Index liefert. JavaScript kann sie dadurch als echte
    Zeitachse darstellen.
    """
    result = []

    for idx in series.dropna().index:
        ts = pd.Timestamp(idx)

        if intraday:
            result.append(ts.isoformat())
        else:
            result.append(ts.strftime("%Y-%m-%d"))

    return result


def hist_count(series):
    return int(series.dropna().shape[0])


def from_date(series, years=None, months=None, days=None):
    if series.empty:
        return series

    end = series.index[-1]

    if years:
        start = end - pd.DateOffset(years=years)
    elif months:
        start = end - pd.DateOffset(months=months)
    elif days:
        start = end - pd.DateOffset(days=days)
    else:
        return series

    return series[series.index >= start]


def empty_history_payload():
    return {
        "history_intraday": [],
        "history_dates_intraday": [],
        "history_1w": [],
        "history_dates_1w": [],
        "history_1m": [],
        "history_dates_1m": [],
        "history_ytd": [],
        "history_dates_ytd": [],
        "history_1y": [],
        "history_dates_1y": [],
        "history_3y": [],
        "history_dates_3y": [],
        "history_5y": [],
        "history_dates_5y": [],
        "history_10y": [],
        "history_dates_10y": [],
        "history_30d": [],
        "history_dates_30d": [],
        "history_points": {
            "1y": 0,
            "3y": 0,
            "5y": 0,
            "10y": 0,
            "all": 0,
        },
    }


def get(asset):
    a = dict(asset)

    close = get_close(a["symbol"], "10y", "1d")
    intra = get_close(a["symbol"], "1d", "5m")

    if close.empty:
        a.update(
            price=None,
            currency="",
            day_pct=None,
            ytd_pct=None,
            ma200_diff_pct=None,
            **empty_history_payload(),
        )
        return a

    price = clean(close.iloc[-1])
    prev = clean(close.iloc[-2]) if len(close) > 1 else None

    idx_tz = close.index.tz if getattr(close.index, "tz", None) else None

    # Dynamisch anhand des letzten verfügbaren Handelstags statt fest auf 2026.
    latest_year = pd.Timestamp(close.index[-1]).year
    ytd_start = pd.Timestamp(f"{latest_year}-01-01", tz=idx_tz)
    ytd = close[close.index >= ytd_start]

    ytd_base = clean(ytd.iloc[0]) if not ytd.empty else None
    ma200 = clean(close.tail(200).mean()) if len(close) >= 200 else None

    try:
        currency = yf.Ticker(a["symbol"]).fast_info.get("currency", "")
    except Exception:
        currency = ""

    h_1w = from_date(close, days=7)
    h_1m = from_date(close, months=1)
    h_1y = from_date(close, years=1)
    h_3y = from_date(close, years=3)
    h_5y = from_date(close, years=5)
    h_10y = from_date(close, years=10)
    h_30d = from_date(close, days=30)

    a.update(
        price=price,
        currency=currency,
        day_pct=pct(price, prev),
        ytd_pct=pct(price, ytd_base),
        ma200_diff_pct=pct(price, ma200),

        history_intraday=hist_list(intra),
        history_dates_intraday=hist_dates(intra, intraday=True),

        history_1w=hist_list(h_1w),
        history_dates_1w=hist_dates(h_1w),

        history_1m=hist_list(h_1m),
        history_dates_1m=hist_dates(h_1m),

        history_ytd=hist_list(ytd),
        history_dates_ytd=hist_dates(ytd),

        history_1y=hist_list(h_1y),
        history_dates_1y=hist_dates(h_1y),

        history_3y=hist_list(h_3y),
        history_dates_3y=hist_dates(h_3y),

        history_5y=hist_list(h_5y),
        history_dates_5y=hist_dates(h_5y),

        history_10y=hist_list(h_10y),
        history_dates_10y=hist_dates(h_10y),

        history_30d=hist_list(h_30d),
        history_dates_30d=hist_dates(h_30d),

        history_points={
            "1y": hist_count(h_1y),
            "3y": hist_count(h_3y),
            "5y": hist_count(h_5y),
            "10y": hist_count(h_10y),
            "all": hist_count(close),
        },
    )

    return a


assets = [get(a) for a in ASSETS]


def avg(items, field):
    vals = [x[field] for x in items if x.get(field) is not None]
    return round(sum(vals) / len(vals), 2) if vals else None


summary = []

for cat in ["Aktien", "Rohstoffe", "Bitcoin"]:
    items = [a for a in assets if a["category"] == cat]
    summary.append(
        {
            "name": cat,
            "day": avg(items, "day_pct"),
            "ytd": avg(items, "ytd_pct"),
        }
    )


os.makedirs("data", exist_ok=True)

with open("data/market_data.json", "w", encoding="utf-8") as handle:
    json.dump(
        {
            "updated": datetime.now().strftime("%d.%m.%Y, %H:%M"),
            "assets": assets,
            "summary": summary,
        },
        handle,
        ensure_ascii=False,
        indent=2,
    )
