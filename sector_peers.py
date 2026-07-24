"""
sector_peers.py - LIVE sector peer sets and medians from Financial Modeling Prep.

This is what retires data/universe_snapshot.csv as the SOURCE OF TRUTH for the sector
benchmark. The snapshot was built once, on 2026-06-19, from yfinance; every peer median
the app showed was therefore frozen at that date and carried no leverage column at all.
Both professors asked for the same thing (Geelen: benchmark leverage against the industry
average rather than fixed cutoffs; Huang: sector-relative, not fixed cutoffs), and a
frozen snapshot cannot answer it honestly.

The peer set now comes from FMP on the /stable base, cached through livecache and
refreshed automatically on a short interval. The snapshot stays in the repo and stays the
FALLBACK: when FMP cannot return a sector set, the benchmark still renders, labeled
clearly as stale rather than failing (see report._benchmark_block).

Two things this module refuses to do, both on purpose:

  * It never uses FMP's own altmanZScore or piotroskiScore as a peer value. FMP publishes
    both, and taking them would be one call cheaper, but the company's Z is computed by
    models.altman_z from its EDGAR filings, and a median computed by somebody else's
    variant of the formula is not comparable to it. Every peer number here comes out of
    OUR models.py, applied to FMP's raw line items. The glass box is the moat; a peer
    median assembled from a different formula would quietly break it.
  * It never blocks a page render on a cold sector build. A cold miss serves the snapshot
    (labeled stale) and kicks the build off in the background, so the next request is
    live. A stale-labeled benchmark beats a fifteen-second page.

Pure and framework-free in the same sense as fmp.py: no Streamlit, no FastAPI, every
entry point returns None on any failure and nothing raises to the caller.
"""
from __future__ import annotations

import datetime as dt
import threading
from typing import List, Optional

import fmp
import livecache
from models import altman_z, leverage_ratio

# ----------------------------------------------------------------------------
# Peer universe filters (approved 2026-07-24)
# ----------------------------------------------------------------------------
PEER_COUNT = 25                       # peers per sector, taken by market cap
MIN_MARKET_CAP = 2_000_000_000        # $2B floor: keeps the peer group S&P-500-like
                                      # without hard-coding index membership
MIN_PEERS_LIVE = 8                    # below this a live set is not worth serving;
                                      # matches benchmark.MIN_PEERS

PEERS_TTL = 24 * 3600                 # the underlying statements are annual, like
                                      # fmp.FUNDAMENTALS_TTL
REFRESH_INTERVAL = 6 * 3600           # how often the background refresher re-checks a
                                      # warm sector (see server.py)

# Depth of the per-peer pull. Set by the live probe (tests/check_sector_peers.py), and
# overridable per environment with SECTOR_PEER_DEPTH.
#   "full" - 3 calls per peer (income, balance sheet, cash flow). Gives leverage, Altman
#            Z, Piotroski F and Beneish M for every peer, all computed by our models.py,
#            so every benchmarked metric is live and methodology-identical.
#   "core" - 1 call per peer (financial-scores). Gives leverage and Altman Z only; F and
#            M fall back to the snapshot, labeled stale per metric. Roughly a third of
#            the call volume, for keys whose rate limit cannot take "full".
DEPTHS = ("full", "core")
METRICS_BY_DEPTH = {
    "full": ("leverage", "z", "f_score", "m_score"),
    "core": ("leverage", "z"),
}

# FMP's sector vocabulary (confirmed against /stable/available-sectors by the probe).
FMP_SECTORS = (
    "Basic Materials", "Communication Services", "Consumer Cyclical",
    "Consumer Defensive", "Energy", "Financial Services", "Healthcare",
    "Industrials", "Real Estate", "Technology", "Utilities",
)

# The snapshot mixes yfinance labels with the GICS names build_universe.py fell back to,
# so a company classified from the snapshot can arrive with a label the screener does not
# accept. An unmapped label returns an empty peer set and the benchmark silently vanishes,
# which is why this map exists and why the probe checks it first.
SECTOR_ALIASES = {
    "Financials": "Financial Services",
    "Financial": "Financial Services",
    "Information Technology": "Technology",
    "Health Care": "Healthcare",
    "Consumer Discretionary": "Consumer Cyclical",
    "Consumer Staples": "Consumer Defensive",
    "Materials": "Basic Materials",
    "Telecommunication Services": "Communication Services",
    "Industrial Goods": "Industrials",
    "Services": "Industrials",
}

CACHE_NAMESPACE = "peers"
SOURCE_LABEL = "FMP live sector peers"

