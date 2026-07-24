"""
Contract tests for report.build_report - the one schema every frontend renders from.

These pin the shape. If a future change breaks them, it breaks every consumer (the
Streamlit views today, the FastAPI/Next.js frontend later), which is exactly what a
contract test is for.

Run:  python3 tests/test_report.py
"""
import copy
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from benchmark import load_universe
from data import PRESETS
from report import SCHEMA_VERSION, SNAPSHOT_SOURCE, build_report, portfolio_row

passed = 0


def check(name, cond):
    global passed
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    assert cond, f"FAILED: {name}"
    passed += 1


HEALTHY = PRESETS["Bluechip Industries (sample: healthy)"]
FLAGGED = PRESETS["Momentum Software Co. (sample: earnings red flags)"]

print("TOP-LEVEL SHAPE")
r = build_report(HEALTHY)
check("schema_version is stamped", r["schema_version"] == SCHEMA_VERSION)
check("has the top-level sections",
      set(r) == {"schema_version", "company", "verdict", "scores", "benchmark",
                 "analyst", "provenance", "periods"})
check("analyst overlay degrades to not-available for a non-live payload",
      r["analyst"]["available"] is False
      and set(r["analyst"]) == {"available", "consensus", "price_target",
                                "estimates", "source", "as_of"})
check("company block carries identity and the leverage read",
      set(r["company"]) == {"name", "ticker", "sector", "is_financial", "leverage"})
check("verdict carries health, integrity, and a plain-English summary",
      set(r["verdict"]) == {"health", "integrity", "summary"} and r["verdict"]["summary"])
check("periods carry current and prior", set(r["periods"]) == {"current", "prior"})

print("THE REPORT IS JSON-SERIALIZABLE (this is what lets FastAPI serve it verbatim)")
blob = json.dumps(r)
check("json.dumps round-trips the whole report", json.loads(blob)["schema_version"] == SCHEMA_VERSION)

print("SCORE BLOCKS: every model reports the same envelope")
for model in ("altman", "piotroski", "beneish"):
    blk = r["scores"][model]
    check(f"{model} has applicable/why/note/value",
          {"applicable", "why", "note", "value"} <= set(blk))
check("altman carries zone and components",
      r["scores"]["altman"]["zone"] in ("Safe", "Grey", "Distress")
      and len(r["scores"]["altman"]["components"]) == 5)
check("piotroski carries max=9 and 9 signals",
      r["scores"]["piotroski"]["max"] == 9 and len(r["scores"]["piotroski"]["signals"]) == 9)
check("beneish carries the -1.78 threshold and 8 indices",
      r["scores"]["beneish"]["threshold"] == -1.78
      and len(r["scores"]["beneish"]["indices"]) == 8)
check("healthy sample is not flagged", r["scores"]["beneish"]["flag"] is False)
check("every applicable score has a why sentence",
      all(r["scores"][m]["why"] for m in ("altman", "piotroski", "beneish")))

print("SPRING SCORE: the composite headline rides the same contract")
sp = r["scores"]["spring"]
check("spring has the standard envelope plus tier/components/coverage",
      {"applicable", "why", "note", "value", "tier", "components", "coverage"} <= set(sp))
# A preset has no live price history, so the Merton ingredient is absent and coverage
# is 100 of 115 weight points, not full. That is the honest degradation, not a miss.
check("fundamentals-only sample covers 100 of 115 weight points",
      sp["applicable"] is True and sp["coverage"] == round(100 / 115, 2))
check("spring value is an int in 0-100",
      isinstance(sp["value"], int) and 0 <= sp["value"] <= 100)
check("spring tier is one of the five",
      sp["tier"] in ("Excellent", "Strong", "Fair", "Weak", "Fragile"))
check("spring carries all seven components",
      set(sp["components"]) == {"altman", "piotroski", "beneish", "accruals",
                                "margin_trend", "leverage_trend", "merton"})
check("spring has a why sentence naming itself", "Spring Score" in sp["why"])

print("MERTON: the market-implied block rides the contract, N/A without a live price feed")
mn = r["scores"]["merton"]
check("merton has the standard envelope plus its fields",
      {"applicable", "why", "note", "value", "dd", "label",
       "asset_vol", "equity_vol", "leverage", "face_debt"} <= set(mn))
check("a preset has no market data, so merton is a first-class N/A with a note",
      mn["applicable"] is False and mn["value"] is None and bool(mn["note"]))

print("A FLAGGED COMPANY surfaces the flag through the contract")
rf = build_report(FLAGGED)
check("beneish flag is True", rf["scores"]["beneish"]["flag"] is True)
check("integrity reads possible manipulation",
      rf["verdict"]["integrity"] == "Possible manipulation")

