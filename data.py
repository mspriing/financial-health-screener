"""
data.py — Data layer. Ways to feed the models:

  1. fetch_live(ticker)  — the live pipeline. Fundamentals come from SEC EDGAR (the
                           official free filings source), prices from Finnhub's free
                           tier, and yfinance is kept only as a fallback behind both.
                           See fetch_live() for the layering and provenance.
  2. PRESETS             — three illustrative sample companies (clearly labeled as
                           sample data) so the app always demos cleanly, even offline.
  3. manual entry        — the UI lets a user type the line items directly.

All produce the same `payload` shape, which run_models() turns into scores. Each
model degrades independently: a company missing the inputs for one model still gets
scored on the others.

The live pipeline is split into pure adapter modules (edgar.py, prices.py, livecache.py)
with no Streamlit imports, so the whole data layer moves into a FastAPI service verbatim
at migration time. This module orchestrates them and stamps data provenance (source +
as-of) onto every payload so the UI can always show where a number came from.
"""
from __future__ import annotations
from models import altman_z, beneish_m, piotroski_f, overall_verdict

# Line items every payload carries (units are arbitrary but must be consistent).
LINE_ITEMS = [
    "sales", "cogs", "receivables", "current_assets", "current_liabilities",
    "ppe", "total_assets", "depreciation", "sga", "long_term_debt",
    "net_income", "cfo", "retained_earnings", "ebit", "total_liabilities", "shares",
]


def run_models(payload: dict):
    """
    Run all three models. Each one degrades to None independently, so a company
    that's missing the inputs for one model (e.g. a bank with no current-asset
    structure) still gets scored on the models that DO apply.

    Returns: (altman, piotroski, beneish, verdict, notes)
             where notes is {model_name: reason} explaining any N/A.
    """
    curr, prior = payload["curr"], payload["prior"]
    notes = {}

    # --- Altman Z ---
    try:
        wc = curr.get("working_capital")
        if wc is None:
            if curr.get("current_assets") is None or curr.get("current_liabilities") is None:
                raise ValueError("no current asset/liability detail "
                                 "(banks & insurers don't report a working-capital split).")
            wc = curr["current_assets"] - curr["current_liabilities"]
        altman = altman_z(
            working_capital=wc,
            retained_earnings=curr.get("retained_earnings"),
            ebit=curr.get("ebit"),
            market_value_equity=payload.get("market_value_equity") or 0.0,
            sales=curr.get("sales"),
            total_assets=curr.get("total_assets"),
            total_liabilities=curr.get("total_liabilities"),
        )
    except (ValueError, KeyError, TypeError) as e:
        altman, notes["Altman Z-Score"] = None, str(e)

    # --- Piotroski F ---
    try:
        piotroski = piotroski_f(curr, prior)
    except (ValueError, KeyError, TypeError) as e:
        piotroski, notes["Piotroski F-Score"] = None, str(e)

    # --- Beneish M ---
    try:
        beneish = beneish_m(curr, prior)
    except (ValueError, KeyError, TypeError) as e:
        beneish, notes["Beneish M-Score"] = None, str(e)

    verdict = overall_verdict(altman, piotroski, beneish)
    return altman, piotroski, beneish, verdict, notes


