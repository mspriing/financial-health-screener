"""
Verification of the portfolio upload module (portfolio.py).
Run:  python3 tests/test_portfolio.py

Proves the CSV parser handles the shapes real brokers export (Robinhood, Schwab
with footer junk, options rows, duplicates, no ticker column), that scoring
degrades to "unscored" rows instead of raising, and that the ranking puts the
weakest holdings first with a correct rollup. No network anywhere: the live path
is stubbed.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from portfolio import (parse_holdings, score_holdings, rank_portfolio,
                       MAX_HOLDINGS, EXAMPLE_CSV)
from data import run_models

passed = 0
def check(name, cond):
    global passed
    status = "PASS" if cond else "FAIL"
    if cond:
        passed += 1
    print(f"  [{status}] {name}")
    assert cond, f"FAILED: {name}"


print("PARSE - Robinhood-shaped export (Symbol / Quantity)")
robinhood = """Symbol,Name,Quantity,Average Cost,Total Return
AAPL,Apple Inc.,10,150.00,320.00
TSLA,Tesla Inc.,5,220.00,-45.00
KO,Coca-Cola Co.,30,58.00,120.00
"""
r = parse_holdings(robinhood)
check("finds all three tickers in order",
      [h["ticker"] for h in r["holdings"]] == ["AAPL", "TSLA", "KO"])
check("reads share counts", r["holdings"][0]["shares"] == 10.0)
check("no cap note on a small file", r["note"] is None)

print("PARSE - Schwab-shaped export with preamble and footer junk")
schwab = """"Positions for account XXXX-1234 as of 07/01/2026"

"Symbol","Description","Qty","Price","Cost Basis"
"MSFT","MICROSOFT CORP","12","430.00","$4,100.00"
"NVDA","NVIDIA CORP","8","120.00","$800.50"
"SWVXX","SCHWAB VALUE ADVANTAGE MONEY FUND","1000","1.00","$1,000.00"
"Account Total","","","","$50,000.00"

