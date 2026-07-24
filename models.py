"""
models.py — Core financial scoring models.

Three published, finance-credentialed models that read financial statements and
score a company on health, distress risk, and earnings-manipulation red flags:

  * Altman Z-Score      — bankruptcy / distress risk (Altman, 1968)
  * Beneish M-Score     — earnings-manipulation detection (Beneish, FAJ 1999)
  * Piotroski F-Score   — fundamental financial strength (Piotroski, 2000)

plus the composite that rolls them up for the product's headline:

  * Spring Score        — 0-100 weighted composite of the three models above plus
                          three quality ingredients (accrual quality per Sloan 1996,
                          gross-margin trend, leverage trend). See spring_score().

Every function here is PURE: it takes explicit numeric inputs and returns numbers.
No network, no globals. That makes the math unit-testable (see tests/test_models.py)
and lets the data layer (data.py) source the inputs from anywhere — live yfinance,
manual entry, or a preset.

A model RAISES ValueError when a required input is missing. The data layer catches
that and shows "N/A" for just that model, so one missing line item never kills the
whole analysis (important for banks, which lack a working-capital structure).
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import Optional


def _safe_div(numerator: float, denominator: float) -> Optional[float]:
    """Divide, returning None when the denominator is zero/None (avoids crashes)."""
    try:
        if denominator in (0, None) or numerator is None:
            return None
        return numerator / denominator
    except (TypeError, ZeroDivisionError):
        return None


def _missing(d: dict, keys) -> list:
    """Return the keys whose value is missing (None) — used to fail a model cleanly."""
    return [k for k in keys if d.get(k) is None]


def leverage_ratio(curr: Optional[dict]) -> Optional[float]:
    """
    Total liabilities / total assets, the headline leverage read.

    It lives here, with the other formulas, because it has to be computed identically on
    both sides of the sector benchmark: on the screened company (from its EDGAR filings)
    and on every peer (from FMP's statements, via sector_peers.py). A company measured
    one way against a median measured another way is not a benchmark. Returns None when
    either input is missing or assets are zero, so it degrades like every other model.
    """
    curr = curr or {}
    ratio = _safe_div(curr.get("total_liabilities"), curr.get("total_assets"))
    return None if ratio is None else round(ratio, 6)


# ----------------------------------------------------------------------------
# 1. ALTMAN Z-SCORE  (original 5-factor model for public companies)
# ----------------------------------------------------------------------------
@dataclass
class AltmanResult:
    z: float
    zone: str            # "Safe" | "Grey" | "Distress"
    components: dict = field(default_factory=dict)


def altman_z(
    working_capital: float,
    retained_earnings: float,
    ebit: float,
    market_value_equity: float,
    sales: float,
    total_assets: float,
    total_liabilities: float,
) -> AltmanResult:
    """
    Z = 1.2*X1 + 1.4*X2 + 3.3*X3 + 0.6*X4 + 1.0*X5

      X1 = Working Capital / Total Assets        (liquidity)
      X2 = Retained Earnings / Total Assets      (cumulative profitability / age)
      X3 = EBIT / Total Assets                   (operating productivity)
      X4 = Market Value of Equity / Total Liabilities  (solvency / market cushion)
      X5 = Sales / Total Assets                  (asset turnover)

    Zones (original public-firm cutoffs):
      Z > 2.99  -> "Safe"
      1.81–2.99 -> "Grey"
      Z < 1.81  -> "Distress"
    """
    x1 = _safe_div(working_capital, total_assets)
    x2 = _safe_div(retained_earnings, total_assets)
    x3 = _safe_div(ebit, total_assets)
    x4 = _safe_div(market_value_equity, total_liabilities)
    x5 = _safe_div(sales, total_assets)
    if None in (x1, x2, x3, x4, x5):
        raise ValueError("Altman Z-Score: missing or zero-denominator inputs.")

    z = 1.2 * x1 + 1.4 * x2 + 3.3 * x3 + 0.6 * x4 + 1.0 * x5
    if z > 2.99:
        zone = "Safe"
    elif z >= 1.81:
        zone = "Grey"
    else:
        zone = "Distress"

    return AltmanResult(
        z=round(z, 2),
        zone=zone,
        components={
            "X1 Working Capital / Total Assets": round(x1, 4),
            "X2 Retained Earnings / Total Assets": round(x2, 4),
            "X3 EBIT / Total Assets": round(x3, 4),
            "X4 Mkt Value Equity / Total Liabilities": round(x4, 4),
            "X5 Sales / Total Assets": round(x5, 4),
        },
    )


# ----------------------------------------------------------------------------
# 2. BENEISH M-SCORE  (8-variable model)
# ----------------------------------------------------------------------------
@dataclass
class BeneishResult:
    m: float
    flag: bool           # True => "possible manipulator"
    indices: dict = field(default_factory=dict)


def beneish_m(curr: dict, prior: dict) -> BeneishResult:
    """
    Eight indices comparing the current year (t) to the prior year (t-1):

      DSRI Days Sales in Receivables Index
      GMI  Gross Margin Index
      AQI  Asset Quality Index       (uses Current Assets + Net PP&E)
      SGI  Sales Growth Index
      DEPI Depreciation Index
      SGAI SG&A Index
      LVGI Leverage Index
      TATA Total Accruals to Total Assets

    M = -4.84 + 0.920*DSRI + 0.528*GMI + 0.404*AQI + 0.892*SGI
              + 0.115*DEPI - 0.172*SGAI + 4.679*TATA - 0.327*LVGI

    Flag: M > -1.78  => earnings possibly manipulated (Beneish threshold).
    """
    req = ["receivables", "sales", "cogs", "current_assets", "ppe", "total_assets",
           "depreciation", "sga", "net_income", "cfo", "current_liabilities", "long_term_debt"]
    miss = _missing(curr, req) + _missing(prior, req)
    if miss:
        raise ValueError(f"needs {sorted(set(miss))} (not reported for this company).")

    def gm(d):                       # gross margin ratio
        return _safe_div(d["sales"] - d["cogs"], d["sales"])

    dsri = _safe_div(_safe_div(curr["receivables"], curr["sales"]),
                     _safe_div(prior["receivables"], prior["sales"]))
    gmi = _safe_div(gm(prior), gm(curr))
    aqi_t = 1 - _safe_div(curr["current_assets"] + curr["ppe"], curr["total_assets"])
    aqi_p = 1 - _safe_div(prior["current_assets"] + prior["ppe"], prior["total_assets"])
    aqi = _safe_div(aqi_t, aqi_p)
    sgi = _safe_div(curr["sales"], prior["sales"])
    depi = _safe_div(
        _safe_div(prior["depreciation"], prior["depreciation"] + prior["ppe"]),
        _safe_div(curr["depreciation"], curr["depreciation"] + curr["ppe"]),
    )
    sgai = _safe_div(_safe_div(curr["sga"], curr["sales"]),
                     _safe_div(prior["sga"], prior["sales"]))
    lvgi = _safe_div(
        _safe_div(curr["long_term_debt"] + curr["current_liabilities"], curr["total_assets"]),
        _safe_div(prior["long_term_debt"] + prior["current_liabilities"], prior["total_assets"]),
    )
    tata = _safe_div(curr["net_income"] - curr["cfo"], curr["total_assets"])

    idx = dict(DSRI=dsri, GMI=gmi, AQI=aqi, SGI=sgi, DEPI=depi,
               SGAI=sgai, LVGI=lvgi, TATA=tata)
    if any(v is None for v in idx.values()):
        bad = [k for k, v in idx.items() if v is None]
        raise ValueError(f"could not compute {bad} (zero or missing values).")

    m = (-4.84
         + 0.920 * dsri
         + 0.528 * gmi
         + 0.404 * aqi
         + 0.892 * sgi
         + 0.115 * depi
         - 0.172 * sgai
         + 4.679 * tata
         - 0.327 * lvgi)

    return BeneishResult(
        m=round(m, 2),
        flag=bool(m > -1.78),
        indices={k: round(v, 3) for k, v in idx.items()},
    )


# ----------------------------------------------------------------------------
# 3. PIOTROSKI F-SCORE  (9 binary signals, 0–9)
# ----------------------------------------------------------------------------
@dataclass
class PiotroskiResult:
    score: int
    signals: dict = field(default_factory=dict)   # name -> 0/1


def piotroski_f(curr: dict, prior: dict) -> PiotroskiResult:
    """
    Nine binary tests across profitability, leverage/liquidity, and efficiency.
    Each passing test = 1 point. 8–9 = strong; 0–2 = weak.
    """
    req = ["net_income", "cfo", "total_assets", "long_term_debt", "current_assets",
           "current_liabilities", "shares", "sales", "cogs"]
    miss = _missing(curr, req) + _missing(prior, req)
    if miss:
        raise ValueError(f"needs {sorted(set(miss))} (not reported for this company).")

    s = {}
    # --- Profitability ---
    s["1. Positive net income (ROA > 0)"] = int(curr["net_income"] > 0)
    s["2. Positive operating cash flow"] = int(curr["cfo"] > 0)
    roa_t = _safe_div(curr["net_income"], curr["total_assets"])
    roa_p = _safe_div(prior["net_income"], prior["total_assets"])
    s["3. ROA improved year over year"] = int(roa_t is not None and roa_p is not None and roa_t > roa_p)
    s["4. Cash flow exceeds net income (quality of earnings)"] = int(curr["cfo"] > curr["net_income"])
    # --- Leverage, liquidity, source of funds ---
    lev_t = _safe_div(curr["long_term_debt"], curr["total_assets"])
    lev_p = _safe_div(prior["long_term_debt"], prior["total_assets"])
    s["5. Long-term debt ratio decreased"] = int(lev_t is not None and lev_p is not None and lev_t < lev_p)
    cr_t = _safe_div(curr["current_assets"], curr["current_liabilities"])
    cr_p = _safe_div(prior["current_assets"], prior["current_liabilities"])
    s["6. Current ratio improved"] = int(cr_t is not None and cr_p is not None and cr_t > cr_p)
    s["7. No new shares issued (no dilution)"] = int(curr["shares"] <= prior["shares"])
    # --- Operating efficiency ---
    gm_t = _safe_div(curr["sales"] - curr["cogs"], curr["sales"])
    gm_p = _safe_div(prior["sales"] - prior["cogs"], prior["sales"])
    s["8. Gross margin improved"] = int(gm_t is not None and gm_p is not None and gm_t > gm_p)
    at_t = _safe_div(curr["sales"], curr["total_assets"])
    at_p = _safe_div(prior["sales"], prior["total_assets"])
    s["9. Asset turnover improved"] = int(at_t is not None and at_p is not None and at_t > at_p)

    return PiotroskiResult(score=int(sum(s.values())), signals=s)


# ----------------------------------------------------------------------------
# 4. MERTON DISTANCE-TO-DEFAULT / PROBABILITY OF DEFAULT  (market-implied)
# ----------------------------------------------------------------------------
# The first DYNAMIC, market-implied signal in the stack. Altman, Piotroski and
# Beneish read filings; Merton (1974) reads the market's own view. It models equity
# as a call option on the firm's assets: shareholders keep the upside above the debt
# and walk away at zero, so equity is worth max(V - F, 0) at the horizon. Two market
# observables (equity value E and equity volatility sigma_E) plus the debt level F
# back out the two unobservables (asset value V, asset volatility sigma_V) through the
# option identity. The distance from the implied asset value down to the debt, measured
# in asset-volatility units, is the distance to default (DD); N(-DD) is the probability
# of default (PD).
#
# We report the RISK-NEUTRAL PD: it uses the risk-free rate r rather than an estimated
# real-world asset drift, which is the market-implied measure computable from public
# inputs alone (no drift to guess), keeping the whole thing reproducible. Pure stdlib
# math (math.erf), no numpy or scipy, so it runs anywhere the other models do.

# A simple public risk-free proxy (roughly a 1-year Treasury bill) and the standard
# 1-year horizon, passed into the function so it stays pure and the assumptions stay
# visible in the glass box.
DEFAULT_RISK_FREE = 0.04
MERTON_HORIZON_YEARS = 1.0


def _norm_cdf(x: float) -> float:
    """Standard normal CDF via the error function (pure stdlib, no scipy)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _merton_label(pd: float) -> str:
    """Plain-English band for a 1-year probability of default."""
    if pd < 0.005:
        return "Remote"
    if pd < 0.02:
        return "Low"
    if pd < 0.10:
        return "Elevated"
    if pd < 0.20:
        return "High"
    return "Severe"


@dataclass
class MertonResult:
    pd: float                # probability of default over the horizon (0-1)
    dd: float                # distance to default in standard deviations (= d2)
    asset_value: float       # implied market value of firm assets (V)
    asset_vol: float         # implied asset volatility (sigma_V, annualized)
    equity_vol: float        # the equity-volatility input (sigma_E, annualized)
    leverage: float          # F / V, debt over implied asset value
    face_debt: float         # the default point F used
    label: str               # Remote | Low | Elevated | High | Severe
    risk_free: float         # r used
    horizon_years: float     # T used
    components: dict = field(default_factory=dict)


def _bs_equity(v: float, sigma_v: float, face_debt: float, r: float, t: float):
    """
    Black-Scholes-Merton value of equity as a call on the firm's assets, and N(d1)
    (the option delta). Caller guarantees v, sigma_v, face_debt, t are all positive.
    """
    root_t = math.sqrt(t)
    d1 = (math.log(v / face_debt) + (r + 0.5 * sigma_v ** 2) * t) / (sigma_v * root_t)
    d2 = d1 - sigma_v * root_t
    equity = v * _norm_cdf(d1) - face_debt * math.exp(-r * t) * _norm_cdf(d2)
    return equity, _norm_cdf(d1)


def _solve_asset_value(sigma_v: float, equity_value: float, face_debt: float,
                       r: float, t: float) -> float:
    """
    Invert the option equation for asset value V at a fixed asset volatility: find the
    V for which _bs_equity(V) equals the observed equity value. Equity is strictly
    increasing in V (its delta N(d1) > 0), so bisection is robust and deterministic.
    """
    lo = equity_value                      # V always exceeds E (debt carries value)
    hi = equity_value + face_debt * 2.0 + 1.0
    for _ in range(100):                   # widen the bracket until it straddles the root
        eq_hi, _ = _bs_equity(hi, sigma_v, face_debt, r, t)
        if eq_hi >= equity_value:
            break
        hi *= 2.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        eq_mid, _ = _bs_equity(mid, sigma_v, face_debt, r, t)
        if abs(eq_mid - equity_value) < 1e-10 * max(1.0, equity_value):
            return mid
        if eq_mid < equity_value:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def merton_dd_pd(
    equity_value: float,
    equity_vol: float,
    face_debt: float,
    risk_free: float = DEFAULT_RISK_FREE,
    t: float = MERTON_HORIZON_YEARS,
) -> MertonResult:
    """
    Merton distance-to-default and (risk-neutral) probability of default.

      equity_value  E        market value of equity (market cap)
      equity_vol    sigma_E  annualized volatility of equity returns
      face_debt     F        default point (KMV: short-term debt + 0.5 * long-term)
      risk_free     r        annual risk-free rate
      t             T        horizon in years (1.0 by convention)

    Solves the two-equation system

        E         = V N(d1) - F e^{-rT} N(d2)
        sigma_E E = N(d1) sigma_V V

    for (V, sigma_V) by an outer fixed-point on sigma_V with an inner bisection for V,
    then returns DD = d2 and PD = N(-DD).

    Raises ValueError when the inputs cannot yield an honest reading (non-positive
    equity, volatility, debt or horizon, a degenerate solve, or non-convergence), so
    the caller drops the component with a plain note, exactly like the filing models.
    """
    if None in (equity_value, equity_vol, face_debt):
        raise ValueError("Merton: needs equity value, equity volatility, and debt.")
    if equity_value <= 0:
        raise ValueError("Merton: equity value must be positive.")
    if equity_vol <= 0:
        raise ValueError("Merton: equity volatility must be positive.")
    if face_debt <= 0:
        raise ValueError("Merton: no debt to default on (face value <= 0).")
    if t <= 0:
        raise ValueError("Merton: horizon must be positive.")

    # Initial guess: assets ~ equity + debt, equity vol scaled down toward asset vol.
    v = equity_value + face_debt
    sigma_v = equity_vol * equity_value / v

    converged = False
    for _ in range(200):
        v = _solve_asset_value(sigma_v, equity_value, face_debt, risk_free, t)
        _eq, n_d1 = _bs_equity(v, sigma_v, face_debt, risk_free, t)
        if n_d1 <= 0:
            raise ValueError("Merton: degenerate solve (option delta collapsed to zero).")
        sigma_v_new = equity_vol * equity_value / (n_d1 * v)
        if sigma_v_new <= 0:
            raise ValueError("Merton: implied asset volatility went non-positive.")
        if abs(sigma_v_new - sigma_v) < 1e-8:
            sigma_v = sigma_v_new
            converged = True
            break
        sigma_v = sigma_v_new

    if not converged:
        raise ValueError("Merton: iterative solve did not converge.")

    v = _solve_asset_value(sigma_v, equity_value, face_debt, risk_free, t)
    dd = (math.log(v / face_debt) + (risk_free - 0.5 * sigma_v ** 2) * t) / (sigma_v * math.sqrt(t))
    pd = _norm_cdf(-dd)

    return MertonResult(
        pd=round(pd, 6),
        dd=round(dd, 4),
        asset_value=round(v, 2),
        asset_vol=round(sigma_v, 6),
        equity_vol=round(equity_vol, 6),
        leverage=round(face_debt / v, 6),
        face_debt=round(face_debt, 2),
        label=_merton_label(pd),
        risk_free=risk_free,
        horizon_years=t,
        components={
            "Equity value (E)": round(equity_value, 2),
            "Equity volatility (sigma_E)": round(equity_vol, 4),
            "Asset value (V, implied)": round(v, 2),
            "Asset volatility (sigma_V, implied)": round(sigma_v, 4),
            "Default point (F)": round(face_debt, 2),
            "Distance to default (DD)": round(dd, 4),
        },
    )


# ----------------------------------------------------------------------------
# 5. SPRING SCORE  (composite 0-100, weighted across the model ingredients)
# ----------------------------------------------------------------------------
# The fundamentals ingredients (100 points) are the filing-based core: the three
# published models as the backbone (60) plus three quality ingredients from the same
# line items (40). Merton (15) is added ALONGSIDE them as the market-implied component,
# clearly labeled, keeping the fundamentals sub-weights in their exact ratios so Prof.
# Simin's pending review of THOSE is untouched. The denominator is the sum of whatever
# weight is actually available (SPRING_TOTAL_WEIGHT when every ingredient is present),
# so a company that is missing an ingredient simply reweights over the rest, the same
# honest degradation the models already practice; a fundamentals-only read (no market
# signal) reports coverage below 1.0 because it genuinely lacks the market dimension.
# Analyst-consensus direction remains a deliberate OPEN SLOT for a later review.
SPRING_WEIGHTS = {
    "altman": 25,           # distress risk
    "piotroski": 20,        # fundamental strength
    "beneish": 15,          # earnings-manipulation risk
    "accruals": 10,         # cash backing of earnings (Sloan, 1996 direction)
    "margin_trend": 15,     # gross margin, year over year
    "leverage_trend": 15,   # long-term debt / total assets, year over year
    "merton": 15,           # market-implied default probability (Merton, 1974)
}

# Full weight when every ingredient is available; the composite denominator is the
# weight actually present, not this total.
SPRING_TOTAL_WEIGHT = sum(SPRING_WEIGHTS.values())

# A composite over less than this much weight is a guess, not a score.
SPRING_MIN_WEIGHT = 40

SPRING_TIERS = (           # lower bound (inclusive) -> tier name
    (85, "Excellent"),
    (70, "Strong"),
    (50, "Fair"),
    (30, "Weak"),
    (0, "Fragile"),
)

# Anchor points mapping each raw ingredient onto 0-100. Every anchor is either the
# model's own published cutoff (Altman 1.81/2.99, Beneish -1.78) or a symmetric
# +/- band around neutral for the trend ingredients. Linear between anchors,
# clamped outside them.
_SPRING_ANCHORS = {
    # Altman Z: distress cutoff 1.81 -> 40, safe cutoff 2.99 -> 70.
    "altman": [(0.0, 0.0), (1.81, 40.0), (2.99, 70.0), (6.0, 100.0)],
    # Beneish M: lower is better. -1.78 is the flag threshold -> 50.
    "beneish": [(-3.0, 100.0), (-1.78, 50.0), (0.0, 0.0)],
    # Accruals (NI - CFO) / TA: negative = cash-backed earnings = good.
    "accruals": [(-0.10, 100.0), (0.0, 50.0), (0.10, 0.0)],
    # Gross-margin change, in fraction points (+0.05 = margin up 5pp).
    "margin_trend": [(-0.05, 0.0), (0.0, 50.0), (0.05, 100.0)],
    # Change in long-term debt / total assets: paying down debt = good.
    "leverage_trend": [(-0.05, 100.0), (0.0, 50.0), (0.05, 0.0)],
    # Merton 1-year probability of default: lower is better. 2% ~ investment-grade
    # boundary -> 70 (the Strong tier line); 10% -> 40 (Altman's distress anchor).
    "merton": [(0.0, 100.0), (0.02, 70.0), (0.10, 40.0), (0.20, 20.0), (0.50, 0.0)],
}


def _piecewise(x: float, anchors) -> float:
    """Linear interpolation through (x, y) anchor points, clamped at the ends."""
    if x <= anchors[0][0]:
        return anchors[0][1]
    if x >= anchors[-1][0]:
        return anchors[-1][1]
    for (x0, y0), (x1, y1) in zip(anchors, anchors[1:]):
        if x <= x1:
            return y0 + (x - x0) / (x1 - x0) * (y1 - y0)
    return anchors[-1][1]


@dataclass
class SpringResult:
    score: int               # 0-100, the headline number
    tier: str                # Excellent | Strong | Fair | Weak | Fragile
    components: dict = field(default_factory=dict)   # key -> sub-score details
    coverage: float = 1.0    # share of total weight that was available (0-1)


def _spring_tier(score: float) -> str:
    for lower, name in SPRING_TIERS:
        if score >= lower:
            return name
    return "Fragile"


def spring_score(
    z: Optional[float] = None,
    f_score: Optional[float] = None,
    m_score: Optional[float] = None,
    curr: Optional[dict] = None,
    prior: Optional[dict] = None,
    pd_merton: Optional[float] = None,
) -> SpringResult:
    """
    The composite 0-100 health score: a weighted average of the sub-scores, each
    scaled through fixed published anchors (see _SPRING_ANCHORS / SPRING_WEIGHTS).

    Inputs are plain numbers, not result objects, so both the live path (which has
    AltmanResult etc.) and the snapshot path (which has bare stored scores) can call
    it. `curr` / `prior` are the standard payload year-dicts; when absent, the three
    quality ingredients degrade and the composite reweights over what remains.
    `pd_merton` is the Merton 1-year probability of default (0-1); when absent, the
    market-implied ingredient drops and coverage falls below 1.0 honestly.

    Raises ValueError when fewer than SPRING_MIN_WEIGHT points of weight are
    available, or when none of the three backbone models scored: a composite built
    on that little is noise wearing a number.
    """
    subs: dict = {}

    subs["altman"] = None if z is None else _piecewise(z, _SPRING_ANCHORS["altman"])
    subs["piotroski"] = None if f_score is None else (f_score / 9.0) * 100.0
    subs["beneish"] = None if m_score is None else _piecewise(m_score, _SPRING_ANCHORS["beneish"])
    subs["merton"] = None if pd_merton is None else _piecewise(pd_merton, _SPRING_ANCHORS["merton"])

    curr = curr or {}
    prior = prior or {}

    acc = _safe_div((curr.get("net_income") - curr.get("cfo"))
                    if None not in (curr.get("net_income"), curr.get("cfo")) else None,
                    curr.get("total_assets"))
    subs["accruals"] = None if acc is None else _piecewise(acc, _SPRING_ANCHORS["accruals"])

    def _gross_margin(d):
        if None in (d.get("sales"), d.get("cogs")):
            return None
        return _safe_div(d["sales"] - d["cogs"], d["sales"])

    gm_c, gm_p = _gross_margin(curr), _gross_margin(prior)
    subs["margin_trend"] = (None if None in (gm_c, gm_p)
                            else _piecewise(gm_c - gm_p, _SPRING_ANCHORS["margin_trend"]))

    lev_c = _safe_div(curr.get("long_term_debt"), curr.get("total_assets"))
    lev_p = _safe_div(prior.get("long_term_debt"), prior.get("total_assets"))
    subs["leverage_trend"] = (None if None in (lev_c, lev_p)
                              else _piecewise(lev_c - lev_p, _SPRING_ANCHORS["leverage_trend"]))

    available = {k: v for k, v in subs.items() if v is not None}
    weight_available = sum(SPRING_WEIGHTS[k] for k in available)
    backbone = any(k in available for k in ("altman", "piotroski", "beneish"))
    if weight_available < SPRING_MIN_WEIGHT or not backbone:
        raise ValueError(
            "Spring Score: not enough inputs to build an honest composite "
            f"(only {weight_available} of {SPRING_TOTAL_WEIGHT} weight points available).")

    composite = sum(SPRING_WEIGHTS[k] * v for k, v in available.items()) / weight_available

    components = {
        k: {
            "sub_score": None if v is None else round(v, 1),
            "weight": SPRING_WEIGHTS[k],
            "available": v is not None,
        }
        for k, v in subs.items()
    }
    return SpringResult(
        score=int(round(composite)),
        tier=_spring_tier(composite),
        components=components,
        coverage=round(weight_available / SPRING_TOTAL_WEIGHT, 2),
    )


# ----------------------------------------------------------------------------
# Combined verdict  (any model may be None / not applicable)
# ----------------------------------------------------------------------------
def overall_verdict(altman: Optional[AltmanResult], piotroski: Optional[PiotroskiResult],
                    beneish: Optional[BeneishResult]) -> dict:
    """Roll whichever models are available into one plain-English call."""
    if altman is not None and piotroski is not None:
        if altman.zone == "Safe" and piotroski.score >= 7:
            health = "Healthy"
        elif altman.zone == "Distress" or piotroski.score <= 3:
            health = "Distressed"
        else:
            health = "Watch"
    elif altman is not None:
        health = {"Safe": "Healthy", "Grey": "Watch", "Distress": "Distressed"}[altman.zone]
    elif piotroski is not None:
        health = ("Healthy" if piotroski.score >= 7
                  else "Distressed" if piotroski.score <= 3 else "Watch")
    else:
        health = "Unknown"

    if beneish is None:
        integrity = "Not enough data"
    elif beneish.flag:
        integrity = "Possible manipulation"
    else:
        integrity = "Clean"

    return {"health": health, "integrity": integrity}
