"""
report.py - THE CONTRACT. One documented company report, assembled once, rendered anywhere.

This is the hinge the whole product turns on. Today three views (single-company screen,
portfolio, M&A) each reach into models.py, commentary.py, and benchmark.py separately and
assemble their own shape. That is why the tool reads as three features bolted onto an
engine instead of one product. It is also what makes a frontend migration expensive.

build_report() assembles ALL of it exactly once:

    payload (data.fetch_live / PRESETS / manual)
        -> models.run_models   (the three scores + verdict)
        -> commentary.explain  (the deterministic why engine)
        -> benchmark           (where it lands vs sector peers)
        -> provenance          (which source, as of when)
        = one dict, schema_version stamped

Every consumer renders FROM this dict and never recomputes anything:
  * the company scorecard renders the whole report,
  * a portfolio row renders portfolio_row(report), a strict projection of it,
  * a future Next.js frontend consumes the same dict as JSON over HTTP.

Because a portfolio row is a projection of the same report the drill-down renders, the
two views can never disagree. That is the connective tissue: the portfolio is the front
door, the company scorecard is the drill-down, and both read one contract.

Pure and framework-free (no Streamlit, no network): FastAPI serves this verbatim later.
The report is JSON-serializable by construction, which is what makes that possible.
"""
from __future__ import annotations

from typing import List, Optional

from benchmark import METRICS, position, sector_stats, snapshot_as_of
from commentary import explain
from data import run_models
from models import leverage_ratio, merton_dd_pd, spring_score

SCHEMA_VERSION = "1.0"

# Which direction is "good" for each benchmarked metric. Stated here, in the contract,
# so no frontend has to know finance to color a percentile correctly. Leverage is the
# one where MORE is worse, which is exactly why it needed a sector median rather than a
# fixed cutoff: 60% liabilities-to-assets is ordinary for a utility and alarming for a
# software company.
HIGHER_IS_BETTER = {"leverage": False, "z": True, "f_score": True, "m_score": False}

SNAPSHOT_SOURCE = "S&P 500 snapshot (data/universe_snapshot.csv)"
# The snapshot predates the leverage benchmark and has no leverage column, so the
# fallback path can carry only these three.
SNAPSHOT_METRICS = ("z", "f_score", "m_score")

BENEISH_THRESHOLD = -1.78
PIOTROSKI_MAX = 9


def _score_block(applicable: bool, note: Optional[str], why: Optional[str], **fields) -> dict:
    """
    Every score reports the same envelope: is it applicable, what is the number, what
    drives it, and if it is N/A, plainly why. A model that cannot run (a bank has no
    working-capital split) is a first-class state here, not a missing key.
    """
    block = {"applicable": applicable, "why": why, "note": note}
    block.update(fields)
    return block


def _candidate_peer_sets(sector: Optional[str], peers: Optional[dict],
                         snapshot_rows: Optional[List[dict]]) -> List[dict]:
    """
    The peer sources to try, best first: the LIVE FMP set, then the committed snapshot.

    This ordering is the whole "retire the snapshot as the source of truth" change. The
    snapshot is not deleted and not ignored; it is demoted to the fallback that keeps the
    benchmark rendering when FMP cannot serve a sector, and it is labeled stale when it
    does serve, so a frozen 2026-06-19 median can never again be shown as if it were
    current.

    Each candidate carries the sector LABEL its own rows are tagged with, which is not
    always the company's label. sector_peers.py normalizes onto FMP's vocabulary, so its
    rows say "Financial Services" even when the company arrived from the snapshot tagged
    "Financials". Matching each row set against its own label is what stops that
    mismatch from silently emptying the peer group.
    """
    out: List[dict] = []
    if peers and peers.get("rows"):
        out.append({
            "rows": peers["rows"],
            "sector_label": peers.get("sector") or sector,
            "metrics": tuple(peers.get("metrics") or METRICS),
            "source": peers.get("source") or "FMP live sector peers",
            "as_of": peers.get("as_of"),
            "peer_count": peers.get("peer_count") or len(peers["rows"]),
            "stale": False,
        })
    if snapshot_rows:
        out.append({
            "rows": snapshot_rows,
            "sector_label": sector,
            "metrics": SNAPSHOT_METRICS,
            "source": SNAPSHOT_SOURCE,
            "as_of": snapshot_as_of(snapshot_rows),
            "peer_count": None,
            "stale": True,
        })
    return out


def _unbenchmarked(name: str, value, note: str) -> dict:
    """A metric with no peer source at all: still reports the company's own number."""
    return {"value": value, "median": None, "p25": None, "p75": None,
            "peer_count": 0, "thin": True, "percentile": None,
            "higher_is_better": HIGHER_IS_BETTER[name],
            "source": None, "as_of": None, "stale": None, "note": note}


