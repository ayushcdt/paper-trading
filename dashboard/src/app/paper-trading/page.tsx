import { getBlob } from '@/lib/blob'
import { Briefcase, TrendingUp, TrendingDown, Activity, AlertTriangle, Target, Zap } from 'lucide-react'

export const revalidate = 120

interface PaperSnapshot {
  generated_at: string
  started_at: string
  starting_capital: number
  current_equity: number
  realized_pnl: number
  unrealized_pnl: number
  total_pnl_pct: number
  open_positions_count: number
  open_positions: {
    symbol: string; variant: string; regime_at_entry: string;
    entry_price: number; qty: number; slot_notional: number;
    stop_at_entry: number; entry_date: string;
    current_price: number; unrealized_pnl_inr: number; unrealized_pnl_pct: number;
  }[]
  recent_trades: {
    symbol: string; variant: string; regime: string; action: string;
    price: number; qty: number; pnl_inr: number | null; pnl_pct: number | null;
    reason: string; timestamp: string;
  }[]
  live_3m_return_by_variant: Record<string, number>
  equity_curve: { date: string; equity: number; realized_cum: number; unrealized: number }[]
  target_status?: {
    monthly:   { period: string; target_pct: number; actual_pct: number; on_track: boolean }
    quarterly: { period: string; target_pct: number; actual_pct: number; on_track: boolean }
    annual:    { period: string; target_pct: number; actual_pct: number; on_track: boolean }
    escalation_level: number
    months_under_target: number
  }
}

async function loadPaper(): Promise<PaperSnapshot | null> {
  return await getBlob<PaperSnapshot>('paper_portfolio')
}

function fmtInr(n: number): string {
  return '₹' + Math.round(n).toLocaleString('en-IN')
}

