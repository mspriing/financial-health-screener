"""
Verification of the Spring Score composite against hand-computed expected values.
Run:  python3 tests/test_spring_score.py

Every expected number below was worked out by hand from the anchors and weights in
models.py (see the arithmetic in the comments), the same discipline test_models.py
uses to prove the three underlying models — so the composite is verified
independently of its own implementation.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from models import spring_score, SPRING_WEIGHTS, SPRING_MIN_WEIGHT
from commentary import explain

passed = 0
def check(name, cond):
    global passed
    status = "PASS" if cond else "FAIL"
    if cond:
        passed += 1
    print(f"  [{status}] {name}")
    assert cond, f"FAILED: {name}"


print("WEIGHTS")
check("weights sum to 100", sum(SPRING_WEIGHTS.values()) == 100)
check("six ingredients", len(SPRING_WEIGHTS) == 6)


print("FULL COMPOSITE — healthy company, all six ingredients")
# z=3.555  -> between 2.99 and 6.0: 70 + (3.555-2.99)/(6-2.99)*30       = 75.6312
# f=9      -> 9/9*100                                                   = 100
# m=-2.48  -> between -3.0 and -1.78: 100 + (0.52/1.22)*(50-100)        = 78.6885
# accruals: (100-120)/1000 = -0.02 -> 100 + (0.08/0.10)*(50-100)        = 60
# margin:   500/1200 - 350/1000 = 0.41667-0.35 = +0.0667 (>= +0.05)     = 100
# leverage: 100/1000 - 150/1000 = -0.05 (anchor)                        = 100
# composite = (25*75.6312 + 20*100 + 15*78.6885 + 10*60 + 15*100 + 15*100)/100
#           = (1890.78 + 2000 + 1180.33 + 600 + 1500 + 1500)/100 = 86.71 -> 87
healthy_curr = dict(net_income=100, cfo=120, total_assets=1000,
                    sales=1200, cogs=700, long_term_debt=100)
healthy_prior = dict(sales=1000, cogs=650, long_term_debt=150, total_assets=1000)
s = spring_score(z=3.555, f_score=9, m_score=-2.48,
                 curr=healthy_curr, prior=healthy_prior)
check("score == 87", s.score == 87)
check("tier == Excellent", s.tier == "Excellent")
check("coverage == 1.0", s.coverage == 1.0)
check("altman sub-score ~ 75.6", abs(s.components["altman"]["sub_score"] - 75.6) < 0.1)
check("beneish sub-score ~ 78.7", abs(s.components["beneish"]["sub_score"] - 78.7) < 0.1)
check("accruals sub-score == 60", s.components["accruals"]["sub_score"] == 60)
check("margin trend clamps at 100", s.components["margin_trend"]["sub_score"] == 100)
check("leverage trend hits the -5pp anchor == 100",
      s.components["leverage_trend"]["sub_score"] == 100)


print("FULL COMPOSITE — weak company, all six ingredients")
# z=0.04   -> between 0 and 1.81: (0.04/1.81)*40                        = 0.8840
# f=0      -> 0
# m=-0.25  -> between -1.78 and 0: 50 + (1.53/1.78)*(0-50)              = 7.0225
# accruals: (-50-(-60))/1000 = +0.01 -> 50 + (0.01/0.10)*(0-50)         = 45
# margin:   100/800 - 300/1000 = 0.125-0.30 = -0.175 (<= -0.05)         = 0
# leverage: 300/1000 - 200/1000 = +0.10 (>= +0.05)                      = 0
# composite = (25*0.8840 + 0 + 15*7.0225 + 10*45 + 0 + 0)/100
#           = (22.10 + 105.34 + 450)/100 = 5.77 -> 6
weak_curr = dict(net_income=-50, cfo=-60, total_assets=1000,
                 sales=800, cogs=700, long_term_debt=300)
weak_prior = dict(sales=1000, cogs=700, long_term_debt=200, total_assets=1000)
w = spring_score(z=0.04, f_score=0, m_score=-0.25, curr=weak_curr, prior=weak_prior)
check("score == 6", w.score == 6)
check("tier == Fragile", w.tier == "Fragile")


print("REWEIGHTING — snapshot path: three model scores, no line items")
# altman at z=3.0 -> 70 + (0.01/3.01)*30 = 70.0997; f=9 -> 100; m=-2.48 -> 78.6885
# available weight = 25+20+15 = 60
# composite = (25*70.0997 + 20*100 + 15*78.6885)/60 = 4932.82/60 = 82.21 -> 82
snap = spring_score(z=3.0, f_score=9, m_score=-2.48)
check("score == 82", snap.score == 82)
check("tier == Strong", snap.tier == "Strong")
check("coverage == 0.6", snap.coverage == 0.6)
check("trend components marked unavailable",
      snap.components["margin_trend"]["available"] is False
      and snap.components["accruals"]["available"] is False)


print("REWEIGHTING — bank-style: no Altman, no Beneish, F plus trends")
# f=8 -> 88.8889; accruals (100-150)/1000=-0.05 -> 75; margin flat -> 50; leverage flat -> 50
# weight = 20+10+15+15 = 60
# composite = (20*88.8889 + 10*75 + 15*50 + 15*50)/60 = 4027.78/60 = 67.13 -> 67
bank_curr = dict(net_income=100, cfo=150, total_assets=1000,
                 sales=1000, cogs=600, long_term_debt=200)
bank_prior = dict(sales=900, cogs=540, long_term_debt=200, total_assets=1000)
bank = spring_score(f_score=8, curr=bank_curr, prior=bank_prior)
check("score == 67", bank.score == 67)
check("tier == Fair", bank.tier == "Fair")


print("CLAMPS at the anchor ends")
top = spring_score(z=10.0, f_score=9, m_score=-5.0)     # all past the good ends
check("everything clamps to 100", top.score == 100 and top.tier == "Excellent")
bottom = spring_score(z=-1.0, f_score=0, m_score=1.0)   # all past the bad ends
check("everything clamps to 0", bottom.score == 0 and bottom.tier == "Fragile")


print("MINIMUM-WEIGHT AND BACKBONE RULES")
try:
    spring_score(f_score=5)                              # 20 of 100 weight
    check("f alone raises ValueError", False)
except ValueError:
    check("f alone raises ValueError (below minimum weight)", True)
check("the minimum is stated in the module", SPRING_MIN_WEIGHT == 40)
try:
    spring_score(curr=healthy_curr, prior=healthy_prior)  # 40 weight but no model
    check("trends without any model raise ValueError", False)
except ValueError:
    check("trends without any model raise ValueError (no backbone)", True)
try:
    spring_score()
    check("no inputs raise ValueError", False)
except ValueError:
    check("no inputs raise ValueError", True)


print("WHY SENTENCE (commentary)")
why = explain(None, None, None, {"health": "Unknown", "integrity": "Not enough data"},
              spring=s)
check("sentence names the score and tier", "Spring Score 87" in why["spring"]
      and "excellent" in why["spring"])
check("full coverage doesn't mention partial data", "partial" not in why["spring"])
why_snap = explain(None, None, None,
                   {"health": "Unknown", "integrity": "Not enough data"}, spring=snap)
check("partial coverage says so", "partial data" in why_snap["spring"])
why_weak = explain(None, None, None,
                   {"health": "Unknown", "integrity": "Not enough data"}, spring=w)
check("weak company sentence names a drag", "held back by" in why_weak["spring"])
check("spring=None keeps the old callers working",
      explain(None, None, None, {"health": "Unknown",
                                 "integrity": "Not enough data"})["spring"] is None)

print(f"\n{passed} checks passed.")
