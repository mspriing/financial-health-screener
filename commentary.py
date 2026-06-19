"""
commentary.py — A SAFE "why" engine for the three scoring models.

Given the result objects that models.py already produced, this module writes one
short, analyst-style sentence per model explaining WHICH components are driving
the number, plus an overall line. The attribution is computed ONLY from the
model's own numbers — the same coefficients models.py uses — so every sentence is
fully reproducible and traceable to the math on screen.

There is NO external data here: no news, no API, no LLM, no I/O, no globals. Like
models.py, explain() is a PURE function — it takes the result objects and returns
a dict of strings. That keeps it unit-testable (see tests/test_commentary.py) and
guarantees the commentary can never disagree with the scores it explains.

Attribution logic:
  * Altman   — weight each raw ratio X1..X5 by its Z coefficient to get that term's
               contribution to Z, then name the largest drags and the main support.
  * Beneish  — measure each index's push on M relative to its neutral value
               (1.0 for the seven indices, 0.0 for TATA), then name what is lifting
               M. Growth/accrual-driven pushes get the standard "validate first" caveat.
  * Piotroski— report the score and name the failed tests, grouped by category.
"""
from __future__ import annotations
from typing import Optional


# ---- minus sign matching the app's typography (− U+2212, not a hyphen) ----
MINUS = "−"


def _join_human(items: list) -> str:
    """Join a list into readable prose: 'a', 'a and b', or 'a, b, and c'."""
    items = [x for x in items if x]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + ", and " + items[-1]


# ----------------------------------------------------------------------------
# ALTMAN — contribution = Z coefficient × raw ratio
# ----------------------------------------------------------------------------
_ALTMAN_COEF = {"X1": 1.2, "X2": 1.4, "X3": 3.3, "X4": 0.6, "X5": 1.0}
_ALTMAN_NAMES = {
    "X1": "thin liquidity (X1)",
    "X2": "weak cumulative profitability (X2)",
    "X3": "weak operating productivity (X3)",
    "X4": "a slim market-value cushion (X4)",
    "X5": "low asset turnover (X5)",
}
# plain (non-judgemental) labels for naming a *support* rather than a drag
_ALTMAN_SUPPORT = {
    "X1": "liquidity (X1)",
    "X2": "cumulative profitability (X2)",
    "X3": "operating productivity (X3)",
    "X4": "the market-value cushion (X4)",
    "X5": "asset turnover (X5)",
}


def _altman_sentence(altman) -> str:
    # Each component key looks like "X3 EBIT / Total Assets" -> code "X3".
    contribs = []
    for key, ratio in altman.components.items():
        code = key.split()[0]
        if code in _ALTMAN_COEF:
            contribs.append((code, _ALTMAN_COEF[code] * ratio))
    contribs.sort(key=lambda t: t[1])                 # ascending: drags first
    mean = sum(c for _, c in contribs) / len(contribs)

    drag_codes = [contribs[0][0]]
    if len(contribs) > 1 and contribs[1][1] < mean:   # name a 2nd drag only if it really is one
        drag_codes.append(contribs[1][0])
    support_code = contribs[-1][0]

    drags = _join_human([_ALTMAN_NAMES[c] for c in drag_codes])
    support = _ALTMAN_SUPPORT[support_code]

    if altman.zone == "Safe":
        light = _ALTMAN_SUPPORT[contribs[0][0]]
        return (f"Z sits comfortably in the safe zone on the strength of {support}, "
                f"with {light} the relatively lightest contributor.")
    if altman.zone == "Distress":
        return (f"Z is in the distress zone, dragged down mainly by {drags}; "
                f"{support} is the main support.")
    return f"Z lands in the grey zone, where {drags} weigh it down while {support} holds it up."


# ----------------------------------------------------------------------------
# BENEISH — push on M = coefficient × (value − neutral)
# ----------------------------------------------------------------------------
_BENEISH_COEF = {"DSRI": 0.920, "GMI": 0.528, "AQI": 0.404, "SGI": 0.892,
                 "DEPI": 0.115, "SGAI": -0.172, "TATA": 4.679, "LVGI": -0.327}
_BENEISH_NAMES = {
    "TATA": "total accruals (TATA)",
    "SGI": "rapid sales growth (SGI)",
    "DSRI": "receivables outpacing sales (DSRI)",
    "GMI": "margin deterioration (GMI)",
    "AQI": "softening asset quality (AQI)",
    "DEPI": "slowing depreciation (DEPI)",
    "SGAI": "rising SG&A intensity (SGAI)",
    "LVGI": "rising leverage (LVGI)",
}
# these indices rise with growth / aggressive accruals, not only with manipulation
_BENEISH_GROWTH_ACCRUAL = {"TATA", "SGI", "DSRI"}
_GROWTH_CAVEAT = (", though these signal rapid growth or aggressive accruals as much "
                  "as manipulation, so validate before concluding")


