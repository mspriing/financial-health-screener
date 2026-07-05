"""
portfolio.py - Portfolio upload: parse a brokerage CSV export, score every holding,
and rank the portfolio weakest first. Pure and testable in the spirit of benchmark.py
and screener.py: explicit inputs, no Streamlit, no network calls inside these
functions. The live-fetch path is injected as a callable (fetch_fn / run_models_fn),
so the module ports cleanly to any frontend and tests stub it with no network.

Flow:
  parse_holdings(file_text)  -> {"holdings": [...], "note": ...}   (broker CSV -> tickers)
  score_holdings(...)        -> one scored dict per holding (snapshot fast path,
                                live fallback, degrades to "unscored" with a reason)
  rank_portfolio(scored)     -> weakest-first ordering + a portfolio-level rollup

Verdicts reuse models.overall_verdict - no new scale is invented here. Z display
respects the same cap convention as screener.fmt_z.
"""
from __future__ import annotations
import csv
import io
import re
from typing import Callable, List, Optional

from models import (AltmanResult, BeneishResult, PiotroskiResult, overall_verdict)
from screener import fmt_z

# Live fetches are rate-limited upstream (Yahoo), so a huge export is capped and the
# cap is reported in the parse result rather than silently truncated.
MAX_HOLDINGS = 30

# Header names brokers actually use, tried in order (case-insensitive).
_TICKER_HEADERS = ("symbol", "ticker", "instrument")
_SHARES_HEADERS = ("shares", "quantity", "qty")
_COST_HEADERS = ("cost basis", "cost basis total", "average cost basis", "avg cost", "average cost")

# A plain U.S. equity ticker: 1-5 letters, optional class suffix (BRK.B / BRK-B).
_EQUITY_RE = re.compile(r"^[A-Z]{1,5}([.\-][A-Z]{1,2})?$")