# ----------------------------------------------------------------------------
# Illustrative sample companies (NOT real filings — designed to show the range)
# ----------------------------------------------------------------------------
PRESETS = {
    "Bluechip Industries (sample: healthy)": {
        "meta": {"name": "Bluechip Industries", "ticker": "SAMPLE", "source": "Illustrative sample data",
                 "period_curr": "FY (latest)", "period_prior": "FY (prior)", "is_financial": False},
        "market_value_equity": 12000,
        "curr": dict(sales=5000, cogs=3000, receivables=400, current_assets=2200,
                     current_liabilities=1200, ppe=1800, total_assets=6000, depreciation=300,
                     sga=600, long_term_debt=900, net_income=700, cfo=850,
                     retained_earnings=2600, ebit=950, total_liabilities=2400, shares=1000),
        "prior": dict(sales=4500, cogs=2800, receivables=380, current_assets=1900,
                      current_liabilities=1150, ppe=1750, total_assets=5600, depreciation=290,
                      sga=560, long_term_debt=1000, net_income=560, cfo=700,
                      retained_earnings=2100, ebit=780, total_liabilities=2450, shares=1000),
    },
    "Legacy Retail Co. (sample: distressed)": {
        "meta": {"name": "Legacy Retail Co.", "ticker": "SAMPLE", "source": "Illustrative sample data",
                 "period_curr": "FY (latest)", "period_prior": "FY (prior)", "is_financial": False},
        "market_value_equity": 300,
        "curr": dict(sales=3000, cogs=2400, receivables=150, current_assets=900,
                     current_liabilities=1300, ppe=1200, total_assets=2800, depreciation=180,
                     sga=520, long_term_debt=1100, net_income=-220, cfo=-50,
                     retained_earnings=-600, ebit=20, total_liabilities=2700, shares=500),
        "prior": dict(sales=3300, cogs=2500, receivables=160, current_assets=1000,
                      current_liabilities=1200, ppe=1300, total_assets=3000, depreciation=190,
                      sga=540, long_term_debt=1000, net_income=-80, cfo=60,
                      retained_earnings=-380, ebit=120, total_liabilities=2600, shares=480),
    },
    "Momentum Software Co. (sample: earnings red flags)": {
        "meta": {"name": "Momentum Software Co.", "ticker": "SAMPLE", "source": "Illustrative sample data",
                 "period_curr": "FY (latest)", "period_prior": "FY (prior)", "is_financial": False},
        "market_value_equity": 4000,
        "curr": dict(sales=1200, cogs=300, receivables=520, current_assets=900,
                     current_liabilities=300, ppe=260, total_assets=1500, depreciation=30,
                     sga=500, long_term_debt=150, net_income=240, cfo=40,
                     retained_earnings=300, ebit=300, total_liabilities=600, shares=200),
        "prior": dict(sales=900, cogs=240, receivables=210, current_assets=600,
                      current_liabilities=280, ppe=240, total_assets=1100, depreciation=40,
                      sga=400, long_term_debt=150, net_income=110, cfo=120,
                      retained_earnings=120, ebit=170, total_liabilities=560, shares=190),
    },
}


def blank_payload() -> dict:
    """An empty payload for the manual-entry form."""
    zero = {k: 0.0 for k in LINE_ITEMS}
    return {"meta": {"name": "Manual entry", "ticker": "", "source": "Manual entry",
                     "period_curr": "Current year", "period_prior": "Prior year",
                     "is_financial": False},
            "market_value_equity": 0.0, "curr": dict(zero), "prior": dict(zero)}


# ----------------------------------------------------------------------------
# Fallback fundamentals via yfinance (behind the EDGAR-first orchestrator below)
# ----------------------------------------------------------------------------
def _row(df, *labels):
    """Return [current, prior] for the first matching row label, else [None, None]."""
    if df is None or getattr(df, "empty", True):
        return [None, None]
    for lab in labels:
        if lab in df.index:
            vals = df.loc[lab].values
            cur = float(vals[0]) if len(vals) > 0 and vals[0] == vals[0] else None
            pri = float(vals[1]) if len(vals) > 1 and vals[1] == vals[1] else None
            return [cur, pri]
    return [None, None]


# Common index / fund symbols people type by mistake (they aren't single companies).
INDEX_FUND_HINTS = {
    "SPY", "QQQ", "DIA", "IVV", "VOO", "VTI", "IWM",
    "^GSPC", "^DJI", "^IXIC", "^RUT", "SPX", "NDX", "DJIA",
    "S&P", "S&P500", "SP500", "NASDAQ", "DOW", "DOWJONES", "RUSSELL",
}


