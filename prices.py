"""
prices.py - current price and market value of equity, cached 15 minutes.

The models need one live market number: market value of equity, the X4 term in
Altman Z and the value of a portfolio holding. A holdings health check is not day
trading, so 15-minute freshness fully satisfies "reflects current holdings" while
staying polite to every source.

Three layers behind one function, live-market source first:
  1. Financial Modeling Prep - the primary live backbone (used when FMP_API_KEY is set).
                          See fmp.py. Real US quotes and market cap on the free tier.
  2. Finnhub free tier  - first fallback when FINNHUB_API_KEY is set. Real-time US quotes.
  3. yfinance           - last-resort fallback so the app keeps working out of the box.
                          Yahoo's endpoints are unofficial, which is exactly why they are
                          the fallback and not the backbone.

fetch_price(ticker) returns a dict with market_cap plus provenance, or None if no
layer can price the ticker (the caller then leaves market_value_equity at 0.0, and
Altman Z simply degrades, the same graceful path the models already handle).

Pure and framework-free: reads the keys from the environment, not from Streamlit, so
it moves into the FastAPI service verbatim. The app bridges st.secrets -> env.
"""
from __future__ import annotations

import math
import os

import requests

import livecache

PRICE_TTL = 15 * 60          # 15 minutes
# Equity volatility feeds the Merton model. It is a statistic of daily returns, so it
# barely moves intraday: a day-long cache is plenty and polite. One trailing year of
# daily closes (~252 trading days) is the standard window.
PRICE_HISTORY_TTL = 24 * 3600
PRICE_HISTORY_PERIOD = "1y"
TRADING_DAYS_PER_YEAR = 252
FINNHUB_QUOTE = "https://finnhub.io/api/v1/quote"
FINNHUB_PROFILE = "https://finnhub.io/api/v1/stock/profile2"


def _key_from_secrets_file():
    """
    Best-effort read of FINNHUB_API_KEY from .streamlit/secrets.toml, so a plain
    `python3` script (tests, the portfolio path) sees the same key the Streamlit app
    does without needing an exported env var. Python 3.9 has no tomllib, so this is a
    minimal single-line parse, not a full TOML reader. Returns the key or None.
    """
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        ".streamlit", "secrets.toml")
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("#") or "FINNHUB_API_KEY" not in line:
                    continue
                _, _, val = line.partition("=")
                val = val.strip().strip('"').strip("'")
                if val and val != "PASTE_YOUR_FINNHUB_KEY_HERE":
                    return val
    except OSError:
        pass
    return None


def _api_key():
    return os.environ.get("FINNHUB_API_KEY") or _key_from_secrets_file() or None


# ----------------------------------------------------------------------------
# Layer 1: Finnhub
# ----------------------------------------------------------------------------
def _finnhub_price(ticker: str):
    key = _api_key()
    if not key:
        return None
    sym = ticker.strip().upper()

    prof = requests.get(FINNHUB_PROFILE, params={"symbol": sym, "token": key}, timeout=20)
    prof.raise_for_status()
    p = prof.json() or {}
    quote = requests.get(FINNHUB_QUOTE, params={"symbol": sym, "token": key}, timeout=20)
    quote.raise_for_status()
    q = quote.json() or {}

    price = q.get("c") or None                       # current price
    # Finnhub reports market cap and shares in millions of the listing currency.
    market_cap = (p.get("marketCapitalization") or 0) * 1_000_000 or None
    shares = (p.get("shareOutstanding") or 0) * 1_000_000 or None
    if not market_cap and price and shares:
        market_cap = price * shares
    if not (market_cap or price):
        return None
    return {"market_cap": market_cap, "price": price, "shares": shares,
            "currency": p.get("currency") or "USD", "source": "Finnhub"}


