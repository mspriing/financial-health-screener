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
from report import SCHEMA_VERSION, build_report, portfolio_row

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
check("has the five top-level sections",
      set(r) == {"schema_version", "company", "verdict", "scores", "benchmark",
                 "provenance", "periods"})
check("company block carries identity",
      set(r["company"]) == {"name", "ticker", "sector", "is_financial"})
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
check("healthy sample gets a full-coverage composite",
      sp["applicable"] is True and sp["coverage"] == 1.0)
check("spring value is an int in 0-100",
      isinstance(sp["value"], int) and 0 <= sp["value"] <= 100)
check("spring tier is one of the five",
      sp["tier"] in ("Excellent", "Strong", "Fair", "Weak", "Fragile"))
check("spring carries all six components",
      set(sp["components"]) == {"altman", "piotroski", "beneish", "accruals",
                                "margin_trend", "leverage_trend"})
check("spring has a why sentence naming itself", "Spring Score" in sp["why"])

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

rows = load_universe()
payload = copy.deepcopy(HEALTHY)
payload["meta"]["sector"] = "Technology"
payload["meta"]["ticker"] = "SAMPLE"
rbm = build_report(payload, rows)
check("with a sector and peers, benchmark is available", rbm["benchmark"]["available"] is True)
check("benchmark covers z, f_score, m_score",
      set(rbm["benchmark"]["metrics"]) == {"z", "f_score", "m_score"})
zm = rbm["benchmark"]["metrics"]["z"]
check("z metric carries median/p25/p75/peer_count/percentile",
      {"median", "p25", "p75", "peer_count", "percentile", "thin"} <= set(zm))
check("contract states direction so a frontend needs no finance knowledge",
      rbm["benchmark"]["metrics"]["z"]["higher_is_better"] is True
      and rbm["benchmark"]["metrics"]["m_score"]["higher_is_better"] is False)
check("percentile computed for a non-thin metric",
      zm["thin"] or isinstance(zm["percentile"], float))

print("PROVENANCE is always present, even for presets (synthesized honestly)")
check("provenance has fundamentals, price, peers",
      {"fundamentals", "price", "peers"} <= set(r["provenance"]))
check("preset fundamentals source names the sample data",
      "sample" in (r["provenance"]["fundamentals"]["source"] or "").lower())

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
