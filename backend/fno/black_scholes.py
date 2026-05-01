"""
Black-Scholes option pricer for NIFTY/BANKNIFTY weekly options.

Standard BS formula. Used by backtest to estimate option premium from
underlying price + time + IV. Validated against actual market prices.

For Indian markets:
  risk-free rate r ≈ 6.5% (10y G-sec)
  NIFTY IV typically 12-18% in normal market, 25-35% in stress
"""
from __future__ import annotations

import math
from dataclasses import dataclass

# Standard normal CDF using error function
_SQRT2 = math.sqrt(2.0)
def _phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / _SQRT2))


@dataclass
class BSResult:
    premium: float
    delta: float
    gamma: float
    theta: float       # per day
    vega: float        # per 1% IV change


def bs_price(spot: float, strike: float, dte_years: float, r: float, iv: float,
             opt_type: str) -> BSResult:
    """Standard Black-Scholes for European-style index option.
    dte_years = days_to_expiry / 365 (calendar days, not trading days)
    iv = implied vol as decimal (e.g. 0.15 for 15%)
    opt_type = 'CE' or 'PE'"""
    if dte_years <= 0 or iv <= 0 or spot <= 0:
        # Intrinsic only at expiry
        intrinsic = max(0.0, spot - strike) if opt_type == "CE" else max(0.0, strike - spot)
        return BSResult(intrinsic, 1.0 if intrinsic > 0 else 0.0, 0.0, 0.0, 0.0)

    d1 = (math.log(spot / strike) + (r + 0.5 * iv * iv) * dte_years) / (iv * math.sqrt(dte_years))
    d2 = d1 - iv * math.sqrt(dte_years)

    if opt_type == "CE":
        premium = spot * _phi(d1) - strike * math.exp(-r * dte_years) * _phi(d2)
        delta = _phi(d1)
        theta = -spot * iv * math.exp(-d1*d1/2) / (math.sqrt(2*math.pi) * 2 * math.sqrt(dte_years)) \
                - r * strike * math.exp(-r * dte_years) * _phi(d2)
    else:  # PE
        premium = strike * math.exp(-r * dte_years) * _phi(-d2) - spot * _phi(-d1)
        delta = _phi(d1) - 1.0
        theta = -spot * iv * math.exp(-d1*d1/2) / (math.sqrt(2*math.pi) * 2 * math.sqrt(dte_years)) \
                + r * strike * math.exp(-r * dte_years) * _phi(-d2)

    gamma = math.exp(-d1*d1/2) / (math.sqrt(2*math.pi) * spot * iv * math.sqrt(dte_years))
    vega = spot * math.exp(-d1*d1/2) / math.sqrt(2*math.pi) * math.sqrt(dte_years)

    return BSResult(
        premium=premium,
        delta=delta,
        gamma=gamma,
        theta=theta / 365.0,  # per day
        vega=vega / 100.0,    # per 1% IV change
    )


def calibrate_iv(market_premium: float, spot: float, strike: float,
                  dte_years: float, r: float, opt_type: str,
                  iv_lo: float = 0.05, iv_hi: float = 0.80) -> float:
    """Reverse-engineer IV from observed market premium (bisection)."""
    for _ in range(50):
        iv_mid = (iv_lo + iv_hi) / 2
        p = bs_price(spot, strike, dte_years, r, iv_mid, opt_type).premium
        if abs(p - market_premium) < 0.05:
            return iv_mid
        if p < market_premium:
            iv_lo = iv_mid
        else:
            iv_hi = iv_mid
    return (iv_lo + iv_hi) / 2


if __name__ == "__main__":
    # Validate against yesterday's actual NIFTY 24050 CE trade
    print("=== Validation: NIFTY 24050 CE 05-May-2026 expiry ===")
    print()

    # At entry (30-Apr 13:18): spot 23930, premium Rs 133.05, 5 days to expiry
    print("ENTRY (30-Apr 13:18):")
    print(f"  Spot 23930  Strike 24050  DTE 5 days  Premium observed: Rs 133.05")
    iv_entry = calibrate_iv(133.05, 23930, 24050, 5/365, 0.065, "CE")
    print(f"  Implied IV (calibrated): {iv_entry*100:.1f}%")
    bs = bs_price(23930, 24050, 5/365, 0.065, iv_entry, "CE")
    print(f"  BS-modeled premium: Rs {bs.premium:.2f}  (validation)")
    print(f"  Delta: {bs.delta:.3f}  Gamma: {bs.gamma:.5f}  Theta: {bs.theta:.2f}/day")
    print()

    # At exit (30-Apr 14:42): spot ~24090, premium Rs 200, 4.95 DTE
    print("EXIT (30-Apr 14:42):")
    print(f"  Spot 24090  Strike 24050  DTE 4.95  Premium observed: Rs 200.00")
    bs_exit = bs_price(24090, 24050, 4.95/365, 0.065, iv_entry, "CE")
    print(f"  BS-modeled premium (same IV): Rs {bs_exit.premium:.2f}")
    print(f"  Actual: Rs 200.00, Modeled: Rs {bs_exit.premium:.2f}, Diff: {(200 - bs_exit.premium):+.2f}")
    print()

    # Sensitivity test: NIFTY moves +1% from 23930 to 24169
    print("SENSITIVITY: spot 23930 -> 24169 (+1%) at same IV/DTE")
    bs_plus = bs_price(24169, 24050, 5/365, 0.065, iv_entry, "CE")
    print(f"  Premium: Rs {bs.premium:.2f} -> Rs {bs_plus.premium:.2f}  ({(bs_plus.premium/bs.premium - 1)*100:+.1f}%)")
