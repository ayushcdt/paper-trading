"""
Reference strategies for the backtest harness.

Each implements the Strategy protocol from research.harness:
  - initialize(universe, capital)
  - on_rebalance(state)  -> list[Decision]
  - on_mark(state)       -> list[Decision]   (default no-op)

Add new strategies here as we test mechanisms.
"""
from __future__ import annotations

import sys
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from research.harness import Decision, MarketState


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

def _close_dropped(state: MarketState, target_symbols: set[str]) -> list[Decision]:
    return [
        Decision(action="CLOSE", symbol=sym, reason="dropped from picks")
        for sym in state.open_positions if sym not in target_symbols
    ]


def _open_new(state: MarketState, target_symbols: set[str]) -> list[Decision]:
    out = []
    for sym in target_symbols:
        if sym in state.open_positions:
            continue
        # Skip if no bars or insufficient history
        df = state.histories.get(sym)
        if df is None or len(df) < 20:
            continue
        out.append(Decision(action="OPEN", symbol=sym, reason="new pick"))
    return out


def _last_close(df: pd.DataFrame) -> float:
    return float(df["Close"].iloc[-1]) if len(df) else 0.0


# ----------------------------------------------------------------------------
# Baselines
# ----------------------------------------------------------------------------

class EqualWeightNifty50:
    """Equal-weight buy-and-hold all Nifty 50 names; monthly rebalance to target weights.
    No selection skill — pure benchmark."""
    name = "equal_weight_nifty50"

    def initialize(self, universe, capital):
        try:
            from stock_picker import NIFTY_50
            self.basket = sorted(set(NIFTY_50) & set(universe))[:50]
        except Exception:
            self.basket = sorted(universe)[:50]

    def on_rebalance(self, state):
        target = set(self.basket)
        return _close_dropped(state, target) + _open_new(state, target)

    def on_mark(self, state):
        return []


class MomentumTop20:
    """Buy top 20 stocks by 12m-1m momentum each month. Classic JT strategy."""
    name = "momentum_top20"

    def initialize(self, universe, capital):
        self.universe = list(universe)
        self.top_n = 20

    def on_rebalance(self, state):
        scores = []
        for sym in self.universe:
            df = state.histories.get(sym)
            if df is None or len(df) < 252:
                continue
            close = df["Close"]
            # Skip last month (1m), take return over the 11m before that.
            try:
                t12 = close.iloc[-252]
                t1 = close.iloc[-21]
                if t12 > 0:
                    scores.append((sym, (t1 - t12) / t12))
            except Exception:
                continue
        if not scores:
            return []
        scores.sort(key=lambda x: -x[1])
        target = set(s for s, _ in scores[: self.top_n])
        return _close_dropped(state, target) + _open_new(state, target)

    def on_mark(self, state):
        return []


class OversoldQualityTop20:
    """Buy top 20 stocks combining oversold (low RSI) AND positive 6m trend (quality).
    Captures the 'mean reversion within an uptrend' setup."""
    name = "oversold_quality_top20"

    def initialize(self, universe, capital):
        self.universe = list(universe)
        self.top_n = 20

    def on_rebalance(self, state):
        scores = []
        for sym in self.universe:
            df = state.histories.get(sym)
            if df is None or len(df) < 130:
                continue
            close = df["Close"]
            try:
                # 6m return (trend / quality proxy)
                t126 = close.iloc[-126]
                t0 = close.iloc[-1]
                trend_6m = (t0 - t126) / t126
                if trend_6m <= 0:
                    continue   # quality gate: must be in 6m uptrend
                # 14-period RSI
                delta = close.diff()
                up = delta.clip(lower=0).rolling(14).mean().iloc[-1]
                down = -delta.clip(upper=0).rolling(14).mean().iloc[-1]
                rsi = 100 - 100 / (1 + (up / down)) if down else 100
                if rsi >= 50:
                    continue   # not oversold enough
                # Score: lower RSI is better (more oversold), bigger 6m trend is better
                score = (50 - rsi) * 1.5 + trend_6m * 100
                scores.append((sym, score))
            except Exception:
                continue
        if not scores:
            return []
        scores.sort(key=lambda x: -x[1])
        target = set(s for s, _ in scores[: self.top_n])
        return _close_dropped(state, target) + _open_new(state, target)

    def on_mark(self, state):
        return []


