"""
Verification of the Merton distance-to-default / probability-of-default model and its
fold-in to the Spring Score. Run:  python3 tests/test_merton.py

The strongest check here is the ROUND TRIP: pick an asset value V and asset volatility
sigma_V, push them forward through the option formulas to get the equity value E and
equity volatility sigma_E a market would observe, then feed E and sigma_E back into
merton_dd_pd and confirm it recovers the V and sigma_V we started from. That proves the
two-equation solver end to end without leaning on any external reference implementation,
the same hand-checked discipline test_models.py and test_spring_score.py use.
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from models import (
    _bs_equity,
    _norm_cdf,
    merton_dd_pd,
    spring_score,
    SPRING_TOTAL_WEIGHT,
    SPRING_WEIGHTS,
)

passed = 0


def check(name, cond):
    global passed
    status = "PASS" if cond else "FAIL"
    if cond:
        passed += 1
    print(f"  [{status}] {name}")
    assert cond, f"FAILED: {name}"


print("NORMAL CDF")
check("N(0) == 0.5", abs(_norm_cdf(0.0) - 0.5) < 1e-12)
check("N(1.96) ~ 0.975", abs(_norm_cdf(1.96) - 0.9750) < 1e-3)
check("N(-1.96) ~ 0.025", abs(_norm_cdf(-1.96) - 0.0250) < 1e-3)
check("symmetry N(x)+N(-x)==1", abs((_norm_cdf(1.3) + _norm_cdf(-1.3)) - 1.0) < 1e-12)


print("ROUND TRIP — solver recovers the assets it was built from")
# Choose the truth, push forward to the observables, then solve back.
V, SIGMA_V, F, R, T = 1000.0, 0.25, 600.0, 0.04, 1.0
E, n_d1 = _bs_equity(V, SIGMA_V, F, R, T)
SIGMA_E = n_d1 * SIGMA_V * V / E
res = merton_dd_pd(equity_value=E, equity_vol=SIGMA_E, face_debt=F, risk_free=R, t=T)
check("recovers asset value V ~ 1000", abs(res.asset_value - V) < 0.5)
check("recovers asset vol sigma_V ~ 0.25", abs(res.asset_vol - SIGMA_V) < 1e-4)
# Independently: DD = [ln(V/F) + (r - 0.5 sigma_V^2)T] / (sigma_V sqrt(T)).
dd_expected = (math.log(V / F) + (R - 0.5 * SIGMA_V ** 2) * T) / (SIGMA_V * math.sqrt(T))
check("DD matches closed form ~ 2.078", abs(res.dd - dd_expected) < 1e-3)
check("PD == N(-DD)", abs(res.pd - _norm_cdf(-res.dd)) < 1e-5)
check("PD ~ 1.9%", abs(res.pd - 0.0188) < 1e-3)
check("leverage == F / V", abs(res.leverage - F / res.asset_value) < 1e-6)
check("label == Low (PD just under 2%)", res.label == "Low")


print("MONOTONICITY — the signal moves the right way")
base = merton_dd_pd(equity_value=500.0, equity_vol=0.40, face_debt=500.0)
more_debt = merton_dd_pd(equity_value=500.0, equity_vol=0.40, face_debt=800.0)
more_vol = merton_dd_pd(equity_value=500.0, equity_vol=0.60, face_debt=500.0)
more_equity = merton_dd_pd(equity_value=900.0, equity_vol=0.40, face_debt=500.0)
check("more debt raises PD", more_debt.pd > base.pd)
check("more equity volatility raises PD", more_vol.pd > base.pd)
check("more equity value (less leverage) lowers PD", more_equity.pd < base.pd)
check("more debt lowers DD", more_debt.dd < base.dd)


print("LABEL BANDS")
# A strongly capitalized, low-vol firm is Remote; a thin, high-vol firm is Severe.
remote = merton_dd_pd(equity_value=5000.0, equity_vol=0.15, face_debt=200.0)
severe = merton_dd_pd(equity_value=100.0, equity_vol=0.90, face_debt=900.0)
check("well-capitalized firm is Remote", remote.label == "Remote")
check("thin, volatile firm is Severe", severe.label == "Severe")
check("Remote PD < Severe PD", remote.pd < severe.pd)


print("DEGRADATION — honest failure, never a fake number")
def raises(fn):
    try:
        fn()
        return False
    except ValueError:
        return True

check("None input raises", raises(lambda: merton_dd_pd(None, 0.3, 500.0)))
check("zero equity raises", raises(lambda: merton_dd_pd(0.0, 0.3, 500.0)))
check("negative equity raises", raises(lambda: merton_dd_pd(-10.0, 0.3, 500.0)))
check("zero volatility raises", raises(lambda: merton_dd_pd(500.0, 0.0, 500.0)))
check("no debt (F<=0) raises", raises(lambda: merton_dd_pd(500.0, 0.3, 0.0)))
check("zero horizon raises", raises(lambda: merton_dd_pd(500.0, 0.3, 500.0, t=0.0)))


print("SPRING FOLD-IN — market-implied ingredient, honest coverage")
check("merton is in the weight table", "merton" in SPRING_WEIGHTS)
check("merton weight == 15", SPRING_WEIGHTS["merton"] == 15)
check("total weight == 115", SPRING_TOTAL_WEIGHT == 115)

# Same fundamentals, with and without the Merton signal.
curr = dict(net_income=100, cfo=120, total_assets=1000,
            sales=1200, cogs=700, long_term_debt=100)
prior = dict(sales=1000, cogs=650, long_term_debt=150, total_assets=1000)

no_merton = spring_score(z=3.555, f_score=9, m_score=-2.48, curr=curr, prior=prior)
# A healthy company: a LOW PD should nudge the composite the same direction (up or equal),
# and a HIGH PD should drag it down. Coverage rises to full only when Merton is present.
low_pd = spring_score(z=3.555, f_score=9, m_score=-2.48, curr=curr, prior=prior,
                      pd_merton=0.005)
high_pd = spring_score(z=3.555, f_score=9, m_score=-2.48, curr=curr, prior=prior,
                       pd_merton=0.40)

check("no-Merton coverage == 100/115 (~0.87)",
      no_merton.coverage == round(100 / 115, 2))
check("with-Merton coverage == 1.0", low_pd.coverage == 1.0)
check("high PD drags the composite below the no-Merton score",
      high_pd.score < no_merton.score)
check("a high-PD firm marks the merton ingredient available",
      high_pd.components["merton"]["available"] is True)
check("no-Merton run marks the merton ingredient unavailable",
      no_merton.components["merton"]["available"] is False)

# The composite VALUE for a fundamentals-only company must be identical to the
# pre-Merton behavior (denominator is available weight, not the 115 total). The
# hand-computed expectation from test_spring_score.py for these inputs is 87.
check("fundamentals-only composite value unchanged (== 87)", no_merton.score == 87)


print("BUILD_REPORT WIRING — offline, through the contract (no network)")
import copy
from data import PRESETS
from report import build_report

live_like = copy.deepcopy(PRESETS["Bluechip Industries (sample: healthy)"])
live_like["equity_volatility"] = 0.30           # what data.fetch_live would stamp
rep = build_report(live_like)
mn = rep["scores"]["merton"]
check("merton block is applicable when equity vol is present", mn["applicable"] is True)
check("value is the PD in 0-1", 0.0 <= mn["value"] <= 1.0)
check("dd and label are carried", mn["dd"] is not None and mn["label"] in
      ("Remote", "Low", "Elevated", "High", "Severe"))
check("why sentence names the market-implied read",
      "default probability" in (mn["why"] or ""))
# F = current_liabilities + 0.5 * long_term_debt = 1200 + 0.5 * 900 = 1650.
check("default point follows the KMV convention (F == 1650)", mn["face_debt"] == 1650.0)
check("spring folds the merton ingredient in",
      rep["scores"]["spring"]["components"]["merton"]["available"] is True
      and rep["scores"]["spring"]["coverage"] == 1.0)

no_vol = copy.deepcopy(PRESETS["Bluechip Industries (sample: healthy)"])
rep2 = build_report(no_vol)
mn2 = rep2["scores"]["merton"]
check("without equity vol the block is N/A with a plain note",
      mn2["applicable"] is False and "history" in mn2["note"])
check("and the composite reweights honestly (coverage 100/115)",
      rep2["scores"]["spring"]["coverage"] == round(100 / 115, 2))


print("SPRING WHY SENTENCE — merton can be named as the lift or the drag")
from commentary import explain

# Make merton the dominant drag: strong fundamentals, severe market-implied PD.
drag_case = spring_score(z=4.0, f_score=9, m_score=-3.0, curr=curr, prior=prior,
                         pd_merton=0.45)
why_drag = explain(None, None, None,
                   {"health": "Watch", "integrity": "Clean"}, spring=drag_case)
check("severe PD is named as the drag",
      "market-implied default risk (Merton)" in why_drag["spring"])
# Make merton the dominant lift: weak fundamentals, remote market-implied PD.
weak_curr2 = dict(net_income=-50, cfo=-60, total_assets=1000,
                  sales=800, cogs=700, long_term_debt=300)
weak_prior2 = dict(sales=1000, cogs=700, long_term_debt=200, total_assets=1000)
lift_case = spring_score(z=0.5, f_score=1, m_score=-0.5, curr=weak_curr2,
                         prior=weak_prior2, pd_merton=0.001)
why_lift = explain(None, None, None,
                   {"health": "Watch", "integrity": "Clean"}, spring=lift_case)
check("remote PD is named as the lift",
      "low market-implied default risk (Merton)" in why_lift["spring"])


print("VOLATILITY HELPER — pure, no network")
from prices import annualized_volatility

check("constant series -> zero volatility",
      annualized_volatility([100.0] * 30) == 0.0)
# Alternating +1%/-1% daily log-returns: sample stdev of the return series is
# sqrt(n/(n-1)) * 0.01... easier: build the series, then just verify against the
# direct formula computed here by hand from the same return list.
series = [100.0]
for i in range(40):
    series.append(series[-1] * (1.01 if i % 2 == 0 else 1 / 1.01))
rets = [math.log(series[i] / series[i - 1]) for i in range(1, len(series))]
mean = sum(rets) / len(rets)
sd = math.sqrt(sum((r - mean) ** 2 for r in rets) / (len(rets) - 1))
check("known series matches the hand formula",
      abs(annualized_volatility(series) - sd * math.sqrt(252)) < 1e-12)
check("too-short series -> None (honest degradation)",
      annualized_volatility([100.0, 101.0, 102.0]) is None)
check("None and NaN entries are dropped, not crashed on",
      annualized_volatility([100.0, None, float("nan")] + [100.0] * 25) == 0.0)
check("empty input -> None", annualized_volatility([]) is None)
check("None input -> None", annualized_volatility(None) is None)


print(f"\n{passed} checks passed.")
