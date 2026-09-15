import json
import math
import os
import statistics
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf


OUTPUT_FILE = "data/fundamentals.json"
BERLIN = ZoneInfo("Europe/Berlin")

# Mehrere ETFs je Region dienen als Plausibilitäts-/Fallback-Quellen.
# Die erste Position ist möglichst der ETF, der auch im Dashboard verwendet wird.
REGIONS = [
    {"market": "USA", "proxies": ["SXR8.DE", "SPY", "IVV"]},
    {"market": "Europa", "proxies": ["EXSA.DE", "VGK", "IEUR"]},
    {"market": "Emerging Markets", "proxies": ["IQQE.DE", "IEMG", "EEM"]},
    {"market": "China", "proxies": ["IQQC.DE", "MCHI", "FXI"]},
    {"market": "Indien", "proxies": ["QDV5.DE", "INDA", "FLIN"]},
    {"market": "Japan", "proxies": ["SJPA.L", "EWJ", "BBJP"]},
]

FIELDS = [
    "pe",
    "forward_pe",
    "pb",
    "dividend_yield",
    "roe",
    "earnings_growth",
]

# Bewusst relativ enge Grenzen für breite Aktienmärkte.
# Damit wird z. B. ein offensichtlich falsches KBV von 0,4 nicht übernommen.
LIMITS = {
    "pe": (5.0, 60.0),
    "forward_pe": (5.0, 60.0),
    "pb": (0.65, 10.0),
    "dividend_yield": (0.10, 10.0),
    "roe": (2.0, 50.0),
    "earnings_growth": (-30.0, 80.0),
}

# Maximale Abweichung eines einzelnen neuen Werts zum Vorwert.
# Bei mindestens 2/3 übereinstimmenden Proxies wird stattdessen der Konsens genutzt.
MAX_SINGLE_CHANGE = {
    "pe": 0.35,
    "forward_pe": 0.35,
    "pb": 0.40,
    "dividend_yield": 0.60,
    "earnings_growth": 0.80,
}


def finite_number(value):
    try:
        if value is None or value is pd.NA:
            return None
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def round1(value):
    value = finite_number(value)
    return None if value is None else round(value, 1)


def to_percent(value):
    """
    Yahoo liefert Prozentsätze je nach Feld teils als 0.123 und teils als 12.3.
    Kleine Verhältniswerte werden deshalb in Prozent umgerechnet.
    """
    value = finite_number(value)
    if value is None:
        return None
    if abs(value) <= 1.5:
        value *= 100.0
    return value


def is_valid(field, value):
    value = finite_number(value)
    if value is None:
        return False
    lo, hi = LIMITS[field]
    return lo <= value <= hi


def first_number(mapping, keys):
    if not isinstance(mapping, dict):
        return None
    for key in keys:
        value = finite_number(mapping.get(key))
        if value is not None:
            return value
    return None


def safe_info(ticker, attempts=3):
    for attempt in range(attempts):
        try:
            data = ticker.get_info()
            return data if isinstance(data, dict) else {}
        except Exception as exc:
            if attempt == attempts - 1:
                print(f"  info failed for {ticker.ticker}: {exc}")
            else:
                time.sleep(1.5 * (attempt + 1))
    return {}


def safe_funds_data(ticker, attempts=3):
    for attempt in range(attempts):
        try:
            return ticker.get_funds_data()
        except Exception as exc:
            if attempt == attempts - 1:
                print(f"  funds_data failed for {ticker.ticker}: {exc}")
            else:
                time.sleep(1.5 * (attempt + 1))
    return None


def fund_metric(funds_data, symbol, row_name):
    """
    yfinance equity_holdings ist normalerweise ein DataFrame:

                           <TICKER>   Category Average
        Price/Earnings       ...
        Price/Book           ...
        ...
        3 Year Earnings Growth ...

    WICHTIG:
    Es wird ausschließlich die ETF-/Ticker-Spalte gelesen.
    'Category Average' wird niemals als Marktwert verwendet.
    """
    if funds_data is None:
        return None

    try:
        df = funds_data.equity_holdings
    except Exception:
        return None

    if not isinstance(df, pd.DataFrame) or df.empty:
        return None

    # Exakter Zeilenname aus yfinance.
    if row_name not in df.index:
        return None

    # Bevorzugt exakt die Spalte des abgefragten Symbols.
    if symbol in df.columns:
        return finite_number(df.at[row_name, symbol])

    # Robust gegen kleine Änderungen im Spaltennamen:
    # nimm die erste Nicht-"Category Average"-Spalte.
    usable_columns = [
        c for c in df.columns
        if str(c).strip().lower() != "category average"
    ]

    if len(usable_columns) == 1:
        return finite_number(df.at[row_name, usable_columns[0]])

    return None


