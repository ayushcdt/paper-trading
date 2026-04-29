"""
India equity real-money fees calculator (Zerodha-style discount broker).

Used to show what a trade would actually have cost in real money, vs the
flat 0.4% round-trip estimate the paper portfolio currently applies.

References (rates as of 2026):
  - Brokerage: Rs 0 delivery; min(0.03% turnover, Rs 20/order) intraday
  - STT: 0.1% on BOTH buy & sell (delivery); 0.025% on sell only (intraday)
  - Exchange transaction (NSE): 0.00322% on turnover (both legs)
  - SEBI charges: 0.0001% on turnover (Rs 10 / crore, both legs)
  - Stamp duty: 0.015% on buy (delivery); 0.003% on buy (intraday)
  - GST: 18% on (brokerage + exchange + SEBI)

A "round trip" = one buy + one sell. We compute fees per LEG and sum.
"""
from __future__ import annotations

from dataclasses import dataclass


# ---- Rates (override if broker differs) ----
BROKERAGE_INTRADAY_PCT = 0.03 / 100        # 0.03%
BROKERAGE_INTRADAY_CAP_INR = 20.0          # Rs 20 per order cap
BROKERAGE_DELIVERY_INR = 0.0               # Zerodha free delivery

STT_DELIVERY_PCT = 0.1 / 100               # 0.1% on both buy + sell
STT_INTRADAY_SELL_PCT = 0.025 / 100        # 0.025% on sell only

EXCHANGE_TX_PCT = 0.00322 / 100            # 0.00322% NSE both legs
SEBI_PCT = 0.0001 / 100                    # 0.0001% both legs

STAMP_DELIVERY_BUY_PCT = 0.015 / 100       # 0.015% on buy only (delivery)
STAMP_INTRADAY_BUY_PCT = 0.003 / 100       # 0.003% on buy only (intraday)

GST_PCT = 18 / 100                         # 18% on (brokerage + exchange + SEBI)


@dataclass
class FeeBreakdown:
    brokerage: float
    stt: float
    exchange: float
    sebi: float
    stamp: float
    gst: float
    total: float
    is_intraday: bool

    def as_dict(self) -> dict:
        return {
            "brokerage": round(self.brokerage, 2),
            "stt": round(self.stt, 2),
            "exchange": round(self.exchange, 2),
            "sebi": round(self.sebi, 2),
            "stamp": round(self.stamp, 2),
            "gst": round(self.gst, 2),
            "total": round(self.total, 2),
            "is_intraday": self.is_intraday,
        }


def _brokerage(notional: float, is_intraday: bool) -> float:
    if not is_intraday:
        return BROKERAGE_DELIVERY_INR
    return min(BROKERAGE_INTRADAY_CAP_INR, notional * BROKERAGE_INTRADAY_PCT)


def compute_round_trip_fees(buy_price: float, sell_price: float, qty: int,
                             is_intraday: bool) -> FeeBreakdown:
    """Compute total real-money fees for one round-trip (buy + sell same qty)."""
    if qty <= 0 or buy_price <= 0 or sell_price <= 0:
        return FeeBreakdown(0, 0, 0, 0, 0, 0, 0, is_intraday)

    buy_notional = buy_price * qty
    sell_notional = sell_price * qty
    total_notional = buy_notional + sell_notional

    # Brokerage: per leg (buy + sell)
    brokerage = _brokerage(buy_notional, is_intraday) + _brokerage(sell_notional, is_intraday)

    # STT
    if is_intraday:
        stt = sell_notional * STT_INTRADAY_SELL_PCT
    else:
        stt = (buy_notional + sell_notional) * STT_DELIVERY_PCT

    # Exchange tx (both legs)
    exchange = total_notional * EXCHANGE_TX_PCT

    # SEBI (both legs)
    sebi = total_notional * SEBI_PCT

    # Stamp (buy only)
    stamp_pct = STAMP_INTRADAY_BUY_PCT if is_intraday else STAMP_DELIVERY_BUY_PCT
    stamp = buy_notional * stamp_pct

    # GST: 18% on (brokerage + exchange + SEBI)
    gst = (brokerage + exchange + sebi) * GST_PCT

    total = brokerage + stt + exchange + sebi + stamp + gst
    return FeeBreakdown(brokerage, stt, exchange, sebi, stamp, gst, total, is_intraday)


def compute_round_trip_fees_pct(buy_price: float, sell_price: float, qty: int,
                                 is_intraday: bool) -> float:
    """Convenience: round-trip fees as % of buy notional."""
    fb = compute_round_trip_fees(buy_price, sell_price, qty, is_intraday)
    bn = buy_price * qty
    return (fb.total / bn) * 100 if bn > 0 else 0.0


if __name__ == "__main__":
    # Sanity: Rs 10K trade, buy 100 @ 100, sell 100 @ 105
    print("Delivery (held overnight):")
    fb = compute_round_trip_fees(100, 105, 100, is_intraday=False)
    print(f"  {fb.as_dict()}  ({fb.total / 10_000 * 100:.3f}% of buy notional)")
    print("Intraday (same-day):")
    fb = compute_round_trip_fees(100, 105, 100, is_intraday=True)
    print(f"  {fb.as_dict()}  ({fb.total / 10_000 * 100:.3f}% of buy notional)")
