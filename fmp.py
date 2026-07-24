"""
fmp.py - Financial Modeling Prep adapter: the primary LIVE market-data source.

FMP is the live backbone for the market-facing half of the data layer: current price
and market value of equity, the daily price history that feeds the Merton equity
volatility, sector classification, and (once the key is on a paid tier) the analyst
estimates and consensus overlay. SEC EDGAR stays the primary source for the SCORED
fundamentals ("scores computed straight from the filings" is the credibility line);
FMP is only the preferred fundamentals FALLBACK, ahead of yfinance, and it covers the
ADRs and foreign filers EDGAR does not.

Every function returns None on any failure (no key, unknown ticker, thin data, network
error, or a free-tier "premium endpoint" rejection), so the existing fallback chain and
the models' honest-degradation paths are untouched. Nothing here ever raises to the
caller and nothing is scored differently: this module only swaps and extends the DATA
the models already read.

Pure and framework-free (mirrors edgar.py / prices.py): reads FMP_API_KEY from the
environment (with the same best-effort .streamlit/secrets.toml read prices.py uses), and
caches through livecache with the same TTL discipline as the rest of the layer.
"""
from __future__ import annotations

import datetime as dt
import os

import requests

import livecache

# ----------------------------------------------------------------------------
# Endpoints + cache TTLs (reuse the livecache pattern; match the existing layer)
# ----------------------------------------------------------------------------
BASE_V3 = "https://financialmodelingprep.com/api/v3"
BASE_V4 = "https://financialmodelingprep.com/api/v4"

QUOTE_TTL = 15 * 60            # price / MVE: same 15-minute freshness as prices.py
HISTORY_TTL = 24 * 3600       # daily closes for volatility: a day, like prices.py
FUNDAMENTALS_TTL = 24 * 3600  # annual statements change quarterly, like EDGAR facts
PROFILE_TTL = 7 * 24 * 3600   # sector/industry is slow-changing reference data
ANALYST_TTL = 24 * 3600       # consensus/estimates refresh about daily

HISTORY_PERIOD = "1y"         # window label stamped on provenance (matches prices.py)
HISTORY_MAX_CLOSES = 370      # ~1 trailing year of trading days, with a small buffer


def _key_from_secrets_file():
    """
    Best-effort read of FMP_API_KEY from .streamlit/secrets.toml, so a plain `python3`
    script (tests, the portfolio path) sees the same key the Streamlit app does without
    an exported env var. Mirrors prices._key_from_secrets_file. Returns the key or None.
    """
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        ".streamlit", "secrets.toml")
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("#") or "FMP_API_KEY" not in line:
                    continue
                _, _, val = line.partition("=")
                val = val.strip().strip('"').strip("'")
                if val and val != "PASTE_YOUR_FMP_KEY_HERE":
                    return val
    except OSError:
        pass
    return None


def _api_key():
    return os.environ.get("FMP_API_KEY") or _key_from_secrets_file() or None


def has_key() -> bool:
    """True when an FMP key is configured (env or secrets). Used by the live smoke test."""
    return _api_key() is not None


# ----------------------------------------------------------------------------
# Low-level fetch: one retry, generous timeout, key never logged
# ----------------------------------------------------------------------------
def _get(url: str, params: dict):
    """
    GET `url` with the api key appended, returning parsed JSON, or None on any failure.
    FMP signals a rejected/premium call with a JSON object carrying an "Error Message";
    that (and an empty list) is treated as "no data" so the caller degrades cleanly.
    """
    key = _api_key()
    if not key:
        return None
    q = dict(params or {})
    q["apikey"] = key
    import time
    for attempt in (1, 2):
        try:
            r = requests.get(url, params=q, timeout=30)
            r.raise_for_status()
            data = r.json()
            if isinstance(data, dict) and data.get("Error Message"):
                return None                      # e.g. free tier hitting a premium route
            return data
        except Exception:
            if attempt == 2:
                return None
            time.sleep(2)
    return None


def _num(v):
    """Coerce to float, treating None / '' / NaN as absent (None)."""
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None                 # drop NaN


