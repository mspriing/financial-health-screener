"""
Verification of the portfolio Sharpe-delta / correlation module (risk.py). Run:
  python3 tests/test_risk.py

The load-bearing check here is the CORRELATION PROOF: a portfolio is built where the
most volatile holding is deliberately the one that moves AGAINST everything else, and
the calmest-looking holding is a near duplicate of what is already owned. If the model
were secretly ranking holdings by their own volatility, the volatile hedge would look
like the worst position. It has to come out as the best one. That single test is what
separates this from a per-holding volatility table.

Everything is built from hand-constructed price series with a deterministic pseudo
random generator (no `random`, no seeds, no network), the same discipline the Merton
round-trip test uses: nothing here is compared against an external implementation.
"""
import datetime as dt
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import risk
from risk import (
    MIN_ALIGNED_DAYS,
    analyze_risk,
    concentration_read,
    correlation,
    covariance,
    daily_returns,
    daily_risk_free,
    portfolio_returns,
    portfolio_risk,
    sharpe_ratio,
    _mean,
    _renormalized_without,
    _stdev,
)

passed = 0


def check(name, cond):
    global passed
    status = "PASS" if cond else "FAIL"
    if cond:
        passed += 1
    print(f"  [{status}] {name}")
    assert cond, f"FAILED: {name}"


# ----------------------------------------------------------------------------
# Deterministic fixtures
# ----------------------------------------------------------------------------
def noise(seed, n):
    """A tiny LCG in [-0.5, 0.5]. Deterministic across machines and Python versions,
    which `random` with a seed is not guaranteed to be."""
    out, x = [], seed
    for _ in range(n):
        x = (1103515245 * x + 12345) % (2 ** 31)
        out.append(x / float(2 ** 31) - 0.5)
    return out


def dates_for(n, offset=0):
    """n consecutive calendar dates as ISO strings. Weekends do not matter: these are
    labels for alignment, and every series uses the same calendar."""
    start = dt.date(2025, 1, 1) + dt.timedelta(days=offset)
    return [(start + dt.timedelta(days=i)).isoformat() for i in range(n)]


def history(rets, offset=0, start=100.0):
    """A {dates, closes} history built from a list of daily simple returns."""
    closes = [start]
    for r in rets:
        closes.append(closes[-1] * (1.0 + r))
    return {"dates": dates_for(len(closes), offset=offset), "closes": closes}


def flat_history(n, offset=0, price=50.0):
    return {"dates": dates_for(n, offset=offset), "closes": [price] * n}


# ----------------------------------------------------------------------------
print("PURE STATISTICS")
# ----------------------------------------------------------------------------
check("mean of a hand series", abs(_mean([1.0, 2.0, 6.0]) - 3.0) < 1e-12)
# sample variance of [1,2,6]: mean 3, deviations -2,-1,3 -> (4+1+9)/2 = 7
check("sample variance uses n-1", abs(_stdev([1.0, 2.0, 6.0]) - math.sqrt(7.0)) < 1e-12)
check("stdev of a constant series is zero", _stdev([4.0, 4.0, 4.0]) == 0.0)

a = [1.0, 2.0, 3.0, 4.0]
b = [2.0, 4.0, 6.0, 8.0]
c = [4.0, 3.0, 2.0, 1.0]
# cov(a,a) is the sample variance of a: mean 2.5, devs -1.5,-.5,.5,1.5 -> 5/3
check("covariance with itself is the variance",
      abs(covariance(a, a) - (5.0 / 3.0)) < 1e-12)
check("correlation with itself is 1", abs(correlation(a, a) - 1.0) < 1e-12)
check("correlation with a perfect scaling is 1", abs(correlation(a, b) - 1.0) < 1e-12)
check("correlation with a perfect inverse is -1", abs(correlation(a, c) + 1.0) < 1e-12)
check("correlation against a flat series is None (undefined, not zero)",
      correlation(a, [7.0, 7.0, 7.0, 7.0]) is None)

check("daily returns are simple returns",
      [round(r, 10) for r in daily_returns([100.0, 110.0, 99.0])] == [0.1, -0.1])
check("n closes give n-1 returns", len(daily_returns([1.0, 2.0, 3.0, 4.0])) == 3)

