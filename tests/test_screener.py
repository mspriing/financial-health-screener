"""
Verification of the M&A target screener (screener.py).
Run:  python3 tests/test_screener.py

Proves the profile gates and — most importantly — the data-quality guards that keep
garbage valuations out:
  * a strong + cheap + grey company surfaces in value mode,
  * a Beneish-flagged company is excluded,
  * a negative or 0.001 P/B is NEVER treated as "cheap",
  * a financial (null Z) is excluded from the value screen,
  * a healthy, clean company surfaces in strategic mode.
All rows are hand-built — no live data, no snapshot file needed.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from screener import (value_targets, strategic_targets, sector_valuation_medians,
                      valid_pb, valid_ev, fmt_z)

passed = 0
def check(name, cond):
    global passed
    status = "PASS" if cond else "FAIL"
    if cond:
        passed += 1
    print(f"  [{status}] {name}")
    assert cond, f"FAILED: {name}"


def row(tr, sector="Tech", z=None, zone=None, f=None, m=-2.5, flag="False",
        pb=None, ev=None, mc=1e10):
    return {"tr": tr, "name": tr + " Inc.", "sector": sector, "z": z, "zone": zone,
            "f_score": f, "m_score": m, "m_flag": flag, "price_to_book": pb,
            "ev_ebitda": ev, "market_cap": mc}


# A Tech sector whose valid P/B values are 2,3,4,5 (median 3.5) and EV 10,12,14 (median 12).
universe = [
    row("PEER1", pb=2.0, ev=10.0, z=4.0, zone="Safe", f=7),
    row("PEER2", pb=3.0, ev=12.0, z=4.0, zone="Safe", f=7),
    row("PEER3", pb=4.0, ev=14.0, z=4.0, zone="Safe", f=7),
    row("PEER4", pb=5.0, z=4.0, zone="Safe", f=7),
    # The classic value target: grey-zone Z, strong F, clean, cheap on BOTH valuations.
    row("GOODBUY", z=2.3, zone="Grey", f=8, flag="False", pb=2.0, ev=8.0),
    # Same profile but Beneish-flagged -> must be excluded from value.
    row("FLAGGED", z=2.3, zone="Grey", f=8, flag="True", pb=2.0, ev=8.0),
    # Same profile but its only "cheap" signal is a glitch P/B (0.001) and an above-median EV.
    row("GLITCH", z=2.3, zone="Grey", f=8, flag="False", pb=0.001, ev=20.0),
    # Same profile but a NEGATIVE P/B and no valid EV -> not cheap.
    row("NEGBOOK", z=2.3, zone="Grey", f=8, flag="False", pb=-1.5, ev=None),
    # A financial: null Z (Altman N/A) -> excluded from value even though it looks cheap.
    row("BANK", sector="Financial Services", z=None, zone=None, f=7, pb=0.8, ev=6.0),
]

print("SECTOR VALUATION MEDIANS — valid values only")
med = sector_valuation_medians(universe)
# Tech valid P/B (GLITCH 0.001 and NEGBOOK -1.5 excluded): PEER1=2, PEER2=3, PEER3=4,
# PEER4=5, GOODBUY=2, FLAGGED=2 -> [2,2,2,3,4,5], median (2+3)/2 = 2.5
check("Tech P/B median ignores glitch & negative", abs(med["Tech"]["pb"] - 2.5) < 1e-9)
check("0.001 P/B is not a valid 'cheap' signal", valid_pb(0.001) is False)
check("negative P/B is not valid", valid_pb(-1.5) is False)
check("30+ P/B is not valid", valid_pb(45.0) is False)
check("2.0 P/B is valid", valid_pb(2.0) is True)
check("negative EV/EBITDA is not valid", valid_ev(-3.0) is False)

print("VALUE MODE — gates and guards")
vt = value_targets(universe)
tickers = [d["ticker"] for d in vt]
check("strong + cheap + grey company surfaces", "GOODBUY" in tickers)
check("Beneish-flagged company excluded", "FLAGGED" not in tickers)
check("glitch 0.001 P/B never counts as cheap", "GLITCH" not in tickers)
check("negative-book company never counts as cheap", "NEGBOOK" not in tickers)
check("financial (null Z) excluded from value screen", "BANK" not in tickers)
good = next(d for d in vt if d["ticker"] == "GOODBUY")
check("value 'why' names the grey zone and F", "Grey-zone Z 2.3" in good["why"] and "F=8" in good["why"])
check("value 'why' quotes a sector-relative multiple", "the sector’s median" in good["why"])
check("value result carries a fit_score", isinstance(good["fit_score"], (int, float)))

print("VALUE MODE — sector filter")
only_fin = value_targets(universe, sector="Financial Services")
check("value screen in a financial sector is empty (Altman N/A)", only_fin == [])

print("STRATEGIC MODE — strong & clean surfaces")
st_targets = strategic_targets(universe)
st_tickers = [d["ticker"] for d in st_targets]
check("healthy clean company (Safe Z, high F) surfaces", "PEER1" in st_tickers)
check("Beneish-flagged company excluded from strategic too", "FLAGGED" not in st_tickers)
# A clearly healthy clean operator, ranked first by F then Z.
universe2 = universe + [row("STAR", sector="Defensive", z=5.1, zone="Safe", f=8, flag="False")]
star = strategic_targets(universe2, sector="Defensive")
check("strong clean operator surfaces in its sector", star and star[0]["ticker"] == "STAR")
check("strategic 'why' reads cleanly", "Safe-zone Z 5.1" in star[0]["why"]
      and "clean earnings" in star[0]["why"] and "Defensive" in star[0]["why"])

print("ASSET-LIGHT Z — capped display and ranking (Altman degrades for low-liability firms)")
check("Z above 15 displays as '15+'", fmt_z(132.3) == "15+")
check("normal Z displays raw (1 dp)", fmt_z(5.06) == "5.1")
check("null Z displays N/A", fmt_z(None) == "N/A")
# Two equal-F clean operators: one absurd Z (132), one solid Z (8). F-led ranking must
# NOT put the Z=132 firm first just because its Z is bigger.
rank_u = [
    row("PLTRLIKE", sector="Tech", z=132.0, zone="Safe", f=8, flag="False"),
    row("SOLID",    sector="Tech", z=8.0,   zone="Safe", f=8, flag="False"),
    row("HIGHERF",  sector="Tech", z=8.0,   zone="Safe", f=9, flag="False"),
]
sr = strategic_targets(rank_u, sector="Tech")
fit = {d["ticker"]: d["fit_score"] for d in sr}
check("highest F ranks first (not the Z=132 firm)", sr[0]["ticker"] == "HIGHERF")
check("extreme-Z firm does NOT outrank a higher-F firm", fit["HIGHERF"] > fit["PLTRLIKE"])
# The Z tiebreaker is bounded: capped Z contributes < 1 full F point, so it can never
# leapfrog a company that is a whole F-point stronger.
check("capped-Z tiebreaker stays below one F point", (fit["PLTRLIKE"] - fit["SOLID"]) < 1.0)
pltr_why = next(d for d in sr if d["ticker"] == "PLTRLIKE")["why"]
check("Z=132 'why' shows 15+, not the raw number", "Z 15+" in pltr_why and "132" not in pltr_why)

print("VALUE MODE — cheap on one metric but pricey on the other is rejected")
# Tech medians: P/B 2.5, EV 12 (from `universe`). Cheap on P/B (1.0) but EV 25 (>1.5x*12=18).
pricey = universe + [row("HALFCHEAP", z=2.3, zone="Grey", f=8, flag="False", pb=1.0, ev=25.0)]
vt2 = [d["ticker"] for d in value_targets(pricey)]
check("cheap-on-PB but pricey-on-EV company is NOT a value target", "HALFCHEAP" not in vt2)
# Same but EV is genuinely cheap (8 < 12) -> should surface.
ok = universe + [row("REALCHEAP", z=2.3, zone="Grey", f=8, flag="False", pb=1.0, ev=8.0)]
check("cheap on both metrics still surfaces", "REALCHEAP" in [d["ticker"] for d in value_targets(ok)])

print("PER-COMPANY DETAIL — 'why it's a target' fields are present, specific, and mode-aware")
DETAIL_KEYS = ("operations", "balance_sheet", "valuation", "read")
# Every value result must carry all four detail fields as non-empty plain sentences.
for d in vt:
    for k in DETAIL_KEYS:
        check(f"value result {d['ticker']} carries a non-empty '{k}'",
              isinstance(d.get(k), str) and d[k].strip().endswith("."))
# The detail must never contain an em/en dash or a curly apostrophe (plain copy only).
for d in vt:
    blob = " ".join(d[k] for k in DETAIL_KEYS)
    check(f"value detail for {d['ticker']} has no em/en dash", "—" not in blob and "–" not in blob)
    check(f"value detail for {d['ticker']} uses straight apostrophes", "’" not in blob)

gd = next(d for d in vt if d["ticker"] == "GOODBUY")
check("operations names the actual F out of 9", "Piotroski F of 8 out of 9" in gd["operations"])
check("balance sheet names the actual Z and its zone",
      "Altman Z of 2.3" in gd["balance_sheet"] and "grey zone" in gd["balance_sheet"])
# Valuation must cite a real sector-relative number, not just a verdict.
check("valuation cites the sector median with a number",
      "sector median of" in gd["valuation"] and "percent" in gd["valuation"])
check("valuation quotes the company's own price-to-book", "price-to-book of 2.0" in gd["valuation"])
check("value read frames the discount / stressed-balance-sheet thesis",
      "discount" in gd["read"] and "balance sheet" in gd["read"])

# Strategic results carry the same fields, but the read and balance-sheet framing differ.
sd_list = strategic_targets(universe)
sd = next(d for d in sd_list if d["ticker"] == "PEER1")   # Safe-zone Z, valid valuations
for k in DETAIL_KEYS:
    check(f"strategic result {sd['ticker']} carries a non-empty '{k}'",
          isinstance(sd.get(k), str) and sd[k].strip().endswith("."))
check("strategic valuation still cites a sector-relative number",
      "sector median of" in sd["valuation"])
check("strategic balance sheet frames Z as strength, not stress",
      "strength" in sd["balance_sheet"] and "stress" not in sd["balance_sheet"])
check("value vs strategic read differ", gd["read"] != sd["read"])
check("strategic read is the 'clean operator / exposure' thesis",
      "clean operator" in sd["read"] and "exposure" in sd["read"])
check("value read is the 'value-buyout' thesis", "value-buyout" in gd["read"])

print(f"\n{passed} checks passed.")
