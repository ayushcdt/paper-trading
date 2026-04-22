/**
 * Shared types for the paper-portfolio blob.
 *
 * Source of truth: `backend/paper/portfolio.py::PaperPortfolio.snapshot()`.
 * Keep these in sync with the Python serializer; if you add a field there,
 * mirror it here so the SSR + live consumers can read it without `any`.
 */

export interface PaperPosition {
  symbol: string
  variant: string
  regime_at_entry?: string
  entry_price: number
  qty: number
  slot_notional?: number
  stop_at_entry: number
  entry_date: string
  current_price: number
  unrealized_pnl_inr: number
  unrealized_pnl_pct: number
}

export interface PaperTrade {
  symbol: string
  variant: string
  regime: string
  action: string
  price: number
  qty: number
  pnl_inr: number | null
  pnl_pct: number | null
  reason: string
  timestamp: string
}

export interface PaperEquityPoint {
  date: string
  equity: number
  realized_cum: number
  unrealized: number
}

export interface PaperTargetStatus {
  monthly:   { period: string; target_pct: number; actual_pct: number; on_track: boolean }
  quarterly: { period: string; target_pct: number; actual_pct: number; on_track: boolean }
  annual:    { period: string; target_pct: number; actual_pct: number; on_track: boolean }
  escalation_level: number
  months_under_target: number
}

export interface PaperSnapshot {
  generated_at: string
  started_at: string
  starting_capital: number
  current_equity: number
  realized_pnl: number
  unrealized_pnl: number
  total_pnl_pct: number
  open_positions_count: number
  open_positions: PaperPosition[]
  recent_trades: PaperTrade[]
  live_3m_return_by_variant: Record<string, number>
  equity_curve: PaperEquityPoint[]
  target_status?: PaperTargetStatus
}
