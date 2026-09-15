import json
import math
import os
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
import yfinance as yf
from bs4 import BeautifulSoup


OUTPUT_FILE = "data/fundamentals.json"
BERLIN = ZoneInfo("Europe/Berlin")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/138.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# Für alle sechs Regionen nutzen wir primär einen iShares-ETF,
# dessen Produktseite P/E, P/B und 12m Trailing Yield veröffentlicht.
REGIONS = [
    {
        "market": "USA",
        "ticker": "IVV",
        "url": "https://www.ishares.com/us/products/239726/ishares-core-sp-500-etf",
    },
    {
        "market": "Europa",
        "ticker": "IEUR",
        "url": "https://www.ishares.com/us/products/264617/ishares-core-msci-europe-etf",
    },
    {
        "market": "Emerging Markets",
        "ticker": "IEMG",
        "url": "https://www.ishares.com/us/products/244050/ishares-core-msci-emerging-markets-etf",
    },
    {
        "market": "China",
        "ticker": "MCHI",
        "url": "https://www.ishares.com/us/products/239619/ishares-msci-china-etf",
    },
    {
        "market": "Indien",
        "ticker": "INDA",
        "url": "https://www.ishares.com/us/products/239659/ishares-msci-india-etf",
    },
    {
        "market": "Japan",
        "ticker": "EWJ",
        "url": "https://www.ishares.com/us/products/239665/ishares-msci-japan-etf",
    },
]

# Plausibilitätsgrenzen für breite Aktienmärkte.
LIMITS = {
    "pe": (5.0, 60.0),
    "forward_pe": (5.0, 60.0),
    "pb": (0.6, 12.0),
    "dividend_yield": (0.0, 12.0),
    "roe": (2.0, 60.0),
    "earnings_growth": (-30.0, 80.0),
}


def finite_number(value):
    try:
        if value is None:
            return None
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def round1(value):
    value = finite_number(value)
    return None if value is None else round(value, 1)


def is_valid(field, value):
    value = finite_number(value)
    if value is None:
        return False
    lo, hi = LIMITS[field]
    return lo <= value <= hi


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


def fetch_text(url, attempts=3):
    last_error = None

    for attempt in range(attempts):
        try:
            response = requests.get(
                url,
                headers=HEADERS,
                timeout=30,
            )
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")

            # Separator ist wichtig, damit Bezeichnung, Wert und Datum
            # nicht direkt aneinanderkleben.
            text = soup.get_text(" ", strip=True)
            text = re.sub(r"\s+", " ", text)

            if len(text) < 500:
                raise RuntimeError("iShares page returned too little text")

            return text

        except Exception as exc:
            last_error = exc
            if attempt < attempts - 1:
                time.sleep(2 * (attempt + 1))

    raise RuntimeError(f"Could not load {url}: {last_error}")


def parse_metric(text, label, percent=False):
    """
    Beispiel:
      P/E Ratio 29.95 as of Sep 11, 2026
      P/B Ratio 5.60 as of Sep 11, 2026
      12m Trailing Yield 1.06% as of Aug 31, 2026

    Liefert (value, as_of_text).
    """
    escaped = re.escape(label)

    pattern = (
        rf"{escaped}\s*"
        rf"([-+]?\d+(?:[.,]\d+)?)"
        rf"\s*{'%' if percent else ''}"
        rf"\s*as of\s*"
        rf"([A-Za-z]{{3}}\s+\d{{1,2}},\s+\d{{4}})"
    )

    match = re.search(pattern, text, flags=re.IGNORECASE)

    if not match:
        return None, None

    value = finite_number(match.group(1).replace(",", "."))
    as_of = match.group(2)

    return value, as_of


def yahoo_forward_pe(ticker_symbol):
    """
    Forward P/E ist auf der iShares-Seite nicht Teil der Portfolio Characteristics.
    Deshalb versuchen wir Yahoo nur für dieses einzelne Feld.

    Fehlt der Wert, bleibt der bisherige Wert erhalten.
    """
    try:
        ticker = yf.Ticker(ticker_symbol)
        info = ticker.get_info()

        for key in ("forwardPE", "forwardPe"):
            value = finite_number(info.get(key))
            if is_valid("forward_pe", value):
                return value

    except Exception as exc:
        print(f"  Yahoo forward P/E failed for {ticker_symbol}: {exc}")

    return None