rf_d = daily_risk_free(0.04)
check("daily risk free compounds back to the annual rate",
      abs((1.0 + rf_d) ** 252 - 1.04) < 1e-12)
check("risk free proxy is imported from models, not redefined here",
      risk.DEFAULT_RISK_FREE == __import__("models").DEFAULT_RISK_FREE)

# ----------------------------------------------------------------------------
print("\nSHARPE RATIO")
# ----------------------------------------------------------------------------
check("a zero-volatility series has no Sharpe (divide by zero, not infinity)",
      sharpe_ratio([0.01] * 30, 0.0) is None)
check("too few observations give None", sharpe_ratio([0.01], 0.0) is None)
check("a zero-mean series has Sharpe 0",
      abs(sharpe_ratio([0.01, -0.01, 0.01, -0.01], 0.0)) < 1e-12)
# [0.02, 0, 0.02, 0]: mean 0.01, sample sd = sqrt(4*0.0001/3) -> mean/sd = sqrt(3)/2
check("hand-computed Sharpe matches sqrt(3)/2 annualized",
      abs(sharpe_ratio([0.02, 0.0, 0.02, 0.0], 0.0)
          - (math.sqrt(3.0) / 2.0) * math.sqrt(252)) < 1e-9)
check("a negative mean excess return gives a negative Sharpe",
      sharpe_ratio([-0.02, 0.0, -0.02, 0.0], 0.0) < 0)
check("raising the risk-free rate lowers the Sharpe",
      sharpe_ratio([0.02, 0.0, 0.02, 0.0], 0.0)
      > sharpe_ratio([0.02, 0.0, 0.02, 0.0], 0.005))

w = {"A": 0.25, "B": 0.75}
rets2 = {"A": [0.10, -0.10], "B": [0.02, 0.02]}
check("portfolio returns are the weighted sum of holding returns",
      [round(x, 10) for x in portfolio_returns(w, rets2, ["A", "B"])]
      == [round(0.25 * 0.10 + 0.75 * 0.02, 10),
          round(0.25 * -0.10 + 0.75 * 0.02, 10)])

rw = _renormalized_without({"A": 0.2, "B": 0.3, "C": 0.5}, "A", ["A", "B", "C"])
check("dropping a holding renormalizes the rest pro rata",
      abs(rw["B"] - 0.375) < 1e-12 and abs(rw["C"] - 0.625) < 1e-12)
check("renormalized weights still sum to 1", abs(sum(rw.values()) - 1.0) < 1e-12)
check("pro rata keeps the ratio between the survivors",
      abs((rw["C"] / rw["B"]) - (0.5 / 0.3)) < 1e-12)
check("dropping a 100 percent position leaves nothing to spread into",
      _renormalized_without({"A": 1.0}, "A", ["A"]) is None)

# ----------------------------------------------------------------------------
print("\nTHE CORRELATION PROOF (the reason this module exists)")
# ----------------------------------------------------------------------------
# Three holdings, 250 days, all with the SAME average drift, so any Sharpe difference
# comes purely from risk and correlation and not from one of them earning more:
#   CORE   a normal holding
#   CLONE  a near duplicate of CORE (correlation close to +1), SAME volatility as CORE
#   HEDGE  the MOST volatile of the three, but moving AGAINST them (correlation near -1)
# A per-holding volatility ranking would call HEDGE the worst position here. Correctly
# accounting for correlation has to call it the best one.
#
# The hedge is deliberately NOT sized to cancel the other two exactly (0.012 against a
# combined 0.020). A perfect cancellation would make the whole portfolio riskless and
# then every removal looks harmful, which tests nothing.
N = 250
u = noise(7, N)
v = noise(99, N)
DRIFT = 0.001
core_r = [DRIFT + 0.010 * u[i] for i in range(N)]
clone_r = [DRIFT + 0.010 * u[i] + 0.0005 * v[i] for i in range(N)]
hedge_r = [DRIFT - 0.012 * u[i] + 0.0005 * v[i] for i in range(N)]

check("fixture: CORE and CLONE are near-duplicates by construction",
      correlation(core_r, clone_r) > 0.99)