print("N/A MODELS are a first-class state, not a missing key")
bank = copy.deepcopy(HEALTHY)
for yr in ("curr", "prior"):
    for k in ("current_assets", "current_liabilities", "cogs", "receivables"):
        bank[yr][k] = None
rb = build_report(bank)
for model in ("altman", "piotroski", "beneish"):
    blk = rb["scores"][model]
    check(f"{model} is applicable=False with a plain reason",
          blk["applicable"] is False and blk["note"] and blk["why"] is None)
check("verdict degrades to Unknown, not a crash", rb["verdict"]["health"] == "Unknown")
check("overall summary still written", bool(rb["verdict"]["summary"]))
check("spring degrades to N/A with a plain reason when the backbone is gone",
      rb["scores"]["spring"]["applicable"] is False
      and rb["scores"]["spring"]["note"] and rb["scores"]["spring"]["why"] is None)

print("BENCHMARK: absent without peers, present with them")
check("no snapshot means benchmark unavailable", r["benchmark"]["available"] is False)
check("company leverage is computed even with no peer set at all",
      abs(r["company"]["leverage"] - (2400 / 6000)) < 1e-9)

rows = load_universe()
payload = copy.deepcopy(HEALTHY)
payload["meta"]["sector"] = "Technology"
payload["meta"]["ticker"] = "SAMPLE"
rbm = build_report(payload, rows)
check("with a sector and peers, benchmark is available", rbm["benchmark"]["available"] is True)
check("benchmark covers leverage, z, f_score, m_score",
      set(rbm["benchmark"]["metrics"]) == {"leverage", "z", "f_score", "m_score"})
zm = rbm["benchmark"]["metrics"]["z"]
check("z metric carries median/p25/p75/peer_count/percentile",
      {"median", "p25", "p75", "peer_count", "percentile", "thin"} <= set(zm))
check("every metric carries its own source, as-of and stale flag",
      all({"source", "as_of", "stale", "note"} <= set(m)
          for m in rbm["benchmark"]["metrics"].values()))
check("contract states direction so a frontend needs no finance knowledge",
      rbm["benchmark"]["metrics"]["z"]["higher_is_better"] is True
      and rbm["benchmark"]["metrics"]["m_score"]["higher_is_better"] is False)
check("leverage is the metric where LOWER is better",
      rbm["benchmark"]["metrics"]["leverage"]["higher_is_better"] is False)
check("percentile computed for a non-thin metric",
      zm["thin"] or isinstance(zm["percentile"], float))

print("SNAPSHOT FALLBACK is labeled STALE, never presented as current")
check("snapshot-served metric is flagged stale", zm["stale"] is True)
check("snapshot-served metric names the snapshot file",
      "snapshot" in (zm["source"] or "").lower())
check("snapshot-served metric carries the snapshot's own as-of date",
      zm["as_of"] == "2026-06-19")
check("block-level source/as_of/stale mirror the primary peer source",
      rbm["benchmark"]["stale"] is True
      and "snapshot" in (rbm["benchmark"]["source"] or "").lower())
lev = rbm["benchmark"]["metrics"]["leverage"]
check("leverage cannot be benchmarked from the snapshot (no such column)",
      lev["thin"] is True and lev["median"] is None and lev["peer_count"] == 0)
check("and it says so in a plain-English note rather than showing a blank",
      "leverage column" in (lev["note"] or ""))
check("but the company's own leverage number is still reported",
      abs(lev["value"] - (2400 / 6000)) < 1e-9)

print("LIVE FMP PEERS take precedence over the snapshot and are NOT stale")
live_peers = {
    "rows": [{"tr": f"P{i}", "sector": "Technology", "leverage": 0.30 + i / 100.0,
              "z": 3.0 + i, "f_score": 5.0, "m_score": -2.5} for i in range(12)],
    "metrics": ["leverage", "z", "f_score", "m_score"],
    "source": "FMP live sector peers",
    "as_of": "2026-07-24T12:00:00+00:00",
    "peer_count": 12,
}
rlive = build_report(payload, rows, peers=live_peers)
lm = rlive["benchmark"]["metrics"]
check("live peers serve z, not the snapshot",
      lm["z"]["source"] == "FMP live sector peers" and lm["z"]["stale"] is False)
check("live peers make leverage benchmarkable at last",
      lm["leverage"]["thin"] is False and lm["leverage"]["median"] is not None)
check("live leverage median is the median of the peer rows",
      abs(lm["leverage"]["median"] - 0.355) < 1e-9)