def fetch_yfinance(ticker: str) -> dict:
    """
    Pull the two most recent annual statements from Yahoo Finance. Missing fields
    come back as None and the affected model degrades to N/A (rather than crashing).
    Raises RuntimeError with a plain-English explanation when the symbol isn't a
    single company we can analyze (an index/fund, a private or unknown ticker, etc.).

    This is the FALLBACK fundamentals source. fetch_live() calls EDGAR first and only
    lands here when EDGAR can't serve the ticker, so the app keeps working out of the
    box while the honest, official data path (EDGAR) is preferred whenever available.
    """
    import yfinance as yf

    raw = ticker.strip().upper()
    t = yf.Ticker(raw)

    quote_type = ""
    try:
        quote_type = ((t.info or {}).get("quoteType") or "").upper()
    except Exception:
        quote_type = ""

    bs, ic, cf = t.balance_sheet, t.income_stmt, t.cashflow
    have_statements = not (getattr(bs, "empty", True) and getattr(ic, "empty", True))

    if not have_statements:
        indexish = raw in INDEX_FUND_HINTS or quote_type in (
            "ETF", "MUTUALFUND", "INDEX", "CURRENCY", "CRYPTOCURRENCY")
        if indexish:
            kind = {"ETF": "an ETF (a fund)", "MUTUALFUND": "a mutual fund",
                    "INDEX": "a market index", "CURRENCY": "a currency",
                    "CRYPTOCURRENCY": "a cryptocurrency"}.get(quote_type, "an index or fund")
            raise RuntimeError(
                f"“{raw}” is {kind}, not a single company. An index like the S&P 500, Nasdaq, "
                "or Dow, along with the funds that track them (SPY, QQQ, DIA), is a basket of "
                "hundreds of companies, so it has no single set of financial statements for these "
                "company-level models to read. Enter one company's ticker instead "
                "(e.g. AAPL, MSFT, KO).")
        raise RuntimeError(
            f"Couldn't find company financials for “{raw}.” Common reasons: the symbol is "
            "mistyped (try AAPL for Apple, MSFT for Microsoft); it's a private company "
            "(like SpaceX) with no public filings; it's a foreign listing or ADR that Yahoo "
            "doesn't fully cover; or Yahoo is briefly rate-limiting, in which case wait a few "
            "seconds and try again.")

    ta = _row(bs, "Total Assets")
    tl = _row(bs, "Total Liabilities Net Minority Interest", "Total Liabilities")
    ca = _row(bs, "Current Assets", "Total Current Assets")
    cl = _row(bs, "Current Liabilities", "Total Current Liabilities")
    re = _row(bs, "Retained Earnings", "Retained Earnings Accumulated Deficit")
    ppe = _row(bs, "Net PPE", "Net Property Plant And Equipment", "Properties", "Gross PPE")
    ltd = _row(bs, "Long Term Debt", "Long Term Debt And Capital Lease Obligation")
    rec = _row(bs, "Accounts Receivable", "Receivables", "Net Receivables",
               "Gross Accounts Receivable")
    sh = _row(bs, "Ordinary Shares Number", "Share Issued")
    eq = _row(bs, "Stockholders Equity", "Common Stock Equity",
              "Total Equity Gross Minority Interest")

    sales = _row(ic, "Total Revenue", "Operating Revenue")
    cogs = _row(ic, "Cost Of Revenue", "Reconciled Cost Of Revenue")
    ebit = _row(ic, "EBIT", "Operating Income", "Total Operating Income As Reported",
                "Pretax Income")
    sga = _row(ic, "Selling General And Administration",
               "Selling General And Administrative Expense", "General And Administrative Expense")
    ni = _row(ic, "Net Income", "Net Income Common Stockholders",
              "Net Income Continuous Operations",
              "Net Income From Continuing Operation Net Minority Interest")

    cfo = _row(cf, "Operating Cash Flow", "Cash Flow From Continuing Operating Activities",
               "Total Cash From Operating Activities")
    dep = _row(cf, "Depreciation And Amortization", "Depreciation Amortization Depletion",
               "Depreciation")

    def total_liab(i):
        if tl[i] is not None:
            return tl[i]
        if ta[i] is not None and eq[i] is not None:   # fall back to Assets − Equity
            return ta[i] - eq[i]
        return None

    def yr(i):
        return dict(
            sales=sales[i], cogs=cogs[i], receivables=rec[i],
            current_assets=ca[i], current_liabilities=cl[i], ppe=ppe[i],
            total_assets=ta[i], depreciation=dep[i], sga=sga[i],
            long_term_debt=(ltd[i] if ltd[i] is not None else 0.0),
            net_income=ni[i], cfo=cfo[i],
            retained_earnings=(re[i] if re[i] is not None else 0.0),
            ebit=ebit[i], total_liabilities=total_liab(i), shares=sh[i],
        )

    curr, prior = yr(0), yr(1)

    # market value of equity (current)
    mve = None
    try:
        mve = t.fast_info.get("market_cap")
    except Exception:
        mve = None
    if not mve:
        mve = (getattr(t, "info", {}) or {}).get("marketCap")
    if not mve and sh[0]:
        try:
            mve = (t.fast_info.get("last_price") or 0) * sh[0]
        except Exception:
            mve = None

    name = ticker.upper()
    try:
        name = (t.fast_info.get("shortName")
                or (getattr(t, "info", {}) or {}).get("shortName") or name)
    except Exception:
        pass

    # Sector — needed to benchmark the company against its peers (see benchmark.py).
    # Resolve from the committed snapshot FIRST: it needs no network and is reliable on
    # Streamlit Cloud, where the live .info call for the sector routinely fails (AAPL ->
    # Technology, JPM -> Financial Services, all proven offline). Only fall back to
    # yfinance's own .info classification for tickers the snapshot doesn't carry.
    sector = None
    try:
        from benchmark import load_universe, lookup_sector
        sector = lookup_sector(load_universe(), ticker.upper())
    except Exception:
        sector = None
    if not sector:
        try:
            sector = ((getattr(t, "info", {}) or {}).get("sector") or "").strip() or None
        except Exception:
            sector = None

    # Banks / insurers don't report a current/non-current split -> classic Z & M don't apply.
    is_financial = (ca[0] is None or cl[0] is None)

    return {
        "meta": {"name": name, "ticker": ticker.upper(),
                 "source": "Yahoo Finance (yfinance)",
                 "period_curr": "Latest annual", "period_prior": "Prior annual",
                 "is_financial": is_financial, "sector": sector,
                 "fundamentals_source": "Yahoo Finance (yfinance)",
                 "fundamentals_as_of": "Latest annual",
                 "fundamentals_fetched_at": None},
        "market_value_equity": float(mve) if mve else 0.0,
        "curr": curr, "prior": prior,
    }


