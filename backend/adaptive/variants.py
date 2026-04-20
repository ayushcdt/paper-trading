"""
Four strategy variants -- one per regime.

Each variant exposes:
    pick(histories, target_date, universe) -> list[Pick]
    check_exit(df, position, current_idx, current_rank) -> ExitSignal

Variants in order of aggressiveness:
    MomentumAgg    -- BULL_LOW_VOL (original v2 design)
    MomentumCons   -- BULL_HIGH_VOL (wider stops, fewer names)
    MeanReversion  -- RANGE (buy oversold within uptrend)
    Defensive      -- BEAR (cash / defensives only)
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Protocol

import numpy as np
import pandas as pd


# ---------- Shared primitives ------------------------------------------------

def annualized_vol(close: pd.Series, window: int = 252) -> float:
    if len(close) < window + 1:
        return float("nan")
    rets = np.log(close).diff().dropna().tail(window)
    return float(rets.std() * np.sqrt(252)) if len(rets) >= 20 else float("nan")


def vol_adj_return(close: pd.Series, lookback: int, skip: int = 0) -> float | None:
    if len(close) < lookback + skip + 1:
        return None
    end = -1 - skip if skip else -1
    start = end - lookback
    if abs(start) > len(close):
        return None
    p0, p1 = float(close.iloc[start]), float(close.iloc[end])
    if p0 <= 0:
        return None
    vol = annualized_vol(close)
    if not np.isfinite(vol) or vol == 0:
        return None
    return ((p1 / p0) - 1) / vol


def momentum_12_1(close: pd.Series) -> float | None:
    s_12_1 = vol_adj_return(close, lookback=231, skip=21)
    s_6m = vol_adj_return(close, lookback=126)
    s_3m = vol_adj_return(close, lookback=63)
    if s_12_1 is None or s_6m is None or s_3m is None:
        return None
    return 0.6 * s_12_1 + 0.3 * s_6m + 0.1 * s_3m


def rsi(close: pd.Series, period: int = 14) -> float | None:
    if len(close) < period + 1:
        return None
    delta = close.diff()
    up = delta.clip(lower=0).rolling(period).mean()
    down = -delta.clip(upper=0).rolling(period).mean()
    if down.iloc[-1] == 0:
        return 100.0
    rs = up.iloc[-1] / down.iloc[-1]
    return float(100 - 100 / (1 + rs))


def ema(close: pd.Series, span: int) -> pd.Series:
    return close.ewm(span=span, adjust=False).mean()


def atr(df: pd.DataFrame, period: int = 14) -> float | None:
    if len(df) < period + 1:
        return None
    high, low, close = df["High"], df["Low"], df["Close"]
    prev = close.shift(1)
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    v = tr.rolling(period).mean().iloc[-1]
    return float(v) if pd.notna(v) else None


# ---------- Data types -------------------------------------------------------

@dataclass
class Pick:
    symbol: str
    score: float
    rank: int
    stop: float
    entry_ref: float  # reference price at pick time
    variant: str


@dataclass
class ExitSignal:
    triggered: bool
    reason: str
    exit_price: float | None = None


# ---------- Variant Protocol -------------------------------------------------

class StrategyVariant(Protocol):
    name: str
    max_picks: int

    def pick(
        self,
        histories: dict[str, pd.DataFrame],
        target_date: pd.Timestamp,
        universe: list[str],
    ) -> list[Pick]: ...

    def check_exit(
        self,
        df: pd.DataFrame,
        entry_price: float,
        entry_idx: int,
        current_idx: int,
        current_rank: int | None,
    ) -> ExitSignal: ...


# ---------- Shared helper: last valid idx ------------------------------------

def _latest_idx(df: pd.DataFrame, target_date: pd.Timestamp) -> int | None:
    matches = df.index[df["Date"] <= target_date]
    return int(matches[-1]) if len(matches) else None


# ---------- Variant 1: MomentumAgg (BULL_LOW_VOL) ----------------------------

@dataclass
class MomentumAgg:
    name: str = "momentum_agg"
    max_picks: int = 15
    hard_stop_pct: float = -15.0
    max_hold_days: int = 90
    rank_drop_threshold: int = 30

    def pick(self, histories, target_date, universe):
        scored = []
        for sym in universe:
            df = histories.get(sym)
            if df is None:
                continue
            li = _latest_idx(df, target_date)
            if li is None or li < 252:
                continue
            close = df.iloc[: li + 1]["Close"]
            # Trend gate: above own 200 DMA
            if float(close.iloc[-1]) <= float(close.tail(200).mean()):
                continue
            s = momentum_12_1(close)
            if s is None:
                continue
            scored.append((sym, s, li))
        scored.sort(key=lambda x: x[1], reverse=True)
        picks = []
        for rank, (sym, s, li) in enumerate(scored[: self.max_picks], 1):
            df = histories[sym]
            close = df.iloc[: li + 1]["Close"]
            low20 = float(df.iloc[max(0, li - 19) : li + 1]["Low"].min())
            ema50 = float(ema(close, 50).iloc[-1])
            stop = max(low20, ema50)
            picks.append(Pick(sym, s, rank, stop, float(close.iloc[-1]), self.name))
        return picks

    def check_exit(self, df, entry_price, entry_idx, current_idx, current_rank):
        if current_idx >= len(df):
            return ExitSignal(False, "OOB")
        close = float(df.iloc[current_idx]["Close"])
        pct = (close - entry_price) / entry_price * 100
        if pct <= self.hard_stop_pct:
            return ExitSignal(True, f"hard stop {pct:.1f}%", close)
        low20 = float(df.iloc[max(0, current_idx - 19) : current_idx + 1]["Low"].min())
        ema50 = float(ema(df.iloc[: current_idx + 1]["Close"], 50).iloc[-1])
        floor = max(low20, ema50)
        if close < floor:
            return ExitSignal(True, f"trail break <{floor:.2f}", close)
        if current_rank is not None and current_rank > self.rank_drop_threshold:
            return ExitSignal(True, f"rank {current_rank}", close)
        if current_idx - entry_idx >= self.max_hold_days:
            return ExitSignal(True, f"time {current_idx - entry_idx}d", close)
        return ExitSignal(False, "hold")


# ---------- Variant 2: MomentumCons (BULL_HIGH_VOL) --------------------------

@dataclass
class MomentumCons:
    name: str = "momentum_cons"
    max_picks: int = 10
    hard_stop_pct: float = -12.0
    max_hold_days: int = 60
    rank_drop_threshold: int = 20
    atr_buffer_multiple: float = 1.0  # wider stops in high vol

    def pick(self, histories, target_date, universe):
        scored = []
        for sym in universe:
            df = histories.get(sym)
            if df is None:
                continue
            li = _latest_idx(df, target_date)
            if li is None or li < 252:
                continue
            close = df.iloc[: li + 1]["Close"]
            if float(close.iloc[-1]) <= float(close.tail(200).mean()):
                continue
            s = momentum_12_1(close)
            if s is None:
                continue
            scored.append((sym, s, li))
        scored.sort(key=lambda x: x[1], reverse=True)
        picks = []
        for rank, (sym, s, li) in enumerate(scored[: self.max_picks], 1):
            df = histories[sym]
            close = df.iloc[: li + 1]["Close"]
            low20 = float(df.iloc[max(0, li - 19) : li + 1]["Low"].min())
            ema100 = float(ema(close, 100).iloc[-1])  # wider structural ref
            a = atr(df.iloc[: li + 1]) or 0
            stop = min(low20, ema100) - self.atr_buffer_multiple * a
            picks.append(Pick(sym, s, rank, stop, float(close.iloc[-1]), self.name))
        return picks

    def check_exit(self, df, entry_price, entry_idx, current_idx, current_rank):
        if current_idx >= len(df):
            return ExitSignal(False, "OOB")
        close = float(df.iloc[current_idx]["Close"])
        pct = (close - entry_price) / entry_price * 100
        if pct <= self.hard_stop_pct:
            return ExitSignal(True, f"hard stop {pct:.1f}%", close)
        low20 = float(df.iloc[max(0, current_idx - 19) : current_idx + 1]["Low"].min())
        ema100 = float(ema(df.iloc[: current_idx + 1]["Close"], 100).iloc[-1])
        a = atr(df.iloc[: current_idx + 1]) or 0
        floor = min(low20, ema100) - self.atr_buffer_multiple * a
        if close < floor:
            return ExitSignal(True, f"wide trail <{floor:.2f}", close)
        if current_rank is not None and current_rank > self.rank_drop_threshold:
            return ExitSignal(True, f"rank {current_rank}", close)
        if current_idx - entry_idx >= self.max_hold_days:
            return ExitSignal(True, f"time {current_idx - entry_idx}d", close)
        return ExitSignal(False, "hold")


# ---------- Variant 3: MeanReversion (RANGE) ---------------------------------

@dataclass
class MeanReversion:
    name: str = "mean_reversion"
    max_picks: int = 10
    min_picks: int = 5            # floor -- never return fewer than this
    hard_stop_pct: float = -5.0
    max_hold_days: int = 20

    def _score_at(self, histories, target_date, universe, rsi_cutoff, dist_cutoff):
        """Score with given relaxation level. Returns [(sym, score, li), ...]."""
        scored = []
        for sym in universe:
            df = histories.get(sym)
            if df is None:
                continue
            li = _latest_idx(df, target_date)
            if li is None or li < 252:
                continue
            close = df.iloc[: li + 1]["Close"]
            last = float(close.iloc[-1])
            ma200 = float(close.tail(200).mean())
            if last <= ma200:
                continue
            r = rsi(close)
            if r is None or r >= rsi_cutoff:
                continue
            ema50 = float(ema(close, 50).iloc[-1])
            dist_50 = abs(last - ema50) / ema50 * 100
            if dist_50 > dist_cutoff:
                continue
            # Score: more oversold + closer to 50EMA = better
            score = (rsi_cutoff - r) + (dist_cutoff - dist_50) * 2
            scored.append((sym, score, li))
        return scored

    def pick(self, histories, target_date, universe):
        """
        Relaxation ladder -- keeps looking until we have min_picks candidates.
        This prevents "playing safe" by returning zero picks in subtle chop.
        """
        ladders = [
            (35, 3.0),   # canonical: RSI<35 + within 3% of 50EMA
            (40, 5.0),   # relaxed L1: RSI<40 + within 5%
            (45, 7.0),   # relaxed L2: RSI<45 + within 7%
            (50, 10.0),  # final: basically "uptrend + slight pullback"
        ]
        scored: list[tuple[str, float, int]] = []
        ladder_used = 0
        for i, (rsi_c, dist_c) in enumerate(ladders):
            scored = self._score_at(histories, target_date, universe, rsi_c, dist_c)
            if len(scored) >= self.min_picks:
                ladder_used = i
                break
        scored.sort(key=lambda x: x[1], reverse=True)
        picks = []
        for rank, (sym, s, li) in enumerate(scored[: self.max_picks], 1):
            df = histories[sym]
            close = df.iloc[: li + 1]["Close"]
            low10 = float(df.iloc[max(0, li - 9) : li + 1]["Low"].min())
            a = atr(df.iloc[: li + 1]) or 0
            last = float(close.iloc[-1])
            stop = max(low10 * 0.99, last - 2 * a)
            picks.append(Pick(sym, s, rank, stop, last, self.name))
        return picks

    def check_exit(self, df, entry_price, entry_idx, current_idx, current_rank):
        if current_idx >= len(df):
            return ExitSignal(False, "OOB")
        close = float(df.iloc[current_idx]["Close"])
        pct = (close - entry_price) / entry_price * 100
        if pct <= self.hard_stop_pct:
            return ExitSignal(True, f"hard stop {pct:.1f}%", close)
        # Mean reversion target: exit when back to 20 EMA or +8%
        ema20 = float(ema(df.iloc[: current_idx + 1]["Close"], 20).iloc[-1])
        if close >= ema20 * 1.002:  # just touching 20 EMA = reversion complete
            return ExitSignal(True, f"reverted to 20EMA", close)
        if pct >= 8:
            return ExitSignal(True, f"target +{pct:.1f}%", close)
        if current_idx - entry_idx >= self.max_hold_days:
            return ExitSignal(True, f"time {current_idx - entry_idx}d", close)
        return ExitSignal(False, "hold")


# ---------- Variant 4: Defensive (BEAR) --------------------------------------

# Defensive basket -- low-vol, quality, dividend-paying names that hold up in
# bear markets. Equal weight. Beats sitting 100% in cash (which has 0% return).
DEFENSIVE_BASKET = [
    "NESTLEIND", "HINDUNILVR", "ITC", "DABUR", "MARICO",
    "BRITANNIA", "COLPAL", "POWERGRID", "NTPC", "COALINDIA",
]


@dataclass
class Defensive:
    """
    BEAR regime: hold a small basket of defensive sector leaders at 25-50% of
    normal size. This is NOT full-risk; it's 'safer than cash' because cash
    loses to inflation while defensives typically pay dividends and hold value.
    """
    name: str = "defensive"
    max_picks: int = 5

    def pick(self, histories, target_date, universe):
        picks = []
        # Pick the intersection of DEFENSIVE_BASKET with universe that has data
        candidates = [s for s in DEFENSIVE_BASKET if s in universe]
        for rank, sym in enumerate(candidates[: self.max_picks], 1):
            df = histories.get(sym)
            if df is None:
                continue
            li = _latest_idx(df, target_date)
            if li is None:
                continue
            close = df.iloc[: li + 1]["Close"]
            last = float(close.iloc[-1])
            a = atr(df.iloc[: li + 1]) or 0
            stop = last - 2 * a if a > 0 else last * 0.92  # wider stops in bear
            picks.append(Pick(sym, float(self.max_picks - rank), rank, stop, last, self.name))
        return picks

    def check_exit(self, df, entry_price, entry_idx, current_idx, current_rank):
        """Defensive holdings: only exit on -8% hard stop or regime change (handled at rebalance)."""
        if current_idx >= len(df):
            return ExitSignal(False, "OOB")
        close = float(df.iloc[current_idx]["Close"])
        pct = (close - entry_price) / entry_price * 100
        if pct <= -8:
            return ExitSignal(True, f"hard stop {pct:.1f}%", close)
        return ExitSignal(False, "hold")


# ---------- Registry ---------------------------------------------------------

def build_variants() -> dict[str, StrategyVariant]:
    """
    Build all variants. If data/variant_params.json exists (written by
    scripts/recalibrate_params.py), use calibrated params. Otherwise defaults.
    """
    import json as _json
    from pathlib import Path as _Path

    params_path = _Path(__file__).resolve().parent.parent.parent / "data" / "variant_params.json"
    calibrated: dict = {}
    if params_path.exists():
        try:
            cal = _json.loads(params_path.read_text(encoding="utf-8"))
            for v_name, v_info in (cal.get("variants") or {}).items():
                if v_info.get("params"):
                    calibrated[v_name] = v_info["params"]
        except Exception:
            pass

    factories = {
        "momentum_agg":   MomentumAgg,
        "momentum_cons":  MomentumCons,
        "mean_reversion": MeanReversion,
        "defensive":      Defensive,
    }
    out = {}
    for name, cls in factories.items():
        if name in calibrated:
            try:
                out[name] = cls(**calibrated[name])
            except Exception:
                out[name] = cls()
        else:
            out[name] = cls()
    return out


# ---------- Regime -> Variant mapping ----------------------------------------

REGIME_TO_VARIANT = {
    "BULL_LOW_VOL":  "momentum_agg",
    "BULL_HIGH_VOL": "momentum_cons",
    "RANGE":         "mean_reversion",
    "BEAR":          "defensive",
}