def _clean_number(v) -> Optional[float]:
    """Parse a broker-formatted number ('1,200.5', '$3,400.00') to float, else None."""
    if v is None:
        return None
    s = str(v).strip().replace("$", "").replace(",", "")
    if s in ("", "--", "n/a", "N/A"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _looks_like_equity(ticker: str) -> bool:
    """
    True for a plain stock symbol. Rejects option symbols (spaces / expiry digits),
    and money market funds, whose five-letter symbols conventionally end in XX
    (SPAXX, SWVXX, VMFXX, FDRXX).
    """
    if not _EQUITY_RE.match(ticker):
        return False
    if len(ticker) == 5 and ticker.endswith("XX"):
        return False
    return True


def parse_holdings(file_text: str) -> dict:
    """
    Parse a brokerage CSV export into equity holdings without asking the user to
    reformat anything. Finds the ticker column by common header names, optionally
    reads shares and cost basis, and skips everything that is not a plain equity row:
    options, money market funds, blank lines, and the footer disclaimers brokers
    append below the table. Tickers are deduped preserving first-seen order and
    capped at MAX_HOLDINGS.

    Returns {"holdings": [{"ticker", "shares", "cost_basis"}, ...], "note": str|None}
    where note explains a cap truncation. Raises ValueError when no recognizable
    ticker column exists.
    """
    # Brokers sometimes put preamble lines above the real header; find the first
    # line that contains a recognizable ticker header and parse from there.
    lines = file_text.splitlines()
    header_idx = None
    for i, line in enumerate(lines):
        cells = [c.strip().strip('"').lower() for c in line.split(",")]
        if any(c in _TICKER_HEADERS for c in cells):
            header_idx = i
            break
    if header_idx is None:
        raise ValueError(
            "Couldn't find a ticker column. The CSV needs a column named "
            "Symbol, Ticker, or Instrument (the standard broker export format).")

    reader = csv.DictReader(io.StringIO("\n".join(lines[header_idx:])))
    fields = {(f or "").strip().lower(): f for f in (reader.fieldnames or [])}

    def col(names):
        for n in names:
            if n in fields:
                return fields[n]
        return None

    tick_col = col(_TICKER_HEADERS)
    shares_col = col(_SHARES_HEADERS)
    cost_col = col(_COST_HEADERS)
    if tick_col is None:
        raise ValueError(
            "Couldn't find a ticker column. The CSV needs a column named "
            "Symbol, Ticker, or Instrument (the standard broker export format).")

    holdings: List[dict] = []
    seen = set()
    for row in reader:
        raw = (row.get(tick_col) or "").strip().strip('"').upper()
        if not raw or not _looks_like_equity(raw):
            continue
        if raw in seen:
            continue
        seen.add(raw)
        holdings.append({
            "ticker": raw,
            "shares": _clean_number(row.get(shares_col)) if shares_col else None,
            "cost_basis": _clean_number(row.get(cost_col)) if cost_col else None,
        })

    note = None
    if len(holdings) > MAX_HOLDINGS:
        note = (f"Your file has {len(holdings)} equity positions; scoring the first "
                f"{MAX_HOLDINGS} (a rate-limit guard on live data fetches).")
        holdings = holdings[:MAX_HOLDINGS]
    return {"holdings": holdings, "note": note}


# ----------------------------------------------------------------------------
# Scoring
# ----------------------------------------------------------------------------
def _is_flagged(m_flag) -> bool:
    if isinstance(m_flag, bool):
        return m_flag
    return str(m_flag).strip().lower() == "true"


def _worst_signal(z, zone, f, m_flag) -> str:
    """One plain line naming the weakest of the holding's scores."""
    if zone == "Distress":
        return f"Altman Z of {fmt_z(z)} sits in the distress zone."
    if m_flag:
        return "Beneish M flags possible earnings manipulation."
    if f is not None and f <= 3:
        return f"Piotroski F of {f} out of 9: weak fundamentals."
    if zone == "Grey":
        return f"Altman Z of {fmt_z(z)} sits in the grey zone, real balance sheet stress."
    if f is not None and f <= 6:
        return f"Piotroski F of {f} out of 9 is the softest score here, a mixed operating picture."
    if z is None and f is None:
        return "No score could be computed for this holding."
    return "No weak signal: the scores read healthy."


def _verdict_from_scores(z, zone, f, m, m_flag) -> dict:
    """Rebuild lightweight model results from stored scores and reuse overall_verdict."""
    altman = AltmanResult(z=z, zone=zone) if (z is not None and zone) else None
    pio = PiotroskiResult(score=int(f)) if f is not None else None
    ben = BeneishResult(m=m, flag=_is_flagged(m_flag)) if m is not None else None
    return overall_verdict(altman, pio, ben)


def _result(ticker, name, sector, z, zone, f, m, m_flag, source, weight) -> dict:
    flag = _is_flagged(m_flag) if m is not None else False
    verdict = _verdict_from_scores(z, zone, f, m, m_flag)
    return {
        "ticker": ticker, "name": name, "sector": sector,
        "z": z, "zone": zone, "f_score": f, "m_score": m, "m_flag": flag,
        "verdict": verdict, "source": source, "weight": weight,
        "worst_signal": _worst_signal(z, zone, f, flag),
        "unscored_reason": None,
    }


def _unscored(holding: dict, reason: str) -> dict:
    return {
        "ticker": holding["ticker"], "name": holding["ticker"], "sector": None,
        "z": None, "zone": None, "f_score": None, "m_score": None, "m_flag": False,
        "verdict": {"health": "Unknown", "integrity": "Not enough data"},
        "source": "unscored", "weight": holding.get("shares"),
        "worst_signal": "No score could be computed for this holding.",
        "unscored_reason": reason,
    }


def score_holdings(holdings: List[dict], snapshot_rows: List[dict],
                   fetch_fn: Callable, run_models_fn: Callable) -> List[dict]:
    """
    Score every holding. Snapshot hit = fast path, no network. Otherwise fall back to
    the live path (fetch_fn -> run_models_fn, the same pipeline the single-company
    screen uses), wrapped so an unknown or failing ticker degrades to an "unscored"
    row carrying the reason, never an exception.
    """
    by_ticker = {str(r.get("tr", "")).strip().upper(): r for r in snapshot_rows}
    out: List[dict] = []
    for h in holdings:
        t = h["ticker"]
        snap = by_ticker.get(t)
        if snap is not None:
            f = snap.get("f_score")
            out.append(_result(
                t, snap.get("name") or t, snap.get("sector"),
                snap.get("z"), snap.get("zone") or None,
                int(f) if isinstance(f, (int, float)) else None,
                snap.get("m_score"), snap.get("m_flag"),
                "snapshot", h.get("shares")))
            continue
        try:
            payload = fetch_fn(t)
            altman, pio, ben, _verdict, _notes = run_models_fn(payload)
            if altman is None and pio is None and ben is None:
                reason = ("Looks like a bank or insurer: these models can't read a "
                          "financial institution's statements."
                          if payload.get("meta", {}).get("is_financial")
                          else "Not enough statement data to run any of the three models.")
                out.append(_unscored(h, reason))
                continue
            out.append(_result(
                t, payload.get("meta", {}).get("name") or t,
                payload.get("meta", {}).get("sector"),
                altman.z if altman else None, altman.zone if altman else None,
                pio.score if pio else None,
                ben.m if ben else None, ben.flag if ben else False,
                "live", h.get("shares")))
        except Exception as e:  # noqa: BLE001 - degrade, never crash the portfolio
            out.append(_unscored(h, str(e) or "Couldn't fetch data for this symbol."))
    return out


# ----------------------------------------------------------------------------
# Ranking + rollup
# ----------------------------------------------------------------------------
# Weakest first: distressed, then Beneish-flagged, then watch, then healthy.
def _rank_key(r: dict):
    health = r["verdict"]["health"]
    if health == "Distressed":
        tier = 0
    elif r.get("m_flag"):
        tier = 1
    elif health == "Watch":
        tier = 2
    else:
        tier = 3
    # within a tier, lower Z (more stressed) then lower F first; None sorts last
    z = r.get("z")
    f = r.get("f_score")
    return (tier, z if z is not None else float("inf"),
            f if f is not None else float("inf"))


def rank_portfolio(scored: List[dict]) -> dict:
    """
    Sort scored holdings weakest first (unscored rows listed separately, last) and
    roll the portfolio up: counts by health verdict, Beneish-flag count, the three
    weakest holdings, and the unscored count.
    """
    ranked = sorted((r for r in scored if r["source"] != "unscored"), key=_rank_key)
    unscored = [r for r in scored if r["source"] == "unscored"]

    counts = {"Distressed": 0, "Watch": 0, "Healthy": 0}
    for r in ranked:
        h = r["verdict"]["health"]
        if h in counts:
            counts[h] += 1
    n_flagged = sum(1 for r in ranked if r.get("m_flag"))

    return {
        "ranked": ranked,
        "unscored": unscored,
        "counts": counts,
        "n_flagged": n_flagged,
        "weakest": ranked[:3],
        "n_unscored": len(unscored),
    }


# ----------------------------------------------------------------------------
# Built-in example (all tickers are in the committed snapshot, so it demos offline)
# ----------------------------------------------------------------------------
EXAMPLE_CSV = """Symbol,Quantity,Cost Basis
AAPL,25,3850.00
MSFT,10,3100.00
KO,60,3480.00
MMM,20,1900.00
AOS,40,2600.00
"""
