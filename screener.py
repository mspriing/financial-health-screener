"""
screener.py — M&A target screener. Pure and testable, in the spirit of models.py
and benchmark.py: it reads the committed snapshot rows and surfaces companies that
match a classic acquisition PROFILE. It does NOT recompute any score — it reads the
already-computed Altman Z, Piotroski F and Beneish M from the snapshot.

Two profiles:

  value_targets      — "strong business, weak balance sheet" buyout candidates:
                       operationally strong (high F) but in real, non-terminal stress
                       (grey-zone Z), clean earnings, and CHEAP versus their sector.
  strategic_targets  — strong, clean operators a strategic buyer would want to own:
                       safe-zone Z or high F, clean earnings.

DATA-QUALITY GUARDS (deliberately strict, because raw valuation data is noisy):
  * P/B counts as a "cheap" signal only when 0.1 < P/B < 30 — this drops negatives
    (negative book value) and glitches like BRK-B's 0.00097.
  * EV/EBITDA counts only when > 0 (negative EV/EBITDA isn't "cheap", it's distressed
    or loss-making).
  * Sector valuation medians are computed over those VALID values only.
  * Financials (null Z — banks/insurers the Altman model can't read) are excluded from
    the value screen entirely; the caller surfaces that fact in the UI.
"""
from __future__ import annotations
from typing import List, Optional

from benchmark import load_universe  # reuse the single snapshot loader

# Valuation sanity bounds.
PB_MIN, PB_MAX = 0.1, 30.0

# Altman Z degrades for asset-light / low-liability firms — the X4 (mkt equity / total
# liabilities) term explodes, producing absurd scores (e.g. Palantir Z≈132). Above this
# cap, Z is no longer a meaningful magnitude, so we both DISPLAY it as "15+" and use the
# capped value as a ranking tiebreaker. The stored/raw score is never altered.
Z_DISPLAY_CAP = 15.0

# In value mode, a company cheap on one valuation but clearly pricey on the other isn't a
# clean "cheap" target. Reject if any valid valuation sits above this multiple of median.
EXPENSIVE_MULT = 1.5

# Value/distress band: real stress, but not terminal. (Grey zone is 1.81–2.99; we open
# the window slightly so a company just outside grey on either side can still qualify.)
VALUE_Z_LO, VALUE_Z_HI = 1.5, 3.0
VALUE_F_MIN = 6                 # operationally strong despite the balance-sheet stress
STRATEGIC_F_MIN = 7
SAFE_Z = 2.99                   # Altman "Safe" cutoff
TOP_N = 12

# Snapshot columns we carry through to each result dict.
_FIELDS = ("ticker", "name", "sector", "z", "zone", "f_score", "m_score", "m_flag",
           "price_to_book", "ev_ebitda", "market_cap")


# ----------------------------------------------------------------------------
# Validity guards
# ----------------------------------------------------------------------------
def valid_pb(v) -> bool:
    """A price-to-book that's meaningful as a 'cheap' signal (drops negatives/glitches)."""
    return isinstance(v, (int, float)) and PB_MIN < v < PB_MAX


def valid_ev(v) -> bool:
    """A positive EV/EBITDA (negative means loss-making / not 'cheap')."""
    return isinstance(v, (int, float)) and v > 0


def _is_flagged(m_flag) -> bool:
    """True only when Beneish explicitly flags the company. Unknown => not flagged."""
    if isinstance(m_flag, bool):
        return m_flag
    return str(m_flag).strip().lower() == "true"


def fmt_z(z) -> str:
    """Present Altman Z honestly: cap the display at 15+ where the model breaks down."""
    if z is None:
        return "N/A"
    if z > Z_DISPLAY_CAP:
        return f"{int(Z_DISPLAY_CAP)}+"
    return f"{z:.1f}"


def _median(vals: List[float]) -> Optional[float]:
    vals = sorted(v for v in vals if isinstance(v, (int, float)))
    n = len(vals)
    if n == 0:
        return None
    mid = n // 2
    return vals[mid] if n % 2 else (vals[mid - 1] + vals[mid]) / 2.0