check("live as-of is the pull timestamp, so the UI can show current conditions",
      lm["leverage"]["as_of"] == "2026-07-24T12:00:00+00:00")
check("block reports the live source and peer count",
      rlive["benchmark"]["peer_count"] == 12
      and rlive["benchmark"]["stale"] is False)
check("nothing is mixed when the live set covers every metric",
      rlive["benchmark"]["mixed_sources"] is False)

print("SECTOR LABEL MISMATCH: live rows use FMP's vocabulary, the company may not")
# A company classified from the snapshot arrives tagged "Financials"; sector_peers.py
# normalizes onto FMP's "Financial Services". Matching each row set against its OWN label
# is what stops that from silently emptying the peer group.
gics = copy.deepcopy(payload)
gics["meta"]["sector"] = "Financials"
fin_peers = dict(live_peers)
fin_peers["sector"] = "Financial Services"
fin_peers["rows"] = [dict(r, sector="Financial Services") for r in live_peers["rows"]]
rgics = build_report(gics, rows, peers=fin_peers)
check("the live peer set still serves despite the label mismatch",
      rgics["benchmark"]["metrics"]["z"]["stale"] is False
      and rgics["benchmark"]["metrics"]["z"]["peer_count"] == 12)
check("the company keeps its own sector label in the report",
      rgics["company"]["sector"] == "Financials")

print("CORE DEPTH: live leverage and Z, snapshot F and M, each labeled per metric")
core_peers = dict(live_peers)
core_peers["metrics"] = ["leverage", "z"]
rcore = build_report(payload, rows, peers=core_peers)
cm = rcore["benchmark"]["metrics"]
check("leverage and z come from the live set", cm["leverage"]["stale"] is False
      and cm["z"]["stale"] is False)
check("f_score and m_score fall back to the snapshot, flagged stale",
      cm["f_score"]["stale"] is True and cm["m_score"]["stale"] is True)
check("the block declares that its metrics came from more than one source",
      rcore["benchmark"]["mixed_sources"] is True)

print("A THIN LIVE SET falls through to the snapshot rather than showing 3 lonely peers")
thin_peers = dict(live_peers)
thin_peers["rows"] = live_peers["rows"][:3]
rthin = build_report(payload, rows, peers=thin_peers)
check("z falls back to the snapshot when the live set is under MIN_PEERS",
      rthin["benchmark"]["metrics"]["z"]["source"] == SNAPSHOT_SOURCE)
check("leverage has no fallback to fall to, so it reports thin honestly",
      rthin["benchmark"]["metrics"]["leverage"]["thin"] is True)

print("PROVENANCE is always present, even for presets (synthesized honestly)")
check("provenance has fundamentals, price, peers",
      {"fundamentals", "price", "peers"} <= set(r["provenance"]))
check("preset fundamentals source names the sample data",
      "sample" in (r["provenance"]["fundamentals"]["source"] or "").lower())
check("peers provenance reports the live source, as-of and count",
      rlive["provenance"]["peers"] == {"source": "FMP live sector peers",
                                       "as_of": "2026-07-24T12:00:00+00:00",
                                       "stale": False, "peer_count": 12,
                                       "mixed_sources": False})
check("peers provenance says stale when the snapshot served",
      rbm["provenance"]["peers"]["stale"] is True)

print("PORTFOLIO ROW is a strict projection of the same report")
row = portfolio_row(rbm, shares=25)
check("row ticker matches report", row["ticker"] == rbm["company"]["ticker"])
check("row z matches report z", row["z"] == rbm["scores"]["altman"]["value"])
check("row f_score matches report", row["f_score"] == rbm["scores"]["piotroski"]["value"])
check("row m_flag matches report", row["m_flag"] == rbm["scores"]["beneish"]["flag"])
check("row health matches report", row["verdict"]["health"] == rbm["verdict"]["health"])
check("row carries shares as weight", row["weight"] == 25)
check("row has a worst_signal line", bool(row["worst_signal"]))
check("row spring_score matches report", row["spring_score"] == rbm["scores"]["spring"]["value"])
check("row shape matches what rank_portfolio consumes",
      {"ticker", "name", "sector", "spring_score", "spring_tier", "z", "zone",
       "f_score", "m_score", "m_flag", "verdict", "source", "data_source", "weight",
       "worst_signal", "unscored_reason"} == set(row))
check("source names the PATH so rank_portfolio can filter", row["source"] == "live")
check("data_source names WHERE the numbers came from, with an as-of",
      set(row["data_source"]) == {"source", "as_of"})

print(f"\n{passed} checks passed.")