# ----------------------------------------------------------------------------
# Live pipeline orchestrator: EDGAR fundamentals + Finnhub price, yfinance fallback
# ----------------------------------------------------------------------------
def fetch_live(ticker: str) -> dict:
    """
    The one entry point the app calls for a live company. It layers the sources so the
    product runs on official, honest data whenever possible and never breaks when a
    layer is unavailable:

      Fundamentals:  SEC EDGAR (companyfacts)  ->  yfinance fallback
      Price / MVE:   Finnhub free tier         ->  yfinance fallback

    Returns the same payload shape as before (meta / market_value_equity / curr /
    prior), plus a meta["provenance"] block recording the source and as-of of both the
    fundamentals and the price, so the UI can show exactly where every number came from.

    Raises RuntimeError with a plain-English message only when NEITHER fundamentals
    source can analyze the symbol (an index/fund, private, or unknown ticker), reusing
    the friendly yfinance messages.
    """
    raw = ticker.strip()

    # --- 1. Fundamentals: EDGAR first ---
    payload = None
    try:
        import edgar
        payload = edgar.fetch_edgar(raw)
    except Exception:
        payload = None

    if payload is None:
        # yfinance fallback also produces the friendly index/unknown-ticker errors.
        payload = fetch_yfinance(raw)

    meta = payload["meta"]

    # --- 2. Price / market value of equity: Finnhub first, yfinance fallback ---
    price_info = None
    try:
        import prices
        price_info = prices.fetch_price(raw)
    except Exception:
        price_info = None

    if price_info and price_info.get("market_cap"):
        payload["market_value_equity"] = float(price_info["market_cap"])

    # --- 2b. Equity volatility for the Merton model (daily history via prices.py) ---
    vol_info = None
    try:
        import prices
        vol_info = prices.fetch_equity_volatility(raw)
    except Exception:
        vol_info = None
    payload["equity_volatility"] = (
        float(vol_info["value"]) if vol_info and vol_info.get("value") else None)

    # --- 3. Provenance stamped on meta (source + as-of for every live input) ---
    meta["source"] = meta.get("fundamentals_source", meta.get("source"))
    meta["provenance"] = {
        "fundamentals": {
            "source": meta.get("fundamentals_source"),
            "as_of": meta.get("fundamentals_as_of"),
            "fetched_at": meta.get("fundamentals_fetched_at"),
        },
        "price": {
            "source": (price_info or {}).get("source"),
            "as_of": (price_info or {}).get("as_of"),
            "value": (price_info or {}).get("market_cap"),
        } if price_info else {"source": None, "as_of": None, "value": None},
        "equity_volatility": {
            "source": (vol_info or {}).get("source"),
            "as_of": (vol_info or {}).get("as_of"),
            "value": (vol_info or {}).get("value"),
            "window": (vol_info or {}).get("window"),
        } if vol_info else {"source": None, "as_of": None, "value": None, "window": None},
    }
    return payload