# ----------------------------------------------------------------------------
# Price + market value of equity  (PRIMARY, ahead of Finnhub then yfinance)
# ----------------------------------------------------------------------------
def _pull_quote(ticker: str):
    data = _get(f"{BASE_V3}/quote/{ticker}", {})
    if not data or not isinstance(data, list):
        return None
    row = data[0] or {}
    price = _num(row.get("price"))
    market_cap = _num(row.get("marketCap"))
    shares = _num(row.get("sharesOutstanding"))
    if not market_cap and price and shares:
        market_cap = price * shares
    if not (market_cap or price):
        return None
    as_of = None
    ts = row.get("timestamp")
    if ts:
        try:
            as_of = dt.datetime.utcfromtimestamp(int(ts)).date().isoformat()
        except (ValueError, OverflowError, OSError):
            as_of = None
    return {"market_cap": market_cap, "price": price, "shares": shares,
            "currency": "USD", "as_of": as_of, "source": "Financial Modeling Prep"}


def fetch_price(ticker: str):
    """
    Cached 15-minute price/market-cap read from FMP, or None. Same return contract as
    prices._finnhub_price so prices.fetch_price can layer FMP -> Finnhub -> yfinance.
    """
    ticker = ticker.strip().upper()

    def pull():
        info = _pull_quote(ticker)
        if info is None:
            raise RuntimeError("no FMP quote")
        return info

    try:
        info, fetched_at, _c = livecache.cached("fmp", f"quote_{ticker}", QUOTE_TTL, pull)
    except Exception:
        return None
    info = dict(info)
    info["fetched_at"] = fetched_at
    if not info.get("as_of"):
        info["as_of"] = fetched_at
    return info


# ----------------------------------------------------------------------------
# Daily closes for the Merton equity volatility  (PRIMARY, replacing yfinance history)
# ----------------------------------------------------------------------------
def _pull_history(ticker: str):
    data = _get(f"{BASE_V3}/historical-price-full/{ticker}", {"serietype": "line"})
    if not data or not isinstance(data, dict):
        return None
    rows = data.get("historical") or []
    if not rows:
        return None
    # FMP returns newest-first; sort ascending by date so it matches yfinance's order.
    rows = sorted((r for r in rows if r.get("date")), key=lambda r: r["date"])
    closes = [c for c in (_num(r.get("close")) for r in rows) if c and c > 0]
    if len(closes) < 20:
        return None
    closes = closes[-HISTORY_MAX_CLOSES:]
    return {"closes": closes, "as_of": rows[-1]["date"], "n": len(closes),
            "window": HISTORY_PERIOD, "source": "Financial Modeling Prep (daily history)"}


def fetch_daily_closes(ticker: str):
    """
    Cached (one day) trailing-year daily closes from FMP, or None. Returns
    {closes, as_of, n, window, source}; prices.py turns closes into annualized volatility
    with its existing pure annualized_volatility(). This replaces the fragile yfinance
    history call as the Merton volatility source, with yfinance kept as the fallback.
    """
    ticker = ticker.strip().upper()

    def pull():
        info = _pull_history(ticker)
        if info is None:
            raise RuntimeError("no FMP history")
        return info

    try:
        info, fetched_at, _c = livecache.cached("fmp", f"hist_{ticker}", HISTORY_TTL, pull)
    except Exception:
        return None
    info = dict(info)
    info["fetched_at"] = fetched_at
    return info


# ----------------------------------------------------------------------------
# Sector / profile classification  (PRIMARY, ahead of the static snapshot + yfinance)
# ----------------------------------------------------------------------------
def _pull_profile(ticker: str):
    data = _get(f"{BASE_V3}/profile/{ticker}", {})
    if not data or not isinstance(data, list):
        return None
    row = data[0] or {}
    sector = (row.get("sector") or "").strip() or None
    if not sector:
        return None
    return {"sector": sector,
            "industry": (row.get("industry") or "").strip() or None,
            "currency": (row.get("currency") or "USD").strip() or "USD",
            "beta": _num(row.get("beta")),
            "name": (row.get("companyName") or "").strip() or None,
            "source": "Financial Modeling Prep"}


def fetch_profile(ticker: str):
    """Cached (7-day) company profile with sector/industry, or None. Sector labels match
    the GICS-style names in the peer snapshot, so the sector benchmark keeps working."""
    ticker = ticker.strip().upper()

    def pull():
        info = _pull_profile(ticker)
        if info is None:
            raise RuntimeError("no FMP profile")
        return info

    try:
        info, fetched_at, _c = livecache.cached("fmp", f"profile_{ticker}", PROFILE_TTL, pull)
    except Exception:
        return None
    info = dict(info)
    info["as_of"] = fetched_at
    return info