def read_existing():
    if not os.path.exists(OUTPUT_FILE):
        return {"updated": "—", "regions": []}

    try:
        with open(OUTPUT_FILE, "r", encoding="utf-8") as handle:
            payload = json.load(handle)

        if isinstance(payload, list):
            return {"updated": "—", "regions": payload}

        if isinstance(payload, dict):
            payload.setdefault("regions", [])
            return payload

    except Exception as exc:
        print(f"Could not read existing {OUTPUT_FILE}: {exc}")

    return {"updated": "—", "regions": []}


def existing_by_market(payload):
    result = {}
    for row in payload.get("regions", []):
        if isinstance(row, dict) and row.get("market"):
            result[row["market"]] = row
    return result


def relative_difference(a, b):
    a = finite_number(a)
    b = finite_number(b)
    if a is None or b is None:
        return None
    denominator = max(abs(b), 1e-9)
    return abs(a - b) / denominator


def consensus_value(field, candidates, previous_value):
    """
    Auswahlregeln:
    1) Ungültige Werte werden entfernt.
    2) >= 3 Werte: Median (robust gegen einen Ausreißer).
    3) 2 Werte:
       - liegen sie halbwegs zusammen -> Median
       - widersprechen sie sich stark -> der Wert näher am plausiblen Vorwert
         wird genommen; ohne Vorwert wird nichts überschrieben.
    4) 1 Wert:
       - wird nur akzeptiert, wenn er nicht extrem vom Vorwert springt.
    """
    clean = []
    for item in candidates:
        value = finite_number(item.get("value"))
        if is_valid(field, value):
            clean.append({**item, "value": value})

    old = finite_number(previous_value)
    old_valid = is_valid(field, old)

    if not clean:
        return (old if old_valid else None), "carried_forward", []

    values = [x["value"] for x in clean]

    if len(clean) >= 3:
        median_value = statistics.median(values)

        # Quellen nahe am Median dokumentieren; Ausreißer bleiben in raw_candidates sichtbar.
        near = sorted(
            clean,
            key=lambda x: abs(x["value"] - median_value)
        )[:2]

        return median_value, "proxy_median", near

    if len(clean) == 2:
        a, b = clean
        midpoint = statistics.median(values)
        disagreement = abs(a["value"] - b["value"]) / max(abs(midpoint), 1e-9)

        # Zwei ähnliche Quellen -> Mittelwert/Median ist in Ordnung.
        if disagreement <= 0.35:
            return midpoint, "proxy_median", clean

        # Zwei stark widersprüchliche Quellen: am bisherigen plausiblen Marktwert orientieren.
        if old_valid:
            chosen = min(clean, key=lambda x: abs(x["value"] - old))
            max_change = MAX_SINGLE_CHANGE.get(field, 0.50)
            if relative_difference(chosen["value"], old) <= max_change:
                return chosen["value"], "proxy_previous_check", [chosen]

        # Ohne belastbaren Vorwert keine automatische Überschreibung.
        return (old if old_valid else None), "carried_forward_conflict", clean

    # Genau eine Quelle.
    chosen = clean[0]
    if old_valid:
        max_change = MAX_SINGLE_CHANGE.get(field, 0.50)
        if relative_difference(chosen["value"], old) > max_change:
            return old, "carried_forward_outlier", clean

    return chosen["value"], "single_proxy", clean


def collect_proxy_data(symbol):
    ticker = yf.Ticker(symbol)
    funds_data = safe_funds_data(ticker)
    info = safe_info(ticker)

    pe = fund_metric(funds_data, symbol, "Price/Earnings")
    pb = fund_metric(funds_data, symbol, "Price/Book")
    growth = fund_metric(funds_data, symbol, "3 Year Earnings Growth")

    # 3Y Earnings Growth ist im Fund-Datensatz häufig als Verhältnis gespeichert.
    growth = to_percent(growth)

    forward_pe = first_number(info, ["forwardPE", "forwardPe"])

    dividend_yield = to_percent(
        first_number(
            info,
            ["dividendYield", "trailingAnnualDividendYield", "yield"],
        )
    )

    return {
        "symbol": symbol,
        "pe": pe,
        "forward_pe": forward_pe,
        "pb": pb,
        "dividend_yield": dividend_yield,
        "earnings_growth": growth,
    }


def make_candidates(proxy_rows, field):
    result = []
    for row in proxy_rows:
        value = row.get(field)
        if value is not None:
            result.append({
                "symbol": row["symbol"],
                "value": value,
            })
    return result


def candidate_source(items):
    if not items:
        return ""
    return ", ".join(
        f"{x['symbol']}={round1(x['value'])}"
        for x in items
    )