# ----------------------------------------------------------------------------
# Layer 2: yfinance fallback
# ----------------------------------------------------------------------------
def _yfinance_price(ticker: str):
    import yfinance as yf
    t = yf.Ticker(ticker.strip().upper())
    market_cap = price = shares = None
    try:
        fi = t.fast_info
        market_cap = fi.get("market_cap")
        price = fi.get("last_price")
        shares = fi.get("shares")
    except Exception:
        pass
    if not market_cap:
        market_cap = (getattr(t, "info", {}) or {}).get("marketCap")
    if not market_cap and price and shares:
        market_cap = price * shares
    if not (market_cap or price):
        return None
    return {"market_cap": market_cap, "price": price, "shares": shares,
            "currency": "USD", "source": "Yahoo Finance (fallback)"}


# ----------------------------------------------------------------------------
# Orchestration + cache
# ----------------------------------------------------------------------------
def fetch_price(ticker: str):
    """
    Cached 15-minute price/market-cap read for a ticker, Finnhub first then yfinance.
    Returns {market_cap, price, shares, currency, source, as_of, fetched_at} or None.
    """
    ticker = ticker.strip().upper()

    def pull():
        info = None
        try:
            import fmp                          # primary live source
            info = fmp.fetch_price(ticker)
        except Exception:
            info = None
        if info is None:
            info = _finnhub_price(ticker)       # first fallback
        if info is None:
            info = _yfinance_price(ticker)      # last-resort fallback
        if info is None:
            raise RuntimeError("no price source returned data")
        return info

    try:
        info, fetched_at, _from_cache = livecache.cached("price", ticker, PRICE_TTL, pull)
    except Exception:
        return None
    info = dict(info)
    info["fetched_at"] = fetched_at
    if not info.get("as_of"):                   # preserve FMP's real quote date if present
        info["as_of"] = fetched_at
    return info


# ----------------------------------------------------------------------------
# Equity volatility (for the Merton model): annualized stdev of daily returns
# ----------------------------------------------------------------------------
def annualized_volatility(closes, periods_per_year: int = TRADING_DAYS_PER_YEAR):
    """
    Annualized volatility from a series of daily closing prices, via the sample standard
    deviation of daily log returns scaled by sqrt(periods_per_year). PURE: no network,
    no globals, so it is unit-testable on a hand-built series (see tests/test_merton.py).

    Returns None when there are too few clean, positive closes for an honest estimate
    (fewer than 20 usable points), so the Merton component degrades rather than reporting
    a volatility computed from a handful of days.
    """
    clean = [float(c) for c in (closes or [])
             if c is not None and c == c and float(c) > 0]      # drop None and NaN
    if len(clean) < 20:
        return None
    rets = [math.log(clean[i] / clean[i - 1]) for i in range(1, len(clean))]
    n = len(rets)
    if n < 2:
        return None
    mean = sum(rets) / n
    variance = sum((r - mean) ** 2 for r in rets) / (n - 1)     # sample (n-1) variance
    return math.sqrt(variance) * math.sqrt(periods_per_year)


def _yfinance_history_bars(ticker: str):
    """
    Dated daily closes over the trailing year from yfinance, as {dates, closes} with the
    two lists index-aligned and dates as plain YYYY-MM-DD strings. This is the FALLBACK
    behind FMP's daily history (Finnhub's free tier does not serve candles at all), so
    both equity volatility and the portfolio correlation read keep working with no paid
    dependency. Returns None when Yahoo has no usable history.
    """
    import yfinance as yf
    t = yf.Ticker(ticker.strip().upper())
    hist = t.history(period=PRICE_HISTORY_PERIOD, auto_adjust=True)
    if hist is None or getattr(hist, "empty", True) or "Close" not in hist:
        return None
    dates, closes = [], []
    for idx, c in zip(hist.index, hist["Close"].values):
        c = float(c)
        if c != c:                                  # drop NaN
            continue
        dates.append(str(idx)[:10])
        closes.append(c)
    if not closes:
        return None
    return {"dates": dates, "closes": closes,
            "source": "Yahoo Finance (daily history)"}


def _yfinance_history_closes(ticker: str):
    """Just the closes from _yfinance_history_bars, the shape the volatility path wants."""
    bars = _yfinance_history_bars(ticker)
    return bars["closes"] if bars else None