# ----------------------------------------------------------------------------
# Sector valuation medians (over VALID values only)
# ----------------------------------------------------------------------------
def sector_valuation_medians(rows: List[dict]) -> dict:
    """{sector: {"pb": median_or_None, "ev": median_or_None}} using only valid valuations."""
    buckets: dict = {}
    for r in rows:
        s = r.get("sector")
        b = buckets.setdefault(s, {"pb": [], "ev": []})
        if valid_pb(r.get("price_to_book")):
            b["pb"].append(r["price_to_book"])
        if valid_ev(r.get("ev_ebitda")):
            b["ev"].append(r["ev_ebitda"])
    return {s: {"pb": _median(b["pb"]), "ev": _median(b["ev"])} for s, b in buckets.items()}


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
def _base(row: dict) -> dict:
    """Copy the carried-through snapshot fields into a fresh result dict."""
    return {
        "ticker": row.get("tr"),
        "name": row.get("name"),
        "sector": row.get("sector"),
        "z": row.get("z"),
        "zone": row.get("zone"),
        "f_score": (int(row["f_score"]) if isinstance(row.get("f_score"), (int, float)) else None),
        "m_score": row.get("m_score"),
        "m_flag": _is_flagged(row.get("m_flag")),
        "price_to_book": row.get("price_to_book"),
        "ev_ebitda": row.get("ev_ebitda"),
        "market_cap": row.get("market_cap"),
    }


def _in_sector(rows: List[dict], sector: Optional[str]) -> List[dict]:
    if not sector or sector == "All sectors":
        return rows
    return [r for r in rows if r.get("sector") == sector]


def sectors(rows: List[dict]) -> List[str]:
    """Sorted unique sector names present in the snapshot (for the UI dropdown)."""
    return sorted({r.get("sector") for r in rows if r.get("sector")})


# ----------------------------------------------------------------------------
# VALUE / DISTRESS targets — "strong business, weak balance sheet"
# ----------------------------------------------------------------------------
def value_targets(rows: List[dict], sector: Optional[str] = None) -> List[dict]:
    """
    Operationally strong but stressed and cheap. Gates: non-financial (Z present), not
    Beneish-flagged, Z in [1.5, 3.0], F >= 6, and cheap vs its sector on at least one
    VALID valuation (P/B and/or EV/EBITDA below the sector median).
    Ranked by a fit score that rewards a high F, a deep valuation discount, and sitting
    in the grey stress band. Returns the top ~12.
    """
    medians = sector_valuation_medians(rows)
    out: List[dict] = []

    for r in _in_sector(rows, sector):
        z = r.get("z")
        f = r.get("f_score")
        if z is None:                                    # financial / Altman N/A -> excluded
            continue
        if _is_flagged(r.get("m_flag")):                 # earnings red flag -> excluded
            continue
        if not (VALUE_Z_LO <= z <= VALUE_Z_HI):          # outside the stress band
            continue
        if f is None or f < VALUE_F_MIN:                 # not operationally strong
            continue

        med = medians.get(r.get("sector"), {"pb": None, "ev": None})
        pb, ev = r.get("price_to_book"), r.get("ev_ebitda")

        # valuation vs sector median, per metric, using valid values only
        pb_ratio = (pb / med["pb"]) if (valid_pb(pb) and med["pb"]) else None
        ev_ratio = (ev / med["ev"]) if (valid_ev(ev) and med["ev"]) else None
        ratios = [x for x in (pb_ratio, ev_ratio) if x is not None]

        # discount = how far BELOW the sector median (only counts when below)
        disc_pb = (1 - pb_ratio) if (pb_ratio is not None and pb_ratio < 1) else None
        disc_ev = (1 - ev_ratio) if (ev_ratio is not None and ev_ratio < 1) else None
        discounts = [d for d in (disc_pb, disc_ev) if d is not None]
        if not discounts:                                # not actually cheap vs sector
            continue
        # ...but reject if it's clearly pricey on the other valid metric (not really cheap)
        if any(x > EXPENSIVE_MULT for x in ratios):
            continue

        disc = sum(discounts) / len(discounts)
        zone = r.get("zone")
        fit = round(f + 6.0 * disc + (1.5 if zone == "Grey" else 0.0), 2)

        item = _base(r)
        item["fit_score"] = fit
        item["why"] = _value_why(item, med, disc_pb, disc_ev)
        item.update(_detail(item, med, "value"))
        out.append(item)

    out.sort(key=lambda d: d["fit_score"], reverse=True)
    return out[:TOP_N]


