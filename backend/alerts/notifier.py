"""
Alert notifier -- compares previous vs current state and dispatches alerts.

Events watched:
  - Regime change (e.g., RANGE -> BULL_LOW_VOL)
  - Variant change (e.g., mean_reversion -> momentum_agg)
  - Kill switch toggle
  - Variant suspension (via guardrail state)
  - Escalation level change (target underperformance)
  - Position opened / closed (from paper portfolio trade log)
  - Major news on held position (sentiment <= -5 OR mentions >= 15 in 24h)
  - Stop / target approach (within 2% of either level)

State persisted in data/alert_state.json so we don't repeat alerts across runs.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

from logzero import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from alerts.channels import dispatch


STATE_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "alert_state.json"


def _load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def check_and_alert(
    stocks_data: dict | None = None,
    paper_snap: dict | None = None,
    news_snap=None,
    guardrail_state: dict | None = None,
    target_state: dict | None = None,
) -> int:
    """
    Main entry. Pass whichever state snapshots you have; the notifier
    gracefully handles any None. Returns number of alerts dispatched.
    """
    prev = _load_state()
    now_iso = datetime.now().isoformat()
    alerts_fired = 0

    # ---- Regime / variant ----
    if stocks_data:
        regime = stocks_data.get("regime")
        variant = stocks_data.get("variant")
        deploy_pct = stocks_data.get("deploy_pct", 0)
        if regime and prev.get("regime") and regime != prev["regime"]:
            dispatch("warning",
                     f"Regime change: {prev['regime']} -> {regime}",
                     f"Variant: {variant} | Deploy: {deploy_pct}%")
            alerts_fired += 1
        if variant and prev.get("variant") and variant != prev["variant"]:
            dispatch("info",
                     f"Variant switch: {prev['variant']} -> {variant}",
                     f"Regime: {regime}")
            alerts_fired += 1
        prev["regime"] = regime
        prev["variant"] = variant

        # Kill switch toggle
        kill = bool(stocks_data.get("kill_switch_active", False))
        if kill and not prev.get("kill_switch", False):
            dispatch("critical",
                     "KILL SWITCH ACTIVATED",
                     stocks_data.get("kill_switch_reason", "reason unknown"))
            alerts_fired += 1
        elif not kill and prev.get("kill_switch", False):
            dispatch("info", "Kill switch released", "System re-armed.")
            alerts_fired += 1
        prev["kill_switch"] = kill

    # ---- Guardrail variant suspensions ----
    if guardrail_state:
        prev_suspended = set(prev.get("suspended_variants", []))
        now_suspended = {
            v for v, h in (guardrail_state.get("variants") or {}).items()
            if h.get("suspended")
        }
        newly_suspended = now_suspended - prev_suspended
        reactivated    = prev_suspended - now_suspended
        for v in newly_suspended:
            dispatch("warning", f"Variant suspended: {v}", "Decay > 2σ for 2 consecutive checks.")
            alerts_fired += 1
        for v in reactivated:
            dispatch("info", f"Variant reactivated: {v}", "Decay no longer significant.")
            alerts_fired += 1
        prev["suspended_variants"] = sorted(now_suspended)

    # ---- Target escalation level ----
    if target_state:
        level = int(target_state.get("escalation_level", 0))
        prev_level = int(prev.get("escalation_level", 0))
        if level > prev_level:
            dispatch("warning",
                     f"Escalation L{prev_level} -> L{level}",
                     f"Months under target: {target_state.get('months_under_target', 0)}. Strategy will become more aggressive.")
            alerts_fired += 1
        elif level < prev_level:
            dispatch("info",
                     f"Escalation L{prev_level} -> L{level}",
                     "Performance back on track; reverting to default aggressiveness.")
            alerts_fired += 1
        prev["escalation_level"] = level

    # ---- Paper position changes ----
    if paper_snap:
        prev_syms = set(prev.get("open_symbols", []))
        current = {
            p["symbol"]: p for p in paper_snap.get("open_positions", [])
        }
        now_syms = set(current.keys())
        opened = now_syms - prev_syms
        closed = prev_syms - now_syms

        # Opens
        for sym in opened:
            p = current[sym]
            dispatch("info",
                     f"Position opened: {sym}",
                     f"Entry ₹{p['entry_price']:.2f} | Qty {p['qty']} | Stop ₹{p['stop_at_entry']:.2f}")
            alerts_fired += 1

        # Closes -- pull from trade log (most recent matching)
        if closed:
            recent = paper_snap.get("recent_trades", [])
            for sym in closed:
                trade = next((t for t in recent if t["symbol"] == sym and t["action"] == "CLOSE"), None)
                if trade:
                    sev = "info" if (trade.get("pnl_pct") or 0) >= 0 else "warning"
                    dispatch(sev,
                             f"Position closed: {sym} ({trade.get('pnl_pct', 0):+.2f}%)",
                             f"Exit ₹{trade.get('price', 0):.2f} | P&L ₹{trade.get('pnl_inr', 0):.0f} | {trade.get('reason', '')}")
                    alerts_fired += 1

        # Stop / target approach
        for sym, p in current.items():
            current_price = p.get("current_price", p["entry_price"])
            stop = p.get("stop_at_entry", 0)
            if stop and current_price <= stop * 1.02:  # within 2% of stop
                key = f"near_stop_{sym}"
                if not prev.get(key):
                    dispatch("warning",
                             f"Near stop: {sym}",
                             f"Current ₹{current_price:.2f}, stop ₹{stop:.2f} (within 2%)")
                    alerts_fired += 1
                    prev[key] = now_iso
            else:
                prev.pop(f"near_stop_{sym}", None)

        prev["open_symbols"] = sorted(now_syms)

    # ---- Bad news on held positions ----
    if news_snap and paper_snap:
        held = {p["symbol"] for p in paper_snap.get("open_positions", [])}
        mentions = getattr(news_snap, "symbol_mentions", None) or {}
        for sym in held:
            m = mentions.get(sym, {})
            sent = int(m.get("sentiment_24h", 0))
            c24 = int(m.get("c24", 0))
            if sent <= -5 or (c24 >= 15 and sent <= -2):
                key = f"bad_news_{sym}"
                if not prev.get(key):
                    dispatch("warning",
                             f"Bad news on held: {sym}",
                             f"24h sentiment {sent:+d} across {c24} articles. Review position.")
                    alerts_fired += 1
                    prev[key] = now_iso
            else:
                # Clear stale bad-news flags
                if f"bad_news_{sym}" in prev:
                    prev.pop(f"bad_news_{sym}")

    # ---- Results filings on held positions (Phase 1: substring match;
    # Phase 2 will use proper SYMBOL_TO_NAMES map). Conservative match: only
    # alert when a single token of the symbol appears verbatim in the title.
    if news_snap and paper_snap:
        held = {p["symbol"] for p in paper_snap.get("open_positions", [])}
        today_filings = getattr(news_snap, "today_results", None) or []
        pending = getattr(news_snap, "pending_results", None) or []
        for sym in held:
            sym_token = sym.split("-")[0].split("&")[0].upper()
            if len(sym_token) < 4:  # avoid spurious 3-letter matches like "ITC" matching "WITCHING"
                continue
            for f in today_filings:
                title_upper = (f.get("title") or "").upper()
                company_upper = (f.get("company") or "").upper()
                if sym_token in title_upper or sym_token in company_upper:
                    key = f"results_filed_{sym}_{f.get('url','')[:60]}"
                    if not prev.get(key):
                        dispatch("info",
                                 f"Results filed on held: {sym}",
                                 f"{f.get('company','?')} -- {f.get('source','?')}\n{f.get('title','')[:200]}\n{f.get('url','')}")
                        alerts_fired += 1
                        prev[key] = now_iso
            for f in pending:
                title_upper = (f.get("title") or "").upper()
                company_upper = (f.get("company") or "").upper()
                if sym_token in title_upper or sym_token in company_upper:
                    key = f"results_intimation_{sym}_{f.get('url','')[:60]}"
                    if not prev.get(key):
                        dispatch("info",
                                 f"Upcoming results: {sym}",
                                 f"{f.get('company','?')} -- {f.get('source','?')}\n{f.get('title','')[:200]}")
                        alerts_fired += 1
                        prev[key] = now_iso

    # ---- Legal-category articles on held positions (SEBI orders, court rulings).
    # Phase 2 uses entity-precise matching: check if any held symbol's name
    # variants appear in the article's extracted org list.
    if news_snap and paper_snap:
        try:
            from news.symbols import names_for as _names_for
        except Exception:
            _names_for = lambda s: [s]
        held = {p["symbol"] for p in paper_snap.get("open_positions", [])}
        legal_today = getattr(news_snap, "legal_today", None) or []
        for sym in held:
            variants_lower = {n.lower() for n in _names_for(sym)}
            if not variants_lower:
                continue
            for art in legal_today:
                art_orgs_lower = {o.lower() for o in (art.get("orgs") or [])}
                if not (art_orgs_lower & variants_lower):
                    continue
                key = f"legal_{sym}_{art.get('url','')[:60]}"
                if not prev.get(key):
                    dispatch("warning",
                             f"Legal/regulatory news: {sym}",
                             f"{art.get('source','?')}\n{art.get('title','')[:200]}\n{art.get('url','')}")
                    alerts_fired += 1
                    prev[key] = now_iso

    prev["updated_at"] = now_iso
    _save_state(prev)
    return alerts_fired


if __name__ == "__main__":
    # Standalone test
    import json as _json
    from pathlib import Path as _P
    data_dir = _P(__file__).resolve().parent.parent.parent / "data"

    def _try(path):
        p = data_dir / path
        if p.exists():
            return _json.loads(p.read_text(encoding="utf-8"))
        return None

    stocks = _try("stocks.json")
    paper = _try("paper_portfolio.json")
    target = _try("target_state.json")
    n = check_and_alert(stocks_data=stocks, paper_snap=paper, target_state=target)
    print(f"Dispatched {n} alerts")