def fetch_equity_volatility(ticker: str):
    """
    Cached (one day) annualized equity volatility for a ticker, from ~1 year of daily
    closes. Returns {value, window, n, source, as_of, fetched_at} or None when history
    is unavailable or too thin, in which case the caller leaves equity_volatility unset
    and the Merton component degrades honestly (the same graceful path the price layer
    already uses for a missing market cap).
    """
    ticker = ticker.strip().upper()

    def pull():
        # FMP primary: daily closes from the live backbone.
        fmp_hist = None
        try:
            import fmp
            fmp_hist = fmp.fetch_daily_closes(ticker)
        except Exception:
            fmp_hist = None
        if fmp_hist and fmp_hist.get("closes"):
            vol = annualized_volatility(fmp_hist["closes"])
            if vol is not None:
                return {"value": vol,
                        "window": fmp_hist.get("window", PRICE_HISTORY_PERIOD),
                        "n": fmp_hist.get("n", len(fmp_hist["closes"])),
                        "as_of": fmp_hist.get("as_of"),
                        "source": fmp_hist.get("source",
                                               "Financial Modeling Prep (daily history)")}

        # yfinance fallback.
        closes = _yfinance_history_closes(ticker)
        if not closes:
            raise RuntimeError("no price history returned")
        vol = annualized_volatility(closes)
        if vol is None:
            raise RuntimeError("history too thin for a volatility estimate")
        return {"value": vol, "window": PRICE_HISTORY_PERIOD, "n": len(closes),
                "source": "Yahoo Finance (daily history)"}

    try:
        info, fetched_at, _from_cache = livecache.cached(
            "equityvol", ticker, PRICE_HISTORY_TTL, pull)
    except Exception:
        return None
    info = dict(info)
    info["fetched_at"] = fetched_at
    if not info.get("as_of"):                   # preserve FMP's real history end-date
        info["as_of"] = fetched_at
    return info


# ----------------------------------------------------------------------------
# Dated price history (for the portfolio correlation / Sharpe-delta read)
# ----------------------------------------------------------------------------
def fetch_price_history(ticker: str):
    """
    Cached (one day) trailing-year DATED daily closes for a ticker:
    {dates, closes, n, window, source, as_of, fetched_at}, or None.

    Same source layering as everything else here, FMP primary then yfinance. The
    difference from fetch_equity_volatility is that this keeps the dates: a single
    holding's volatility only needs the closes, but correlating two holdings requires
    both series lined up on the same trading days, which a bare list of closes can not
    support. risk.py does that alignment; this function only fetches.

    Returns None (never raises) when no layer has usable history, so the caller lists the
    holding as excluded with a reason rather than guessing at its returns.
    """
    ticker = ticker.strip().upper()

    def pull():
        bars = None
        try:
            import fmp                          # primary live source
            bars = fmp.fetch_daily_closes(ticker)
        except Exception:
            bars = None
        # A cached payload written before dates existed is not usable here; treat it as a
        # miss and let the fallback answer rather than reporting a date-less history.
        if not (bars and bars.get("dates") and bars.get("closes")):
            bars = _yfinance_history_bars(ticker)
        if not (bars and bars.get("dates") and bars.get("closes")):
            raise RuntimeError("no price history returned")
        dates, closes = bars["dates"], bars["closes"]
        n = min(len(dates), len(closes))
        if n < 2:
            raise RuntimeError("price history too thin")
        return {"dates": list(dates[:n]), "closes": [float(c) for c in closes[:n]],
                "n": n, "window": bars.get("window", PRICE_HISTORY_PERIOD),
                "as_of": bars.get("as_of") or dates[n - 1],
                "source": bars.get("source", "unknown")}

    try:
        info, fetched_at, _from_cache = livecache.cached(
            "pricehist", ticker, PRICE_HISTORY_TTL, pull)
    except Exception:
        return None
    info = dict(info)
    info["fetched_at"] = fetched_at
    if not info.get("as_of"):
        info["as_of"] = fetched_at
    return info