def _value_why(item: dict, med: dict, disc_pb, disc_ev) -> str:
    z, f, zone = item["z"], item["f_score"], item["zone"]
    zone_label = {"Grey": "Grey zone", "Safe": "Safe zone", "Distress": "Distress zone"}.get(zone, "")
    # Quote the valuation it's actually cheap on (prefer P/B when both apply).
    if disc_pb is not None and med.get("pb"):
        mult = item["price_to_book"] / med["pb"]
        val_phrase = f"trading at {mult:.1f}x the sector’s median P/B"
    elif disc_ev is not None and med.get("ev"):
        mult = item["ev_ebitda"] / med["ev"]
        val_phrase = f"trading at {mult:.1f}x the sector’s median EV/EBITDA"
    else:
        val_phrase = "cheap vs its sector"
    return (f"{zone_label} Z {fmt_z(z)} with F={f}, {val_phrase}. Strong operations, "
            f"stressed balance sheet, cheap.")


# ----------------------------------------------------------------------------
# STRATEGIC targets — strong, clean operators
# ----------------------------------------------------------------------------
def strategic_targets(rows: List[dict], sector: Optional[str] = None) -> List[dict]:
    """
    Strong, clean businesses a strategic buyer would want. Gates: not Beneish-flagged,
    and either safe-zone Z (> 2.99) OR F >= 7. Ranked by F, then Z. Returns the top ~12.
    """
    medians = sector_valuation_medians(rows)     # for the valuation line in the detail
    out: List[dict] = []
    for r in _in_sector(rows, sector):
        if _is_flagged(r.get("m_flag")):
            continue
        z, f = r.get("z"), r.get("f_score")
        safe = z is not None and z > SAFE_Z
        strong_f = f is not None and f >= STRATEGIC_F_MIN
        if not (safe or strong_f):
            continue

        item = _base(r)
        # Rank by F first, then Z — encode both into one monotonic score. Z is CAPPED at
        # Z_DISPLAY_CAP first, so an asset-light firm's absurd Z (e.g. 132) can't dominate:
        # the tiebreaker is min(z,15)/100 < 1, never enough to leapfrog a whole F point.
        z_tie = min(z, Z_DISPLAY_CAP) if z is not None else 0
        item["fit_score"] = round((f or 0) + z_tie / 100.0, 3)
        item["why"] = _strategic_why(item)
        med = medians.get(r.get("sector"), {"pb": None, "ev": None})
        item.update(_detail(item, med, "strategic"))
        out.append(item)

    out.sort(key=lambda d: d["fit_score"], reverse=True)
    return out[:TOP_N]


def _strategic_why(item: dict) -> str:
    z, f, sector = item["z"], item["f_score"], item["sector"]
    bits = []
    if z is not None and z > SAFE_Z:
        bits.append(f"Safe zone Z {fmt_z(z)}")
    elif z is not None:
        bits.append(f"Z {fmt_z(z)}")
    if f is not None:
        bits.append(f"F={f}")
    bits.append("clean earnings")
    head = ", ".join(bits)
    where = f" in {sector}" if sector else ""
    return f"{head}. A strong, clean operator{where}."


