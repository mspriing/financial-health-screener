"""
Tests for the connective logic: sector concentration and the "changed since last check"
monitoring loop.

Run:  python3 tests/test_connective.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from history import (changed_holdings, diff_holding, diff_portfolio, load_history,
                     save_run, snapshot_of)
from portfolio import CONCENTRATION_ALERT_PCT, rank_portfolio, sector_concentration

passed = 0


def check(name, cond):
    global passed
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    assert cond, f"FAILED: {name}"
    passed += 1


def row(ticker, health="Healthy", sector="Technology", z=4.0, f=8, m=-2.5,
        m_flag=False, source="live"):
    return {"ticker": ticker, "name": ticker, "sector": sector, "z": z, "zone": "Safe",
            "f_score": f, "m_score": m, "m_flag": m_flag, "source": source,
            "weight": None, "worst_signal": "", "unscored_reason": None,
            "verdict": {"health": health, "integrity":
                        "Possible manipulation" if m_flag else "Clean"}}


print("SECTOR CONCENTRATION")
all_tech = [row("AAPL"), row("MSFT"), row("NVDA")]
c = sector_concentration(all_tech)
check("single-sector portfolio is 100%", c["top_pct"] == 100.0)
check("single-sector flagged as concentrated", c["concentrated"] is True)
check("basis defaults to position count", c["basis"] == "position count")
check("headline names the sector", "Technology" in c["headline"])

mixed = [row("AAPL"), row("MSFT"), row("KO", sector="Consumer Defensive"),
         row("JPM", sector="Financial Services")]
c2 = sector_concentration(mixed)
check("mixed portfolio spreads across 3 sectors", len(c2["sectors"]) == 3)
check("top sector is Technology at 50%", c2["top_sector"] == "Technology" and c2["top_pct"] == 50.0)
check("50% clears the alert threshold", c2["concentrated"] is (50.0 >= CONCENTRATION_ALERT_PCT))
check("sectors sorted by weight descending",
      [s["pct"] for s in c2["sectors"]] == sorted([s["pct"] for s in c2["sectors"]], reverse=True))

diversified = [row(t, sector=s) for t, s in
               [("A", "Technology"), ("B", "Healthcare"), ("C", "Energy"),
                ("D", "Utilities"), ("E", "Industrials")]]
c3 = sector_concentration(diversified)
check("evenly spread portfolio is not flagged", c3["concentrated"] is False)
check("headline reports the spread", "5 sectors" in c3["headline"])

print("CONCENTRATION IS VALUE-WEIGHTED WHEN VALUES ARE SUPPLIED")
vals = {"AAPL": 90000.0, "MSFT": 5000.0, "KO": 2500.0, "JPM": 2500.0}
c4 = sector_concentration(mixed, value_by_ticker=vals)
check("basis switches to market value", c4["basis"] == "market value")
check("value weighting puts Technology at 95%", c4["top_pct"] == 95.0)
check("value-weighted headline says so", "market value" in c4["headline"])
partial = {"AAPL": 100.0}
check("partial values fall back to count basis, never a silent mix",
      sector_concentration(mixed, value_by_ticker=partial)["basis"] == "position count")

print("UNSCORED HOLDINGS still count toward concentration, bucketed as Unknown")
with_unknown = [row("AAPL"), {"ticker": "XYZ", "sector": None, "source": "unscored",
                              "verdict": {"health": "Unknown"}, "m_flag": False}]
c5 = sector_concentration(with_unknown)
check("Unknown bucket appears", any(s["sector"] == "Unknown" for s in c5["sectors"]))
check("an Unknown top sector never trips the alert",
      sector_concentration([with_unknown[1]])["concentrated"] is False)

print("DELTA: first sighting is not a change")
d = diff_holding(row("AAPL"), None)
check("first_seen is True", d["first_seen"] is True)
check("first_seen is not a change", d["changed"] is False and d["direction"] == "unchanged")

print("DELTA: the health ladder drives direction")
prev_healthy = dict(snapshot_of(row("MMM", health="Healthy")), checked_at=1000.0)
d = diff_holding(row("MMM", health="Watch", z=2.2), prev_healthy)
check("Healthy -> Watch is deterioration", d["direction"] == "deteriorated" and d["changed"])
check("headline names both states", "Healthy" in d["headline"] and "Watch" in d["headline"])
check("last_checked is carried through", d["last_checked"] == 1000.0)
check("z_delta computed", d["z_delta"] == round(2.2 - 4.0, 4))

prev_distressed = dict(snapshot_of(row("X", health="Distressed")), checked_at=1.0)
d = diff_holding(row("X", health="Watch"), prev_distressed)
check("Distressed -> Watch is improvement", d["direction"] == "improved")

d = diff_holding(row("Y", health="Healthy"), dict(snapshot_of(row("Y", health="Healthy")), checked_at=1.0))
check("same health is unchanged", d["direction"] == "unchanged" and d["changed"] is False)
check("unchanged headline says so", "No change" in d["headline"])

print("DELTA: a new Beneish flag is deterioration even when health holds")
prev_clean = dict(snapshot_of(row("Z", health="Healthy", m_flag=False)), checked_at=1.0)
d = diff_holding(row("Z", health="Healthy", m_flag=True), prev_clean)
check("new flag counts as deterioration", d["direction"] == "deteriorated")
check("new_flag is set", d["new_flag"] is True and d["cleared_flag"] is False)
check("headline calls the flag new", "new" in d["headline"].lower())

prev_flagged = dict(snapshot_of(row("Z", health="Healthy", m_flag=True)), checked_at=1.0)
d = diff_holding(row("Z", health="Healthy", m_flag=False), prev_flagged)
check("cleared flag counts as improvement", d["direction"] == "improved" and d["cleared_flag"])

print("DELTA: Unknown is 'we lost the data', never a verdict move")
prev_unknown = dict(snapshot_of(row("Q", health="Unknown")), checked_at=1.0)
d = diff_holding(row("Q", health="Healthy"), prev_unknown)
check("Unknown -> Healthy is not called an improvement", d["direction"] == "unchanged")
d = diff_holding(row("Q", health="Unknown"), dict(snapshot_of(row("Q", health="Healthy")), checked_at=1.0))
check("Healthy -> Unknown is not called a deterioration", d["direction"] == "unchanged")

print("PORTFOLIO DIFF + ROLLUP")
history = {"tickers": {
    "AAPL": dict(snapshot_of(row("AAPL", health="Healthy")), checked_at=1.0),
    "MMM": dict(snapshot_of(row("MMM", health="Watch")), checked_at=1.0),
}}
current = [row("AAPL", health="Watch", z=2.5),          # deteriorated
           row("MMM", health="Healthy"),                # improved
           row("NEW")]                                  # first seen
tagged = diff_portfolio(current, history)
check("diff_portfolio does not mutate the inputs", "delta" not in current[0])
check("every row gets a delta block", all("delta" in r for r in tagged))
summary = changed_holdings(tagged)
check("one deterioration detected", summary["n_deteriorated"] == 1)
check("one improvement detected", summary["n_improved"] == 1)
check("one first sighting detected", summary["n_first_seen"] == 1)
check("any_change is True", summary["any_change"] is True)

rollup = rank_portfolio(tagged)
check("rank_portfolio surfaces changes when deltas are present", "changes" in rollup)
check("rollup carries the concentration read", "concentration" in rollup)
check("rollup without deltas omits changes", "changes" not in rank_portfolio(current))

print("HISTORY STORE round-trips, and never remembers an unscored row")
with tempfile.TemporaryDirectory() as tmp:
    path = os.path.join(tmp, "history", "scores.json")
    check("missing file loads as empty history", load_history(path)["tickers"] == {})
    scored = [row("AAPL", health="Healthy"),
              {"ticker": "BAD", "source": "unscored", "verdict": {"health": "Unknown"},
               "m_flag": False}]
    save_run(scored, path=path, now=1234.0)
    h = load_history(path)
    check("scored ticker is remembered", "AAPL" in h["tickers"])
    check("unscored ticker is NOT remembered (would fake an improvement next run)",
          "BAD" not in h["tickers"])
    check("checked_at is stamped", h["tickers"]["AAPL"]["checked_at"] == 1234.0)

    # second run: AAPL deteriorates, and the store updates
    save_run([row("AAPL", health="Distressed", z=1.0)], path=path, now=5678.0)
    h2 = load_history(path)
    check("store updates on the next run", h2["tickers"]["AAPL"]["health"] == "Distressed")
    check("checked_at advances", h2["tickers"]["AAPL"]["checked_at"] == 5678.0)

    with open(path, "w") as fh:
        fh.write("{ this is not json")
    check("a corrupt history file degrades to empty, never crashes",
          load_history(path)["tickers"] == {})

print("EVERY ROW CARRIES ITS OWN PROVENANCE (snapshot staleness is visible, not silent)")
from data import PRESETS
from portfolio import score_holdings

snapshot_rows = [{"tr": "SNAP", "name": "Snapshot Co", "sector": "Utilities", "z": 3.4,
                  "zone": "Safe", "f_score": 8, "m_score": -2.6, "m_flag": "False",
                  "as_of_date": "2026-06-19"}]


def stub_fetch(ticker):
    p = dict(PRESETS["Bluechip Industries (sample: healthy)"])
    p = {**p, "meta": {**p["meta"], "ticker": ticker, "name": ticker, "sector": "Technology"}}
    return p


from data import run_models as real_run_models

mixed_rows = score_holdings([{"ticker": "SNAP", "shares": 5},
                             {"ticker": "LIVEONE", "shares": 5}],
                            snapshot_rows, stub_fetch, real_run_models)
snap_row = next(r for r in mixed_rows if r["ticker"] == "SNAP")
live_row = next(r for r in mixed_rows if r["ticker"] == "LIVEONE")
check("snapshot row is labeled source=snapshot", snap_row["source"] == "snapshot")
check("snapshot row carries the snapshot's own as-of date",
      snap_row["data_source"]["as_of"] == "2026-06-19")
check("snapshot row names the snapshot as its data source",
      "snapshot" in snap_row["data_source"]["source"].lower())
check("live row is labeled source=live", live_row["source"] == "live")
check("live row carries its own data provenance",
      live_row["data_source"]["source"] is not None)
check("live row was built through the report contract (has worst_signal + verdict)",
      bool(live_row["worst_signal"]) and live_row["verdict"]["health"] == "Healthy")
check("both paths produce the same row keys", set(snap_row) == set(live_row))

print("A BANK-LIKE LIVE HOLDING still degrades to unscored with a plain reason")
def bank_fetch(ticker):
    import copy as _copy
    p = _copy.deepcopy(PRESETS["Bluechip Industries (sample: healthy)"])
    p["meta"] = {**p["meta"], "ticker": ticker, "is_financial": True}
    for yr in ("curr", "prior"):
        for k in ("current_assets", "current_liabilities", "cogs", "receivables"):
            p[yr][k] = None
    return p

bank_rows = score_holdings([{"ticker": "BANKY", "shares": 1}], [], bank_fetch, real_run_models)
check("unscorable live holding degrades, never crashes", bank_rows[0]["source"] == "unscored")
check("reason names the bank case", "bank" in bank_rows[0]["unscored_reason"].lower())

print(f"\n{passed} checks passed.")
