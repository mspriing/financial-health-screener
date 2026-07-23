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

from benchmark import position, sector_stats
from commentary import explain
from data import run_models
from models import merton_dd_pd, spring_score

SCHEMA_VERSION = "1.0"

# Which direction is "good" for each benchmarked metric. Stated here, in the contract,
# so no frontend has to know finance to color a percentile correctly.
HIGHER_IS_BETTER = {"z": True, "f_score": True, "m_score": False}

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


def _benchmark_block(sector: Optional[str], ticker: Optional[str],
                     z, f_score, m_score, snapshot_rows: Optional[List[dict]]) -> dict:
    """Where this company lands against its sector peers, per metric, with peer counts."""
    if not sector or not snapshot_rows:
        return {"sector": sector, "available": False, "metrics": {}}

    stats = sector_stats(snapshot_rows, sector, exclude_ticker=ticker)
    own = {"z": z, "f_score": f_score, "m_score": m_score}
    metrics = {}
    for name, stat in stats.items():
        value = own.get(name)
        metrics[name] = {
            "value": value,
            "median": stat.median,
            "p25": stat.p25,
            "p75": stat.p75,
            "peer_count": stat.count,
            "thin": stat.thin,                     # too few peers to benchmark honestly
            "percentile": None if stat.thin else position(value, stat.values),
            "higher_is_better": HIGHER_IS_BETTER[name],
        }
    return {"sector": sector, "available": True, "metrics": metrics}


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


def _provenance_block(meta: dict) -> dict:
    """
    Where every number came from and as of when. Live payloads carry a provenance block
    from data.fetch_live; presets and manual entry do not, so we synthesize an honest
    one rather than leaving the field absent.
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
    block["peers"] = {"source": "S&P 500 snapshot (data/universe_snapshot.csv)"}
    return block


def build_report(payload: dict, snapshot_rows: Optional[List[dict]] = None,
                 run_models_fn=None) -> dict:
    """
    Assemble the one company report. `payload` is any data.py payload (live, preset, or
    manual). `snapshot_rows` is benchmark.load_universe() output; omit it to skip the
    sector benchmark (the rest of the report is unaffected). `run_models_fn` is injectable
    so callers (and tests) can stub the scoring pass without a network or a monkeypatch.

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

    return {
        "schema_version": SCHEMA_VERSION,
        "company": {
            "name": meta.get("name"),
            "ticker": ticker,
            "sector": sector,
            "is_financial": bool(meta.get("is_financial")),
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
        "benchmark": _benchmark_block(sector, ticker, z, f_score, m_score, snapshot_rows),
        "provenance": _provenance_block(meta),
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
