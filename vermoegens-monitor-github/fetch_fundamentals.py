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

# Primärquelle für KGV / KBV / Dividendenrendite bleibt iShares.
# Forward-KGV kommt aus Yahoo Finance.
# ROE wird aus KBV/KGV abgeleitet.
#
# EPS-Wachstum:
# - USA / Europa / EM / China: State Street, "Est. 3-5 Year EPS Growth"
# - Indien / Japan: Morningstar, "Long-Term Projected Earnings Growth",
#   jeweils Benchmark-Wert (nicht nur der konkrete ETF).
REGIONS = [
    {
        "market": "USA",
        "ticker": "IVV",
        "ishares_url": "https://www.ishares.com/us/products/239726/ishares-core-sp-500-etf",
        "eps_provider": "state_street",
        "eps_url": "https://www.ssga.com/us/en/individual/etfs/state-street-spdr-sp-500-etf-trust-spy",
        "eps_source_name": "State Street SPY / S&P 500 Index",
        "forward_fallback_provider": "state_street",
        "forward_fallback_url": "https://www.ssga.com/us/en/individual/etfs/state-street-spdr-sp-500-etf-trust-spy",
        "forward_fallback_name": "State Street SPY / S&P 500 Index",
    },
    {
        "market": "Europa",
        "ticker": "IEUR",
        "ishares_url": "https://www.ishares.com/us/products/264617/ishares-core-msci-europe-etf",
        "eps_provider": "state_street",
        "eps_url": "https://www.ssga.com/us/en/individual/etfs/state-street-spdr-portfolio-europe-etf-speu",
        "eps_source_name": "State Street SPEU / Europe Index",
        "forward_fallback_provider": "state_street",
        "forward_fallback_url": "https://www.ssga.com/us/en/individual/etfs/state-street-spdr-portfolio-europe-etf-speu",
        "forward_fallback_name": "State Street SPEU / Europe Index",
    },
    {
        "market": "Emerging Markets",
        "ticker": "IEMG",
        "ishares_url": "https://www.ishares.com/us/products/244050/ishares-core-msci-emerging-markets-etf",
        "eps_provider": "state_street",
        "eps_url": "https://www.ssga.com/us/en/individual/etfs/state-street-spdr-portfolio-emerging-markets-etf-spem",
        "eps_source_name": "State Street SPEM / Emerging Markets Index",
        "forward_fallback_provider": "state_street",
        "forward_fallback_url": "https://www.ssga.com/us/en/individual/etfs/state-street-spdr-portfolio-emerging-markets-etf-spem",
        "forward_fallback_name": "State Street SPEM / Emerging Markets Index",
    },
    {
        "market": "China",
        "ticker": "MCHI",
        "ishares_url": "https://www.ishares.com/us/products/239619/ishares-msci-china-etf",
        "eps_provider": "state_street",
        "eps_url": "https://www.ssga.com/us/en/intermediary/etfs/state-street-spdr-sp-china-etf-gxc",
        "eps_source_name": "State Street GXC / S&P China BMI Index",
        "forward_fallback_provider": "state_street",
        "forward_fallback_url": "https://www.ssga.com/us/en/intermediary/etfs/state-street-spdr-sp-china-etf-gxc",
        "forward_fallback_name": "State Street GXC / S&P China BMI Index",
    },
    {
        "market": "Indien",
        "ticker": "INDA",
        "ishares_url": "https://www.ishares.com/us/products/239659/ishares-msci-india-etf",
        "eps_provider": "morningstar",
        "eps_url": (
            "https://lt.morningstar.com/1c6qh1t6k9/etfreport/default.aspx"
            "?1=1&ClientFund=0&CurrencyId=USD&Id=0P0001HV9D"
            "&SecurityToken=0P0001HV9D%5D22%5D0%5DETEXG%24XLON&tab=3"
        ),
        "eps_source_name": "Morningstar / India Benchmark",
        "forward_fallback_provider": "msci_india",
        "forward_fallback_url": "https://www.msci.com/indexes/index/935600/msci-india-index",
        "forward_fallback_name": "MSCI India Index",
        "regional_dividend_provider": "msci_india",
        "regional_dividend_url": "https://www.msci.com/indexes/index/935600/msci-india-index",
        "regional_dividend_name": "MSCI India Index",
    },
    {
        "market": "Japan",
        "ticker": "EWJ",
        "ishares_url": "https://www.ishares.com/us/products/239665/ishares-msci-japan-etf",
        "eps_provider": "morningstar",
        "eps_url": (
            "https://lt.morningstar.com/1c6qh1t6k9/etfreport/default.aspx"
            "?1=1&ClientFund=0&CurrencyId=USD&Id=0P00012NWR"
            "&SecurityToken=0P00012NWR%5D22%5D0%5DETEXG%24XLON&tab=3"
        ),
        "eps_source_name": "Morningstar / Japan Benchmark",
        "forward_fallback_provider": "state_street",
        "forward_fallback_url": "https://www.ssga.com/de/en_gb/institutional/etfs/state-street-spdr-msci-japan-ucits-etf-zpdj-gy",
        "forward_fallback_name": "State Street ZPDJ / MSCI Japan",
    },
]

