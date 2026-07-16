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
import time
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

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
# uses with st.cache_data). It is read-only after load.
_UNIVERSE = load_universe()


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
    snapshot loaded, without hitting EDGAR or Finnhub."""
    return {"status": "ok", "universe_rows": len(_UNIVERSE),
            "history_backend": backend_name(), "time": time.time()}


@app.get("/api/samples")
def list_samples():
    """The illustrative sample companies, for a 'try a sample' path with no network."""
    return {"samples": list(PRESETS.keys())}


@app.get("/api/samples/{name}")
def sample_report(name: str):
    if name not in PRESETS:
        raise HTTPException(status_code=404, detail=f"No sample named '{name}'.")
    return build_report(PRESETS[name], _UNIVERSE)


@app.get("/api/company/{ticker}")
def company_report(ticker: str, benchmark: bool = Query(True)):
    """The live company report: EDGAR fundamentals + Finnhub price (Stage 1), scored
    and benchmarked (Stage 2). This is what the Company drill-down screen renders."""
    try:
        payload = fetch_live(ticker)
    except RuntimeError as e:
        raise _friendly_error(e)
    return build_report(payload, _UNIVERSE if benchmark else None)


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