check("fixture: HEDGE moves against CORE by construction",
      correlation(core_r, hedge_r) < -0.99)
check("fixture: all three have the same average return, so only risk differs",
      abs(_mean(core_r) - _mean(hedge_r)) < 1e-4)

hist3 = {"CORE": history(core_r), "CLONE": history(clone_r), "HEDGE": history(hedge_r)}
res = analyze_risk(hist3, order=["CORE", "CLONE", "HEDGE"])
check("the three-holding read is available", res["available"] is True)
by_t = {r["ticker"]: r for r in res["holdings"]}

check("HEDGE really is the most volatile holding on its own",
      by_t["HEDGE"]["annual_volatility_pct"] > by_t["CORE"]["annual_volatility_pct"]
      and by_t["HEDGE"]["annual_volatility_pct"] > by_t["CLONE"]["annual_volatility_pct"])
check("HEDGE is negatively correlated with the rest of the portfolio",
      by_t["HEDGE"]["correlation_to_rest"] < -0.8)
check("correlation to the rest is measured against the REST as a portfolio, not "
      "against the nearest single holding",
      by_t["CLONE"]["correlation_to_rest"] < 0)
check("removing the most volatile holding HURTS the portfolio (negative delta)",
      by_t["HEDGE"]["sharpe_delta"] < 0)
check("removing the duplicate holding HELPS the portfolio (positive delta)",
      by_t["CLONE"]["sharpe_delta"] > 0)
check("the volatile hedge is the single best holding to keep",
      by_t["HEDGE"]["sharpe_delta"] < by_t["CORE"]["sharpe_delta"]
      and by_t["HEDGE"]["sharpe_delta"] < by_t["CLONE"]["sharpe_delta"])
check("HEDGE is labeled support (removing it would hurt)",
      by_t["HEDGE"]["role"] == "support")
check("CLONE is labeled a drag", by_t["CLONE"]["role"] == "drag")
# The mechanism split: HEDGE has the WORST Sharpe of the three on its own (same drift,
# most volatility), so it is worth keeping for diversification, not for its return. A
# label that called this a return driver would be wrong.
check("HEDGE has the worst standalone Sharpe of the three",
      by_t["HEDGE"]["own_sharpe"] < by_t["CORE"]["own_sharpe"]
      and by_t["HEDGE"]["own_sharpe"] < by_t["CLONE"]["own_sharpe"])
check("HEDGE's mechanism is diversification, not return",
      by_t["HEDGE"]["mechanism"] == "risk reducer")
check("its plain-English line says it moves differently",
      "moves differently" in by_t["HEDGE"]["headline"])
check("a drag has no mechanism (the field only explains why to KEEP something)",
      by_t["CLONE"]["mechanism"] is None)
check("HEDGE's beta to the portfolio is negative", by_t["HEDGE"]["beta_to_portfolio"] < 0)
check("holdings are ordered by delta, biggest improvement-on-removal first",
      [r["ticker"] for r in res["holdings"]][-1] == "HEDGE"
      and res["holdings"][0]["sharpe_delta"] >= res["holdings"][1]["sharpe_delta"])
check("the portfolio headline names the biggest drag",
      res["headline"].startswith(res["holdings"][0]["ticker"]))
check("the headline names the diversifier too", "HEDGE" in res["headline"])
check("each holding carries a plain-English line",
      all(len(r["headline"]) > 30 for r in res["holdings"]))

# The OTHER reason a holding is worth keeping. STAR is highly correlated with the rest
# and does nothing for diversification, but it out-earns them badly. Removing it hurts.
# Reporting that as "diversification work" would be a lie, and it is exactly the shape a
# real portfolio's largest winner takes.
star_r = [0.004 + 0.010 * u[i] + 0.0005 * v[i] for i in range(N)]
dull_a = [0.0005 + 0.010 * u[i] for i in range(N)]
dull_b = [0.0005 + 0.010 * u[i] + 0.0004 * v[i] for i in range(N)]
winner = analyze_risk({"DULLA": history(dull_a), "DULLB": history(dull_b),
                       "STAR": history(star_r)},
                      order=["DULLA", "DULLB", "STAR"])