LIMITS = {
    "pe": (5.0, 60.0),
    "forward_pe": (5.0, 60.0),
    "pb": (0.6, 12.0),
    "dividend_yield": (0.0, 12.0),
    "roe": (2.0, 60.0),
    "earnings_growth": (-20.0, 80.0),
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


def fetch_response(url, attempts=3):
    last_error = None

    for attempt in range(attempts):
        try:
            response = requests.get(
                url,
                headers=HEADERS,
                timeout=35,
            )
            response.raise_for_status()

            if len(response.text) < 500:
                raise RuntimeError("Provider returned too little HTML")

            return response

        except Exception as exc:
            last_error = exc

            if attempt < attempts - 1:
                time.sleep(2.0 * (attempt + 1))

    raise RuntimeError(f"Could not load {url}: {last_error}")


def fetch_text(url, attempts=3):
    response = fetch_response(url, attempts=attempts)
    soup = BeautifulSoup(response.text, "html.parser")
    text = soup.get_text(" ", strip=True)
    return re.sub(r"\s+", " ", text)


def parse_ishares_metric(text, label, percent=False):
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


def parse_state_street_fy1(text):
    """Price/Earnings Ratio FY1 = erwartetes KGV für das nächste Geschäftsjahr."""
    section = text
    as_of = None

    index_match = re.search(
        r"Index Characteristics\s+as of\s+"
        r"([A-Za-z]{3}\s+\d{1,2}\s+\d{4})"
        r"(.*?)(?:Index Statistics|Yields|Fund Market Price|$)",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if index_match:
        as_of = index_match.group(1)
        section = index_match.group(2)
    else:
        fund_match = re.search(
            r"Fund Characteristics\s+as of\s+"
            r"(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})"
            r"(.*?)(?:Index Characteristics|Fund Market Price|$)",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if fund_match:
            as_of = fund_match.group(1)
            section = fund_match.group(2)

    match = re.search(
        r"Price/Earnings Ratio FY1\s*\|?\s*([-+]?\d+(?:[.,]\d+)?)",
        section,
        flags=re.IGNORECASE,
    )
    if not match:
        return None, as_of

    return finite_number(match.group(1).replace(",", ".")), as_of


def parse_msci_india_metrics(text):
    """
    MSCI India Index Profile.

    Die MSCI-Seite veröffentlicht direkt:
      Div Yld (%)
      P/E
      P/E Fwd
      P/BV

    Der Parser ist bewusst tolerant gegenüber Leerzeichen,
    Zeilenumbrüchen, Pipes und HTML-bedingten Trennzeichen.
    """

    # Leerzeichen / Zeilenumbrüche vereinheitlichen
    clean = re.sub(r"\s+", " ", text)

    def find_value(patterns):
        for pattern in patterns:
            match = re.search(
                pattern,
                clean,
                flags=re.IGNORECASE
            )

            if match:
                value = finite_number(
                    match.group(1).replace(",", ".")
                )

                if value is not None:
                    return value

        return None


    dividend_yield = find_value([
        r"Div\s*Yld\s*\(%\)[^\d+-]{0,100}([-+]?\d+(?:[.,]\d+)?)",
        r"Dividend\s*Yield[^\d+-]{0,100}([-+]?\d+(?:[.,]\d+)?)",
    ])


    forward_pe = find_value([
        r"P/E\s*Fwd[^\d+-]{0,100}([-+]?\d+(?:[.,]\d+)?)",
        r"Forward\s*P/E[^\d+-]{0,100}([-+]?\d+(?:[.,]\d+)?)",
    ])


    # Optional zusätzlich für Debugging / spätere Nutzung
    pe = find_value([
        r"P/E(?!\s*Fwd)[^\d+-]{0,100}([-+]?\d+(?:[.,]\d+)?)"
    ])


    pb = find_value([
        r"P/BV[^\d+-]{0,100}([-+]?\d+(?:[.,]\d+)?)",
        r"P/B[^\d+-]{0,100}([-+]?\d+(?:[.,]\d+)?)",
    ])


    date_match = re.search(
        r"Data\s+as\s+of[^\w]{0,20}"
        r"([A-Za-z]{3}\.?\s+\d{1,2},\s+\d{4})",
        clean,
        flags=re.IGNORECASE
    )

    as_of = (
        date_match.group(1)
        if date_match
        else None
    )


    print(
        "  MSCI India parsed:",
        f"Div={dividend_yield},",
        f"FwdPE={forward_pe},",
        f"PE={pe},",
        f"PB={pb},",
        f"Date={as_of}"
    )


    return {
        "dividend_yield": dividend_yield,
        "forward_pe": forward_pe,
        "pe": pe,
        "pb": pb,
        "as_of": as_of,
    }


def fetch_forward_fallback(region):
    provider = region.get("forward_fallback_provider")
    url = region.get("forward_fallback_url")
    if not provider or not url:
        return None, None

    try:
        text = fetch_text(url)
        if provider == "state_street":
            return parse_state_street_fy1(text)
        if provider == "msci_india":
            metrics = parse_msci_india_metrics(text)
            return metrics["forward_pe"], metrics["as_of"]
    except Exception as exc:
        print(f"  Forward P/E fallback failed for {region['market']}: {exc}")

    return None, None


def fetch_india_regional_dividend(region):
    if region.get("regional_dividend_provider") != "msci_india":
        return None, None
    try:
        text = fetch_text(region["regional_dividend_url"])
        metrics = parse_msci_india_metrics(text)
        return metrics["dividend_yield"], metrics["as_of"]
    except Exception as exc:
        print(f"  India regional dividend fallback failed: {exc}")
    return None, None


# -------------------------------------------------------------------
# EPS GROWTH
# -------------------------------------------------------------------

def parse_state_street_eps_growth(text):
    """
    Verwendet den INDEX-Wert, nicht nur den ETF-Fund-Wert.
    """
    section_match = re.search(
        r"Index Characteristics\s+as of\s+"
        r"([A-Za-z]{3}\s+\d{1,2}\s+\d{4})"
        r"(.*?)(?:Index Statistics|Yields|Fund Market Price|$)",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    if not section_match:
        return None, None

    as_of = section_match.group(1)
    section = section_match.group(2)

    value_match = re.search(
        r"Est\.?\s*3\s*-\s*5\s*Year\s*EPS\s*Growth"
        r".{0,700}?"
        r"([-+]?\d+(?:[.,]\d+)?)\s*%",
        section,
        flags=re.IGNORECASE | re.DOTALL,
    )

    if not value_match:
        return None, None

    value = finite_number(value_match.group(1).replace(",", "."))
    return value, as_of


def parse_morningstar_eps_growth(response):
    """
    Morningstar-Tabelle:
      Long-Term Projected Earnings Growth | Fund | Category | Benchmark
    Verwendet den Benchmark-Wert = letzte Spalte.
    """
    soup = BeautifulSoup(response.text, "html.parser")
    page_text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))

    date_match = re.search(
        r"Valuations and Growth Rates\s+(\d{2}/\d{2}/\d{4})",
        page_text,
        flags=re.IGNORECASE,
    )
    as_of = date_match.group(1) if date_match else None

    for row in soup.find_all("tr"):
        cells = [
            re.sub(r"\s+", " ", cell.get_text(" ", strip=True))
            for cell in row.find_all(["th", "td"])
        ]

        if not cells:
            continue

        if "long-term projected earnings growth" in cells[0].lower():
            numeric = []

            for cell in cells[1:]:
                match = re.search(r"[-+]?\d+(?:[.,]\d+)?", cell)

                if match:
                    value = finite_number(match.group(0).replace(",", "."))
                    if value is not None:
                        numeric.append(value)

            if numeric:
                return numeric[-1], as_of

    match = re.search(
        r"Long-Term Projected Earnings Growth"
        r"\s*\|?\s*([-+]?\d+(?:[.,]\d+)?)"
        r"\s*\|?\s*([-+]?\d+(?:[.,]\d+)?)"
        r"\s*\|?\s*([-+]?\d+(?:[.,]\d+)?)",
        page_text,
        flags=re.IGNORECASE,
    )

    if match:
        benchmark = finite_number(match.group(3).replace(",", "."))
        return benchmark, as_of

    return None, as_of


def fetch_eps_growth(region):
    provider = region["eps_provider"]
    url = region["eps_url"]

    try:
        response = fetch_response(url)

        if provider == "state_street":
            soup = BeautifulSoup(response.text, "html.parser")
            text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
            value, as_of = parse_state_street_eps_growth(text)

        elif provider == "morningstar":
            value, as_of = parse_morningstar_eps_growth(response)

        else:
            raise ValueError(f"Unknown EPS provider: {provider}")

        if is_valid("earnings_growth", value):
            return value, as_of

        print(
            f"  EPS growth from {region['eps_source_name']} invalid or missing: {value}"
        )

    except Exception as exc:
        print(f"  EPS growth failed for {region['market']}: {exc}")

    return None, None


def fetch_region(region, previous):
    market = region["market"]
    ticker = region["ticker"]
    ishares_url = region["ishares_url"]

    print(f"\n=== {market} / {ticker} ===")

    field_sources = {}
    field_dates = {}
    quality = {}
    carried_forward = []

    try:
        page_text = fetch_text(ishares_url)

        pe, pe_date = parse_ishares_metric(page_text, "P/E Ratio")
        pb, pb_date = parse_ishares_metric(page_text, "P/B Ratio")
        dividend_yield, div_date = parse_ishares_metric(
            page_text,
            "12m Trailing Yield",
            percent=True,
        )

    except Exception as exc:
        print(f"  iShares fetch/parse failed: {exc}")
        pe = pb = dividend_yield = None
        pe_date = pb_date = div_date = None

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

    if is_valid("pb", pb):
        selected_pb = pb
        field_sources["pb"] = f"iShares {ticker}"
        field_dates["pb"] = pb_date
        quality["pb"] = "primary_ishares"
    else:
        selected_pb = safe_old(previous, "pb")
        field_sources["pb"] = "Vorwert aus fundamentals.json"
        field_dates["pb"] = previous.get("field_dates", {}).get("pb")
        quality["pb"] = "carried_forward"
        carried_forward.append("pb")

    # Indien: INDA weist offiziell 0,00 % 12m Trailing Yield aus. Für die
    # regionale Bewertung verwenden wir bei 0/nahe 0 stattdessen den
    # offiziellen Div Yld des MSCI India Index.
    if market == "Indien" and dividend_yield is not None and dividend_yield <= 0.05:
        regional_dividend, regional_dividend_date = fetch_india_regional_dividend(region)
        if is_valid("dividend_yield", regional_dividend) and regional_dividend > 0.05:
            selected_dividend = regional_dividend
            field_sources["dividend_yield"] = "MSCI India Index – Div Yld (%)"
            field_dates["dividend_yield"] = regional_dividend_date
            quality["dividend_yield"] = "regional_index_msci"
        else:
            selected_dividend = dividend_yield
            field_sources["dividend_yield"] = f"iShares {ticker} – 12m Trailing Yield"
            field_dates["dividend_yield"] = div_date
            quality["dividend_yield"] = "primary_ishares"
    elif is_valid("dividend_yield", dividend_yield):
        selected_dividend = dividend_yield
        field_sources["dividend_yield"] = f"iShares {ticker} – 12m Trailing Yield"
        field_dates["dividend_yield"] = div_date
        quality["dividend_yield"] = "primary_ishares"
    else:
        selected_dividend = safe_old(previous, "dividend_yield")
        field_sources["dividend_yield"] = "Vorwert aus fundamentals.json"
        field_dates["dividend_yield"] = previous.get("field_dates", {}).get("dividend_yield")
        quality["dividend_yield"] = "carried_forward"
        carried_forward.append("dividend_yield")

    # Forward-KGV: Yahoo zuerst. Falls Yahoo bei ETFs keinen Wert liefert,
    # wird auf eine institutionelle Quelle ausgewichen.
    forward_pe = yahoo_forward_pe(ticker)

    if is_valid("forward_pe", forward_pe):
        selected_forward_pe = forward_pe
        field_sources["forward_pe"] = f"Yahoo Finance {ticker}"
        field_dates["forward_pe"] = datetime.now(BERLIN).strftime("%d.%m.%Y")
        quality["forward_pe"] = "yahoo_quote"
    else:
        fallback_forward, fallback_date = fetch_forward_fallback(region)
        if is_valid("forward_pe", fallback_forward):
            selected_forward_pe = fallback_forward
            field_sources["forward_pe"] = region["forward_fallback_name"]
            field_dates["forward_pe"] = fallback_date
            quality["forward_pe"] = (
                "state_street_fy1"
                if region["forward_fallback_provider"] == "state_street"
                else "msci_forward_pe"
            )
        else:
            selected_forward_pe = safe_old(previous, "forward_pe")
            field_sources["forward_pe"] = "Vorwert aus fundamentals.json"
            field_dates["forward_pe"] = previous.get("field_dates", {}).get("forward_pe")
            quality["forward_pe"] = "carried_forward"
            carried_forward.append("forward_pe")

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

    eps_growth, eps_date = fetch_eps_growth(region)

    if is_valid("earnings_growth", eps_growth):
        selected_growth = eps_growth
        field_sources["earnings_growth"] = region["eps_source_name"]
        field_dates["earnings_growth"] = eps_date
        quality["earnings_growth"] = (
            "projected_3_5y_state_street"
            if region["eps_provider"] == "state_street"
            else "long_term_projected_morningstar_benchmark"
        )
    else:
        selected_growth = safe_old(previous, "earnings_growth")

        if selected_growth is not None:
            field_sources["earnings_growth"] = "Vorwert aus fundamentals.json"
            field_dates["earnings_growth"] = previous.get("field_dates", {}).get("earnings_growth")
            quality["earnings_growth"] = "carried_forward"
            carried_forward.append("earnings_growth")
        else:
            field_sources["earnings_growth"] = "Keine belastbare Quelle verfügbar"
            field_dates["earnings_growth"] = None
            quality["earnings_growth"] = "unavailable"

    raw_primary = {
        "ticker": ticker,
        "pe": round1(pe),
        "pb": round1(pb),
        "dividend_yield": round1(dividend_yield),
        "pe_as_of": pe_date,
        "pb_as_of": pb_date,
        "dividend_yield_as_of": div_date,
        "earnings_growth": round1(eps_growth),
        "earnings_growth_as_of": eps_date,
        "earnings_growth_source": region["eps_source_name"],
        "selected_forward_pe": round1(selected_forward_pe),
        "selected_dividend_yield": round1(selected_dividend),
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
            f"iShares {ticker}: KGV/KBV/Div.; "
            f"Forward-KGV: Yahoo Finance mit Provider-Fallback; "
            f"ROE: KBV/KGV; "
            f"{region['eps_source_name']}: EPS-Wachstum"
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