def _beneish_sentence(beneish) -> str:
    pushes = []
    for code, value in beneish.indices.items():
        neutral = 0.0 if code == "TATA" else 1.0
        pushes.append((code, _BENEISH_COEF[code] * (value - neutral)))
    positives = sorted([p for p in pushes if p[1] > 0], key=lambda t: t[1], reverse=True)

    driver_codes = []
    if positives:
        driver_codes = [positives[0][0]]
        # name a second driver only if it is a meaningful share of the top push
        if len(positives) > 1 and positives[1][1] >= 0.25 * positives[0][1]:
            driver_codes.append(positives[1][0])

    drivers = _join_human([_BENEISH_NAMES[c] for c in driver_codes])
    caveat = _GROWTH_CAVEAT if any(c in _BENEISH_GROWTH_ACCRUAL for c in driver_codes) else ""

    if not driver_codes:
        return (f"M stays clean (below {MINUS}1.78), with no index pushing it toward "
                "the manipulation range.")
    if beneish.flag:
        return f"M clears the {MINUS}1.78 threshold, lifted mainly by {drivers}{caveat}."
    return (f"M stays clean below {MINUS}1.78; the strongest upward pushes come from "
            f"{drivers}, but they fall short of the flag{caveat}.")


# ----------------------------------------------------------------------------
# PIOTROSKI — score + which categories lost points
# ----------------------------------------------------------------------------
_PIOTROSKI_CATEGORY = {
    1: "Profitability", 2: "Profitability", 3: "Profitability", 4: "Profitability",
    5: "Leverage & liquidity", 6: "Leverage & liquidity", 7: "Leverage & liquidity",
    8: "Operating efficiency", 9: "Operating efficiency",
}
_PIOTROSKI_FAIL = {
    1: "negative net income",
    2: "negative operating cash flow",
    3: "a year-over-year drop in ROA",
    4: "earnings outrunning cash flow",
    5: "a higher long-term-debt ratio",
    6: "a weaker current ratio",
    7: "new-share dilution",
    8: "compressing gross margin",
    9: "falling asset turnover",
}
_PIOTROSKI_ORDER = ["Profitability", "Leverage & liquidity", "Operating efficiency"]


def _piotroski_sentence(piotroski) -> str:
    failed = []     # test numbers that scored 0, in order
    passed_by_cat = {c: 0 for c in _PIOTROSKI_ORDER}
    total_by_cat = {c: 0 for c in _PIOTROSKI_ORDER}
    for key, value in piotroski.signals.items():
        n = int(key.split(".")[0])
        cat = _PIOTROSKI_CATEGORY[n]
        total_by_cat[cat] += 1
        if value:
            passed_by_cat[cat] += 1
        else:
            failed.append(n)

    score = piotroski.score
    if not failed:
        return (f"F = {score}/9, with full marks across profitability, leverage & liquidity, "
                "and operating efficiency.")

    full_cats = [c for c in _PIOTROSKI_ORDER if passed_by_cat[c] == total_by_cat[c]]
    praise = ("full marks on " + _join_human([c.lower() for c in full_cats])
              if full_cats else "")

    if len(failed) <= 3:                       # name the specific tests it missed
        losses = _join_human([_PIOTROSKI_FAIL[n] for n in sorted(failed)])
    else:                                      # too many to list — name the weak categories
        lost_cats = [c for c in _PIOTROSKI_ORDER if passed_by_cat[c] < total_by_cat[c]]
        losses = "weakness across " + _join_human([c.lower() for c in lost_cats])

    if praise:
        return f"F = {score}/9: {praise}, but lost points on {losses}."
    return f"F = {score}/9: lost points on {losses}."


# ----------------------------------------------------------------------------
# OVERALL — synthesise the existing verdict into one plain-English line
# ----------------------------------------------------------------------------
_HEALTH_PHRASE = {
    "Healthy": "a financially healthy company",
    "Watch": "a mixed picture that warrants watching",
    "Distressed": "elevated financial-distress risk",
    "Unknown": "an incomplete financial picture",
}
_INTEGRITY_PHRASE = {
    "Clean": "clean",
    "Possible manipulation": "worth a closer look",
    "Not enough data": "untested for lack of data",
}


def _overall_sentence(verdict) -> str:
    health = _HEALTH_PHRASE.get(verdict.get("health"), "an incomplete financial picture")
    integrity = _INTEGRITY_PHRASE.get(verdict.get("integrity"), "untested for lack of data")
    return (f"Taken together, the models point to {health}, with earnings quality "
            f"that reads {integrity}.")


# ----------------------------------------------------------------------------
# Public entry point
# ----------------------------------------------------------------------------
def explain(altman, piotroski, beneish, verdict) -> dict:
    """
    Build the "why" commentary from the model result objects.

    Returns a dict with keys 'altman', 'piotroski', 'beneish', 'overall'. A model
    that is None (not applicable for this company) maps to None so the caller can
    skip it. 'overall' is always a string.
    """
    return {
        "altman": _altman_sentence(altman) if altman is not None else None,
        "piotroski": _piotroski_sentence(piotroski) if piotroski is not None else None,
        "beneish": _beneish_sentence(beneish) if beneish is not None else None,
        "overall": _overall_sentence(verdict),
    }