star = {r["ticker"]: r for r in winner["holdings"]}["STAR"]
check("a high-earning, highly correlated holding is still worth keeping",
      star["role"] == "support" and star["sharpe_delta"] < 0)
check("it is NOT correlated away from the rest (no diversification story here)",
      star["correlation_to_rest"] > 0.9)
check("its mechanism is return, not diversification",
      star["mechanism"] == "return driver")
check("its standalone Sharpe beats the portfolio's, which is what makes it a driver",
      star["own_sharpe"] > winner["portfolio"]["sharpe"])
check("the plain-English line credits return, not diversification",
      "carrying more of the portfolio's return" in star["headline"]
      and "moves differently" not in star["headline"])
check("the portfolio headline credits it for return too",
      "carrying the most return for its risk" in winner["headline"])

# Same three names, but now compare against the version WITHOUT the hedge: the hedge
# must be lowering portfolio volatility, which is the mechanism behind its delta.
two = analyze_risk({"CORE": history(core_r), "CLONE": history(clone_r)},
                   order=["CORE", "CLONE"])
check("dropping the hedge leaves a portfolio with visibly more volatility",
      two["portfolio"]["annual_volatility_pct"]
      > res["portfolio"]["annual_volatility_pct"])

# ----------------------------------------------------------------------------
print("\nIDENTITIES THAT MUST HOLD")
# ----------------------------------------------------------------------------
# Percentages are rounded to one decimal for display, so a sum of n of them can sit up
# to n * 0.05 away from 100. The identity is exact before rounding; the tolerance here
# is the rounding, nothing else.
ROUND_TOL = 0.05 * len(res["holdings"])
check("weights sum to 100 percent",
      abs(sum(r["weight_pct"] for r in res["holdings"]) - 100.0) <= ROUND_TOL)
check("risk contributions sum to 100 percent of portfolio risk",
      abs(sum(r["risk_contribution_pct"] for r in res["holdings"]) - 100.0) <= ROUND_TOL)
check("a negatively correlated holding contributes NEGATIVE risk",
      by_t["HEDGE"]["risk_contribution_pct"] < 0)
check("correlations stay inside [-1, 1]",
      all(-1.0 <= r["correlation_to_rest"] <= 1.0 for r in res["holdings"]))
check("the window reports the shared trading days used",
      res["window"]["n_days"] == N and res["window"]["start"] < res["window"]["end"])
check("the portfolio block reports Sharpe, return and volatility",
      all(res["portfolio"][k] is not None
          for k in ("sharpe", "annual_return_pct", "annual_volatility_pct")))

conc = res["concentration"]
check("equal weights give effective holdings equal to the count",
      abs(conc["effective_holdings"] - 3.0) < 0.05)
check("HHI of n equal weights is 1/n",
      abs(conc["hhi"] - (1.0 / 3.0)) < 1e-4)          # 1e-4: reported to 4 decimals
check("the most correlated pair is the duplicate pair",
      sorted(conc["most_correlated_pair"]["tickers"]) == ["CLONE", "CORE"])
check("that pair's correlation is near 1",
      conc["most_correlated_pair"]["correlation"] > 0.9)
check("average pairwise correlation reflects the two negative pairs",
      -0.5 < conc["avg_pairwise_correlation"] < -0.1)
check("concentration carries a plain-English headline",
      "independent risk" in conc["headline"])

# Effective holdings on a hand-built lopsided book: 80/10/10 -> 1/0.66 = 1.515
lop = {"A": 0.8, "B": 0.1, "C": 0.1}
lop_conc = concentration_read(lop, ["A", "B", "C"], {"A": 90.0, "B": 5.0, "C": 5.0},
                              {"A": [0.01, -0.01, 0.02], "B": [0.01, 0.0, 0.01],
                               "C": [0.0, 0.01, -0.01]}, "market value")
check("a lopsided book reports few effective holdings",
      abs(lop_conc["effective_holdings"] - (1.0 / 0.66)) < 0.05)
check("the lopsided book is flagged concentrated", lop_conc["concentrated"] is True)
check("top weight and top-3 weight are reported",
      lop_conc["top_weight_pct"] == 80.0 and abs(lop_conc["top3_weight_pct"] - 100.0) < 0.05)
