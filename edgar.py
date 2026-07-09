"""
edgar.py - fundamentals from SEC EDGAR, the official free filings source (no API key).

This is the fundamentals backbone the product spec calls for. Balance-sheet and
income-statement line items come straight from the company's own filings via EDGAR's
companyfacts API, which is a better story in a finance meeting ("scores computed from
the filings, straight from EDGAR") than scraping Yahoo, and it removes the biggest
dependency on yfinance's unofficial, rate-limited endpoints.

It generalizes the extraction proven in the Intel backtest (fetch_intel_history.py):
the same us-gaap tag map and the same earliest-filed-wins indexing, but scoped to the
two most recent fiscal years, which is all the three models need for a current read.

fetch_edgar(ticker) returns a payload in the exact shape data.fetch_live produces
(meta / market_value_equity / curr / prior), or None when EDGAR cannot serve the
ticker (unknown symbol, foreign filer, or too little tagged data), so the caller can
fall back to yfinance. Market value of equity is left at 0.0 here and filled by the
price layer (prices.py); EDGAR carries filings, not quotes.

Pure and framework-free: moves into the FastAPI service verbatim at migration.
"""
from __future__ import annotations

import datetime as dt

import requests

import livecache

# SEC fair-use policy requires a real contact in the User-Agent (same one the Intel
# backtest already uses successfully against EDGAR).
UA = {"User-Agent": "Michael Spring mspring823@gmail.com (financial-health-screener)"}

TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

# Filings change quarterly, so a day-long cache is plenty and keeps us polite.
FACTS_TTL = 24 * 3600
TICKER_MAP_TTL = 7 * 24 * 3600