# ----------------------------------------------------------------------------
# Fundamentals  (FALLBACK: EDGAR stays primary for the scored line items)
# ----------------------------------------------------------------------------
def _by_date(rows):
    """Index a statement list (newest-first from FMP) by its fiscal-period date."""
    out = {}
    for r in rows or []:
        d = r.get("date")
        if d and d not in out:
            out[d] = r
    return out


def _year_dict(inc: dict, bal: dict, cf: dict) -> dict:
    """Build one payload year from aligned income / balance / cash-flow rows."""
    inc, bal, cf = inc or {}, bal or {}, cf or {}

    sga = _num(inc.get("sellingGeneralAndAdministrativeExpenses"))
    if sga is None:
        gen = _num(inc.get("generalAndAdministrativeExpenses"))
        sell = _num(inc.get("sellingAndMarketingExpenses"))
        if gen is not None or sell is not None:
            sga = (gen or 0.0) + (sell or 0.0)

    dep = _num(cf.get("depreciationAndAmortization"))
    if dep is None:
        dep = _num(inc.get("depreciationAndAmortization"))

    cfo = _num(cf.get("operatingCashFlow"))
    if cfo is None:
        cfo = _num(cf.get("netCashProvidedByOperatingActivities"))

    total_assets = _num(bal.get("totalAssets"))
    total_liab = _num(bal.get("totalLiabilities"))
    if total_liab is None:
        eq = _num(bal.get("totalStockholdersEquity"))
        if total_assets is not None and eq is not None:
            total_liab = total_assets - eq

    ltd = _num(bal.get("longTermDebt"))
    re = _num(bal.get("retainedEarnings"))

    return {
        "sales": _num(inc.get("revenue")),
        "cogs": _num(inc.get("costOfRevenue")),
        "receivables": _num(bal.get("netReceivables")),
        "current_assets": _num(bal.get("totalCurrentAssets")),
        "current_liabilities": _num(bal.get("totalCurrentLiabilities")),
        "ppe": _num(bal.get("propertyPlantEquipmentNet")),
        "total_assets": total_assets,
        "depreciation": dep,
        "sga": sga,
        "long_term_debt": ltd if ltd is not None else 0.0,   # match edgar/yfinance
        "net_income": _num(inc.get("netIncome")),
        "cfo": cfo,
        "retained_earnings": re if re is not None else 0.0,  # match edgar/yfinance
        "ebit": _num(inc.get("operatingIncome")),
        "total_liabilities": total_liab,
        "shares": _num(inc.get("weightedAverageShsOut")),
    }


def _pull_fundamentals(ticker: str):
    p = {"period": "annual", "limit": 4}
    inc = _get(f"{BASE_V3}/income-statement/{ticker}", p)
    bal = _get(f"{BASE_V3}/balance-sheet-statement/{ticker}", p)
    cf = _get(f"{BASE_V3}/cash-flow-statement/{ticker}", p)
    if not (isinstance(inc, list) and isinstance(bal, list) and inc and bal):
        return None

    inc_by, bal_by, cf_by = _by_date(inc), _by_date(bal), _by_date(cf)
    # Two most recent fiscal years present in BOTH income and balance sheet.
    dates = sorted(set(inc_by) & set(bal_by), reverse=True)
    if len(dates) < 2:
        return None
    curr_end, prior_end = dates[0], dates[1]

    curr = _year_dict(inc_by.get(curr_end), bal_by.get(curr_end), cf_by.get(curr_end))
    prior = _year_dict(inc_by.get(prior_end), bal_by.get(prior_end), cf_by.get(prior_end))

    # Reject a thin pull for the same reason EDGAR does: without core balance-sheet and
    # revenue we do worse than yfinance, so fall through to it.
    if curr.get("total_assets") is None or curr.get("sales") is None:
        return None

    name = (inc_by.get(curr_end) or {}).get("symbol") or ticker
    is_financial = (curr.get("current_assets") is None
                    or curr.get("current_liabilities") is None)
    return {"curr": curr, "prior": prior, "curr_end": curr_end, "prior_end": prior_end,
            "name": name, "is_financial": is_financial}


