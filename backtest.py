"""
backtest.py - Point-in-time backtest engine. Pure and testable in the spirit of
benchmark.py / screener.py / portfolio.py: explicit inputs, no Streamlit, no network.
The fetch runner (fetch_intel_history.py) gathers filings and prices; this module
answers "what would the models have said on each historical date?"

The discipline that makes the result mean anything:
  * A filing is usable on an as-of date only if its FILING DATE (not its period end)
    is on or before that date. A 10-K for FY2024 filed in late January 2025 does not
    exist on 2025-01-01.
  * Market value of equity is the share price ON the as-of date (last close at or
    before it) times shares outstanding from the selected filing. Never today's cap.
No model math lives here: every snapshot is scored by data.run_models, which calls
the same models.py formulas the live app uses.

Input shapes:
  filings: list of dicts, each one payload-ready:
      {"filing_date": "YYYY-MM-DD", "period_end": "YYYY-MM-DD", "form": "10-K"|"10-Q",
       "curr": {line items}, "prior": {line items}, "shares": float|None}
  prices: {"YYYY-MM-DD": close, ...} daily closes
  as_of_dates: iterable of "YYYY-MM-DD" strings
"""
from __future__ import annotations

import datetime as _dt
from typing import Callable, List, Optional

from data import run_models

EVENT_START = "2025-12-01"
EVENT_END = "2026-01-31"


def _d(s) -> _dt.date:
    return s if isinstance(s, _dt.date) else _dt.date.fromisoformat(str(s)[:10])


# ----------------------------------------------------------------------------
# Point-in-time selection
# ----------------------------------------------------------------------------
def select_filing(filings: List[dict], as_of) -> Optional[dict]:
    """
    The latest filing actually AVAILABLE on the as-of date: filing_date <= as_of,
    ranked by filing date then period end. Returns None when nothing was on file yet.
    """
    cutoff = _d(as_of)
    avail = [f for f in filings if _d(f["filing_date"]) <= cutoff]
    if not avail:
        return None
    return max(avail, key=lambda f: (_d(f["filing_date"]), _d(f["period_end"])))


def price_on(prices: dict, as_of):
    """(date_used, close) for the last trading day at or before as_of, else (None, None)."""
    cutoff = _d(as_of)
    best = None
    for ds, px in prices.items():
        d = _d(ds)
        if d <= cutoff and px is not None and (best is None or d > best[0]):
            best = (d, float(px))
    return (best[0].isoformat(), best[1]) if best else (None, None)


def point_in_time_mve(prices: dict, as_of, shares) -> tuple:
    """(price_used, mve): as-of close times shares outstanding from the selected filing."""
    date_used, px = price_on(prices, as_of)
    if px is None or not shares:
        return (px, None)
    return (px, px * float(shares))


# ----------------------------------------------------------------------------
# Snapshot trail
# ----------------------------------------------------------------------------
def _payload(filing: dict, mve) -> dict:
    """Assemble the exact payload shape data.py produces, so run_models is reused as-is."""
    return {
        "meta": {"name": "backtest", "ticker": "", "source": "backtest",
                 "period_curr": filing["period_end"], "period_prior": "prior year",
                 "is_financial": False},
        "market_value_equity": float(mve) if mve else 0.0,
        "curr": dict(filing["curr"]),
        "prior": dict(filing["prior"]),
    }