# One build per sector at a time: two concurrent requests for the same cold sector must
# not fire two identical bursts of calls at the API.
_BUILD_LOCKS: dict = {}
_LOCKS_GUARD = threading.Lock()


def _default_depth() -> str:
    import os
    want = (os.environ.get("SECTOR_PEER_DEPTH") or "").strip().lower()
    return want if want in DEPTHS else "full"


def normalize_sector(sector: Optional[str]) -> Optional[str]:
    """Map any sector label we might hold onto FMP's vocabulary, or None."""
    if not sector:
        return None
    s = str(sector).strip()
    if not s:
        return None
    s = SECTOR_ALIASES.get(s, s)
    for known in FMP_SECTORS:
        if s.lower() == known.lower():
            return known
    return s          # unknown label: pass it through, the screener will return nothing


# ----------------------------------------------------------------------------
# Pure helpers (no network, so the tests can pin them directly)
# ----------------------------------------------------------------------------
def altman_from_scores(row: dict) -> Optional[float]:
    """
    Recompute Altman Z with OUR formula from the raw inputs /stable/financial-scores
    returns (workingCapital, retainedEarnings, ebit, marketCap, revenue, totalAssets,
    totalLiabilities). FMP publishes its own altmanZScore in the same payload and we
    deliberately ignore it: see the module docstring.
    """
    try:
        result = altman_z(
            working_capital=fmp._num(row.get("workingCapital")),
            retained_earnings=fmp._num(row.get("retainedEarnings")),
            ebit=fmp._num(row.get("ebit")),
            market_value_equity=fmp._num(row.get("marketCap")),
            sales=fmp._num(row.get("revenue")),
            total_assets=fmp._num(row.get("totalAssets")),
            total_liabilities=fmp._num(row.get("totalLiabilities")),
        )
    except (ValueError, TypeError):
        return None
    return result.z


# ----------------------------------------------------------------------------
# 1. The peer SET: one screener call per sector
# ----------------------------------------------------------------------------
def screen_sector(sector: str, limit: int = PEER_COUNT) -> Optional[List[dict]]:
    """
    The sector's largest `limit` US-listed operating companies, as
    [{"symbol", "name", "market_cap"}, ...], or None when FMP serves nothing.

    market_cap is carried forward deliberately: fmp.fetch_fundamentals leaves
    market_value_equity at 0.0 (the price layer normally fills it), and Altman's X4 is
    market value of equity over total liabilities. Without this the peer set would score
    every peer as if it had no equity value at all, and every median would be wrong in
    the same direction. It costs nothing here because the screener already returns it.
    """
    sector = normalize_sector(sector)
    if not sector:
        return None
    params = {
        "sector": sector,
        "country": "US",
        "isEtf": "false",
        "isFund": "false",
        "isActivelyTrading": "true",
        "marketCapMoreThan": MIN_MARKET_CAP,
        "limit": limit,
    }
    data = fmp._get(f"{fmp.BASE}/company-screener", params)
    if not isinstance(data, list) or not data:
        return None
    out = []
    for r in data:
        sym = (r.get("symbol") or "").strip().upper()
        if not sym:
            continue
        out.append({"symbol": sym,
                    "name": (r.get("companyName") or sym).strip(),
                    "market_cap": fmp._num(r.get("marketCap"))})
    return out or None


# ----------------------------------------------------------------------------
# 2. The peer METRICS: one row per peer, scored by our models
# ----------------------------------------------------------------------------
def _row_full(peer: dict, sector: str) -> Optional[dict]:
    """
    Full depth: pull the peer's annual statements through the existing
    fmp.fetch_fundamentals and score them with the existing data.run_models, so a peer is
    scored by exactly the code path the screened company is scored by. Three calls, and
    all four metrics come back live.
    """
    from data import run_models          # local import: data imports models, not this

    payload = fmp.fetch_fundamentals(peer["symbol"])
    if payload is None:
        return None
    if peer.get("market_cap"):
        payload["market_value_equity"] = float(peer["market_cap"])

    altman, piotroski, beneish, _verdict, _notes = run_models(payload)
    return {
        "tr": peer["symbol"],
        "name": peer.get("name") or peer["symbol"],
        "sector": sector,
        "leverage": leverage_ratio(payload.get("curr")),
        "z": altman.z if altman else None,
        "f_score": float(piotroski.score) if piotroski else None,
        "m_score": beneish.m if beneish else None,
        "market_cap": peer.get("market_cap"),
    }


