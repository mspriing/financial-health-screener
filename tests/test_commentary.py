"""
Tests for the "why" engine (commentary.py). These prove the attribution is
finance-correct and reproducible: the sentences must name the components the
model's own coefficients say are driving each score.
Run:  python3 tests/test_commentary.py   (also collectable by pytest)
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from models import altman_z, beneish_m, piotroski_f, overall_verdict
from commentary import explain

passed = 0
def check(name, cond):
    global passed
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    assert cond, f"FAILED: {name}"
    passed += 1


print("ALTMAN COMMENTARY")
# Distressed firm engineered so a deeply negative EBIT makes X3 the single largest
# drag, thin working capital makes X1 the second, and a fat market cushion (X4) the
# main support. Contributions (coef x ratio): X1 .06, X2 .56, X3 -.99, X4 1.35, X5 .80.
ad = altman_z(working_capital=50, retained_earnings=400, ebit=-300,
              market_value_equity=900, sales=800, total_assets=1000,
              total_liabilities=400)
out = explain(ad, None, None, {"health": "Distressed", "integrity": "Not enough data"})
check("altman sentence present", isinstance(out["altman"], str) and out["altman"])
check("names operating productivity (X3) as a drag", "operating productivity (X3)" in out["altman"])
check("X3 is the lowest contribution -> named before the second drag X1",
      out["altman"].index("operating productivity (X3)") < out["altman"].index("thin liquidity (X1)"))
check("names the market-value cushion (X4) as the main support",
      "the market-value cushion (X4)" in out["altman"])
check("identifies the distress zone", "distress zone" in out["altman"])

# A clearly safe firm should read as supported, not dragged.
asafe = altman_z(working_capital=200, retained_earnings=300, ebit=150,
                 market_value_equity=800, sales=1200, total_assets=1000,
                 total_liabilities=400)
safe_out = explain(asafe, None, None, {"health": "Healthy", "integrity": "Clean"})
check("safe firm reads as 'safe zone'", "safe zone" in safe_out["altman"])
check("safe firm is framed by its strength, not dragged", "on the strength of" in safe_out["altman"])
check("safe firm names thin liquidity (X1) as the lightest contributor",
      "liquidity (X1) the relatively lightest" in safe_out["altman"])


print("BENEISH COMMENTARY")
# Manipulator driven by ballooning receivables (DSRI) + big accruals (TATA).
man_curr = dict(receivables=300, sales=1100, cogs=600, current_assets=700, ppe=300,
                total_assets=1100, depreciation=40, sga=100, current_liabilities=200,
                long_term_debt=100, net_income=200, cfo=20)
man_prior = dict(receivables=100, sales=1000, cogs=600, current_assets=500, ppe=300,
                 total_assets=1000, depreciation=50, sga=100, current_liabilities=200,
                 long_term_debt=100, net_income=80, cfo=80)
bm = beneish_m(curr=man_curr, prior=man_prior)
bout = explain(None, None, bm, {"health": "Unknown", "integrity": "Possible manipulation"})
check("beneish sentence present", isinstance(bout["beneish"], str) and bout["beneish"])
check("flagged M says it clears the threshold", "clears the" in bout["beneish"])
check("names total accruals (TATA)", "total accruals (TATA)" in bout["beneish"])
check("includes the growth/accruals caveat",
      "validate before concluding" in bout["beneish"])

# Steady-state firm: every index ~1, TATA ~0 -> clean, no manipulation push.
flat = dict(receivables=100, sales=1000, cogs=600, current_assets=500, ppe=300,
            total_assets=1000, depreciation=50, sga=100, current_liabilities=200,
            long_term_debt=100, net_income=80, cfo=80)
bflat = beneish_m(curr=dict(flat), prior=dict(flat))
flat_out = explain(None, None, bflat, {"health": "Unknown", "integrity": "Clean"})
check("clean M says it stays clean", "stays clean" in flat_out["beneish"])
check("clean M does NOT claim to clear the threshold", "clears the" not in flat_out["beneish"])


print("PIOTROSKI COMMENTARY")
# Strong-on-profitability firm that loses points on leverage (debt up) and
# dilution (new shares). Build it so exactly tests 5 and 7 fail.
curr = dict(net_income=100, cfo=120, total_assets=1000, long_term_debt=160,
            current_assets=400, current_liabilities=200, shares=80,
            sales=1200, cogs=700)
prior = dict(net_income=50, cfo=60, total_assets=1000, long_term_debt=150,
             current_assets=300, current_liabilities=200, shares=50,
             sales=1000, cogs=650)
p = piotroski_f(curr, prior)
pout = explain(None, p, None, {"health": "Watch", "integrity": "Not enough data"})
check("piotroski sentence present", isinstance(pout["piotroski"], str) and pout["piotroski"])
check("reports the score as F = N/9", f"F = {p.score}/9" in pout["piotroski"])
check("gives profitability full marks", "full marks on profitability" in pout["piotroski"])
check("names the higher debt ratio", "higher long-term-debt ratio" in pout["piotroski"])
check("names new-share dilution", "new-share dilution" in pout["piotroski"])

# Perfect 9/9 firm -> clean sweep wording, no "lost points".
strong_curr = dict(net_income=100, cfo=120, total_assets=1000, long_term_debt=100,
                   current_assets=400, current_liabilities=200, shares=50,
                   sales=1200, cogs=700)
strong_prior = dict(net_income=50, cfo=60, total_assets=1000, long_term_debt=150,
                    current_assets=300, current_liabilities=200, shares=50,
                    sales=1000, cogs=650)
p9 = piotroski_f(strong_curr, strong_prior)
p9out = explain(None, p9, None, {"health": "Healthy", "integrity": "Clean"})
check("9/9 reads as full marks", "F = 9/9 — full marks" in p9out["piotroski"])
check("9/9 has no 'lost points'", "lost points" not in p9out["piotroski"])


print("OVERALL LINE + N/A HANDLING")
full = explain(asafe, p9, bflat, overall_verdict(asafe, p9, bflat))
check("overall is always a string", isinstance(full["overall"], str) and full["overall"])
check("healthy + clean reflected in overall",
      "financially healthy" in full["overall"] and "clean" in full["overall"])
check("all three model sentences present when all models run",
      full["altman"] and full["piotroski"] and full["beneish"])

none_out = explain(None, None, None, {"health": "Unknown", "integrity": "Not enough data"})
check("N/A models map to None", none_out["altman"] is None
      and none_out["piotroski"] is None and none_out["beneish"] is None)
check("overall still produced for an all-N/A company", isinstance(none_out["overall"], str))


print(f"\n{passed} checks passed.")