def _benchmark_block(sector: Optional[str], ticker: Optional[str], own: dict,
                     peers: Optional[dict],
                     snapshot_rows: Optional[List[dict]]) -> dict:
    """
    Where this company lands against its sector peers, per metric, with peer counts and
    PER-METRIC provenance.

    Provenance is per metric, not per block, because the two paths do not cover the same
    metrics. On core depth the live set carries leverage and Z while F and M still come
    from the snapshot, and a single block-level "live" flag would quietly present a
    month-old Beneish median as current. Each metric therefore says which peer set
    answered it, as of when, and whether that set is stale.
    """
    candidates = _candidate_peer_sets(sector, peers, snapshot_rows)
    if not sector or not candidates:
        return {"sector": sector, "available": False, "metrics": {},
                "source": None, "as_of": None, "stale": None,
                "peer_count": None, "mixed_sources": False}

    stats_by_set = [
        (c, sector_stats(c["rows"], c["sector_label"], exclude_ticker=ticker))
        for c in candidates]

    metrics = {}
    used_sources = set()
    for name in METRICS:
        chosen = None
        first_supporting = None
        for c, stats in stats_by_set:
            if name not in c["metrics"]:
                continue
            stat = stats[name]
            if first_supporting is None:
                first_supporting = (c, stat)
            if not stat.thin:                      # first source with enough peers wins
                chosen = (c, stat)
                break
        chosen = chosen or first_supporting

        value = own.get(name)
        if chosen is None:
            metrics[name] = _unbenchmarked(
                name, value,
                "No live peer set for this sector, and the snapshot fallback carries no "
                "leverage column, so there is nothing honest to compare against.")
            continue

        c, stat = chosen
        used_sources.add(c["source"])
        metrics[name] = {
            "value": value,
            "median": stat.median,
            "p25": stat.p25,
            "p75": stat.p75,
            "peer_count": stat.count,
            "thin": stat.thin,                     # too few peers to benchmark honestly
            "percentile": None if stat.thin else position(value, stat.values),
            "higher_is_better": HIGHER_IS_BETTER[name],
            "source": c["source"],
            "as_of": c["as_of"],
            "stale": c["stale"],
            "note": None,
        }

    primary = candidates[0]
    return {"sector": sector, "available": True, "metrics": metrics,
            "source": primary["source"], "as_of": primary["as_of"],
            "stale": primary["stale"], "peer_count": primary["peer_count"],
            "mixed_sources": len(used_sources) > 1}


def _merton_face_debt(payload: dict) -> Optional[float]:
    """
    The default point F for Merton: the standard KMV convention is short-term debt plus
    half of long-term debt. current_liabilities is the available short-term-obligations
    proxy from the filings; when a company reports no current/non-current split (banks,
    insurers) fall back to total liabilities so the model still has a debt level to work
    from. Returns None when neither is present.
    """
    curr = payload.get("curr") or {}
    cl = curr.get("current_liabilities")
    ltd = curr.get("long_term_debt") or 0.0
    if cl is not None:
        return cl + 0.5 * ltd
    return curr.get("total_liabilities")


def _compute_merton(payload: dict):
    """
    Run Merton from a payload, or return (None, plain-English note) when an input the
    market-implied read needs is missing. The note is what the score block shows in
    place of a number, the same honest N/A the filing models use.
    """
    equity_value = payload.get("market_value_equity") or 0.0
    equity_vol = payload.get("equity_volatility")
    face_debt = _merton_face_debt(payload)

    if not equity_vol:
        return None, ("No equity-price history available, so there is no market-implied "
                      "volatility to read default risk from.")
    if not equity_value:
        return None, "No live market value of equity to imply an asset value from."
    if not face_debt:
        return None, "No reported debt, so there is no default point to measure against."
    try:
        return merton_dd_pd(equity_value, equity_vol, face_debt), None
    except ValueError as e:
        return None, str(e)


def _analyst_block(overlay: Optional[dict]) -> dict:
    """
    The analyst-consensus OVERLAY: a labeled, non-scored view of what the sell side thinks,
    kept strictly out of the deterministic scores (SCREENER-NORTH-STAR sec 5, Group A.3).
    Populated from FMP's premium analyst endpoints; on the free tier the fetch returns None
    and this degrades to available=False, so a view renders "not available" without any
    special-casing, and it lights up automatically once the FMP key is on a paid tier.
    """
    if not overlay:
        return {"available": False, "consensus": None, "price_target": None,
                "estimates": None, "source": None, "as_of": None}
    return {
        "available": True,
        "consensus": overlay.get("consensus"),
        "price_target": overlay.get("price_target"),
        "estimates": overlay.get("estimates"),
        "source": overlay.get("source"),
        "as_of": overlay.get("as_of"),
    }


