"""
Production momentum picker — replaces V3 adaptive picker.

WHY THIS REPLACES V3:
  V3 adaptive selector returns mean_reversion 100% of the time (regime
  classifier outputs UNKNOWN), and the mean_reversion variant has a
  "relaxation ladder" that forces weak picks. Result: V3 lost to Nifty
  buy-and-hold by -3.78% CAGR over 4.3y backtest with WR 31.9%.

  Single-variant momentum_agg, alone, gave +20.55% CAGR / Sharpe 1.05.
  Adding the risk overlay below pushed that to +21.69% CAGR / Sharpe 1.35
  while CUTTING max drawdown from -33.5% to -15.9%.

  Source: data/research/sustainability/q4_momentum_with_risk.json
          data/research/backtest_v3_momentum_agg.json

WHAT THIS PICKER DOES:
  1. Run momentum_agg variant on the trading universe (top 12-1m momentum,
     filtered to names above their 200 DMA).
  2. Apply risk overlay:
     - 10 positions max (vs V3's 5) — spreads risk
     - 30% notional cap per NSE industry — sector concentration
     - Portfolio drawdown circuit breaker: halt new entries when portfolio
       is -8% from rolling peak; resume after recovering to -4%
     - Tail-event halts: VIX > 25 halves position sizes; Nifty -3% in a
       day blocks new entries that day; portfolio -15% from peak halts
       all trading until manual review
  3. Return picks in the same dict shape generate_analysis expects.

When the picker is in HALT state (DD breaker engaged), it still returns
held positions in `picks` so the paper runner doesn't close them — only
new entries are blocked.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from logzero import logger
import pandas as pd

from data_fetcher import SYMBOL_TOKENS, get_fetcher
from data_store import get_bars
from adaptive.variants import build_variants
from common.market_hours import now_ist


# ---------- Config ----------------------------------------------------------

MAX_POSITIONS = 10
SECTOR_CAP_PCT = 30.0
DD_HALT_PCT = -8.0           # halt new entries when portfolio is here-or-worse from peak
DD_RESUME_PCT = -4.0         # resume when recovered to here-or-better
TAIL_DD_PCT = -15.0          # full halt (manual review only) past this
VIX_GATE_HIGH = 25.0         # above this, halve position sizes
VIX_GATE_EXTREME = 35.0      # above this, no new entries
NIFTY_DAILY_DROP_HALT_PCT = -3.0   # if nifty fell this much today, no new entries today

PICKER_STATE_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "picker_state.json"


# ---------- State persistence -----------------------------------------------

def _load_state() -> dict:
    if not PICKER_STATE_PATH.exists():
        return {"peak_equity": 0.0, "halt_active": False, "halt_reason": "", "tail_halt": False}
    try:
        return json.loads(PICKER_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"peak_equity": 0.0, "halt_active": False, "halt_reason": "", "tail_halt": False}


def _save_state(state: dict) -> None:
    PICKER_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        PICKER_STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"picker state save failed: {e}")


# ---------- Sector / industry lookups ---------------------------------------

def _industry_of(symbol: str) -> str:
    try:
        from news.sector_map import industry_of
        return industry_of(symbol) or "UNKNOWN"
    except Exception:
        return "UNKNOWN"


# ---------- Risk overlay primitives ------------------------------------------

def _regime_health_gate() -> tuple[float, str]:
    """P17: regime drawdown circuit breaker. Reads recent live performance from
    paper trade log. If 3-month CAGR negative or 6-month Sharpe negative,
    we are in a FAILED REGIME for momentum_agg -- halve sizes and tighten
    strength gate. Returns (size_multiplier, reason).

    Rationale: walk-forward shows 3-month CAGR -8.47% / 6m Sharpe -0.19 as of
    today. We can't trust the strategy at full size in this regime. Halving
    sizes preserves capital for the recovery rather than compounding losses."""
    try:
        from paper.portfolio import PaperPortfolio, STARTING_CAPITAL
        pf = PaperPortfolio()
        curve = pf.equity_curve(days=90)
        if len(curve) < 5:
            return 1.0, "regime gate: too few days of data"
        # 3-month return (proxy for CAGR direction)
        first = curve[0].get("equity", STARTING_CAPITAL)
        last = curve[-1].get("equity", STARTING_CAPITAL)
        if first <= 0:
            return 1.0, "regime gate: first equity zero"
        ret_3m = (last - first) / first * 100
        # Daily-return std for Sharpe approx
        import math
        equities = [c.get("equity", STARTING_CAPITAL) for c in curve]
        rets = [(equities[i+1] - equities[i]) / equities[i] for i in range(len(equities)-1)]
        if rets and any(rets):
            mean_ret = sum(rets) / len(rets)
            var = sum((r - mean_ret) ** 2 for r in rets) / max(1, len(rets) - 1)
            std = math.sqrt(var) if var > 0 else 0
            sharpe_proxy = (mean_ret * 252) / (std * math.sqrt(252)) if std > 0 else 0
        else:
            sharpe_proxy = 0
        if ret_3m < -3.0 or sharpe_proxy < -0.2:
            return 0.5, f"FAILED REGIME (3m {ret_3m:+.2f}%, Sharpe {sharpe_proxy:+.2f}): half-size + tight gates"
        return 1.0, f"regime healthy (3m {ret_3m:+.2f}%, Sharpe {sharpe_proxy:+.2f})"
    except Exception as e:
        logger.warning(f"regime gate read failed: {e}")
        return 1.0, "regime gate skipped"


def _vix_gate() -> tuple[float, str]:
    """Returns (size_multiplier, reason). 1.0 = full size; 0.5 = halved; 0 = no entries."""
    try:
        df = get_bars("INDIAVIX", n_days=10)
        if len(df) == 0:
            return 1.0, "vix unavailable"
        vix = float(df["Close"].iloc[-1])
        if vix >= VIX_GATE_EXTREME:
            return 0.0, f"VIX {vix:.1f} >= {VIX_GATE_EXTREME} (extreme): no new entries"
        if vix >= VIX_GATE_HIGH:
            return 0.5, f"VIX {vix:.1f} >= {VIX_GATE_HIGH}: half-size entries"
        return 1.0, f"VIX {vix:.1f} normal"
    except Exception as e:
        logger.warning(f"vix gate read failed: {e}")
        return 1.0, "vix gate skipped"


def _nifty_today_check() -> tuple[bool, str]:
    """Returns (allow_new_entries, reason). False if Nifty dropped >3% today."""
    try:
        df = get_bars("NIFTY", n_days=10)
        if len(df) < 2:
            return True, "nifty data short"
        df = df.copy()
        df["Date"] = pd.to_datetime(df["Date"])
        df = df.sort_values("Date").reset_index(drop=True)
        # If today's bar is in DB, use prev_close vs today_close
        today = now_ist().date()
        last_date = df["Date"].iloc[-1].date()
        if last_date != today:
            return True, "nifty today bar not yet finalised"
        prev = float(df["Close"].iloc[-2])
        curr = float(df["Close"].iloc[-1])
        chg = (curr - prev) / prev * 100
        if chg <= NIFTY_DAILY_DROP_HALT_PCT:
            return False, f"Nifty {chg:+.2f}% today (<= {NIFTY_DAILY_DROP_HALT_PCT}%): no new entries"
        return True, f"Nifty {chg:+.2f}% today: ok"
    except Exception:
        return True, "nifty check skipped"


def _update_dd_halt(state: dict, current_equity: float) -> tuple[dict, str]:
    """Mutates state in place: tracks peak, toggles halt_active + tail_halt."""
    if current_equity > state.get("peak_equity", 0):
        state["peak_equity"] = current_equity
    peak = state["peak_equity"]
    if peak <= 0:
        return state, "no peak yet"
    dd = (current_equity - peak) / peak * 100

    # Tail halt — engages and stays engaged (manual reset)
    if dd <= TAIL_DD_PCT and not state.get("tail_halt"):
        state["tail_halt"] = True
        logger.error(f"TAIL HALT engaged: portfolio {dd:.2f}% from peak; manual review required")

    # Soft halt
    if state.get("halt_active"):
        if dd >= DD_RESUME_PCT:
            state["halt_active"] = False
            state["halt_reason"] = ""
            return state, f"DD halt RELEASED at {dd:+.2f}%"
        return state, f"DD halt ACTIVE: {dd:+.2f}% from peak"
    if dd <= DD_HALT_PCT:
        state["halt_active"] = True
        state["halt_reason"] = f"DD {dd:.2f}% triggered halt at {datetime.now().isoformat()}"
        return state, f"DD halt ENGAGED at {dd:+.2f}%"
    return state, f"DD {dd:+.2f}% (within tolerance)"


# ---------- Pick generation -------------------------------------------------

def _build_universe_histories(min_bars: int = 252, intraday_ltps: Optional[dict[str, float]] = None) -> dict[str, pd.DataFrame]:
    """Load bars for universe stocks with enough history for momentum_agg.

    If `intraday_ltps` provided: append today's running close (LTP) onto each
    history so the picker uses live intraday price for the most-recent close
    in 6m/3m momentum components. The 12-1m component (60% weight) is unaffected
    because it uses the close from 21+ days ago — but 6m/3m shifts ARE captured.
    """
    today_ist = pd.Timestamp(now_ist().date())
    out = {}
    for sym, tok in SYMBOL_TOKENS.items():
        if tok.startswith("999"):  # indices
            continue
        df = get_bars(sym, n_days=400)
        if len(df) < min_bars:
            continue
        df = df.copy()
        df["Date"] = pd.to_datetime(df["Date"])
        df = df.sort_values("Date").reset_index(drop=True)
        # If intraday LTPs supplied AND today's bar isn't already in DB, append
        # a synthetic running candle. (If today's bar IS in DB, leave it alone —
        # that means market is closed and the daily bar has been finalised.)
        if intraday_ltps and sym in intraday_ltps:
            ltp = float(intraday_ltps[sym])
            if ltp > 0 and df["Date"].iloc[-1] < today_ist:
                # Append a synthetic row: open/high/low approximated, close=LTP.
                # Only Close is used by momentum_12_1; OHLV are filler.
                last = df.iloc[-1]
                synthetic = {
                    "Date": today_ist,
                    "Open": ltp, "High": max(ltp, last["High"]),
                    "Low": min(ltp, last["Low"]),
                    "Close": ltp, "Volume": 0,
                }
                df = pd.concat([df, pd.DataFrame([synthetic])], ignore_index=True)
        out[sym] = df
    return out


def _apply_sector_cap(picks_list, current_held_industries: dict[str, float], equity: float) -> list:
    """Trim picks_list to respect 30% sector cap given existing holdings.
    Returns the kept subset (in original order)."""
    cap = equity * (SECTOR_CAP_PCT / 100.0)
    sector_notional = dict(current_held_industries)
    kept = []
    avg_slot = equity / MAX_POSITIONS
    for p in picks_list:
        sec = _industry_of(p.symbol)
        used = sector_notional.get(sec, 0.0)
        if used + avg_slot > cap:
            continue
        kept.append(p)
        sector_notional[sec] = used + avg_slot
    return kept


def _current_held_sector_exposure() -> tuple[dict[str, float], float]:
    """Read paper_portfolio.json to get current sector notional + equity."""
    try:
        pp_path = Path(__file__).resolve().parent.parent.parent / "data" / "paper_portfolio.json"
        if not pp_path.exists():
            return {}, 0.0
        d = json.loads(pp_path.read_text(encoding="utf-8"))
    except Exception:
        return {}, 0.0
    equity = float(d.get("current_equity", 0.0))
    by_sector: dict[str, float] = defaultdict(float)
    for p in d.get("open_positions", []) or []:
        sec = _industry_of(p.get("symbol", ""))
        notional = float(p.get("entry_price", 0)) * int(p.get("qty", 0))
        by_sector[sec] += notional
    return dict(by_sector), equity


# ---------- Public API ------------------------------------------------------

def run_momentum_picker(max_picks: int = MAX_POSITIONS, intraday_ltps: Optional[dict[str, float]] = None) -> dict:
    """Production picker — replaces run_stock_picker_v3.
    Returns same dict shape as the V3 picker for compatibility.

    If `intraday_ltps` provided, the picker uses live intraday prices for
    today's close (via _build_universe_histories). Used by the intraday
    rebalance loop in mark_to_market.py.
    """
    state = _load_state()

    # Risk gates BEFORE generating picks
    held_by_sector, current_equity = _current_held_sector_exposure()
    state, dd_reason = _update_dd_halt(state, current_equity if current_equity > 0 else state.get("peak_equity", 0))
    vix_mult, vix_reason = _vix_gate()
    regime_mult, regime_reason = _regime_health_gate()
    nifty_ok, nifty_reason = _nifty_today_check()

    halt_blocking_new = state.get("halt_active") or state.get("tail_halt") or vix_mult == 0.0 or not nifty_ok
    halt_reasons = []
    if state.get("tail_halt"):
        halt_reasons.append(f"TAIL_HALT (manual reset required)")
    if state.get("halt_active"):
        halt_reasons.append(dd_reason)
    if vix_mult == 0.0:
        halt_reasons.append(vix_reason)
    if not nifty_ok:
        halt_reasons.append(nifty_reason)

    # Generate momentum_agg picks
    histories = _build_universe_histories(min_bars=252, intraday_ltps=intraday_ltps)
    if not histories:
        logger.warning("momentum_picker: no histories loaded")
        _save_state(state)
        return _empty_result(state, "no histories", halt_reasons, vix_mult, vix_reason)

    variants = build_variants()
    momentum = variants.get("momentum_agg")
    if momentum is None:
        logger.error("momentum_agg variant not found")
        _save_state(state)
        return _empty_result(state, "variant unavailable", halt_reasons, vix_mult, vix_reason)

    today = pd.Timestamp(now_ist().date())
    # Generate a wider pool than max_picks so we can return both:
    #   picks         -> top max_picks (10) for OPENs (strict)
    #   picks_extended-> top HOLD_BUFFER (20) for HOLD checks (asymmetric guard)
    # Asymmetric: opens require rank<=10 (high conviction) but holds tolerate
    # rank<=20 (small intraday rank flickers don't trigger churn). Fixes the
    # whipsaw where a held name flipping #10 <-> #11 every 15 min was being
    # closed and reopened, bleeding friction.
    HOLD_BUFFER = 20
    momentum.max_picks = max(HOLD_BUFFER, max_picks * 2, 25)
    try:
        raw_picks = momentum.pick(histories, today, list(histories.keys()))
    except Exception as e:
        logger.error(f"momentum.pick failed: {e}")
        _save_state(state)
        return _empty_result(state, str(e), halt_reasons, vix_mult, vix_reason)

    # P13: junk filter — drop fundamentals red-flags BEFORE sector cap.
    # P15: earnings exclusion — drop names within T-2/T+1 of earnings.
    try:
        from strategy.quality_filter import passes_junk_filter
        from news.earnings_calendar import is_in_earnings_window
        clean_picks = []
        rejected = []
        for p in raw_picks:
            ok, reason = passes_junk_filter(p.symbol)
            if not ok:
                rejected.append((p.symbol, f"junk: {reason}"))
                continue
            in_earn, earn_reason = is_in_earnings_window(p.symbol)
            if in_earn:
                rejected.append((p.symbol, f"earnings: {earn_reason}"))
                continue
            clean_picks.append(p)
        if rejected:
            logger.info(f"Pre-sector filters dropped {len(rejected)} picks: " +
                        "; ".join(f"{s}({r})" for s, r in rejected[:5]))
        raw_picks = clean_picks
    except Exception as e:
        logger.warning(f"junk/earnings filter pass failed: {e}; continuing without filter")

    # Apply sector cap (using current portfolio sector exposure as starting state)
    capped = _apply_sector_cap(raw_picks, held_by_sector, current_equity or 1_000_000)

    # Trim to max_picks (for OPEN candidates — these are the ones we'd buy fresh)
    final_picks_objs = capped[:max_picks]
    # Wider pool for HOLD-buffer check — uses raw_picks (NO sector cap)
    # because sector cap is a "new open" filter; we already hold the position
    # and just want to know if it's still a momentum leader by raw rank.
    hold_universe_objs = raw_picks[:HOLD_BUFFER]

    # Convert to dict shape generate_analysis expects.
    # Targets are ATR-based (3x ATR for momentum) instead of a flat 10% — gives
    # high-volatility names more room and tightens up low-vol names so we don't
    # leave money on the table or force premature exits.
    TARGET_ATR_MULT_MOMENTUM = 3.0
    picks_dicts = []
    for rank, p in enumerate(final_picks_objs, 1):
        atr_val = float(getattr(p, "atr", 0.0) or 0.0)
        if atr_val > 0:
            target = float(p.entry_ref) + TARGET_ATR_MULT_MOMENTUM * atr_val
        else:
            target = float(p.entry_ref) * 1.10  # fallback when ATR unavailable
        upside_pct = (target - float(p.entry_ref)) / float(p.entry_ref) * 100 if p.entry_ref else 0
        picks_dicts.append({
            "rank": rank,
            "symbol": p.symbol,
            "name": p.symbol,            # full company name not stored on Pick; symbol is fine for now
            "sector": _industry_of(p.symbol),
            "cmp": float(p.entry_ref),
            "target": round(target, 2),
            "target_2": round(float(p.entry_ref) + 1.5 * TARGET_ATR_MULT_MOMENTUM * atr_val, 2) if atr_val > 0 else round(float(p.entry_ref) * 1.15, 2),
            "stop_loss": float(p.stop),
            "atr": round(atr_val, 2),
            "risk_pct": round((float(p.entry_ref) - float(p.stop)) / float(p.entry_ref) * 100, 2) if p.entry_ref else 0,
            "upside_pct": round(upside_pct, 2),
            "conviction": "HIGH" if rank <= 3 else ("MEDIUM" if rank <= 7 else "LOW"),
            "scores": {"quality": None, "momentum": round(p.score, 2), "technical": None, "overall": round(p.score, 2)},
            "momentum": {"rs_6m": p.score, "rs_3m": p.score / 2},
            "technicals": {"trend": "MOMENTUM", "rsi": 0, "volume_ratio": 0, "setup": "12-1m momentum top"},
            "levels": {"support": [p.stop], "resistance": [], "atr": 0},
            "reasoning": f"momentum_agg rank #{rank} (12-1m momentum {p.score:.2f}); risk overlay sector-capped",
            "variant": "momentum_agg",
        })

    if halt_blocking_new:
        # Keep picks list visible for transparency, but signal kill_switch so
        # generate_analysis -> paper.runner blocks new opens. Existing positions
        # remain held (not closed) because regime != BEAR and deploy_pct unchanged.
        logger.warning(f"momentum_picker HALT: {' | '.join(halt_reasons)}")

    # Save updated state
    _save_state(state)

    return {
        "regime": "MOMENTUM_BASE",                # not adaptive anymore
        "regime_reason": "Momentum-only base strategy (V3 retired due to negative alpha)",
        "regime_inputs": {
            "vix_gate": vix_reason,
            "nifty_today": nifty_reason,
            "dd_state": dd_reason,
            "regime_health": regime_reason,
            "peak_equity": state.get("peak_equity"),
        },
        "variant": "momentum_agg",
        "variant_reason": "Single-variant momentum_agg + risk overlay (Q4 backtest winner: +21.69% CAGR, Sharpe 1.35, MaxDD -16%)",
        "deploy_pct": int(100 * vix_mult * regime_mult) if not halt_blocking_new else 0,
        "kill_switch_active": halt_blocking_new,
        "kill_switch_reason": " | ".join(halt_reasons) if halt_reasons else "",
        "picks": picks_dicts,
        "picks_extended": [
            {"rank": i + 1, "symbol": p.symbol, "score": round(p.score, 2)}
            for i, p in enumerate(hold_universe_objs)
        ],
        "risk_overlay": {
            "max_positions": MAX_POSITIONS,
            "sector_cap_pct": SECTOR_CAP_PCT,
            "dd_halt_pct": DD_HALT_PCT,
            "dd_resume_pct": DD_RESUME_PCT,
            "tail_halt_pct": TAIL_DD_PCT,
            "vix_gate_high": VIX_GATE_HIGH,
            "vix_gate_extreme": VIX_GATE_EXTREME,
            "vix_size_multiplier": vix_mult,
            "halt_active": state.get("halt_active", False),
            "tail_halt": state.get("tail_halt", False),
            "halt_reason": state.get("halt_reason", ""),
            "current_equity": current_equity,
            "peak_equity": state.get("peak_equity"),
            "current_dd_pct": round((current_equity - state["peak_equity"]) / state["peak_equity"] * 100, 2) if state.get("peak_equity") else 0,
            "raw_pick_count": len(raw_picks),
            "after_sector_cap": len(capped),
            "final_pick_count": len(picks_dicts),
        },
    }


def _empty_result(state: dict, reason: str, halt_reasons: list, vix_mult: float, vix_reason: str) -> dict:
    return {
        "regime": "MOMENTUM_BASE", "regime_reason": reason,
        "regime_inputs": {"vix": vix_reason},
        "variant": "momentum_agg",
        "variant_reason": "could not generate picks",
        "deploy_pct": 0, "kill_switch_active": True,
        "kill_switch_reason": " | ".join(halt_reasons) if halt_reasons else reason,
        "picks": [],
        "risk_overlay": {"halt_active": state.get("halt_active"), "tail_halt": state.get("tail_halt")},
    }


def reset_tail_halt():
    """Manual reset of tail halt (after review). Call from a script when you've
    decided the system can resume after a -15%+ drawdown event."""
    state = _load_state()
    if state.get("tail_halt"):
        state["tail_halt"] = False
        state["halt_active"] = False
        state["halt_reason"] = ""
        _save_state(state)
        logger.info("Tail halt RESET manually")
        return True
    return False


if __name__ == "__main__":
    # Quick test
    result = run_momentum_picker()
    print(json.dumps({k: v for k, v in result.items() if k != "picks"}, indent=2, default=str))
    print(f"\nPicks ({len(result.get('picks', []))}):")
    for p in result.get("picks", []):
        print(f"  #{p['rank']:2d} {p['symbol']:14s} cmp={p['cmp']:.2f} stop={p['stop_loss']:.2f} "
              f"sector={p['sector'][:25]} momentum={p['scores']['momentum']:.2f}")
