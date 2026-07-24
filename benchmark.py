"""
benchmark.py: Sector benchmarking stats.

Given the screened company's scores, this module answers a single question:
"how does this land versus its sector peers?" It is PURE and testable in the same
spirit as models.py — the stats functions take explicit rows (a list of dicts) and
return numbers, with no network and no Streamlit.

WHERE THE ROWS COME FROM (changed 2026-07-24): the peer rows are now built LIVE from
FMP by sector_peers.py, and the committed S&P 500 snapshot is the FALLBACK rather than
the source of truth. Nothing in this file cares which one it got: both paths hand in the
same row shape, which is what makes the fallback a one-line decision in report.py rather
than a second code path. load_universe() still reads the snapshot, because the snapshot
is still the fallback and still backs the portfolio fast path.

Robustness rules:
  * Use MEDIAN and quartiles (p25 / p75), never the mean — one outlier shouldn't
    move the benchmark.
  * Compute over NON-NULL peers only, and always EXCLUDE the screened company itself.
  * If a sector has fewer than MIN_PEERS peers with data for a metric, that metric is
    marked "too thin to benchmark" rather than shown as a number. This is exactly what
    happens to Z and M for financials (banks/insurers don't report the inputs), so the
    UI degrades honestly instead of comparing against three lonely data points. It is
    also what happens to leverage on the snapshot path, which carries no leverage column.
"""
from __future__ import annotations
import csv
import os
from dataclasses import dataclass, field
from typing import List, Optional

# Below this many valid peers, a sector metric is too thin to benchmark against.
MIN_PEERS = 8

# Metrics we benchmark. `leverage` (total liabilities / total assets) is the one the
# advisors actually asked for and the one the old snapshot could never answer: it has no
# leverage column, so on the fallback path leverage reports thin rather than a number.
METRICS = ("leverage", "z", "f_score", "m_score")

# Numeric columns (snapshot cells, and the live peer rows sector_peers.py builds).
_NUMERIC_COLS = ("z", "f_score", "m_score", "leverage",
                 "price_to_book", "ev_ebitda", "market_cap")

_DEFAULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "data", "universe_snapshot.csv")


# ----------------------------------------------------------------------------
# Snapshot loading (csv stdlib — handles quoted company names with commas)
# ----------------------------------------------------------------------------
def load_universe(path: str = _DEFAULT_PATH) -> List[dict]:
    """
    Read the peer snapshot into a list of dicts. Numeric columns are parsed to float
    or None (blank cells become None so they're naturally excluded from the stats).
    """
    rows: List[dict] = []
    with open(path, newline="", encoding="utf-8") as fh:
        for raw in csv.DictReader(fh):
            row = dict(raw)
            for col in _NUMERIC_COLS:
                row[col] = _to_float(row.get(col))
            rows.append(row)
    return rows


def _to_float(v) -> Optional[float]:
    """Parse a cell to float; blanks, None, and unparseable/NaN values become None."""
    if v is None:
        return None
    s = str(v).strip()
    if s == "":
        return None
    try:
        f = float(s)
    except (TypeError, ValueError):
        return None
    return None if f != f else f          # drop NaN


def snapshot_as_of(rows: Optional[List[dict]]) -> Optional[str]:
    """
    The newest as_of_date in the snapshot rows. This is what lets the fallback path say
    "these peer medians are from 2026-06-19" instead of presenting frozen numbers as if
    they were current.
    """
    dates = sorted({str(r.get("as_of_date") or "").strip() for r in (rows or [])} - {""})
    return dates[-1] if dates else None


def lookup_sector(rows: List[dict], ticker: str) -> Optional[str]:
    """Find a ticker's sector in the snapshot (used as a fallback when .info has none)."""
    if not ticker:
        return None
    want = str(ticker).strip().upper()
    for r in rows:
        if str(r.get("tr", "")).strip().upper() == want:
            return r.get("sector") or None
    return None


# ----------------------------------------------------------------------------
# Stats
# ----------------------------------------------------------------------------
@dataclass
class MetricStat:
    metric: str
    count: int                          # number of valid (non-null) peers
    thin: bool                          # True => fewer than MIN_PEERS, don't benchmark
    median: Optional[float] = None
    p25: Optional[float] = None
    p75: Optional[float] = None
    values: List[float] = field(default_factory=list)   # clean peer values (for position())


def _valid_values(rows: List[dict], sector: str, metric: str,
                  exclude_ticker: Optional[str]) -> List[float]:
    """All non-null values of `metric` for peers in `sector`, minus the screened company."""
    skip = str(exclude_ticker).strip().upper() if exclude_ticker else None
    out: List[float] = []
    for r in rows:
        if r.get("sector") != sector:
            continue
        if skip and str(r.get("tr", "")).strip().upper() == skip:
            continue
        v = _to_float(r.get(metric))
        if v is not None:
            out.append(v)
    return out


def _quantile(sorted_vals: List[float], q: float) -> Optional[float]:
    """Linear-interpolated quantile (same convention as numpy's default)."""
    n = len(sorted_vals)
    if n == 0:
        return None
    if n == 1:
        return sorted_vals[0]
    pos = q * (n - 1)
    lo = int(pos)
    frac = pos - lo
    if lo + 1 < n:
        return sorted_vals[lo] + frac * (sorted_vals[lo + 1] - sorted_vals[lo])
    return sorted_vals[lo]


def sector_stats(rows: List[dict], sector: Optional[str],
                 exclude_ticker: Optional[str] = None) -> dict:
    """
    For each of Z / F / M, summarise the sector's peers with median, p25, p75 and a
    peer count. Metrics with fewer than MIN_PEERS valid peers are flagged thin (their
    quartiles are left None). Returns {metric: MetricStat}.
    """
    out = {}
    for m in METRICS:
        vals = sorted(_valid_values(rows, sector, m, exclude_ticker)) if sector else []
        n = len(vals)
        if n < MIN_PEERS:
            out[m] = MetricStat(metric=m, count=n, thin=True, values=vals)
        else:
            out[m] = MetricStat(
                metric=m, count=n, thin=False,
                median=_quantile(vals, 0.50),
                p25=_quantile(vals, 0.25),
                p75=_quantile(vals, 0.75),
                values=vals,
            )
    return out


def position(value: Optional[float], peer_values: List[float]) -> Optional[float]:
    """
    The company's percentile rank (0–100) within its sector: the share of peers it
    scores at or above, counting ties at half weight. Returns None if there's no value
    or no peers. Direction-neutral — whether a high rank is good (Z, F) or bad (M) is a
    presentation concern, handled in the render layer.
    """
    if value is None or not peer_values:
        return None
    below = sum(1 for v in peer_values if v < value)
    equal = sum(1 for v in peer_values if v == value)
    return 100.0 * (below + 0.5 * equal) / len(peer_values)
