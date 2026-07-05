"""
Verification of the point-in-time backtest engine (backtest.py).
Run:  python3 tests/test_backtest.py

No network, all fixtures synthetic. Proves the four things that make a backtest
honest:
  * filing-date discipline: a filing dated after the as-of date is never selected,
    even if its statement period ended before that date,
  * the assembled payload matches the data.py shape and run_models returns exactly
    the same scores as a hand-built payload,
  * classify_trail returns the right first-warning model, date, and lead time on a
    known trail, and an honest miss on a clean trail,
  * point-in-time MVE is the as-of close times the selected filing's shares.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backtest import (select_filing, price_on, point_in_time_mve, build_snapshots,
                      classify_trail)
from data import run_models, PRESETS

passed = 0
def check(name, cond):
    global passed
    status = "PASS" if cond else "FAIL"
    if cond:
        passed += 1
    print(f"  [{status}] {name}")
    assert cond, f"FAILED: {name}"


# Two synthetic filings built from the sample payloads (real model output, no network).
HEALTHY = PRESETS["Bluechip Industries (sample: healthy)"]
DISTRESSED = pl = PRESETS["Legacy Retail Co. (sample: distressed)"]

def filing(filing_date, period_end, payload, form="10-K", shares=1000.0):
    return {"filing_date": filing_date, "period_end": period_end, "form": form,
            "curr": dict(payload["curr"]), "prior": dict(payload["prior"]),
            "shares": shares}

F_2023 = filing("2024-01-26", "2023-12-30", HEALTHY)             # healthy year
F_2024 = filing("2025-01-31", "2024-12-28", DISTRESSED)          # distressed year

PRICES = {"2024-12-31": 20.0, "2025-01-30": 19.0, "2025-01-31": 18.0,
          "2025-03-28": 12.0, "2025-06-30": 10.0}

print("FILING-DATE DISCIPLINE")
# On 2025-01-01 the FY2024 10-K (filed 2025-01-31) does not exist yet, even though
# its statement period ended 2024-12-28.
sel = select_filing([F_2023, F_2024], "2025-01-01")
check("period ended before as-of but filed after: NOT selected",
      sel["period_end"] == "2023-12-30")
check("once filed, the newer filing is selected",
      select_filing([F_2023, F_2024], "2025-01-31")["period_end"] == "2024-12-28")
check("no filing yet -> None", select_filing([F_2023, F_2024], "2023-06-30") is None)

print("POINT-IN-TIME PRICES AND MVE")
d, px = price_on(PRICES, "2025-03-31")           # weekend: last close is 03-28
check("price falls back to the last close at or before the date",
      d == "2025-03-28" and px == 12.0)
px_used, mve = point_in_time_mve(PRICES, "2025-06-30", 1000.0)
check("MVE = as-of close times filing shares (10.0 x 1000)", mve == 10000.0)
check("missing shares -> MVE None, price still reported",
      point_in_time_mve(PRICES, "2025-06-30", None) == (10.0, None))

print("PAYLOAD ASSEMBLY MATCHES data.py EXACTLY")
rows = build_snapshots([F_2023, F_2024], PRICES, ["2025-01-01", "2025-06-30"])
# Hand-built payload for the same date: healthy filing + MVE 20.0 x 1000 (close 2024-12-31)
hand = dict(HEALTHY)
hand = {"meta": HEALTHY["meta"], "market_value_equity": 20.0 * 1000.0,
        "curr": HEALTHY["curr"], "prior": HEALTHY["prior"]}
h_alt, h_pio, h_ben, h_verdict, _ = run_models(hand)
r0 = rows[0]
check("snapshot uses the healthy filing on 2025-01-01",
      r0["statement_period_used"] == "2023-12-30" and r0["filing_date_used"] == "2024-01-26")
check("Z matches a hand-built run_models call", r0["z"] == h_alt.z)
check("F matches", r0["f_score"] == h_pio.score)
check("M matches", r0["m_score"] == h_ben.m)
check("verdict matches", r0["verdict"] == h_verdict["health"])
check("MVE recorded (20.0 x 1000)", r0["mve_used"] == 20000.0)
check("price recorded", r0["price_used"] == 20.0)

r1 = rows[1]
check("after the 10-K files, the distressed year takes over",
      r1["statement_period_used"] == "2024-12-28")
check("distressed year scores as a warning", r1["zone"] in ("Grey", "Distress"))

print("BUILD - date with no filing degrades honestly")
r_none = build_snapshots([F_2023], PRICES, ["2023-06-30"])[0]
check("no-filing row has None scores and a note",
      r_none["z"] is None and "No filing" in r_none["note"])

print("CLASSIFY - known first warning: right model, date, lead time")
def snap(as_of, z=None, zone=None, f=None, m=None, m_flag=None, verdict="Healthy"):
    return {"as_of_date": as_of, "statement_period_used": "x", "filing_date_used": "x",
            "z": z, "zone": zone, "f_score": f, "m_score": m, "m_flag": m_flag,
            "verdict": verdict, "integrity": "Clean"}

trail = [
    snap("2025-03-31", z=3.5, zone="Safe", f=7, m=-2.5, m_flag=False),
    snap("2025-06-30", z=2.4, zone="Grey", f=6, m=-2.5, m_flag=False, verdict="Watch"),
    snap("2025-09-30", z=1.5, zone="Distress", f=2, m=-1.0, m_flag=True, verdict="Distressed"),
]
v = classify_trail(trail, "2025-12-01", "2026-01-31")
check("hit is declared", v["hit"] is True)
check("first warning date is the grey-zone quarter", v["first_warning_date"] == "2025-06-30")
check("Altman moved first", any("Altman" in m for m in v["first_warning_models"]))
check("only Altman on the first-warning date", len(v["first_warning_models"]) == 1)
check("lead time is 154 days (2025-06-30 to 2025-12-01)", v["lead_days"] == 154)
check("verdict before the window was already a warning",
      v["verdict_before_event"] == "Distressed" and v["warned_before_event"] is True)
check("summary says HIT with the date",
      v["summary"].startswith("HIT") and "2025-06-30" in v["summary"])

print("CLASSIFY - F and M warnings also count")
v2 = classify_trail([snap("2025-06-30", z=4.0, zone="Safe", f=3, m=-2.5, m_flag=False)],
                    "2025-12-01", "2026-01-31")
check("F <= 3 alone is a warning", v2["hit"] and "Piotroski" in v2["first_warning_models"][0])
v3 = classify_trail([snap("2025-06-30", z=4.0, zone="Safe", f=7, m=-1.0, m_flag=True)],
                    "2025-12-01", "2026-01-31")
check("Beneish flag alone is a warning", v3["hit"] and "Beneish" in v3["first_warning_models"][0])

print("CLASSIFY - honest miss")
clean = [snap("2025-03-31", z=4.0, zone="Safe", f=8, m=-2.6, m_flag=False),
         snap("2025-09-30", z=3.8, zone="Safe", f=7, m=-2.4, m_flag=False)]
m = classify_trail(clean, "2025-12-01", "2026-01-31")
check("no warning -> miss", m["hit"] is False and m["first_warning_date"] is None)
check("miss summary says MISS plainly", m["summary"].startswith("MISS"))
check("lead time is None on a miss", m["lead_days"] is None)

late = clean + [snap("2025-12-31", z=1.2, zone="Distress", f=2, m=-1.0, m_flag=True,
                     verdict="Distressed")]
ml = classify_trail(late, "2025-12-01", "2026-01-31")
check("warning inside the window is still a miss", ml["hit"] is False)
check("late-warning summary names the too-late date",
      ml["summary"].startswith("MISS") and "2025-12-31" in ml["summary"])

print(f"\n{passed} checks passed.")
