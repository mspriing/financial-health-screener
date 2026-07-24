"""
server.py - FastAPI service, the API the real Next.js frontend calls.

This is the "migration hinge" the spec pointed at: models.py, data.py, benchmark.py,
commentary.py, portfolio.py, history.py, and report.py are already pure Python with no
Streamlit import anywhere, so this file is almost entirely wiring. It imports those
modules exactly the way app.py does and puts an HTTP door in front of them. No scoring
logic, no new business rules, live here.

Every endpoint returns the same report.py schema (schema_version, company, verdict,
scores, benchmark, provenance) or a rollup built from portfolio.rank_portfolio(), so the
frontend renders from one contract regardless of which screen it's on.

Run locally:  python3 -m uvicorn server:app --reload --port 8000
Docs:         http://localhost:8000/docs  (FastAPI's auto-generated API explorer)
"""
from __future__ import annotations

import os
import threading
import time
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import sector_peers
from benchmark import load_universe
from data import PRESETS, fetch_live, run_models
from history import diff_portfolio
from portfolio import EXAMPLE_CSV, parse_holdings, rank_portfolio, score_holdings
from report import build_report
from store import backend_name, load_history, save_run

app = FastAPI(title="Financial Health Screener API", version="1.0")

# Local Next.js dev server + the eventual Vercel domain. CORS_ORIGINS overrides in
# production (comma-separated), so this file does not need editing at deploy time.
_default_origins = "http://localhost:3000,http://127.0.0.1:3000"
origins = [o.strip() for o in os.environ.get("CORS_ORIGINS", _default_origins).split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

# The S&P snapshot loads once per process, not per request (same discipline app.py
# uses with st.cache_data). It is read-only after load. Since 2026-07-24 it is the
# FALLBACK peer source, not the primary one: sector_peers.py serves live FMP medians.
_UNIVERSE = load_universe()

# ----------------------------------------------------------------------------
# Live sector peers: warmed off the request path, refreshed on a timer
# ----------------------------------------------------------------------------
# Sectors any request has asked about. The refresher only rebuilds these, so call volume
# stays proportional to what people actually look at instead of rebuilding all eleven
# sectors on every deploy.
_WARM_SECTORS = set()
_WARM_LOCK = threading.Lock()

# Off by default: with an ephemeral filesystem, warming every sector at boot re-spends
# the whole build on each redeploy. Set PEER_WARM_ON_START=1 if that is ever wanted.
_WARM_ON_START = os.environ.get("PEER_WARM_ON_START", "").strip() in ("1", "true", "yes")


def _peers_for(sector: Optional[str]) -> Optional[dict]:
    """
    The live peer set for a sector if one is cached and fresh, else None (the report then
    falls back to the snapshot, labeled stale).

    This NEVER builds. A cold sector costs one screener call plus up to seventy-five
    statement calls, which is fine on a background thread and completely wrong inside a
    page render, so a miss registers the sector for warming and returns None. The next
    request for that sector gets live medians.
    """
    sector = sector_peers.normalize_sector(sector)
    if not sector:
        return None
    with _WARM_LOCK:
        new_sector = sector not in _WARM_SECTORS
        _WARM_SECTORS.add(sector)
    hit = sector_peers.cached_peer_set(sector)
    if hit is None and new_sector:
        threading.Thread(target=sector_peers.warm, args=(sector,),
                         name=f"peer-warm-{sector}", daemon=True).start()
    return hit


def _refresh_loop() -> None:
    """
    Rebuild any warm sector whose cached peer set has aged past its TTL. This is what
    makes the benchmark self-maintaining: there is no rebuild script to remember to run,
    which is the whole complaint against the old universe_snapshot.csv.
    """
    while True:
        time.sleep(sector_peers.REFRESH_INTERVAL)
        with _WARM_LOCK:
            sectors = sorted(_WARM_SECTORS)
        for sector in sectors:
            if sector_peers.is_stale(sector):
                try:
                    sector_peers.warm(sector)
                except Exception:      # noqa: BLE001 - a refresher must never die
                    pass


@app.on_event("startup")
def _start_peer_refresher() -> None:
    threading.Thread(target=_refresh_loop, name="peer-refresh", daemon=True).start()
    if _WARM_ON_START:
        for sector in sector_peers.FMP_SECTORS:
            with _WARM_LOCK:
                _WARM_SECTORS.add(sector)
            threading.Thread(target=sector_peers.warm, args=(sector,),
                             name=f"peer-warm-{sector}", daemon=True).start()


class PortfolioRequest(BaseModel):
    csv_text: str


def _friendly_error(e: Exception) -> HTTPException:
    """data.fetch_live raises RuntimeError with an already plain-English message
    (unknown ticker, index/fund, rate limiting). Pass that straight through as the
    error body instead of a generic 500, so the frontend can show it directly."""
    return HTTPException(status_code=400, detail=str(e) or "Could not process that ticker.")


@app.get("/health")
def health():
    """Uptime-ping target (spec item D4): confirms the process is alive and the
    snapshot loaded, without hitting EDGAR or Finnhub. It also reports which sectors
    currently have a live peer set cached, which is how you confirm from outside that
    the FMP benchmark is actually serving rather than silently sitting on the snapshot."""
    with _WARM_LOCK:
        warm = sorted(_WARM_SECTORS)
    return {"status": "ok", "universe_rows": len(_UNIVERSE),
            "history_backend": backend_name(),
            "peer_depth": sector_peers._default_depth(),
            "peer_sectors_warm": warm,
            "peer_sectors_live": [s for s in warm
                                  if sector_peers.cached_peer_set(s) is not None],
            "time": time.time()}


@app.get("/api/samples")
def list_samples():
    """The illustrative sample companies, for a 'try a sample' path with no network."""
    return {"samples": list(PRESETS.keys())}


@app.get("/api/samples/{name}")
def sample_report(name: str):
    if name not in PRESETS:
        raise HTTPException(status_code=404, detail=f"No sample named '{name}'.")
    sample = PRESETS[name]
    sector = (sample.get("meta") or {}).get("sector")
    return build_report(sample, _UNIVERSE, peers=_peers_for(sector))


@app.get("/api/company/{ticker}")
def company_report(ticker: str, benchmark: bool = Query(True)):
    """The live company report: EDGAR fundamentals + FMP price/sector (Stage 1), scored
    and benchmarked against LIVE FMP sector peers (Stage 2). This is what the Company
    drill-down screen renders."""
    try:
        payload = fetch_live(ticker)
    except RuntimeError as e:
        raise _friendly_error(e)
    if not benchmark:
        return build_report(payload)
    sector = (payload.get("meta") or {}).get("sector")
    return build_report(payload, _UNIVERSE, peers=_peers_for(sector))


@app.get("/api/portfolio/example")
def portfolio_example():
    """The built-in example CSV (portfolio.py's own constant), so the frontend's
    'try a sample portfolio' button stays in sync with the real one, not a copy."""
    return {"csv_text": EXAMPLE_CSV}


@app.post("/api/portfolio")
def score_portfolio(req: PortfolioRequest, remember: bool = Query(True)):
    """
    Parse a brokerage CSV, score every holding (snapshot fast path, live EDGAR/Finnhub
    fallback), and return the weakest-first rollup, sector concentration, and what
    changed since the last time this ticker set was checked.

    remember=false skips writing to history (used for one-off "try a sample" runs so
    they do not pollute the real deterioration trail against a demo portfolio).
    """
    try:
        parsed = parse_holdings(req.csv_text)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    holdings = parsed["holdings"]
    if not holdings:
        raise HTTPException(status_code=400, detail="No equity holdings found in that file.")

    scored = score_holdings(holdings, _UNIVERSE, fetch_live, run_models)

    history = load_history()
    tagged = diff_portfolio(scored, history)
    rollup = rank_portfolio(tagged)

    if remember:
        save_run(scored)

    return {"parse_note": parsed["note"], "rollup": rollup}