export default async function PaperTradingPage() {
  const snap = await loadPaper()

  if (!snap) {
    return (
      <div className="space-y-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Paper Trading</h1>
          <p className="text-gray-500 mt-1">Virtual portfolio tracking live V3 picks.</p>
        </div>
        <div className="card bg-yellow-50 border-yellow-200">
          <div className="flex items-start gap-3">
            <AlertTriangle className="w-5 h-5 text-yellow-600 mt-0.5 flex-shrink-0" />
            <div>
              <p className="font-medium text-yellow-900">Paper trading not initialized yet.</p>
              <p className="text-sm text-yellow-800 mt-1">
                Run: <code className="bg-yellow-100 px-2 py-0.5 rounded font-mono text-xs">cd c:\trading\backend && python -m paper.runner</code>
              </p>
            </div>
          </div>
        </div>
      </div>
    )
  }

  const daysRunning = Math.max(1, Math.round((Date.now() - new Date(snap.started_at).getTime()) / (1000 * 60 * 60 * 24)))
  const up = snap.total_pnl_pct >= 0

  return (
    <div className="space-y-8">
      <div>
        <div className="flex items-center gap-2">
          <Briefcase className="w-6 h-6 text-indigo-600" />
          <h1 className="text-2xl font-bold text-gray-900">Paper Trading</h1>
        </div>
        <p className="text-gray-500 mt-1">
          Virtual ₹{(snap.starting_capital / 100000).toFixed(0)}L capital · Running {daysRunning}d ·
          Started {new Date(snap.started_at).toLocaleDateString('en-IN')}
        </p>
      </div>

      {/* Target scorecard */}
      {snap.target_status && (
        <div className={`card ${
          snap.target_status.escalation_level === 0 ? 'bg-green-50 border-green-200' :
          snap.target_status.escalation_level === 1 ? 'bg-yellow-50 border-yellow-200' :
          'bg-red-50 border-red-200'
        }`}>
          <h3 className="card-header flex items-center gap-2">
            <Target className="w-5 h-5 text-indigo-600" />
            Performance targets
            {snap.target_status.escalation_level > 0 && (
              <span className="ml-auto inline-flex items-center gap-1 text-xs px-2 py-1 rounded bg-red-100 text-red-800">
                <Zap className="w-3 h-3" /> Escalation L{snap.target_status.escalation_level}
              </span>
            )}
          </h3>
          <div className="grid grid-cols-3 gap-4">
            {(['monthly', 'quarterly', 'annual'] as const).map(period => {
              const t = snap.target_status![period]
              return (
                <div key={period} className="p-3 bg-white rounded-lg border">
                  <div className="text-xs uppercase text-gray-500">{period}</div>
                  <div className="text-xs text-gray-500 mt-1">Target: +{t.target_pct}%</div>
                  <div className={`text-2xl font-bold mt-1 ${t.actual_pct >= t.target_pct ? 'text-green-600' : 'text-red-600'}`}>
                    {t.actual_pct >= 0 ? '+' : ''}{t.actual_pct.toFixed(2)}%
                  </div>
                  <div className={`text-xs mt-1 ${t.on_track ? 'text-green-700' : 'text-red-700'}`}>
                    {t.on_track ? '✓ on track' : `gap: ${(t.target_pct - t.actual_pct).toFixed(2)}%`}
                  </div>
                </div>
              )
            })}
          </div>
          {snap.target_status.months_under_target > 0 && (
            <p className="text-xs text-gray-600 mt-3">
              {snap.target_status.months_under_target} month(s) consecutively under target.
              {snap.target_status.escalation_level >= 1 && ' Mean-reversion criteria relaxed.'}
              {snap.target_status.escalation_level >= 2 && ' RANGE regime now uses momentum_cons.'}
              {snap.target_status.escalation_level >= 3 && ' All variants sized up 50%.'}
            </p>
          )}
        </div>
      )}

      {/* Hero metrics */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div className="card">
          <div className="text-sm text-gray-500 mb-1">Current equity</div>
          <div className="text-3xl font-bold text-gray-900">{fmtInr(snap.current_equity)}</div>
        </div>
        <div className="card">
          <div className="text-sm text-gray-500 mb-1">Total P&L</div>
          <div className={`text-3xl font-bold ${up ? 'text-green-600' : 'text-red-600'}`}>
            {up ? '+' : ''}{snap.total_pnl_pct.toFixed(2)}%
          </div>
          <div className="text-xs text-gray-400 mt-1">{fmtInr(snap.current_equity - snap.starting_capital)}</div>
        </div>
        <div className="card">
          <div className="text-sm text-gray-500 mb-1">Realized</div>
          <div className={`text-2xl font-bold ${snap.realized_pnl >= 0 ? 'text-green-600' : 'text-red-600'}`}>
            {fmtInr(snap.realized_pnl)}
          </div>
        </div>
        <div className="card">
          <div className="text-sm text-gray-500 mb-1">Unrealized</div>
          <div className={`text-2xl font-bold ${snap.unrealized_pnl >= 0 ? 'text-green-600' : 'text-red-600'}`}>
            {fmtInr(snap.unrealized_pnl)}
          </div>
          <div className="text-xs text-gray-400 mt-1">{snap.open_positions_count} open positions</div>
        </div>
      </div>

      {/* Open positions */}
      <div className="card">
        <h3 className="card-header flex items-center gap-2">
          <Activity className="w-5 h-5 text-blue-500" />
          Open positions ({snap.open_positions_count})
        </h3>
        {snap.open_positions.length === 0 ? (
          <p className="text-sm text-gray-500">
            No open positions. System is in cash — typically because regime is BEAR, kill switch is active, or no picks met criteria.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-xs uppercase text-gray-500 border-b border-gray-200">
                <tr>
                  <th className="text-left py-2">Symbol</th>
                  <th className="text-left">Variant</th>
                  <th className="text-right">Entry</th>
                  <th className="text-right">Current</th>
                  <th className="text-right">Qty</th>
                  <th className="text-right">P&L ₹</th>
                  <th className="text-right">P&L %</th>
                  <th className="text-right">Stop</th>
                  <th className="text-left pl-4">Entry date</th>
                </tr>
              </thead>
              <tbody>
                {snap.open_positions.map((p, i) => (
                  <tr key={i} className="border-b border-gray-50">
                    <td className="py-2 font-medium">{p.symbol}</td>
                    <td className="text-gray-600">{p.variant}</td>
                    <td className="text-right">₹{p.entry_price.toFixed(2)}</td>
                    <td className="text-right">₹{p.current_price.toFixed(2)}</td>
                    <td className="text-right">{p.qty}</td>
                    <td className={`text-right font-medium ${p.unrealized_pnl_inr >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                      {fmtInr(p.unrealized_pnl_inr)}
                    </td>
                    <td className={`text-right ${p.unrealized_pnl_pct >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                      {p.unrealized_pnl_pct >= 0 ? '+' : ''}{p.unrealized_pnl_pct.toFixed(2)}%
                    </td>
                    <td className="text-right text-gray-500">₹{p.stop_at_entry.toFixed(2)}</td>
                    <td className="pl-4 text-xs text-gray-500">{new Date(p.entry_date).toLocaleDateString('en-IN')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Variant live P&L */}
      {Object.keys(snap.live_3m_return_by_variant).length > 0 && (
        <div className="card">
          <h3 className="card-header">Live 3M P&L by variant (feeds guardrails)</h3>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            {Object.entries(snap.live_3m_return_by_variant).map(([v, pct]) => (
              <div key={v} className="p-3 bg-gray-50 rounded-lg">
                <div className="text-sm font-medium">{v}</div>
                <div className={`text-2xl font-bold ${pct >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                  {pct >= 0 ? '+' : ''}{pct.toFixed(2)}%
                </div>
              </div>
            ))}
          </div>
          <p className="mt-3 text-xs text-gray-500">
            These numbers feed <code className="bg-gray-100 px-1 rounded">check_variant_decay()</code>. If any
            variant's live P&L falls {'>'}2σ below backtest expectation for 2 consecutive nightly checks, it auto-suspends.
          </p>
        </div>
      )}

      {/* Trade log */}
      <div className="card">
        <h3 className="card-header">Recent trades (last 50)</h3>
        {snap.recent_trades.length === 0 ? (
          <p className="text-sm text-gray-500">No trades yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-xs uppercase text-gray-500 border-b border-gray-200">
                <tr>
                  <th className="text-left py-2">Time</th>
                  <th className="text-left">Action</th>
                  <th className="text-left">Symbol</th>
                  <th className="text-left">Variant</th>
                  <th className="text-right">Price</th>
                  <th className="text-right">Qty</th>
                  <th className="text-right">P&L ₹</th>
                  <th className="text-right">P&L %</th>
                  <th className="text-left pl-4">Reason</th>
                </tr>
              </thead>
              <tbody>
                {snap.recent_trades.map((t, i) => (
                  <tr key={i} className="border-b border-gray-50">
                    <td className="py-2 text-xs text-gray-500">{new Date(t.timestamp).toLocaleString('en-IN')}</td>
                    <td>
                      <span className={`text-xs font-semibold px-2 py-0.5 rounded ${
                        t.action === 'OPEN' ? 'bg-blue-100 text-blue-700' : 'bg-purple-100 text-purple-700'
                      }`}>{t.action}</span>
                    </td>
                    <td className="font-medium">{t.symbol}</td>
                    <td className="text-gray-600">{t.variant}</td>
                    <td className="text-right">₹{t.price.toFixed(2)}</td>
                    <td className="text-right">{t.qty}</td>
                    <td className={`text-right font-medium ${
                      (t.pnl_inr ?? 0) > 0 ? 'text-green-600' : (t.pnl_inr ?? 0) < 0 ? 'text-red-600' : 'text-gray-400'
                    }`}>
                      {t.pnl_inr == null || t.action === 'OPEN' ? '—' : fmtInr(t.pnl_inr)}
                    </td>
                    <td className={`text-right ${
                      (t.pnl_pct ?? 0) > 0 ? 'text-green-600' : (t.pnl_pct ?? 0) < 0 ? 'text-red-600' : 'text-gray-400'
                    }`}>
                      {t.pnl_pct == null || t.action === 'OPEN' ? '—' : `${t.pnl_pct >= 0 ? '+' : ''}${t.pnl_pct.toFixed(2)}%`}
                    </td>
                    <td className="pl-4 text-xs text-gray-500">{t.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