# ----------------------------------------------------------------------------
# PER-COMPANY DETAIL  ("why it's a target": the evidence behind the one-line thesis)
#
# Four plain-sentence fields, each derived ONLY from the snapshot scores already on the
# result dict (F, Z/zone, P/B, EV/EBITDA, sector). No live data, no recomputation, so the
# detail stays pure and testable and can never disagree with the numbers on the card.
# 'mode' is "value" or "strategic"; it changes how the balance sheet and the read are framed
# (stress is the opportunity for value; strength is the point for strategic).
# ----------------------------------------------------------------------------
def _operations_text(f) -> str:
    """What the Piotroski F-score says about the operating business."""
    if f is None:
        return "Piotroski F is not available, so operating strength can't be read for this company."
    if f >= 7:
        desc = "strong, improving fundamentals"
    elif f >= 3:
        desc = "moderate fundamentals, a mixed operating picture"
    else:
        desc = "weak fundamentals"
    return f"Piotroski F of {f} out of 9: {desc}."


def _balance_sheet_text(z, zone, mode: str) -> str:
    """What the Altman Z and its zone say about the balance sheet, framed by mode."""
    if z is None:
        if mode == "strategic":
            return ("Altman Z is not available, so the case rests on the Piotroski strength and "
                    "clean earnings rather than the Z model.")
        return "Altman Z is not available, so this company's balance sheet can't be read by the Altman model."
    zdisp = fmt_z(z)
    if mode == "strategic":
        framing = {
            "Safe": "sits in the safe zone, a sign of balance sheet strength.",
            "Grey": ("sits in the grey zone, so the balance sheet is adequate and the case rests "
                     "mainly on strong, clean operations."),
            "Distress": ("sits in the distress zone, so this name leans on its operating strength "
                         "rather than its balance sheet."),
        }.get(zone, "is outside the model's usual range.")
    else:
        framing = {
            "Grey": "sits in the grey zone: real balance sheet stress, but not terminal distress.",
            "Distress": "sits in the distress zone: clear balance sheet stress a buyer would need to fix.",
            "Safe": "sits in the safe zone, so the balance sheet is sound rather than stressed.",
        }.get(zone, "is outside the model's usual range.")
    return f"Altman Z of {zdisp} {framing}"


def _valuation_text(pb, ev, med: dict) -> str:
    """
    The actual valuation versus the sector median, in numbers. Only valid, positive
    valuations are cited (negatives and glitch values are dropped, same guards as the screen).
    """
    med_pb, med_ev = med.get("pb"), med.get("ev")
    parts = []                                   # (label, article, value, sector_median)
    if valid_pb(pb) and med_pb:
        parts.append(("price-to-book", "a", pb, med_pb))
    if valid_ev(ev) and med_ev:
        parts.append(("EV/EBITDA", "an", ev, med_ev))
    if not parts:
        return "No clean sector-relative valuation is available for this company in the snapshot."

    label, article, v, m = parts[0]
    pct = round((1 - v / m) * 100)
    direction = "below" if v < m else "above"
    text = (f"Trades at {article} {label} of {v:.1f} versus the sector median of {m:.1f}, "
            f"about {abs(pct)} percent {direction}.")
    if len(parts) > 1:
        label2, _a, v2, m2 = parts[1]
        text += f" {label2} of {v2:.1f} versus {m2:.1f}."
    return text


def _read_text(mode: str, sector) -> str:
    """One plain sentence on why a buyer would care."""
    if mode == "value":
        return ("For a buyer, this is a strong operation available at a discount because the "
                "market is fixated on the stressed balance sheet, the classic value buyout setup.")
    where = f" in {sector}" if sector else ""
    return ("For a buyer, this is a healthy, clean operator that adds exposure to a strong "
            f"business{where}.")


def _detail(item: dict, med: dict, mode: str) -> dict:
    """Bundle the four 'why it's a target' fields, all from the snapshot scores on `item`."""
    return {
        "operations": _operations_text(item.get("f_score")),
        "balance_sheet": _balance_sheet_text(item.get("z"), item.get("zone"), mode),
        "valuation": _valuation_text(item.get("price_to_book"), item.get("ev_ebitda"), med),
        "read": _read_text(mode, item.get("sector")),
    }