def safe_old(previous, field):
    value = finite_number(previous.get(field))
    return value if is_valid(field, value) else None


def fetch_region(region, previous):
    market = region["market"]
    ticker = region["ticker"]
    url = region["url"]

    print(f"\n=== {market} / {ticker} ===")

    field_sources = {}
    field_dates = {}
    quality = {}
    carried_forward = []

    try:
        page_text = fetch_text(url)

        pe, pe_date = parse_metric(page_text, "P/E Ratio")
        pb, pb_date = parse_metric(page_text, "P/B Ratio")
        dividend_yield, div_date = parse_metric(
            page_text,
            "12m Trailing Yield",
            percent=True,
        )

    except Exception as exc:
        print(f"  iShares fetch/parse failed: {exc}")
        page_text = ""
        pe = pb = dividend_yield = None
        pe_date = pb_date = div_date = None

    # ------------------------------------------------------------
    # P/E
    # ------------------------------------------------------------
    if is_valid("pe", pe):
        selected_pe = pe
        field_sources["pe"] = f"iShares {ticker}"
        field_dates["pe"] = pe_date
        quality["pe"] = "primary_ishares"
    else:
        selected_pe = safe_old(previous, "pe")
        field_sources["pe"] = "Vorwert aus fundamentals.json"
        field_dates["pe"] = previous.get("field_dates", {}).get("pe")
        quality["pe"] = "carried_forward"
        carried_forward.append("pe")

    # ------------------------------------------------------------
    # P/B
    # ------------------------------------------------------------
    if is_valid("pb", pb):
        selected_pb = pb
        field_sources["pb"] = f"iShares {ticker}"
        field_dates["pb"] = pb_date
        quality["pb"] = "primary_ishares"
    else:
        # Alte 0.2/0.4/0.6-Werte aus der fehlerhaften Yahoo-FundData-
        # Interpretation erfüllen die neue Plausibilitätsgrenze nicht.
        selected_pb = safe_old(previous, "pb")
        field_sources["pb"] = "Vorwert aus fundamentals.json"
        field_dates["pb"] = previous.get("field_dates", {}).get("pb")
        quality["pb"] = "carried_forward"
        carried_forward.append("pb")

    # ------------------------------------------------------------
    # Dividend yield
    # ------------------------------------------------------------
    if is_valid("dividend_yield", dividend_yield):
        selected_dividend = dividend_yield
        field_sources["dividend_yield"] = f"iShares {ticker} – 12m Trailing Yield"
        field_dates["dividend_yield"] = div_date
        quality["dividend_yield"] = "primary_ishares"
    else:
        selected_dividend = safe_old(previous, "dividend_yield")
        field_sources["dividend_yield"] = "Vorwert aus fundamentals.json"
        field_dates["dividend_yield"] = previous.get("field_dates", {}).get(
            "dividend_yield"
        )
        quality["dividend_yield"] = "carried_forward"
        carried_forward.append("dividend_yield")

    # ------------------------------------------------------------
    # Forward P/E
    # ------------------------------------------------------------
    forward_pe = yahoo_forward_pe(ticker)

    if is_valid("forward_pe", forward_pe):
        selected_forward_pe = forward_pe
        field_sources["forward_pe"] = f"Yahoo Finance {ticker}"
        field_dates["forward_pe"] = datetime.now(BERLIN).strftime("%d.%m.%Y")
        quality["forward_pe"] = "yahoo_quote"
    else:
        selected_forward_pe = safe_old(previous, "forward_pe")
        field_sources["forward_pe"] = "Vorwert aus fundamentals.json"
        field_dates["forward_pe"] = previous.get("field_dates", {}).get(
            "forward_pe"
        )
        quality["forward_pe"] = "carried_forward"
        carried_forward.append("forward_pe")

    # ------------------------------------------------------------
    # ROE
    # ------------------------------------------------------------
    # Näherungsweise gilt bei aggregierten Bewertungskennzahlen:
    # P/B ÷ P/E = Earnings / Book Value = ROE.
    # Das ist für ein Portfolio ein impliziter Näherungswert, kein
    # bilanziell direkt von iShares ausgewiesener ROE.
    implied_roe = None

    if (
        is_valid("pe", selected_pe)
        and is_valid("pb", selected_pb)
        and selected_pe != 0
    ):
        implied_roe = 100.0 * selected_pb / selected_pe

    if is_valid("roe", implied_roe):
        selected_roe = implied_roe
        field_sources["roe"] = "Implizit: 100 × KBV / KGV"
        field_dates["roe"] = pe_date or pb_date
        quality["roe"] = "implied_from_pb_pe"
    else:
        selected_roe = safe_old(previous, "roe")
        field_sources["roe"] = "Vorwert aus fundamentals.json"
        field_dates["roe"] = previous.get("field_dates", {}).get("roe")
        quality["roe"] = "carried_forward"
        carried_forward.append("roe")

    # ------------------------------------------------------------
    # EPS growth
    # ------------------------------------------------------------
    # iShares veröffentlicht auf diesen Produktseiten keinen direkt
    # vergleichbaren aktuellen EPS-Growth-Wert. Deshalb wird hier NICHT
    # mehr aus nur wenigen Top Holdings hochgerechnet.
    #
    # Ein vorhandener Vorwert bleibt erhalten, bis eine belastbare
    # konsistente Quelle ergänzt wird.
    selected_growth = safe_old(previous, "earnings_growth")

    if selected_growth is not None:
        field_sources["earnings_growth"] = "Vorwert aus fundamentals.json"
        field_dates["earnings_growth"] = previous.get("field_dates", {}).get(
            "earnings_growth"
        )
        quality["earnings_growth"] = "carried_forward"
        carried_forward.append("earnings_growth")
    else:
        field_sources["earnings_growth"] = "Keine belastbare Quelle verfügbar"
        field_dates["earnings_growth"] = None
        quality["earnings_growth"] = "unavailable"

    # Neue Primärwerte kompakt dokumentieren.
    raw_primary = {
        "ticker": ticker,
        "pe": round1(pe),
        "pb": round1(pb),
        "dividend_yield": round1(dividend_yield),
        "pe_as_of": pe_date,
        "pb_as_of": pb_date,
        "dividend_yield_as_of": div_date,
    }

    return {
        "market": market,
        "pe": round1(selected_pe),
        "forward_pe": round1(selected_forward_pe),
        "pb": round1(selected_pb),
        "dividend_yield": round1(selected_dividend),
        "roe": round1(selected_roe),
        "earnings_growth": round1(selected_growth),
        "source": (
            f"iShares {ticker} für KGV/KBV/Dividendenrendite; "
            "Yahoo nur für Forward-KGV"
        ),
        "as_of": datetime.now(BERLIN).strftime("%d.%m.%Y"),
        "field_sources": field_sources,
        "field_dates": field_dates,
        "quality": quality,
        "carried_forward": carried_forward,
        "raw_primary": raw_primary,
    }


def main():
    existing = read_existing()
    previous = existing_by_market(existing)

    regions = []

    for region in REGIONS:
        market = region["market"]

        row = fetch_region(
            region,
            previous.get(market, {}),
        )

        regions.append(row)

        print(
            f"{market}: "
            f"KGV={row['pe']}, "
            f"Fwd.KGV={row['forward_pe']}, "
            f"KBV={row['pb']}, "
            f"Div={row['dividend_yield']}%, "
            f"ROE={row['roe']}%, "
            f"EPS Growth={row['earnings_growth']}%"
        )

        # Kleine Pause zwischen Provider-Abfragen.
        time.sleep(1.0)

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)

    payload = {
        "updated": datetime.now(BERLIN).strftime("%d.%m.%Y"),
        "regions": regions,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    print(f"\nWrote {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