check("a 10 percent position can still be the top risk contributor",
      lop_conc["top_risk_ticker"] == "A")
check("a three-holding book is concentrated even when equally weighted (three names "
      "is three names)", conc["concentrated"] is True)
wide = {f"T{i}": 0.125 for i in range(8)}
wide_conc = concentration_read(wide, list(wide), {t: 12.5 for t in wide},
                               {t: [0.01, -0.01, 0.02, 0.0] for t in wide},
                               "market value")
check("a genuinely spread book is NOT flagged concentrated",
      wide_conc["concentrated"] is False
      and abs(wide_conc["effective_holdings"] - 8.0) < 0.05)

# ----------------------------------------------------------------------------
print("\nWEIGHTING BASIS")
# ----------------------------------------------------------------------------
val = analyze_risk(hist3, shares_by_ticker={"CORE": 800, "CLONE": 100, "HEDGE": 100},
                   order=["CORE", "CLONE", "HEDGE"])
check("share counts give a market-value basis", val["basis"] == "market value")
val_by_t = {r["ticker"]: r for r in val["holdings"]}
check("market-value weights track shares times latest close",
      val_by_t["CORE"]["weight_pct"] > 60.0)
check("weights still sum to 100 percent on the value basis",
      abs(sum(r["weight_pct"] for r in val["holdings"]) - 100.0) < 0.05)
check("weighting basis is stated in the method block",
      val["method"]["weighting"] == "market value")

partial = analyze_risk(hist3, shares_by_ticker={"CORE": 800, "CLONE": 100},
                       order=["CORE", "CLONE", "HEDGE"])
check("one missing share count drops the WHOLE read to equal weight, labeled",
      partial["basis"].startswith("equal weight"))
check("the equal-weight fallback says why in the label",
      "no share counts" in partial["basis"])
check("equal weight really is equal",
      all(abs(r["weight_pct"] - 100.0 / 3.0) < 0.05 for r in partial["holdings"]))
check("a zero share count counts as missing, not as a zero position",
      analyze_risk(hist3, shares_by_ticker={"CORE": 800, "CLONE": 100, "HEDGE": 0},
                   order=["CORE", "CLONE", "HEDGE"])["basis"].startswith("equal weight"))
check("a junk share cell degrades to equal weight instead of raising",
      analyze_risk(hist3, shares_by_ticker={"CORE": "--", "CLONE": 100, "HEDGE": 5},
                   order=["CORE", "CLONE", "HEDGE"])["basis"].startswith("equal weight"))
check("a negative share count is not treated as a real position",
      analyze_risk(hist3, shares_by_ticker={"CORE": -800, "CLONE": 100, "HEDGE": 5},
                   order=["CORE", "CLONE", "HEDGE"])["basis"].startswith("equal weight"))

full_val = analyze_risk(hist3, shares_by_ticker={"CORE": 800, "CLONE": 100, "HEDGE": 100},
                        order=["CORE", "CLONE", "HEDGE"])
check("percent of value IS claimed when every holding can be valued",
      abs(full_val["coverage"]["pct_of_value"] - 100.0) < 0.05)

# ----------------------------------------------------------------------------
print("\nHONEST DEGRADATION")
# ----------------------------------------------------------------------------
mixed = analyze_risk(
    {"CORE": history(core_r), "CLONE": history(clone_r), "HEDGE": history(hedge_r),
     "NOHIST": None, "THIN": history([0.001] * 30), "FLAT": flat_history(200)},
    order=["CORE", "CLONE", "HEDGE", "NOHIST", "THIN", "FLAT"])
ex = {e["ticker"]: e["reason"] for e in mixed["excluded"]}
check("the read still works with three good holdings among the broken ones",
      mixed["available"] is True and mixed["coverage"]["n_covered"] == 3)
check("a holding with no history is excluded BY NAME", "NOHIST" in ex)
check("the no-history reason says so plainly",
      "No price history" in ex["NOHIST"])
check("a holding with too little history is excluded", "THIN" in ex)
check("the thin-history reason names both what was found and what is needed",
      str(MIN_ALIGNED_DAYS) in ex["THIN"] and "31 days" in ex["THIN"])  # 30 returns
