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


def _result(ticker, name, sector, z, zone, f, m, m_flag, source, weight,
            data_source=None) -> dict:
    flag = _is_flagged(m_flag) if m is not None else False
    verdict = _verdict_from_scores(z, zone, f, m, m_flag)
    return {
        "ticker": ticker, "name": name, "sector": sector,
        "z": z, "zone": zone, "f_score": f, "m_score": m, "m_flag": flag,
        "verdict": verdict, "source": source, "weight": weight,
        "data_source": data_source or {"source": None, "as_of": None},
        "worst_signal": _worst_signal(z, zone, f, flag),
        "unscored_reason": None,
    }


def _unscored(holding: dict, reason: str) -> dict:
    return {
        "ticker": holding["ticker"], "name": holding["ticker"], "sector": None,
        "z": None, "zone": None, "f_score": None, "m_score": None, "m_flag": False,
        "verdict": {"health": "Unknown", "integrity": "Not enough data"},
        "source": "unscored", "weight": holding.get("shares"),
        "data_source": {"source": None, "as_of": None},
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
    from report import build_report, portfolio_row   # the one contract both paths speak

    by_ticker = {str(r.get("tr", "")).strip().upper(): r for r in snapshot_rows}
    out: List[dict] = []
    for h in holdings:
        t = h["ticker"]
        snap = by_ticker.get(t)
        if snap is not None:
            # Fast path: precomputed scores, no network. They are as fresh as the
            # snapshot build, which is why the row carries that date. A snapshot row and
            # a live drill-down of the same ticker can legitimately differ; the UI shows
            # the as-of so the difference is visible rather than silent.
            #
            # Financials (banks, insurers) were left blank when the snapshot was built,
            # the same reason the live path can't score them. A blank snapshot row must
            # degrade to unscored here too, or it silently reports a fake "Unknown"
            # verdict instead of the honest reason, and sorts alongside real Healthy
            # holdings in rank_portfolio instead of its own unscored bucket.
            z, f, m = snap.get("z"), snap.get("f_score"), snap.get("m_score")
            if z is None and f is None and m is None:
                out.append(_unscored(
                    h, "Looks like a bank or insurer: these models can't read a "
                       "financial institution's statements."))
                continue
            out.append(_result(
                t, snap.get("name") or t, snap.get("sector"),
                z, snap.get("zone") or None,
                int(f) if isinstance(f, (int, float)) else None,
                m, snap.get("m_flag"),
                "snapshot", h.get("shares"),
                data_source={"source": "S&P 500 snapshot",
                             "as_of": snap.get("as_of_date")}))
            continue
        try:
            # Live path: build the SAME report the company drill-down renders, then
            # project it to a row. One contract, so the row and the detail cannot disagree.
            payload = fetch_fn(t)
            report = build_report(payload, run_models_fn=run_models_fn)
            scores = report["scores"]
            if not any(scores[m]["applicable"] for m in ("altman", "piotroski", "beneish")):
                reason = ("Looks like a bank or insurer: these models can't read a "
                          "financial institution's statements."
                          if report["company"]["is_financial"]
                          else "Not enough statement data to run any of the three models.")
                out.append(_unscored(h, reason))
                continue
            out.append(portfolio_row(report, shares=h.get("shares"), source="live"))
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


# A single sector at or above this share of the portfolio is worth naming out loud.
CONCENTRATION_ALERT_PCT = 40.0


def sector_concentration(scored: List[dict],
                         value_by_ticker: Optional[dict] = None) -> dict:
    """
    How the portfolio is spread across sectors, weakest-diversification answer first.
    This is the "am I exposed without realizing it" read: a portfolio of nine healthy
    holdings that are all Technology is not a diversified portfolio.

    Weighting is honest about what it knows. Pass value_by_ticker {ticker: market value}
    and the read is value-weighted. Without it, every position counts once and the basis
    says so, rather than silently pretending equal weight is dollar weight.

    Unscored holdings still count toward concentration (you own them), bucketed under
    an "Unknown" sector when we could not resolve one.
    """
    if not scored:
        return {"basis": "position count", "sectors": [], "top_sector": None,
                "top_pct": None, "concentrated": False,
                "headline": "No holdings to analyze."}

    have_values = bool(value_by_ticker) and all(
        value_by_ticker.get(r.get("ticker")) for r in scored)
    basis = "market value" if have_values else "position count"

    totals: dict = {}
    counts: dict = {}
    for r in scored:
        sector = r.get("sector") or "Unknown"
        w = float(value_by_ticker[r["ticker"]]) if have_values else 1.0
        totals[sector] = totals.get(sector, 0.0) + w
        counts[sector] = counts.get(sector, 0) + 1

    grand = sum(totals.values()) or 1.0
    sectors = sorted(
        ({"sector": s, "n": counts[s], "weight": totals[s],
          "pct": round(100.0 * totals[s] / grand, 1)} for s in totals),
        key=lambda d: (-d["pct"], d["sector"]))

    top = sectors[0]
    concentrated = top["pct"] >= CONCENTRATION_ALERT_PCT and top["sector"] != "Unknown"
    if concentrated:
        headline = (f"{top['pct']:.0f}% of this portfolio sits in {top['sector']}, "
                    f"by {basis}. A sector shock hits most of it at once.")
    elif len(sectors) == 1:
        headline = f"Every holding is in one sector ({top['sector']}), by {basis}."
    else:
        headline = (f"Spread across {len(sectors)} sectors, with {top['sector']} the "
                    f"largest at {top['pct']:.0f}% by {basis}.")

    return {"basis": basis, "sectors": sectors, "top_sector": top["sector"],
            "top_pct": top["pct"], "concentrated": concentrated, "headline": headline}


def rank_portfolio(scored: List[dict],
                   value_by_ticker: Optional[dict] = None) -> dict:
    """
    Sort scored holdings weakest first (unscored rows listed separately, last) and
    roll the portfolio up: counts by health verdict, Beneish-flag count, the three
    weakest holdings, the unscored count, and the sector-concentration read.

    When rows carry a `delta` block (see history.diff_portfolio), the rollup also
    summarises what changed since the last check, which is the monitoring loop's payoff.
    """
    ranked = sorted((r for r in scored if r["source"] != "unscored"), key=_rank_key)
    unscored = [r for r in scored if r["source"] == "unscored"]

    counts = {"Distressed": 0, "Watch": 0, "Healthy": 0}
    for r in ranked:
        h = r["verdict"]["health"]
        if h in counts:
            counts[h] += 1
    n_flagged = sum(1 for r in ranked if r.get("m_flag"))

    rollup = {
        "ranked": ranked,
        "unscored": unscored,
        "counts": counts,
        "n_flagged": n_flagged,
        "weakest": ranked[:3],
        "n_unscored": len(unscored),
        "concentration": sector_concentration(scored, value_by_ticker),
    }

    if any("delta" in r for r in scored):
        from history import changed_holdings
        rollup["changes"] = changed_holdings(ranked)
    return rollup


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
