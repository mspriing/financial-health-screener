"""
Verification of the sector-benchmarking stats (benchmark.py).
Run:  python3 tests/test_benchmark.py

These prove the pure stats behave as the UI relies on:
  * a company above its sector median lands above the 50th percentile,
  * a sector with fewer than MIN_PEERS valid peers is reported "too thin",
  * null / missing peer values are excluded from the stats (not treated as 0).
All inputs are tiny hand-built row lists — no live data, no snapshot file needed.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from benchmark import sector_stats, position, MIN_PEERS

passed = 0
def check(name, cond):
    global passed
    status = "PASS" if cond else "FAIL"
    if cond:
        passed += 1
    print(f"  [{status}] {name}")
    assert cond, f"FAILED: {name}"


def row(tr, sector, z=None, f=None, m=None):
    return {"tr": tr, "sector": sector, "z": z, "f_score": f, "m_score": m}


# A Technology sector with 10 peers whose Z runs 1..10, plus the screened company SELF.
tech = [row(f"T{i}", "Technology", z=float(i), f=i % 9, m=-2.0) for i in range(1, 11)]
tech.append(row("SELF", "Technology", z=8.0, f=8, m=-2.5))

print("SECTOR STATS — Technology (10 peers, Z = 1..10)")
stats = sector_stats(tech, "Technology", exclude_ticker="SELF")
sz = stats["z"]
check("peer count excludes SELF (10, not 11)", sz.count == 10)
check("not thin (>= MIN_PEERS)", sz.thin is False)
# median of 1..10 (linear) = 5.5; p25 = 3.25; p75 = 7.75
check("median ≈ 5.5", abs(sz.median - 5.5) < 1e-9)
check("p25 ≈ 3.25", abs(sz.p25 - 3.25) < 1e-9)
check("p75 ≈ 7.75", abs(sz.p75 - 7.75) < 1e-9)

print("PERCENTILE — above-median company lands > 50th")
# SELF Z = 8.0 vs peers 1..10 -> 7 strictly below, 1 equal (the peer at 8) -> 75th
pos = position(8.0, sz.values)
check("Z=8 in Tech is > 50th percentile", pos > 50)
check("Z=8 in Tech ≈ 75th percentile (ties at half weight)", abs(pos - 75.0) < 1e-9)
# A below-median company lands < 50th
check("Z=2 in Tech is < 50th percentile", position(2.0, sz.values) < 50)

print("TOO THIN — fewer than MIN_PEERS valid peers")
# Only 4 financials carry a Z; the rest are null (like real banks).
fin = [row(f"F{i}", "Financial Services", z=float(i)) for i in range(1, 5)]
fin += [row(f"B{i}", "Financial Services", z=None) for i in range(1, 8)]   # banks: null Z
fstats = sector_stats(fin, "Financial Services")
check(f"MIN_PEERS is {MIN_PEERS}", MIN_PEERS == 8)
check("Z marked thin (only 4 valid peers)", fstats["z"].thin is True)
check("thin metric reports its real count (4)", fstats["z"].count == 4)
check("thin metric leaves quartiles None", fstats["z"].median is None)

print("NULLS EXCLUDED — missing values never enter the stats")
# 8 valid Z values all = 10, plus 5 nulls. Median must be 10 (nulls are not 0).
mixed = [row(f"V{i}", "Healthcare", z=10.0) for i in range(8)]
mixed += [row(f"N{i}", "Healthcare", z=None) for i in range(5)]
hstats = sector_stats(mixed, "Healthcare")
check("count counts only non-null peers (8, not 13)", hstats["z"].count == 8)
check("median is 10, not dragged toward 0 by nulls", abs(hstats["z"].median - 10.0) < 1e-9)
check("position ignores nulls (value=10 among all-10 peers = 50th)",
      abs(position(10.0, hstats["z"].values) - 50.0) < 1e-9)

print("EMPTY / UNKNOWN SECTOR — degrades, doesn't crash")
none_stats = sector_stats(tech, None)
check("no sector => thin for every metric", all(none_stats[m].thin for m in none_stats))
check("position with no peers returns None", position(5.0, []) is None)
check("position with no value returns None", position(None, [1.0, 2.0]) is None)

print(f"\n{passed} checks passed.")