def _provenance_block(meta: dict, benchmark_block: dict) -> dict:
    """
    Where every number came from and as of when. Live payloads carry a provenance block
    from data.fetch_live; presets and manual entry do not, so we synthesize an honest
    one rather than leaving the field absent.

    The peers entry used to be a hardcoded string naming the snapshot. It is now read off
    the benchmark block that was actually built, so it reports the live FMP peer set and
    its as-of when there is one, and says stale when the snapshot had to serve.
    """
    prov = meta.get("provenance")
    if prov:
        block = dict(prov)
    else:
        block = {
            "fundamentals": {"source": meta.get("source"),
                             "as_of": meta.get("period_curr"), "fetched_at": None},
            "price": {"source": None, "as_of": None, "value": None},
            "equity_volatility": {"source": None, "as_of": None,
                                  "value": None, "window": None},
        }
    block["peers"] = {
        "source": benchmark_block.get("source"),
        "as_of": benchmark_block.get("as_of"),
        "stale": benchmark_block.get("stale"),
        "peer_count": benchmark_block.get("peer_count"),
        "mixed_sources": benchmark_block.get("mixed_sources", False),
    }
    return block


def build_report(payload: dict, snapshot_rows: Optional[List[dict]] = None,
                 run_models_fn=None, peers: Optional[dict] = None) -> dict:
    """
    Assemble the one company report. `payload` is any data.py payload (live, preset, or
    manual). `run_models_fn` is injectable so callers (and tests) can stub the scoring
    pass without a network or a monkeypatch.

    Two peer sources, tried in that order:
      `peers`          - a LIVE FMP sector peer set from sector_peers.cached_peer_set().
                         Preferred, and stamped with the timestamp it was pulled.
      `snapshot_rows`  - benchmark.load_universe() output, the committed S&P 500 file.
                         The fallback, labeled stale wherever it is used.
    Pass neither to skip the sector benchmark entirely (the rest of the report is
    unaffected).

    This function stays network-free by design: the caller resolves the peer set (the
    server does it off the request path) and hands the rows in, exactly the way
    snapshot_rows has always worked. That is what keeps report.py pure and keeps a cold
    sector build from ever landing inside a page render.

    Returns a JSON-serializable dict. See the module docstring for the shape, and
    tests/test_report.py for the contract that pins it.
    """
    runner = run_models_fn or run_models
    altman, piotroski, beneish, verdict, notes = runner(payload)

    meta = payload.get("meta", {})
    ticker = meta.get("ticker")
    sector = meta.get("sector")

    z = altman.z if altman else None
    f_score = piotroski.score if piotroski else None
    m_score = beneish.m if beneish else None

    # Merton: the market-implied, dynamic default signal. Computed from the payload's
    # live equity value and volatility plus the filing debt level; degrades to a
    # first-class N/A block (with a plain note) whenever a market input is missing,
    # which is the common case for presets and manual entry.
    merton, merton_note = _compute_merton(payload)
    pd_merton = merton.pd if merton else None

    # The composite headline. Built from the same numbers the score blocks carry plus
    # the payload's own line items and the Merton PD, so it can never disagree with
    # them. When too little is available (spring_score's minimum-weight rule) it
    # degrades to a first-class N/A block, exactly like any single model.
    try:
        spring = spring_score(z=z, f_score=f_score, m_score=m_score,
                              curr=payload.get("curr"), prior=payload.get("prior"),
                              pd_merton=pd_merton)
        spring_note = None
    except ValueError as e:
        spring, spring_note = None, str(e)

    why = explain(altman, piotroski, beneish, verdict, spring=spring, merton=merton)

    # Leverage: not a model score, but the metric both professors singled out, and the
    # one the benchmark exists to put in context. Computed with models.leverage_ratio,
    # the same function sector_peers.py applies to every peer.
    leverage = leverage_ratio(payload.get("curr"))
    benchmark_block = _benchmark_block(
        sector, ticker,
        {"leverage": leverage, "z": z, "f_score": f_score, "m_score": m_score},
        peers, snapshot_rows)

    return {
        "schema_version": SCHEMA_VERSION,
        "company": {
            "name": meta.get("name"),
            "ticker": ticker,
            "sector": sector,
            "is_financial": bool(meta.get("is_financial")),
            # Total liabilities / total assets. Carried here as well as inside the
            # benchmark block so a view can show the raw number without reaching into
            # the peer comparison, and so it survives when there is no peer set at all.
            "leverage": leverage,
        },
        "verdict": {
            "health": verdict["health"],            # Healthy | Watch | Distressed | Unknown
            "integrity": verdict["integrity"],      # Clean | Possible manipulation | Not enough data
            "summary": why["overall"],              # the one plain-English line
        },
        "scores": {
            "spring": _score_block(
                applicable=spring is not None,
                note=spring_note,
                why=why["spring"],
                value=spring.score if spring else None,
                tier=spring.tier if spring else None,
                components=spring.components if spring else {},
                coverage=spring.coverage if spring else None,
            ),
            "altman": _score_block(
                applicable=altman is not None,
                note=notes.get("Altman Z-Score"),
                why=why["altman"],
                value=z, zone=altman.zone if altman else None,
                components=altman.components if altman else {},
            ),
            "piotroski": _score_block(
                applicable=piotroski is not None,
                note=notes.get("Piotroski F-Score"),
                why=why["piotroski"],
                value=f_score, max=PIOTROSKI_MAX,
                signals=piotroski.signals if piotroski else {},
            ),
            "beneish": _score_block(
                applicable=beneish is not None,
                note=notes.get("Beneish M-Score"),
                why=why["beneish"],
                value=m_score, flag=bool(beneish.flag) if beneish else False,
                threshold=BENEISH_THRESHOLD,
                indices=beneish.indices if beneish else {},
            ),
            # Market-implied, dynamic default signal (Merton). value is the 1-year
            # probability of default (0-1); dd is the distance to default.
            "merton": _score_block(
                applicable=merton is not None,
                note=merton_note,
                why=why["merton"],
                value=merton.pd if merton else None,
                dd=merton.dd if merton else None,
                label=merton.label if merton else None,
                asset_vol=merton.asset_vol if merton else None,
                equity_vol=merton.equity_vol if merton else None,
                leverage=merton.leverage if merton else None,
                face_debt=merton.face_debt if merton else None,
                risk_free=merton.risk_free if merton else None,
                horizon_years=merton.horizon_years if merton else None,
                components=merton.components if merton else {},
            ),
        },
        "benchmark": benchmark_block,
        # Labeled analyst overlay, never mixed into the scores (Group A.3). Degrades to
        # available=False until the FMP key is on a paid tier.
        "analyst": _analyst_block(payload.get("analyst")),
        "provenance": _provenance_block(meta, benchmark_block),
        "periods": {"current": meta.get("period_curr"), "prior": meta.get("period_prior")},
    }


