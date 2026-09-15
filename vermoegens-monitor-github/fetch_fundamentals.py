import json
import math
import os
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

OUTPUT_FILE = "data/fundamentals.json"
BERLIN = ZoneInfo("Europe/Berlin")

# First ticker = ETF already used in your dashboard where possible.
# The following tickers are fallbacks if Yahoo exposes more fund data there.
REGIONS = [
    {
        "market": "USA",
        "proxies": ["SXR8.DE", "SPY", "IVV"],
    },
    {
        "market": "Europa",
        "proxies": ["EXSA.DE", "VGK", "IEUR"],
    },
    {
        "market": "Emerging Markets",
        "proxies": ["IQQE.DE", "IEMG", "EEM"],
    },
    {
        "market": "China",
        "proxies": ["IQQC.DE", "MCHI", "FXI"],
    },
    {
        "market": "Indien",
        "proxies": ["QDV5.DE", "INDA", "FLIN"],
    },
    {
        "market": "Japan",
        "proxies": ["SJPA.L", "EWJ", "BBJP"],
    },
]

FIELDS = [
    "pe",
    "forward_pe",
    "pb",
    "dividend_yield",
    "roe",
    "earnings_growth",
]

# Plausibility limits. Values outside these ranges are ignored instead of
# overwriting a previously valid value with obviously broken Yahoo data.
LIMITS = {
    "pe": (2.0, 100.0),
    "forward_pe": (2.0, 100.0),
    "pb": (0.1, 25.0),
    "dividend_yield": (0.0, 20.0),
    "roe": (-50.0, 100.0),
    "earnings_growth": (-80.0, 150.0),
}


def num(value):
    """Return a finite float or None."""
    try:
        if value is None:
            return None
        value = float(value)
        if not math.isfinite(value):
            return None
        return value
    except (TypeError, ValueError):
        return None


def round1(value):
    value = num(value)
    return None if value is None else round(value, 1)


def as_percent(value):
    """
    Yahoo often returns ratios such as 0.198 for 19.8%.
    If the value is already expressed as e.g. 19.8, keep it unchanged.
    """
    value = num(value)
    if value is None:
        return None
    if abs(value) <= 1.5:
        value *= 100.0
    return value


def valid(field, value):
    value = num(value)
    if value is None:
        return False
    low, high = LIMITS[field]
    return low <= value <= high


def first_number(mapping, keys):
    if not isinstance(mapping, dict):
        return None
    for key in keys:
        value = num(mapping.get(key))
        if value is not None:
            return value
    return None


def normalize_label(value):
    return re.sub(r"[^a-z0-9]+", "", str(value).lower())


def extract_from_equity_holdings(equity_holdings, aliases):
    """
    yfinance exposes fund valuation data in funds_data.equity_holdings.
    Depending on the yfinance/Yahoo version this can be a DataFrame or dict.
    This function intentionally accepts several layouts.
    """
    aliases = [normalize_label(x) for x in aliases]

    if equity_holdings is None:
        return None

    if isinstance(equity_holdings, dict):
        for key, value in equity_holdings.items():
            key_norm = normalize_label(key)
            if any(alias in key_norm or key_norm in alias for alias in aliases):
                if isinstance(value, dict):
                    for preferred in ["fund", "average", "value", "raw"]:
                        if preferred in value:
                            candidate = num(value[preferred])
                            if candidate is not None:
                                return candidate
                    for candidate in value.values():
                        candidate = num(candidate)
                        if candidate is not None:
                            return candidate
                candidate = num(value)
                if candidate is not None:
                    return candidate
        return None

    if not isinstance(equity_holdings, pd.DataFrame) or equity_holdings.empty:
        return None

    # Most common layout: metrics as rows, e.g. Price/Earnings, Price/Book,
    # and columns such as Average / Category Average.
    for idx, row in equity_holdings.iterrows():
        idx_norm = normalize_label(idx)
        if any(alias in idx_norm or idx_norm in alias for alias in aliases):
            preferred_cols = []
            other_cols = []
            for col in equity_holdings.columns:
                col_norm = normalize_label(col)
                if "category" in col_norm:
                    other_cols.append(col)
                elif any(x in col_norm for x in ["average", "fund", "portfolio", "value"]):
                    preferred_cols.append(col)
                else:
                    other_cols.append(col)

            for col in preferred_cols + other_cols:
                candidate = num(row.get(col))
                if candidate is not None:
                    return candidate

    # Alternative layout: metrics as columns.
    for col in equity_holdings.columns:
        col_norm = normalize_label(col)
        if any(alias in col_norm or col_norm in alias for alias in aliases):
            for candidate in equity_holdings[col].tolist():
                candidate = num(candidate)
                if candidate is not None:
                    return candidate

    return None