class V3BaselineWrapper:
    """Wraps the live V3 picker (adaptive engine + variants) into the harness Strategy interface.

    Implementation note: V3's live code path (adaptive.engine.pick_stocks_v3) was built
    for production with side effects. For backtest we replicate the *decision logic*
    directly here — same regime classifier + variant selector — without the side effects.
    """
    name = "v3_baseline"

    def initialize(self, universe, capital):
        self.universe = list(universe)
        self.top_n = 5

    def on_rebalance(self, state):
        try:
            from adaptive.regime import classify_regime, compute_breadth
            from adaptive.variants import build_variants, REGIME_TO_VARIANT
            from data_store import get_bars as _get_bars
        except Exception:
            return []

        # Universe + histories with enough warmup
        histories = {sym: df for sym, df in state.histories.items() if len(df) >= 252}
        if not histories:
            return []
        nifty_df = state.nifty_history
        if len(nifty_df) < 252:
            return []

        # Pull VIX history up to current date (so regime classifier works properly)
        try:
            vix_df = _get_bars("INDIAVIX", n_days=5000).copy()
            import pandas as _pd
            vix_df["Date"] = _pd.to_datetime(vix_df["Date"])
            vix_df = vix_df[vix_df["Date"] <= state.date].reset_index(drop=True)
            if len(vix_df) < 50:
                vix_df = None
        except Exception:
            vix_df = None

        # Classify regime
        try:
            breadth = compute_breadth(histories)
            regime, _ = classify_regime(nifty_df, vix_df=vix_df, breadth=breadth)
        except Exception:
            regime = "UNKNOWN"

        variant_id = REGIME_TO_VARIANT.get(regime, "mean_reversion")
        variants = build_variants()
        var = variants.get(variant_id)
        if var is None:
            return []

        try:
            picks_list = var.pick(histories, state.date, list(histories.keys()))
        except Exception as e:
            print(f"  V3 pick failed at {state.date.date()}: {e}")
            return []

        if not picks_list:
            return []
        # picks_list is list of Pick objects with symbol, stop, entry attrs
        target = set(p.symbol for p in picks_list[: self.top_n])
        decisions = _close_dropped(state, target) + _open_new(state, target)
        for d in decisions:
            if d.action == "OPEN":
                d.reason = f"v3 {variant_id}/{regime}"
                p = next((x for x in picks_list if x.symbol == d.symbol), None)
                if p:
                    d.new_stop = p.stop
        # Stash regime on positions for per-regime attribution
        self._last_regime = regime
        self._last_variant = variant_id
        return decisions

    def on_mark(self, state):
        return []


class V3SingleVariant:
    """Run a single V3 variant continuously (no adaptive switching).
    Useful to isolate which variant carries the signal."""
    def __init__(self, variant_id: str, top_n: int = 5):
        self.variant_id = variant_id
        self.name = f"v3_{variant_id}_only"
        self.top_n = top_n

    def initialize(self, universe, capital):
        self.universe = list(universe)

    def on_rebalance(self, state):
        try:
            from adaptive.variants import build_variants
        except Exception:
            return []
        histories = {sym: df for sym, df in state.histories.items() if len(df) >= 252}
        if not histories:
            return []
        var = build_variants().get(self.variant_id)
        if var is None:
            return []
        try:
            picks_list = var.pick(histories, state.date, list(histories.keys()))
        except Exception as e:
            print(f"  {self.variant_id} pick failed at {state.date.date()}: {e}")
            return []
        if not picks_list:
            return []
        target = set(p.symbol for p in picks_list[: self.top_n])
        decisions = _close_dropped(state, target) + _open_new(state, target)
        for d in decisions:
            if d.action == "OPEN":
                d.reason = self.variant_id
                p = next((x for x in picks_list if x.symbol == d.symbol), None)
                if p:
                    d.new_stop = p.stop
        return decisions

    def on_mark(self, state):
        return []

    def on_mark(self, state):
        return []