def _row_core(peer: dict, sector: str) -> Optional[dict]:
    """
    Core depth: one financial-scores call per peer. Leverage and Altman Z come back live
    (Z recomputed with our formula from their raw inputs). F and M are left None on
    purpose: Piotroski needs a prior year and Beneish needs eight two-year indices, and
    neither is in this payload. FMP's own piotroskiScore sits right there in the response
    and is deliberately not taken, for the reason in the module docstring.
    """
    data = fmp._get(f"{fmp.BASE}/financial-scores", {"symbol": peer["symbol"]})
    if not isinstance(data, list) or not data:
        return None
    row = data[0] or {}
    ta = fmp._num(row.get("totalAssets"))
    tl = fmp._num(row.get("totalLiabilities"))
    return {
        "tr": peer["symbol"],
        "name": peer.get("name") or peer["symbol"],
        "sector": sector,
        "leverage": leverage_ratio({"total_assets": ta, "total_liabilities": tl}),
        "z": altman_from_scores(row),
        "f_score": None,
        "m_score": None,
        "market_cap": peer.get("market_cap"),
    }


def build_rows(sector: str, depth: Optional[str] = None,
               limit: int = PEER_COUNT) -> Optional[dict]:
    """
    Build one sector's peer rows from scratch (no cache read). Returns
    {"rows", "sector", "depth", "metrics", "as_of", "peer_count"} or None.

    One peer failing is skipped, never fatal: a sector where three of twenty-five names
    have unusable statements still benchmarks honestly on the twenty-two that worked,
    which is exactly what MIN_PEERS is there to police.
    """
    sector = normalize_sector(sector)
    depth = depth if depth in DEPTHS else _default_depth()
    if not sector:
        return None

    peers = screen_sector(sector, limit=limit)
    if not peers:
        return None

    build = _row_full if depth == "full" else _row_core
    rows = []
    for peer in peers:
        try:
            row = build(peer, sector)
        except Exception:      # noqa: BLE001 - one bad peer never kills the sector
            row = None
        if row is not None:
            rows.append(row)

    if len(rows) < MIN_PEERS_LIVE:
        return None            # too thin to be worth serving; the snapshot is better

    return {
        "rows": rows,
        "sector": sector,
        "depth": depth,
        "metrics": list(METRICS_BY_DEPTH[depth]),
        "as_of": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "peer_count": len(rows),
    }


# ----------------------------------------------------------------------------
# 3. The cached, refreshing entry point the server calls
# ----------------------------------------------------------------------------
def _lock_for(sector: str) -> threading.Lock:
    with _LOCKS_GUARD:
        if sector not in _BUILD_LOCKS:
            _BUILD_LOCKS[sector] = threading.Lock()
        return _BUILD_LOCKS[sector]


def cached_peer_set(sector: str, ttl: float = PEERS_TTL) -> Optional[dict]:
    """
    The fresh cached peer set for a sector, or None if there is not one. Pure cache read:
    it never fetches, so a request thread can call it and move on. Warming is warm()'s
    job, which the server runs off the request path.
    """
    sector = normalize_sector(sector)
    if not sector:
        return None
    hit = livecache.load(CACHE_NAMESPACE, sector, ttl)
    if hit is None:
        return None
    value, fetched_at = hit
    if not value or not value.get("rows"):
        return None
    out = dict(value)
    out["source"] = SOURCE_LABEL
    out["fetched_at"] = fetched_at
    out["stale"] = False
    return out


def warm(sector: str, depth: Optional[str] = None,
         ttl: float = PEERS_TTL) -> Optional[dict]:
    """
    Ensure a sector's peer set is cached and fresh, building it if it is not. This is the
    call that costs money and time, so it belongs on a background thread, never on a
    request. Returns the peer set, or None when FMP could not serve one.
    """
    sector = normalize_sector(sector)
    if not sector:
        return None

    with _lock_for(sector):
        existing = cached_peer_set(sector, ttl)
        if existing is not None:
            return existing                      # another thread got there first
        built = build_rows(sector, depth=depth)
        if built is None:
            return None
        fetched_at = livecache.store(CACHE_NAMESPACE, sector, built)

    out = dict(built)
    out["source"] = SOURCE_LABEL
    out["fetched_at"] = fetched_at
    out["stale"] = False
    return out


def is_stale(sector: str, ttl: float = PEERS_TTL) -> bool:
    """True when a sector has no fresh cached set (so it is due a background rebuild)."""
    return cached_peer_set(sector, ttl) is None


def clear_cache(sector: str) -> None:
    """Drop a sector's cached peer set. Used by the live probe to time a cold build."""
    import os
    sector = normalize_sector(sector)
    if not sector:
        return
    path = livecache._path(CACHE_NAMESPACE, sector)
    try:
        os.remove(path)
    except OSError:
        pass