# LINE_ITEMS (data.py) -> candidate us-gaap tags, first hit wins. Copied from the
# Intel runner so both paths read filings identically.
TAGS = {
    "sales": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues"],
    "cogs": ["CostOfGoodsAndServicesSold", "CostOfRevenue", "CostOfGoodsSold"],
    "depreciation": ["DepreciationDepletionAndAmortization",
                     "DepreciationAmortizationAndAccretionNet", "Depreciation"],
    "sga": ["SellingGeneralAndAdministrativeExpense",
            "MarketingGeneralAndAdministrativeExpense",
            "GeneralAndAdministrativeExpense"],
    "net_income": ["NetIncomeLoss"],
    "cfo": ["NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "ebit": ["OperatingIncomeLoss"],
    "receivables": ["AccountsReceivableNetCurrent", "ReceivablesNetCurrent"],
    "current_assets": ["AssetsCurrent"],
    "current_liabilities": ["LiabilitiesCurrent"],
    "ppe": ["PropertyPlantAndEquipmentNet",
            "PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulatedDepreciationAndAmortization"],
    "total_assets": ["Assets"],
    "long_term_debt": ["LongTermDebtNoncurrent", "LongTermDebt"],
    "retained_earnings": ["RetainedEarningsAccumulatedDeficit"],
    "total_liabilities": ["Liabilities"],
}
# Intel and others never tag "Liabilities"; derive it as Assets - Stockholders' Equity,
# the same fallback data.fetch_live and the backtest use.
EQUITY_TAGS = ["StockholdersEquity",
               "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"]
FLOW_ITEMS = ("sales", "cogs", "depreciation", "sga", "net_income", "cfo", "ebit")
INSTANT_ITEMS = tuple(k for k in TAGS if k not in FLOW_ITEMS)


# ----------------------------------------------------------------------------
# Polite, cached fetching
# ----------------------------------------------------------------------------
def _get_json(url: str) -> dict:
    """One retry, generous timeout, never hammered (mirrors the backtest runner)."""
    import time
    for attempt in (1, 2):
        try:
            r = requests.get(url, headers=UA, timeout=60)
            r.raise_for_status()
            return r.json()
        except Exception:
            if attempt == 2:
                raise
            time.sleep(3)


def resolve_cik(ticker: str):
    """Map a ticker to its zero-padded 10-digit CIK via SEC's official list, or None."""
    ticker = ticker.strip().upper()

    def pull():
        return _get_json(TICKER_MAP_URL)

    table, _fetched, _cached = livecache.cached(
        "edgar", "company_tickers", TICKER_MAP_TTL, pull)
    for row in table.values():
        if str(row.get("ticker", "")).upper() == ticker:
            return str(row["cik_str"]).zfill(10)
    return None


def fetch_companyfacts(ticker: str):
    """Full companyfacts payload for a ticker (cached per ticker), or None if unknown."""
    cik = resolve_cik(ticker)
    if not cik:
        return None, None

    def pull():
        return _get_json(COMPANYFACTS_URL.format(cik=cik))

    facts, fetched_at, _from_cache = livecache.cached(
        "edgar", f"facts_{ticker.strip().upper()}", FACTS_TTL, pull)
    return facts, fetched_at


# ----------------------------------------------------------------------------
# Fact extraction (earliest-filed wins, so results are deterministic)
# ----------------------------------------------------------------------------
def _days(a: str, b: str) -> int:
    return (dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days


def _facts_for(facts: dict, tags):
    gaap = facts.get("facts", {}).get("us-gaap", {})
    out = []
    for tag in tags:
        node = gaap.get(tag)
        if not node:
            continue
        units = node.get("units", {})
        vals = units.get("USD") or next(iter(units.values()), [])
        out.extend(vals)
    return out


def _index_durations(vals):
    out = {}
    for v in vals:
        if "start" not in v or v.get("val") is None:
            continue
        key = (v["start"], v["end"])
        if key not in out or v["filed"] < out[key]["filed"]:
            out[key] = v
    return out


def _index_instants(vals):
    out = {}
    for v in vals:
        if v.get("val") is None:
            continue
        end = v["end"]
        if end not in out or v["filed"] < out[end]["filed"]:
            out[end] = v
    return out


def _near(target: str, candidates, tol=7):
    t = dt.date.fromisoformat(target)
    best = None
    for c in candidates:
        gap = abs((dt.date.fromisoformat(c) - t).days)
        if gap <= tol and (best is None or gap < best[1]):
            best = (c, gap)
    return best[0] if best else None


class _FactBook:
    """Per-item indexes over one companyfacts payload, scoped to annual figures."""

    def __init__(self, facts: dict):
        self.dur = {}       # flow item -> {(start, end): fact}
        self.inst = {}      # instant item -> {end: fact}
        for item in FLOW_ITEMS:
            self.dur[item] = _index_durations(_facts_for(facts, TAGS[item]))
        for item in INSTANT_ITEMS:
            self.inst[item] = _index_instants(_facts_for(facts, TAGS[item]))
        self.equity = _index_instants(_facts_for(facts, EQUITY_TAGS))
        dei = facts.get("facts", {}).get("dei", {}).get(
            "EntityCommonStockSharesOutstanding", {})
        self.shares_by_filed = {}
        for v in next(iter(dei.get("units", {}).values()), []):
            if v.get("val"):
                self.shares_by_filed[v["filed"]] = float(v["val"])

    def fy_ends(self):
        """Annual (10-K) period ends, newest first, keyed off net income."""
        return sorted({end for (start, end) in self.dur["net_income"]
                       if 340 <= _days(start, end) <= 380}, reverse=True)

    def annual_flow(self, item: str, end: str):
        for (s, e), f in self.dur[item].items():
            if e == end and 340 <= _days(s, e) <= 380:
                return f
        return None

    def instant(self, item: str, end: str):
        f = self.inst[item].get(end)
        if f is None:
            near = _near(end, self.inst[item].keys())
            f = self.inst[item].get(near) if near else None
        return f

    def equity_at(self, end: str):
        f = self.equity.get(end)
        if f is None:
            near = _near(end, self.equity.keys())
            f = self.equity.get(near) if near else None
        return f

    def shares_latest(self):
        return self.shares_by_filed[max(self.shares_by_filed)] if self.shares_by_filed else None


def _year_dict(book: _FactBook, end: str) -> dict:
    """Build one payload year (curr or prior) from the fiscal-year ending at `end`."""
    out = {}
    for item in FLOW_ITEMS:
        f = book.annual_flow(item, end)
        out[item] = float(f["val"]) if f else None
    for item in INSTANT_ITEMS:
        if item == "total_liabilities":
            continue
        f = book.instant(item, end)
        out[item] = float(f["val"]) if f else None

    # total_liabilities: use the tagged value, else derive Assets - Equity.
    tl = book.instant("total_liabilities", end)
    if tl is not None:
        out["total_liabilities"] = float(tl["val"])
    else:
        eq = book.equity_at(end)
        ta = out.get("total_assets")
        out["total_liabilities"] = (ta - float(eq["val"])) if (eq and ta is not None) else None

    # Match fetch_live's conventions so the models behave identically across sources.
    if out.get("long_term_debt") is None:
        out["long_term_debt"] = 0.0
    if out.get("retained_earnings") is None:
        out["retained_earnings"] = 0.0
    out["shares"] = book.shares_latest()
    return out


def fetch_edgar(ticker: str):
    """
    Payload (meta / market_value_equity / curr / prior) built from EDGAR filings, or
    None when EDGAR has no usable two-year annual history for the ticker. Never raises
    on a missing ticker: returning None lets data.fetch_live fall back to yfinance.
    """
    ticker = ticker.strip().upper()
    try:
        facts, fetched_at = fetch_companyfacts(ticker)
    except Exception:
        return None
    if not facts:
        return None

    book = _FactBook(facts)
    ends = book.fy_ends()
    if len(ends) < 2:
        return None
    curr_end, prior_end = ends[0], ends[1]

    curr = _year_dict(book, curr_end)
    prior = _year_dict(book, prior_end)

    # Reject a thin pull: without core balance-sheet and revenue we do worse than yfinance.
    if curr.get("total_assets") is None or curr.get("sales") is None:
        return None

    name = facts.get("entityName") or ticker
    is_financial = (curr.get("current_assets") is None or curr.get("current_liabilities") is None)

    sector = None
    try:
        from benchmark import load_universe, lookup_sector
        sector = lookup_sector(load_universe(), ticker)
    except Exception:
        sector = None

    return {
        "meta": {
            "name": name, "ticker": ticker,
            "source": "SEC EDGAR (companyfacts)",
            "period_curr": f"FY ending {curr_end}",
            "period_prior": f"FY ending {prior_end}",
            "is_financial": is_financial, "sector": sector,
            "fundamentals_source": "SEC EDGAR (companyfacts)",
            "fundamentals_as_of": curr_end,          # the filing period the scores read
            "fundamentals_fetched_at": fetched_at,   # when we last pulled from EDGAR
        },
        "market_value_equity": 0.0,   # filled by the price layer (prices.py)
        "curr": curr, "prior": prior,
    }
