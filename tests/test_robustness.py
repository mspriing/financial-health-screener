"""
Graceful-degradation tests: a company missing inputs for one model must still get
scored on the others, and must never crash.
Run:  python3 tests/test_robustness.py
"""
import sys, os, copy
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data import PRESETS, run_models

passed = 0
def check(name, cond):
    global passed
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    assert cond, f"FAILED: {name}"
    passed += 1


print("BANK-LIKE COMPANY (no current-asset structure, no COGS)")
bank = copy.deepcopy(PRESETS["Bluechip Industries  —  (sample: healthy)"])
for yr in ("curr", "prior"):
    for k in ("current_assets", "current_liabilities", "cogs", "receivables"):
        bank[yr][k] = None
altman, piotroski, beneish, verdict, notes = run_models(bank)   # must NOT raise
check("Altman is N/A", altman is None)
check("Piotroski is N/A", piotroski is None)
check("Beneish is N/A", beneish is None)
check("health == Unknown", verdict["health"] == "Unknown")
check("integrity == Not enough data", verdict["integrity"] == "Not enough data")
check("notes explain all three", len(notes) == 3)

print("SINGLE-YEAR DATA (prior year missing)")
one = copy.deepcopy(PRESETS["Bluechip Industries  —  (sample: healthy)"])
one["prior"] = {k: None for k in one["prior"]}
altman, piotroski, beneish, verdict, notes = run_models(one)    # must NOT raise
check("Altman still computes (uses current year only)", altman is not None)
check("Piotroski is N/A (needs prior year)", piotroski is None)
check("Beneish is N/A (needs prior year)", beneish is None)
check("health still derived from Altman", verdict["health"] in ("Healthy", "Watch", "Distressed"))

print("NORMAL COMPANY still fully scores")
ok = run_models(PRESETS["Momentum Software Co.  —  (sample: earnings red flags)"])
a, p, b, v, n = ok
check("all three present", a is not None and p is not None and b is not None)
check("no N/A notes", len(n) == 0)

print(f"\n{passed} checks passed.")