check("a flat price series is excluded", "FLAT" in ex)
check("the flat reason explains there is no measurable risk",
      "has not moved" in ex["FLAT"])
check("excluded holdings are NEVER silently zero-filled into the math",
      all(r["ticker"] not in ("NOHIST", "THIN", "FLAT") for r in mixed["holdings"]))
check("coverage counts every holding, not just the covered ones",
      mixed["coverage"]["n_holdings"] == 6)
check("coverage note admits what is missing",
      "3 of 6 holdings" in mixed["coverage"]["note"])
check("percent of value is not claimed when a holding cannot be valued",
      mixed["coverage"]["pct_of_value"] is None)
check("the deltas are unchanged by the presence of unusable holdings",
      abs(by_t["HEDGE"]["sharpe_delta"]
          - {r["ticker"]: r for r in mixed["holdings"]}["HEDGE"]["sharpe_delta"]) < 1e-9)

# A holding whose own history is long enough, but which barely overlaps the others.
short_overlap = analyze_risk(
    {"CORE": history(core_r), "CLONE": history(clone_r),
     "LATE": history([DRIFT + 0.01 * x for x in noise(3, 70)], offset=210)},
    order=["CORE", "CLONE", "LATE"])
late_ex = {e["ticker"]: e["reason"] for e in short_overlap["excluded"]}
check("a late-listing holding that would truncate the shared window is dropped",
      "LATE" in late_ex and short_overlap["coverage"]["n_covered"] == 2)
check("the window-drop reason explains the tradeoff plainly",
      "Shorter price history" in late_ex["LATE"])
check("the survivors keep the full window rather than being cut to the overlap",
      short_overlap["window"]["n_days"] == N)

one = analyze_risk({"CORE": history(core_r), "NOHIST": None},
                   order=["CORE", "NOHIST"])
check("a single usable holding cannot have a Sharpe delta", one["available"] is False)
check("the single-holding reason explains what a delta compares against",
      "at least two holdings" in one["reason"])
check("an unavailable block still lists the exclusions",
      [e["ticker"] for e in one["excluded"]] == ["NOHIST"])
check("an unavailable block still carries the caveat and a headline",
      one["caveat"] and one["headline"])
check("an empty portfolio degrades instead of raising",
      analyze_risk({}, order=[])["available"] is False)

disjoint = analyze_risk({"A": history(core_r[:80]), "B": history(clone_r[:80], offset=400)},
                        order=["A", "B"])
check("two holdings with no overlapping dates degrade honestly",
      disjoint["available"] is False)
check("the no-overlap reason names the problem",
      "overlapping" in disjoint["reason"] or "at least two holdings" in disjoint["reason"])

# ----------------------------------------------------------------------------
print("\nCONTRACT: SERIALIZABLE, DETERMINISTIC, PURE")
# ----------------------------------------------------------------------------
check("the whole block is JSON-serializable", isinstance(json.dumps(res), str))
check("the unavailable block is JSON-serializable too", isinstance(json.dumps(one), str))
check("the mixed-degradation block is JSON-serializable",
      isinstance(json.dumps(mixed), str))
check("the same input twice gives byte-identical output",
      json.dumps(analyze_risk(hist3, order=["CORE", "CLONE", "HEDGE"]), sort_keys=True)
      == json.dumps(analyze_risk(hist3, order=["CORE", "CLONE", "HEDGE"]), sort_keys=True))
shuffled = analyze_risk({"HEDGE": history(hedge_r), "CORE": history(core_r),
                         "CLONE": history(clone_r)},
                        order=["HEDGE", "CORE", "CLONE"])
check("input ordering does not change any number",
      {r["ticker"]: r["sharpe_delta"] for r in shuffled["holdings"]}
      == {r["ticker"]: r["sharpe_delta"] for r in res["holdings"]})
check("the method block states the returns window", "daily simple returns"
      in res["method"]["returns"])
check("the method block states the counterfactual explicitly",
      "pro rata" in res["method"]["counterfactual"])
check("the method block states the no-shrinkage choice",
      "no shrinkage" in res["method"]["correlations"])
