"""
history.py - "what changed since you last checked": the feature that makes this a
monitor instead of a report.

The whole product promise is that it watches what you already own. A tool that recomputes
the same scores every visit is a report. A tool that says "MMM slipped from Watch to
Distressed since your last check on March 3" is a monitor, and that is the thing no free
tool does: brokerage apps fire on price, not on fundamentals quietly deteriorating for six
quarters (the Intel case exactly).

Two layers, deliberately separated:
  * diff_holding / diff_portfolio  - PURE functions over dicts. No I/O, no clock.
  * load_history / save_run        - the small JSON store (data/history/scores.json).

Keeping the diff pure is what lets tests pin the semantics with no filesystem, and lets a
FastAPI backend later swap the JSON file for a real per-user table without touching a line
of the comparison logic.

Direction is judged on the health ladder (Distressed < Watch < Healthy). A newly raised
Beneish manipulation flag counts as deterioration even when health is unchanged, because
it is new information the owner needs to see.
"""
from __future__ import annotations

import json
import os
import time
from typing import List, Optional

SCHEMA_VERSION = "1.0"
_DEFAULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "data", "history", "scores.json")

# The health ladder. Unknown sits outside it: we never call a move to/from Unknown a
# deterioration or an improvement, because it means "we lost the data", not "it got worse".
_HEALTH_RANK = {"Distressed": 0, "Watch": 1, "Healthy": 2}

# The fields we remember per ticker between runs. spring_score is stored but not yet
# diffed: the direction logic stays on the health ladder; the trail is for the weekly
# email and history views to read later.
_TRACKED = ("z", "zone", "f_score", "m_score", "m_flag", "health", "integrity",
            "spring_score")


# ----------------------------------------------------------------------------
# Pure diff
# ----------------------------------------------------------------------------
def _delta(curr, prev):
    """Numeric change, or None when either side is missing."""
    if curr is None or prev is None:
        return None
    return round(curr - prev, 4)


def snapshot_of(row: dict) -> dict:
    """The subset of a scored holding worth remembering between runs."""
    verdict = row.get("verdict") or {}
    return {
        "z": row.get("z"),
        "zone": row.get("zone"),
        "f_score": row.get("f_score"),
        "m_score": row.get("m_score"),
        "m_flag": bool(row.get("m_flag")),
        "health": verdict.get("health"),
        "integrity": verdict.get("integrity"),
        "spring_score": row.get("spring_score"),
    }


def diff_holding(curr_row: dict, prev: Optional[dict]) -> dict:
    """
    Compare one scored holding against what we stored last time.

    Returns a `delta` block:
      first_seen    - no prior record, so nothing to compare (not a change)
      direction     - "deteriorated" | "improved" | "unchanged"
      changed       - True when direction is not "unchanged"
      health_from/to, z_delta, f_delta, new_flag, cleared_flag, headline
    """
    curr = snapshot_of(curr_row)
    if not prev:
        return {"first_seen": True, "changed": False, "direction": "unchanged",
                "health_from": None, "health_to": curr["health"],
                "z_delta": None, "f_delta": None,
                "new_flag": False, "cleared_flag": False,
                "last_checked": None, "headline": "First time checking this holding."}

    h_from, h_to = prev.get("health"), curr["health"]
    new_flag = bool(curr["m_flag"]) and not bool(prev.get("m_flag"))
    cleared_flag = bool(prev.get("m_flag")) and not bool(curr["m_flag"])

    direction = "unchanged"
    if h_from in _HEALTH_RANK and h_to in _HEALTH_RANK and h_from != h_to:
        direction = ("deteriorated" if _HEALTH_RANK[h_to] < _HEALTH_RANK[h_from]
                     else "improved")
    elif new_flag:
        direction = "deteriorated"
    elif cleared_flag:
        direction = "improved"

    if direction == "deteriorated" and h_from != h_to:
        headline = f"Slipped from {h_from} to {h_to} since your last check."
    elif direction == "improved" and h_from != h_to:
        headline = f"Improved from {h_from} to {h_to} since your last check."
    elif new_flag:
        headline = "Beneish now flags possible earnings manipulation. That is new."
    elif cleared_flag:
        headline = "The earnings-manipulation flag has cleared since your last check."
    else:
        headline = "No change in verdict since your last check."

    return {
        "first_seen": False,
        "changed": direction != "unchanged",
        "direction": direction,
        "health_from": h_from, "health_to": h_to,
        "z_delta": _delta(curr["z"], prev.get("z")),
        "f_delta": _delta(curr["f_score"], prev.get("f_score")),
        "new_flag": new_flag, "cleared_flag": cleared_flag,
        "last_checked": prev.get("checked_at"),
        "headline": headline,
    }


def diff_portfolio(scored: List[dict], history: dict) -> List[dict]:
    """
    Attach a `delta` block to every scored holding. Pure: `history` is the loaded store,
    nothing is read from disk here. Returns new row dicts (inputs are not mutated).
    """
    tickers = (history or {}).get("tickers", {})
    out = []
    for row in scored:
        row = dict(row)
        row["delta"] = diff_holding(row, tickers.get(str(row.get("ticker", "")).upper()))
        out.append(row)
    return out


def changed_holdings(rows: List[dict]) -> dict:
    """Roll the deltas up: what moved since last check, worst news first."""
    deteriorated = [r for r in rows if (r.get("delta") or {}).get("direction") == "deteriorated"]
    improved = [r for r in rows if (r.get("delta") or {}).get("direction") == "improved"]
    first_seen = [r for r in rows if (r.get("delta") or {}).get("first_seen")]
    return {
        "deteriorated": deteriorated,
        "improved": improved,
        "n_deteriorated": len(deteriorated),
        "n_improved": len(improved),
        "n_first_seen": len(first_seen),
        "any_change": bool(deteriorated or improved),
    }


# ----------------------------------------------------------------------------
# The store (I/O only, no comparison logic)
# ----------------------------------------------------------------------------
def load_history(path: str = _DEFAULT_PATH) -> dict:
    """Load the prior-run store. A missing or corrupt file is an empty history, never an error."""
    try:
        with open(path) as fh:
            blob = json.load(fh)
    except (OSError, ValueError):
        return {"schema_version": SCHEMA_VERSION, "tickers": {}}
    if not isinstance(blob, dict) or "tickers" not in blob:
        return {"schema_version": SCHEMA_VERSION, "tickers": {}}
    return blob


def save_run(scored: List[dict], path: str = _DEFAULT_PATH, now: Optional[float] = None) -> dict:
    """
    Merge this run's scores into the store, stamping each ticker with when it was checked.
    Only scored rows are remembered: an unscored holding must not overwrite a good prior
    reading with nulls, or the next run would report a phantom improvement.
    """
    now = time.time() if now is None else now
    history = load_history(path)
    tickers = history.setdefault("tickers", {})
    for row in scored:
        if row.get("source") == "unscored":
            continue
        entry = snapshot_of(row)
        entry["checked_at"] = now
        tickers[str(row.get("ticker", "")).upper()] = entry
    history["schema_version"] = SCHEMA_VERSION
    history["last_run_at"] = now

    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(history, fh, indent=2)
    os.replace(tmp, path)          # atomic: a crash mid-write never corrupts history
    return history