def build_snapshots(filings: List[dict], prices: dict, as_of_dates,
                    run_models_fn: Callable = run_models) -> List[dict]:
    """
    One scored row per as-of date, using only information available on that date.
    Rows where no filing existed yet carry None scores and a note, never an exception.
    """
    rows: List[dict] = []
    for as_of in as_of_dates:
        as_of = _d(as_of).isoformat()
        f = select_filing(filings, as_of)
        if f is None:
            rows.append({"as_of_date": as_of, "statement_period_used": None,
                         "filing_date_used": None, "form_used": None,
                         "z": None, "zone": None, "f_score": None,
                         "m_score": None, "m_flag": None,
                         "verdict": "Unknown", "integrity": "Not enough data",
                         "price_used": None, "mve_used": None,
                         "note": "No filing was on file yet at this date."})
            continue

        price_used, mve = point_in_time_mve(prices, as_of, f.get("shares"))
        altman, pio, ben, verdict, notes = run_models_fn(_payload(f, mve))
        rows.append({
            "as_of_date": as_of,
            "statement_period_used": f["period_end"],
            "filing_date_used": f["filing_date"],
            "form_used": f.get("form"),
            "z": altman.z if altman else None,
            "zone": altman.zone if altman else None,
            "f_score": pio.score if pio else None,
            "m_score": ben.m if ben else None,
            "m_flag": ben.flag if ben else None,
            "verdict": verdict["health"],
            "integrity": verdict["integrity"],
            "price_used": price_used,
            "mve_used": mve,
            "note": "; ".join(f"{k}: {v}" for k, v in notes.items()) if notes else "",
        })
    return rows


# ----------------------------------------------------------------------------
# Hit / miss classification
# ----------------------------------------------------------------------------
def _warnings_on(row: dict) -> List[str]:
    """Which models are in a warning state on this snapshot row."""
    out = []
    if row.get("zone") in ("Grey", "Distress"):
        out.append(f"Altman Z {row['z']} ({row['zone']} zone)")
    if row.get("f_score") is not None and row["f_score"] <= 3:
        out.append(f"Piotroski F {row['f_score']} (weak, 3 or below)")
    if row.get("m_flag"):
        out.append(f"Beneish M {row['m_score']} (manipulation flag)")
    return out


def classify_trail(snapshots: List[dict], event_start: str = EVENT_START,
                   event_end: str = EVENT_END) -> dict:
    """
    The verdict, stated plainly: did any model reach a warning state (grey/distress Z,
    F <= 3, Beneish flag) BEFORE the event window opened, which model moved first, and
    with how many days of lead time. A trail with no pre-event warning is an honest miss.
    """
    ev_start = _d(event_start)
    ordered = sorted(snapshots, key=lambda r: r["as_of_date"])

    first_date, first_models = None, []
    for row in ordered:
        w = _warnings_on(row)
        if w:
            first_date, first_models = row["as_of_date"], w
            break

    pre_event = [r for r in ordered if _d(r["as_of_date"]) < ev_start]
    last_before = pre_event[-1] if pre_event else None
    verdict_before = last_before["verdict"] if last_before else None

    hit = first_date is not None and _d(first_date) < ev_start
    lead_days = (ev_start - _d(first_date)).days if hit else None

    if hit:
        summary = (f"HIT. The models were already in a warning state on {first_date}, "
                   f"{lead_days} days before the event window opened on {event_start}. "
                   f"First warning: {'; '.join(first_models)}. The overall verdict on the "
                   f"last snapshot before the window ({last_before['as_of_date']}) was "
                   f"{verdict_before}.")
    elif first_date is not None:
        summary = (f"MISS. The first warning only appears on {first_date}, inside or after "
                   f"the event window that opened on {event_start}. On the last snapshot "
                   f"before the window the verdict was {verdict_before}. The models did not "
                   f"see this one coming in time.")
    else:
        summary = (f"MISS. No model reached a warning state at any point in the trail. "
                   f"The verdict before the {event_start} window was {verdict_before}. "
                   f"The models did not see this one coming.")

    return {
        "hit": hit,
        "first_warning_date": first_date,
        "first_warning_models": first_models,
        "lead_days": lead_days,
        "event_start": event_start,
        "event_end": event_end,
        "verdict_before_event": verdict_before,
        "warned_before_event": bool(hit and verdict_before in ("Watch", "Distressed")),
        "summary": summary,
    }
