"""
Weekly evaluation of hybrid_overlay shadow log.

Reads data/research/shadow_log.jsonl, joins each decision with the symbol's
forward 5-day return, then compares:
  - V3-only Information Ratio (using v3_score as the signal)
  - Hybrid IR (using hybrid_score)

Output: data/research/shadow_evaluation.json — read by the dashboard.

Run weekly via cron OR after every postclose run.

INTERPRETATION GATES (per the build plan):
  - Hybrid IR > V3 IR + 0.2  -> overlay adds alpha; consider going LIVE
  - Hybrid IR < V3 IR - 0.2  -> overlay HURTS; turn it off entirely
  - Within +/- 0.2          -> noise, keep collecting data
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
import statistics

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from logzero import logger
import pandas as pd

from data_store import get_bars


SHADOW_LOG = Path(__file__).resolve().parent.parent.parent / "data" / "research" / "shadow_log.jsonl"
OUTPUT = Path(__file__).resolve().parent.parent.parent / "data" / "research" / "shadow_evaluation.json"


def _load_decisions(min_age_days: int = 5) -> list[dict]:
    """Load shadow log entries that are at least min_age_days old (so forward returns exist)."""
    if not SHADOW_LOG.exists():
        return []
    cutoff = datetime.now() - timedelta(days=min_age_days)
    out = []
    with SHADOW_LOG.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                ts = d.get("timestamp", "")
                # ISO with timezone -> compare in UTC
                pub = datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(tzinfo=None)
                if pub <= cutoff:
                    out.append(d)
            except Exception:
                continue
    return out


def _forward_5d_return(symbol: str, decision_iso: str) -> float | None:
    """Symbol return from decision date to T+5d (close-to-close)."""
    df = get_bars(symbol, n_days=400)
    if len(df) < 10:
        return None
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.sort_values("Date").reset_index(drop=True)
    target = pd.Timestamp(decision_iso[:10])
    idx = df.index[df["Date"] >= target]
    if len(idx) == 0:
        return None
    t0 = idx[0]
    if t0 + 5 >= len(df):
        return None
    p0 = df.iloc[t0]["Close"]
    p5 = df.iloc[t0 + 5]["Close"]
    return (p5 - p0) / p0 * 100.0


def _ir(scores: list[float], returns: list[float]) -> float | None:
    """Spearman-style IR: bucket scores into top/bottom thirds, compare mean returns."""
    if len(scores) < 10:
        return None
    n = len(scores)
    paired = sorted(zip(scores, returns))
    bot = paired[: n // 3]
    top = paired[2 * n // 3 :]
    if not top or not bot:
        return None
    top_mean = statistics.mean([r for _, r in top])
    bot_mean = statistics.mean([r for _, r in bot])
    spread = top_mean - bot_mean
    spread_std = statistics.stdev([r for _, r in top + bot]) if n > 1 else 1.0
    return spread / spread_std if spread_std else None


def main() -> int:
    decisions = _load_decisions(min_age_days=5)
    logger.info(f"Loaded {len(decisions)} decisions older than 5 days from shadow log")

    if not decisions:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(json.dumps({
            "status": "insufficient_data",
            "decisions_evaluated": 0,
            "min_age_days": 5,
        }, indent=2), encoding="utf-8")
        return 0

    enriched = []
    for d in decisions:
        r5 = _forward_5d_return(d["symbol"], d["timestamp"])
        if r5 is None:
            continue
        enriched.append({**d, "fwd_5d_return": r5})

    logger.info(f"Decisions with forward returns: {len(enriched)}")
    if len(enriched) < 10:
        OUTPUT.write_text(json.dumps({
            "status": "insufficient_data",
            "decisions_evaluated": len(enriched),
            "note": "Need >=10 evaluated decisions for IR estimate",
        }, indent=2), encoding="utf-8")
        return 0

    v3_ir = _ir([e["v3_score"] for e in enriched], [e["fwd_5d_return"] for e in enriched])
    hybrid_ir = _ir([e["hybrid_score"] for e in enriched], [e["fwd_5d_return"] for e in enriched])

    # Per-day breakdown
    by_date = defaultdict(list)
    for e in enriched:
        by_date[e["timestamp"][:10]].append(e)
    daily_summary = []
    for date in sorted(by_date.keys()):
        items = by_date[date]
        avg_ret = statistics.mean(e["fwd_5d_return"] for e in items)
        n_picks = len(items)
        any_adj = sum(1 for e in items if abs(e.get("adjustment_pct", 0)) > 0.5)
        daily_summary.append({
            "date": date,
            "n_picks": n_picks,
            "avg_fwd_5d_return": round(avg_ret, 3),
            "adjusted_picks": any_adj,
        })

    verdict = "INSUFFICIENT" if v3_ir is None or hybrid_ir is None else (
        "HYBRID HELPS" if hybrid_ir > v3_ir + 0.2 else
        "HYBRID HURTS" if hybrid_ir < v3_ir - 0.2 else
        "INCONCLUSIVE"
    )

    output = {
        "status": "ok",
        "decisions_evaluated": len(enriched),
        "trading_days_covered": len(by_date),
        "v3_only_IR": round(v3_ir, 3) if v3_ir is not None else None,
        "hybrid_IR": round(hybrid_ir, 3) if hybrid_ir is not None else None,
        "ir_delta": round((hybrid_ir or 0) - (v3_ir or 0), 3) if v3_ir and hybrid_ir else None,
        "verdict": verdict,
        "daily_summary": daily_summary[-30:],
        "evaluated_at": datetime.now().isoformat(),
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(output, indent=2), encoding="utf-8")
    logger.info(f"Verdict: {verdict} (V3 IR={output['v3_only_IR']}, hybrid IR={output['hybrid_IR']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