check("the method block states the risk-free proxy and its source",
      res["method"]["risk_free_annual"] == risk.DEFAULT_RISK_FREE
      and "Merton" in res["method"]["risk_free_note"])
check("the caveat warns that the Sharpe level is noisy",
      "noisy" in res["caveat"] and "deltas" in res["caveat"])
check("the caveat warns that correlations rise in a selloff",
      "selloff" in res["caveat"])
check("a different risk-free rate changes the Sharpe",
      analyze_risk(hist3, order=["CORE", "CLONE", "HEDGE"],
                   risk_free=0.20)["portfolio"]["sharpe"]
      != res["portfolio"]["sharpe"])

# ----------------------------------------------------------------------------
print("\nORCHESTRATION (injected fetch, no network)")
# ----------------------------------------------------------------------------
FIXTURES = {"CORE": history(core_r), "CLONE": history(clone_r), "HEDGE": history(hedge_r)}
calls = []


def fake_history(ticker):
    calls.append(ticker)
    if ticker == "BOOM":
        raise RuntimeError("upstream exploded")
    return FIXTURES.get(ticker)


def row(ticker, shares=None, source="live"):
    return {"ticker": ticker, "weight": shares, "source": source,
            "verdict": {"health": "Healthy"}}


orch = portfolio_risk([row("CORE", 800), row("CLONE", 100), row("HEDGE", 100)],
                      fake_history)
check("the orchestrator runs the analytic off injected history",
      orch["available"] is True and orch["coverage"]["n_covered"] == 3)
check("share counts on the row drive the market-value basis",
      orch["basis"] == "market value")
check("one fetch per holding, no duplicates", sorted(calls) == ["CLONE", "CORE", "HEDGE"])

orch2 = portfolio_risk(
    [row("CORE", 10), row("CLONE", 10), row("HEDGE", 10), row("BOOM", 10),
     row("MISSING", 10), row("GONE", None, source="unscored")], fake_history)
ex2 = {e["ticker"]: e["reason"] for e in orch2["excluded"]}
check("a fetch that raises degrades to an exclusion, never a crash", "BOOM" in ex2)
check("a fetch that returns None degrades to an exclusion", "MISSING" in ex2)
check("unscored holdings are still counted (you own them)",
      "GONE" in ex2 and orch2["coverage"]["n_holdings"] == 6)
check("the orchestrator still produces a usable read", orch2["available"] is True)
check("a duplicate ticker in the upload is fetched once",
      portfolio_risk([row("CORE", 1), row("CORE", 1), row("CLONE", 1), row("HEDGE", 1)],
                     fake_history)["coverage"]["n_holdings"] == 3)
check("no holdings at all degrades honestly",
      portfolio_risk([], fake_history)["available"] is False)
check("a non-numeric share count falls back to equal weight rather than crashing",
      portfolio_risk([row("CORE", "n/a"), row("CLONE", 5), row("HEDGE", 5)],
                     fake_history)["basis"].startswith("equal weight"))
check("the orchestrator's output is JSON-serializable",
      isinstance(json.dumps(orch2), str))

# ----------------------------------------------------------------------------
print("\nNO EFFECT ON THE EXISTING PORTFOLIO CONTRACT")
# ----------------------------------------------------------------------------
from portfolio import rank_portfolio

hand = [{"ticker": "AAA", "z": 5.0, "zone": "Safe", "f_score": 7, "m_score": -2.5,
         "m_flag": False, "sector": "Technology", "source": "live",
         "verdict": {"health": "Healthy", "integrity": "Clean"}, "weight": 10},
        {"ticker": "BBB", "z": 1.0, "zone": "Distress", "f_score": 3, "m_score": -1.0,
         "m_flag": False, "sector": "Energy", "source": "live",
         "verdict": {"health": "Distressed", "integrity": "Clean"}, "weight": 5}]
roll = rank_portfolio(hand)
check("rank_portfolio does not gain a risk block on its own", "risk" not in roll)
check("the existing rollup keys are untouched",
      all(k in roll for k in ("ranked", "unscored", "counts", "weakest", "concentration")))
check("sector concentration still answers its own separate question",
      roll["concentration"]["basis"] == "position count")

print(f"\n{passed} checks passed.")