def portfolio_row(report: dict, shares: Optional[float] = None,
                  source: str = "live") -> dict:
    """
    Project a full report down to the compact row the portfolio table shows.

    This is a STRICT projection: every field is read from the report, nothing is
    recomputed. That is what guarantees the portfolio row and the drill-down scorecard
    can never disagree about a holding. Click a row, build_report() the same ticker,
    and you are looking at the same numbers by construction.

    Two different fields, deliberately:
      source      - WHICH PATH produced this row: "live" | "snapshot" | "unscored".
                    rank_portfolio filters on it.
      data_source - WHERE THE NUMBERS CAME FROM ("SEC EDGAR (companyfacts)", the S&P
                    snapshot, ...) plus its as-of. A snapshot row and a live row can
                    legitimately differ, so the row carries its own provenance and the
                    UI can say so instead of the difference being silent.
    """
    from portfolio import _worst_signal        # local import: report is imported by portfolio

    scores = report["scores"]
    z = scores["altman"]["value"]
    zone = scores["altman"]["zone"]
    f_score = scores["piotroski"]["value"]
    m_score = scores["beneish"]["value"]
    m_flag = scores["beneish"]["flag"]
    fundamentals = report["provenance"]["fundamentals"] or {}

    return {
        "ticker": report["company"]["ticker"],
        "name": report["company"]["name"],
        "sector": report["company"]["sector"],
        "spring_score": scores["spring"]["value"],
        "spring_tier": scores["spring"]["tier"],
        "z": z, "zone": zone,
        "f_score": f_score, "m_score": m_score, "m_flag": m_flag,
        "verdict": {"health": report["verdict"]["health"],
                    "integrity": report["verdict"]["integrity"]},
        "source": source,
        "data_source": {"source": fundamentals.get("source"),
                        "as_of": fundamentals.get("as_of")},
        "weight": shares,
        "worst_signal": _worst_signal(z, zone, f_score, m_flag),
        "unscored_reason": None,
    }