def safe_get_info(ticker, attempts=3):
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


def safe_get_funds_data(ticker, attempts=2):
    for attempt in range(attempts):
        try:
            data = ticker.get_funds_data()
            return data
        except Exception as exc:
            if attempt == attempts - 1:
                print(f"  funds_data failed for {ticker.ticker}: {exc}")
            else:
                time.sleep(1.5 * (attempt + 1))
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


def get_top_holdings(funds_data, max_holdings=8):
    if funds_data is None:
        return []

    try:
        df = funds_data.top_holdings
    except Exception:
        return []

    if not isinstance(df, pd.DataFrame) or df.empty:
        return []

    weight_col = None
    symbol_col = None

    for col in df.columns:
        col_norm = normalize_label(col)
        if weight_col is None and (
            "holdingpercent" in col_norm
            or "weight" in col_norm
            or "percentassets" in col_norm
        ):
            weight_col = col
        if symbol_col is None and "symbol" in col_norm:
            symbol_col = col

    rows = []
    for idx, row in df.head(max_holdings).iterrows():
        symbol = row.get(symbol_col) if symbol_col is not None else idx
        symbol = str(symbol).strip() if symbol is not None else ""
        if not symbol or symbol.lower() == "nan":
            continue

        weight = num(row.get(weight_col)) if weight_col is not None else None
        if weight is None:
            continue
        if weight > 1.0:
            weight /= 100.0
        if weight <= 0:
            continue

        rows.append((symbol, weight))

    return rows


def weighted_top_holdings_metrics(holdings):
    """
    ROE and EPS growth are not reliably provided for whole ETFs by Yahoo.
    As a secondary source, calculate a weighted proxy from up to 8 largest
    holdings. The calculation is only accepted when at least 4 holdings return
    valid data. Missing holdings are ignored and successful weights are
    re-normalized.
    """
    if not holdings:
        return {}, []

    buckets = {
        "roe": [],
        "earnings_growth": [],
    }
    used_symbols = set()

    for symbol, weight in holdings:
        try:
            info = safe_get_info(yf.Ticker(symbol), attempts=2)
        except Exception:
            info = {}

        roe = as_percent(first_number(info, ["returnOnEquity"]))
        growth = as_percent(
            first_number(info, ["earningsGrowth", "earningsQuarterlyGrowth"])
        )

        if valid("roe", roe):
            buckets["roe"].append((roe, weight))
            used_symbols.add(symbol)

        if valid("earnings_growth", growth):
            buckets["earnings_growth"].append((growth, weight))
            used_symbols.add(symbol)

        # Be polite to Yahoo and reduce rate-limit risk.
        time.sleep(0.35)

    result = {}
    for field, values in buckets.items():
        if len(values) < 4:
            continue
        weight_sum = sum(weight for _, weight in values)
        if weight_sum <= 0:
            continue
        result[field] = sum(value * weight for value, weight in values) / weight_sum

    return result, sorted(used_symbols)