"The information contained herein is obtained from sources believed to be reliable."
"""
s = parse_holdings(schwab)
check("parses past the preamble line",
      [h["ticker"] for h in s["holdings"]] == ["MSFT", "NVDA"])
check("money market fund (SWVXX) is skipped",
      all(h["ticker"] != "SWVXX" for h in s["holdings"]))
check("footer disclaimer rows are skipped", len(s["holdings"]) == 2)
check("cost basis parses through $ and commas", s["holdings"][0]["cost_basis"] == 4100.0)

print("PARSE - options rows are skipped")
options = """Symbol,Quantity
AAPL,10
AAPL 01/17/2026 200.00 C,2
SPY 06/20/2026 500.00 P,1
GE,15
"""
o = parse_holdings(options)
check("option symbols with spaces/expiry are dropped",
      [h["ticker"] for h in o["holdings"]] == ["AAPL", "GE"])

print("PARSE - duplicate tickers are deduped, first-seen order kept")
dupes = """Ticker,Shares
KO,10
PEP,5
KO,20
PEP,1
MMM,3
"""
d = parse_holdings(dupes)
check("dedupes preserving order",
      [h["ticker"] for h in d["holdings"]] == ["KO", "PEP", "MMM"])
check("first-seen share count wins", d["holdings"][0]["shares"] == 10.0)

print("PARSE - no recognizable ticker column raises a clear ValueError")
try:
    parse_holdings("Account,Balance\nBrokerage,50000\n")
    check("ValueError raised", False)
except ValueError as e:
    check("ValueError raised", True)
    check("error names the expected columns", "Symbol" in str(e))

print("PARSE - cap at MAX_HOLDINGS with a note")
big = "Symbol,Shares\n" + "".join(f"T{chr(65 + i // 26)}{chr(65 + i % 26)},1\n" for i in range(40))
b = parse_holdings(big)
check(f"capped at {MAX_HOLDINGS}", len(b["holdings"]) == MAX_HOLDINGS)
check("cap note present and explains why", b["note"] is not None and "40" in b["note"])

print("PARSE - the built-in example parses cleanly")
ex = parse_holdings(EXAMPLE_CSV)
check("example has 5 holdings", len(ex["holdings"]) == 5)

print("SCORE - snapshot hit, live fallback, failing ticker")
snapshot = [
    {"tr": "AAPL", "name": "Apple Inc.", "sector": "Technology", "z": 8.5, "zone": "Safe",
     "f_score": 7.0, "m_score": -2.6, "m_flag": "False"},
    {"tr": "WEAK", "name": "Weak Co.", "sector": "Industrials", "z": 1.2, "zone": "Distress",
     "f_score": 2.0, "m_score": -1.2, "m_flag": "True"},
]

def stub_fetch(ticker):
    if ticker == "FAIL":
        raise RuntimeError("Couldn't find company financials for FAIL.")
    # A healthy live payload (reuses the Bluechip sample numbers).
    from data import PRESETS
    p = dict(PRESETS["Bluechip Industries (sample: healthy)"])
    p["meta"] = dict(p["meta"], name="Live Co.", ticker=ticker, sector="Utilities")
    return p

holdings = [{"ticker": t, "shares": 1.0, "cost_basis": None}
            for t in ("AAPL", "WEAK", "LIVE", "FAIL")]
scored = score_holdings(holdings, snapshot, stub_fetch, run_models)
by = {r["ticker"]: r for r in scored}

check("snapshot hit uses the snapshot (no fetch)", by["AAPL"]["source"] == "snapshot")
check("snapshot verdict reuses overall_verdict (Healthy)",
      by["AAPL"]["verdict"]["health"] == "Healthy")
check("distressed snapshot row reads Distressed",
      by["WEAK"]["verdict"]["health"] == "Distressed")
check("flagged snapshot row carries the Beneish flag", by["WEAK"]["m_flag"] is True)
check("worst signal names the distress zone", "distress zone" in by["WEAK"]["worst_signal"])
check("unknown ticker falls back to live", by["LIVE"]["source"] == "live")
check("live row is scored", by["LIVE"]["z"] is not None and by["LIVE"]["f_score"] is not None)
check("failing ticker degrades to unscored, no exception", by["FAIL"]["source"] == "unscored")
check("unscored row carries the reason", "FAIL" in by["FAIL"]["unscored_reason"])
check("every row carries a weight", all(r["weight"] == 1.0 for r in scored))

print("RANK - weakest first + rollup")
def mk(t, health, z=None, zone=None, f=None, flag=False, source="snapshot"):
    return {"ticker": t, "name": t, "sector": None, "z": z, "zone": zone,
            "f_score": f, "m_score": -2.0, "m_flag": flag,
            "verdict": {"health": health, "integrity": "Clean"},
            "source": source, "weight": None, "worst_signal": "x",
            "unscored_reason": ("failed" if source == "unscored" else None)}

hand = [
    mk("HLTH", "Healthy", z=6.0, zone="Safe", f=8),
    mk("DIST", "Distressed", z=1.1, zone="Distress", f=2),
    mk("FLAG", "Watch", z=3.5, zone="Safe", f=5, flag=True),
    mk("WTCH", "Watch", z=2.4, zone="Grey", f=5),
    mk("GONE", "Unknown", source="unscored"),
]
roll = rank_portfolio(hand)
order = [r["ticker"] for r in roll["ranked"]]
check("distressed first, then flagged, then watch, then healthy",
      order == ["DIST", "FLAG", "WTCH", "HLTH"])
check("unscored listed separately, last",
      [r["ticker"] for r in roll["unscored"]] == ["GONE"])
check("counts by verdict are right",
      roll["counts"] == {"Distressed": 1, "Watch": 2, "Healthy": 1})
check("flagged count is right", roll["n_flagged"] == 1)
check("weakest three are the top of the ranking",
      [r["ticker"] for r in roll["weakest"]] == ["DIST", "FLAG", "WTCH"])
check("n_unscored is right", roll["n_unscored"] == 1)

print(f"\n{passed} checks passed.")
