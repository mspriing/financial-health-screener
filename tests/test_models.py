"""
Verification of the scoring math against hand-computed expected values.
Run:  python3 tests/test_models.py
Every expected number below was worked out by hand (see comments) — this proves
the formulas are implemented correctly, independent of any live data source.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from models import altman_z, beneish_m, piotroski_f, overall_verdict

passed = 0
def check(name, cond):
    global passed
    status = "PASS" if cond else "FAIL"
    if cond:
        passed += 1
    print(f"  [{status}] {name}")
    assert cond, f"FAILED: {name}"


print("ALTMAN Z-SCORE")
# TA=1000 TL=400 WC=200 RE=300 EBIT=150 MVE=800 Sales=1200
# X1=.2 X2=.3 X3=.15 X4=2.0 X5=1.2
# Z = 1.2(.2)+1.4(.3)+3.3(.15)+0.6(2.0)+1.0(1.2) = .24+.42+.495+1.2+1.2 = 3.555
a = altman_z(working_capital=200, retained_earnings=300, ebit=150,
             market_value_equity=800, sales=1200, total_assets=1000,
             total_liabilities=400)
check("Z ≈ 3.555", abs(a.z - 3.555) < 0.02)
check("zone == Safe", a.zone == "Safe")
# A distressed firm: negative WC, negative RE, thin EBIT, low market cushion
ad = altman_z(working_capital=-150, retained_earnings=-200, ebit=10,
              market_value_equity=100, sales=400, total_assets=1000,
              total_liabilities=900)
# X1=-.15 X2=-.2 X3=.01 X4=.111 X5=.4
# Z = 1.2(-.15)+1.4(-.2)+3.3(.01)+0.6(.111)+1.0(.4)
#   = -.18-.28+.033+.0667+.4 = .0397
check("distressed Z < 1.81", ad.z < 1.81)
check("zone == Distress", ad.zone == "Distress")

print("PIOTROSKI F-SCORE")
strong_curr = dict(net_income=100, cfo=120, total_assets=1000, long_term_debt=100,
                   current_assets=400, current_liabilities=200, shares=50,
                   sales=1200, cogs=700)
strong_prior = dict(net_income=50, cfo=60, total_assets=1000, long_term_debt=150,
                    current_assets=300, current_liabilities=200, shares=50,
                    sales=1000, cogs=650)
p = piotroski_f(strong_curr, strong_prior)   # all 9 criteria pass by construction
check("strong company F == 9", p.score == 9)

weak_curr = dict(net_income=-50, cfo=-60, total_assets=1000, long_term_debt=300,
                 current_assets=200, current_liabilities=300, shares=80,
                 sales=800, cogs=700)
weak_prior = dict(net_income=20, cfo=30, total_assets=1000, long_term_debt=200,
                  current_assets=300, current_liabilities=200, shares=50,
                  sales=1000, cogs=700)
pw = piotroski_f(weak_curr, weak_prior)       # 0 criteria pass by construction
check("weak company F == 0", pw.score == 0)

print("BENEISH M-SCORE")
# Steady-state firm: every index = 1, TATA = 0  ->  M = -4.84 + 2.360 = -2.48
flat = dict(receivables=100, sales=1000, cogs=600, current_assets=500, ppe=300,
            total_assets=1000, depreciation=50, sga=100, current_liabilities=200,
            long_term_debt=100, net_income=80, cfo=80)
b = beneish_m(curr=dict(flat), prior=dict(flat))
check("steady-state M ≈ -2.48", abs(b.m - (-2.48)) < 0.05)
check("steady-state NOT flagged", b.flag is False)

# Manipulator: receivables balloon (DSRI 2.73) + big accruals (TATA .164) -> M ≈ -0.25
man_curr = dict(receivables=300, sales=1100, cogs=600, current_assets=700, ppe=300,
                total_assets=1100, depreciation=40, sga=100, current_liabilities=200,
                long_term_debt=100, net_income=200, cfo=20)
man_prior = dict(receivables=100, sales=1000, cogs=600, current_assets=500, ppe=300,
                 total_assets=1000, depreciation=50, sga=100, current_liabilities=200,
                 long_term_debt=100, net_income=80, cfo=80)
bm = beneish_m(curr=man_curr, prior=man_prior)
check("manipulator M ≈ -0.25", abs(bm.m - (-0.25)) < 0.1)
check("manipulator IS flagged", bm.flag is True)
check("DSRI ≈ 2.727", abs(bm.indices["DSRI"] - 2.727) < 0.01)
check("TATA ≈ 0.164", abs(bm.indices["TATA"] - 0.164) < 0.01)

print("OVERALL VERDICT")
v = overall_verdict(a, p, b)
check("healthy + clean", v["health"] == "Healthy" and v["integrity"] == "Clean")
vman = overall_verdict(ad, pw, bm)
check("distressed + manipulation", vman["health"] == "Distressed" and vman["integrity"] == "Possible manipulation")

print(f"\n{passed} checks passed.")