def fetch_region(region, previous):
    market = region["market"]
    values = {field: None for field in FIELDS}
    field_sources = {}
    best_funds_data = None
    best_funds_symbol = None

    print(f"\n{market}")

    for symbol in region["proxies"]:
        print(f"  trying {symbol}")
        ticker = yf.Ticker(symbol)
        info = safe_get_info(ticker)
        funds_data = safe_get_funds_data(ticker)

        if best_funds_data is None and funds_data is not None:
            best_funds_data = funds_data
            best_funds_symbol = symbol

        equity = None
        if funds_data is not None:
            try:
                equity = funds_data.equity_holdings
            except Exception:
                equity = None

        candidates = {
            "pe": extract_from_equity_holdings(
                equity,
                ["priceToEarnings", "price/earnings", "price earnings", "peRatio"],
            ),
            "pb": extract_from_equity_holdings(
                equity,
                ["priceToBook", "price/book", "price book", "pbRatio"],
            ),
            "forward_pe": first_number(info, ["forwardPE", "forwardPe"]),
            "dividend_yield": as_percent(
                first_number(
                    info,
                    ["dividendYield", "trailingAnnualDividendYield", "yield"],
                )
            ),
            "roe": as_percent(first_number(info, ["returnOnEquity"])),
            "earnings_growth": as_percent(
                first_number(info, ["earningsGrowth", "earningsQuarterlyGrowth"])
            ),
        }

        # If fund profile did not provide P/E or P/B, try quote info.
        if candidates["pe"] is None:
            candidates["pe"] = first_number(info, ["trailingPE", "peRatio"])
        if candidates["pb"] is None:
            candidates["pb"] = first_number(info, ["priceToBook"])

        for field, candidate in candidates.items():
            if values[field] is None and valid(field, candidate):
                values[field] = candidate
                field_sources[field] = f"Yahoo Finance ({symbol})"

        # The important market-level valuation fields are present; no need to
        # query every fallback ETF unless something is still missing.
        if all(values[f] is not None for f in ["pe", "forward_pe", "pb", "dividend_yield"]):
            break

        time.sleep(0.6)

    # Yahoo usually does not expose ETF-level ROE/EPS growth. Use the largest
    # holdings only as a proxy when these fields are still missing.
    if values["roe"] is None or values["earnings_growth"] is None:
        holdings = get_top_holdings(best_funds_data, max_holdings=8)
        proxy_values, used_symbols = weighted_top_holdings_metrics(holdings)

        for field in ["roe", "earnings_growth"]:
            candidate = proxy_values.get(field)
            if values[field] is None and valid(field, candidate):
                values[field] = candidate
                field_sources[field] = (
                    f"Yahoo Finance Top-Holdings-Proxy ({best_funds_symbol}; "
                    f"{len(used_symbols)} Titel)"
                )

    # Never destroy a previously valid table because Yahoo omitted a field on
    # one run. Missing fields are carried forward from the existing JSON.
    carried_forward = []
    for field in FIELDS:
        if values[field] is None:
            old_value = num(previous.get(field))
            if old_value is not None and valid(field, old_value):
                values[field] = old_value
                field_sources[field] = "Vorwert aus fundamentals.json"
                carried_forward.append(field)

    rounded = {field: round1(values[field]) for field in FIELDS}

    source_parts = []
    direct_symbols = []
    proxy_used = False
    old_used = False
    for source in field_sources.values():
        if source.startswith("Yahoo Finance ("):
            symbol = source.split("(", 1)[1].rstrip(")")
            if symbol not in direct_symbols:
                direct_symbols.append(symbol)
        elif "Top-Holdings-Proxy" in source:
            proxy_used = True
        elif source.startswith("Vorwert"):
            old_used = True

    if direct_symbols:
        source_parts.append("Yahoo Finance: " + ", ".join(direct_symbols))
    if proxy_used:
        source_parts.append("ROE/EPS: Top-Holdings-Proxy")
    if old_used:
        source_parts.append("fehlende Felder: Vorwert")

    return {
        "market": market,
        **rounded,
        "source": "; ".join(source_parts) if source_parts else "Vorwert aus fundamentals.json",
        "as_of": datetime.now(BERLIN).strftime("%d.%m.%Y"),
        "field_sources": field_sources,
        "carried_forward": carried_forward,
    }


def main():
    existing = read_existing()
    previous = existing_by_market(existing)

    regions = []
    for region in REGIONS:
        market = region["market"]
        regions.append(fetch_region(region, previous.get(market, {})))

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
            f"KGV {row['pe']}, Fwd {row['forward_pe']}, KBV {row['pb']}, "
            f"Div {row['dividend_yield']}%, ROE {row['roe']}%, "
            f"EPS {row['earnings_growth']}%"
        )


if __name__ == "__main__":
    main()