def fetch_region(region, previous):
    market = region["market"]
    print(f"\n=== {market} ===")

    proxy_rows = []

    for symbol in region["proxies"]:
        print(f"  fetching {symbol}")
        try:
            row = collect_proxy_data(symbol)
            proxy_rows.append(row)
            print(
                "   ",
                f"PE={round1(row['pe'])}, "
                f"FwdPE={round1(row['forward_pe'])}, "
                f"PB={round1(row['pb'])}, "
                f"Div={round1(row['dividend_yield'])}, "
                f"Growth={round1(row['earnings_growth'])}"
            )
        except Exception as exc:
            print(f"  unexpected error for {symbol}: {exc}")

        time.sleep(0.7)

    selected = {}
    field_sources = {}
    quality = {}

    # Direkt/Proxy-basierte Felder.
    for field in [
        "pe",
        "forward_pe",
        "pb",
        "dividend_yield",
        "earnings_growth",
    ]:
        candidates = make_candidates(proxy_rows, field)

        value, method, used = consensus_value(
            field,
            candidates,
            previous.get(field),
        )

        selected[field] = value
        quality[field] = method

        if method.startswith("carried_forward"):
            field_sources[field] = "Vorwert aus fundamentals.json"
        elif field in ("pe", "pb", "earnings_growth"):
            # Diese drei stammen aus Yahoo FundData/equity_holdings.
            field_sources[field] = (
                f"Yahoo Finance FundData ({candidate_source(used)})"
            )
        else:
            field_sources[field] = (
                f"Yahoo Finance ETF quote data ({candidate_source(used)})"
            )

    # ROE wird NICHT mehr aus nur 8 Top-Holdings hochgerechnet.
    # Mathematischer Zusammenhang:
    # P/B ÷ P/E = (Price/Book) ÷ (Price/Earnings) = Earnings/Book = ROE.
    # Bei aggregierten ETF-Ratios ist das ein Näherungswert, aber deutlich
    # stabiler und repräsentativer als ein Top-8-Holdings-Proxy.
    pe = finite_number(selected.get("pe"))
    pb = finite_number(selected.get("pb"))

    implied_roe = None
    if pe and pb and pe > 0:
        implied_roe = 100.0 * pb / pe

    if is_valid("roe", implied_roe):
        selected["roe"] = implied_roe
        quality["roe"] = "implied_from_pb_pe"
        field_sources["roe"] = "Berechnet aus Yahoo FundData: 100 × KBV / KGV"
    else:
        old_roe = finite_number(previous.get("roe"))
        selected["roe"] = old_roe if is_valid("roe", old_roe) else None
        quality["roe"] = "carried_forward"
        field_sources["roe"] = "Vorwert aus fundamentals.json"

    carried_forward = [
        field for field, method in quality.items()
        if method.startswith("carried_forward")
    ]

    # Kompakte Quelle für das bestehende Frontend-Tooltip.
    new_fields = [
        field for field, method in quality.items()
        if not method.startswith("carried_forward")
    ]

    if new_fields:
        source = (
            "Yahoo Finance ETF/FundData; "
            "mehrere Regional-Proxies mit Plausibilitätsprüfung"
        )
    else:
        source = "Vorwerte aus fundamentals.json"

    # Rohwerte helfen bei späterer Fehlersuche, werden vom Frontend ignoriert.
    raw_candidates = {}
    for field in [
        "pe",
        "forward_pe",
        "pb",
        "dividend_yield",
        "earnings_growth",
    ]:
        raw_candidates[field] = [
            {
                "symbol": item["symbol"],
                "value": round1(item.get(field)),
            }
            for item in proxy_rows
            if item.get(field) is not None
        ]

    return {
        "market": market,
        "pe": round1(selected.get("pe")),
        "forward_pe": round1(selected.get("forward_pe")),
        "pb": round1(selected.get("pb")),
        "dividend_yield": round1(selected.get("dividend_yield")),
        "roe": round1(selected.get("roe")),
        "earnings_growth": round1(selected.get("earnings_growth")),
        "source": source,
        "as_of": datetime.now(BERLIN).strftime("%d.%m.%Y"),
        "field_sources": field_sources,
        "quality": quality,
        "carried_forward": carried_forward,
        "raw_candidates": raw_candidates,
    }


def main():
    existing = read_existing()
    previous = existing_by_market(existing)

    regions = []

    for region in REGIONS:
        market = region["market"]
        regions.append(
            fetch_region(
                region,
                previous.get(market, {}),
            )
        )

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)

    payload = {
        "updated": datetime.now(BERLIN).strftime("%d.%m.%Y"),
        "regions": regions,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    print(f"\nWrote {OUTPUT_FILE}")

    for row in regions:
        print(
            f"{row['market']}: "
            f"KGV {row['pe']}, "
            f"Fwd {row['forward_pe']}, "
            f"KBV {row['pb']}, "
            f"Div {row['dividend_yield']}%, "
            f"ROE {row['roe']}%, "
            f"Growth {row['earnings_growth']}% "
            f"| carried: {row['carried_forward']}"
        )


if __name__ == "__main__":
    main()