def fetch_fundamentals(ticker: str):
    """
    Payload (meta / market_value_equity / curr / prior) built from FMP's annual income,
    balance-sheet and cash-flow statements, or None when FMP has no usable two-year annual
    history. Same shape data.fetch_live produces, so it slots straight into the fallback
    chain (EDGAR -> FMP -> yfinance). Market value of equity is left at 0.0 and filled by
    the price layer, exactly like edgar.fetch_edgar.
    """
    ticker = ticker.strip().upper()

    def pull():
        info = _pull_fundamentals(ticker)
        if info is None:
            raise RuntimeError("no FMP fundamentals")
        return info

    try:
        info, fetched_at, _c = livecache.cached(
            "fmp", f"fundamentals_{ticker}", FUNDAMENTALS_TTL, pull)
    except Exception:
        return None

    return {
        "meta": {
            "name": info["name"], "ticker": ticker,
            "source": "Financial Modeling Prep",
            "period_curr": f"FY ending {info['curr_end']}",
            "period_prior": f"FY ending {info['prior_end']}",
            "is_financial": info["is_financial"], "sector": None,
            "fundamentals_source": "Financial Modeling Prep",
            "fundamentals_as_of": info["curr_end"],
            "fundamentals_fetched_at": fetched_at,
        },
        "market_value_equity": 0.0,      # filled by the price layer (prices.py / FMP quote)
        "curr": info["curr"], "prior": info["prior"],
    }


# ----------------------------------------------------------------------------
# Analyst estimates + consensus overlay  (PREMIUM: degrades to None on the free tier,
# lights up automatically once FMP_API_KEY is on a paid plan). NEVER fed to any score.
# ----------------------------------------------------------------------------
def _pull_analyst(ticker: str):
    overlay = {"consensus": None, "price_target": None, "estimates": None,
               "as_of": None, "source": "Financial Modeling Prep"}

    # Buy/hold/sell counts + a plain consensus rating (v4, premium).
    cons = _get(f"{BASE_V4}/upgrades-downgrades-consensus", {"symbol": ticker})
    if isinstance(cons, list) and cons:
        c = cons[0] or {}
        overlay["consensus"] = {
            "rating": (c.get("consensus") or "").strip() or None,
            "strong_buy": _num(c.get("strongBuy")), "buy": _num(c.get("buy")),
            "hold": _num(c.get("hold")), "sell": _num(c.get("sell")),
            "strong_sell": _num(c.get("strongSell")),
        }

    # Price-target consensus (v4, premium).
    pt = _get(f"{BASE_V4}/price-target-consensus", {"symbol": ticker})
    if isinstance(pt, list) and pt:
        p = pt[0] or {}
        overlay["price_target"] = {
            "consensus": _num(p.get("targetConsensus")), "high": _num(p.get("targetHigh")),
            "low": _num(p.get("targetLow")), "median": _num(p.get("targetMedian")),
        }

    # Forward EPS / revenue estimates, most recent period (v3, premium).
    est = _get(f"{BASE_V3}/analyst-estimates/{ticker}", {"limit": 1})
    if isinstance(est, list) and est:
        e = est[0] or {}
        overlay["estimates"] = {
            "period": e.get("date"),
            "eps_avg": _num(e.get("estimatedEpsAvg")),
            "revenue_avg": _num(e.get("estimatedRevenueAvg")),
        }
        overlay["as_of"] = e.get("date")

    if not (overlay["consensus"] or overlay["price_target"] or overlay["estimates"]):
        return None                       # free tier: every premium call declined -> None
    return overlay


def fetch_analyst(ticker: str):
    """
    Cached (one day) analyst consensus + price-target + forward-estimate overlay, or None.
    These are FMP premium endpoints, so on the current free key every call is declined and
    this returns None (the overlay renders as "not available"). The moment the key is on a
    paid tier the same calls return data and the overlay lights up with no code change.

    This is a LABELED OVERLAY only. It is never mixed into the deterministic scores
    (SCREENER-NORTH-STAR sec 5, Group A.3): the caller attaches it beside the payload, not
    inside curr/prior, so run_models never sees it.
    """
    ticker = ticker.strip().upper()

    def pull():
        info = _pull_analyst(ticker)
        if info is None:
            raise RuntimeError("no FMP analyst data (free tier or unavailable)")
        return info

    try:
        info, fetched_at, _c = livecache.cached("fmp", f"analyst_{ticker}", ANALYST_TTL, pull)
    except Exception:
        return None
    info = dict(info)
    info["fetched_at"] = fetched_at
    if not info.get("as_of"):
        info["as_of"] = fetched_at
    return info
