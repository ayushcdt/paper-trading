import Link from 'next/link'
import { getBlob } from '@/lib/blob'
import { Briefcase, AlertTriangle, Target, Zap } from 'lucide-react'
import { LivePaperSnapshot } from '@/components/LivePaperSnapshot'
import { RealFeesCard } from '@/components/RealFeesCard'
import type { PaperSnapshot } from '@/lib/paper'

// SSR fallback only — LivePaperSnapshot client overrides with 10s polling
// during market hours via LiveDataProvider (only on P1).
export const revalidate = 60

type PortfolioKey = 'p1' | 'p2' | 'p3'

const PORTFOLIOS: Record<PortfolioKey, {
  blobKey: string
  label: string
  tagline: string
  showLivePaper: boolean       // P1 has live F&O ticks worth polling; P2/P3 update once per cron
  showVariantHealth: boolean   // V3-style variant decay numbers only meaningful for P1
}> = {
  p1: {
    blobKey: 'paper_portfolio',
    label: 'P1 F&O',
    tagline: 'F&O autonomous test — claude_autotrade conviction model.',
    showLivePaper: true,
    showVariantHealth: true,
  },
  p2: {
    blobKey: 'paper_portfolio_p2',
    label: 'P2 Equity',
    tagline: 'Equity, momentum_agg picks executed as-is (raw signal reference).',
    showLivePaper: false,
    showVariantHealth: false,
  },
  p3: {
    blobKey: 'paper_portfolio_p3',
    label: 'P3 Hardened',
    tagline: 'Equity, hardened overlay: -8% stop cap, max 2/sector, LTCG-aware, monthly rebalance.',
    showLivePaper: false,
    showVariantHealth: false,
  },
}

function isPortfolioKey(s: unknown): s is PortfolioKey {
  return s === 'p1' || s === 'p2' || s === 'p3'
}

function fmtInr(n: number): string {
  return '₹' + Math.round(n).toLocaleString('en-IN')
}

async function loadPaper(blobKey: string): Promise<PaperSnapshot | null> {
  return await getBlob<PaperSnapshot>(blobKey)
}

export default async function PaperTradingPage({
  searchParams,
}: {
  searchParams: Promise<{ p?: string }>
}) {
  const params = await searchParams
  const portfolioKey: PortfolioKey = isPortfolioKey(params.p) ? params.p : 'p1'
  const cfg = PORTFOLIOS[portfolioKey]
  const snap = await loadPaper(cfg.blobKey)

  const tabs = (
    <div className="flex gap-1 border-b border-gray-200 -mb-px">
      {(['p1', 'p2', 'p3'] as const).map(k => {
        const active = portfolioKey === k
        return (
          <Link
            key={k}
            href={`/paper-trading?p=${k}`}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
              active
                ? 'border-indigo-600 text-indigo-600'
                : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
            }`}
          >
            {PORTFOLIOS[k].label}
          </Link>
        )
      })}
    </div>
  )

  if (!snap) {
    return (
      <div className="space-y-6">
        <div>
          <div className="flex items-center gap-2">
            <Briefcase className="w-6 h-6 text-indigo-600" />
            <h1 className="text-2xl font-bold text-gray-900">Paper Trading</h1>
          </div>
          <p className="text-gray-500 mt-1">{cfg.tagline}</p>
        </div>
        {tabs}
        <div className="card bg-yellow-50 border-yellow-200">
          <div className="flex items-start gap-3">
            <AlertTriangle className="w-5 h-5 text-yellow-600 mt-0.5 flex-shrink-0" />
            <div>
              <p className="font-medium text-yellow-900">
                {cfg.label}: no snapshot yet on Vercel.
              </p>
              <p className="text-sm text-yellow-800 mt-1">
                {portfolioKey === 'p1'
                  ? 'Run: cd backend && python -m paper.runner'
                  : 'Snapshot appears after the first daily executor run pushes via intraday_refresh (within 15 min).'}
              </p>
            </div>
          </div>
        </div>
      </div>
    )
  }

  const daysRunning = Math.max(
    1,
    Math.round((Date.now() - new Date(snap.started_at).getTime()) / (1000 * 60 * 60 * 24)),
  )

  return (
    <div className="space-y-8">
      <div>
        <div className="flex items-center gap-2">
          <Briefcase className="w-6 h-6 text-indigo-600" />
          <h1 className="text-2xl font-bold text-gray-900">Paper Trading</h1>
        </div>
        <p className="text-gray-500 mt-1">
          {cfg.tagline} · Virtual ₹{(snap.starting_capital / 100000).toFixed(0)}L capital ·{' '}
          Running {daysRunning}d · Started{' '}
          {new Date(snap.started_at).toLocaleDateString('en-IN')}
        </p>
      </div>

      {tabs}

      {/* Target scorecard */}
      {snap.target_status && (
        <div
          className={`card ${
            snap.target_status.escalation_level === 0
              ? 'bg-green-50 border-green-200'
              : snap.target_status.escalation_level === 1
              ? 'bg-yellow-50 border-yellow-200'
              : 'bg-red-50 border-red-200'
          }`}
        >
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
                  <div
                    className={`text-2xl font-bold mt-1 ${
                      t.actual_pct >= t.target_pct ? 'text-green-600' : 'text-red-600'
                    }`}
                  >
                    {t.actual_pct >= 0 ? '+' : ''}
                    {t.actual_pct.toFixed(2)}%
                  </div>
                  <div className={`text-xs mt-1 ${t.on_track ? 'text-green-700' : 'text-red-700'}`}>
                    {t.on_track ? '✓ on track' : `gap: ${(t.target_pct - t.actual_pct).toFixed(2)}%`}
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* Hero metrics + open positions */}
      {cfg.showLivePaper ? (
        <LivePaperSnapshot fallback={snap} />
      ) : (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <div className="card">
            <div className="text-xs uppercase text-gray-500">Equity</div>
            <div className="text-2xl font-bold mt-1">{fmtInr(snap.current_equity)}</div>
          </div>
          <div className="card">
            <div className="text-xs uppercase text-gray-500">Total P&L</div>
            <div
              className={`text-2xl font-bold mt-1 ${
                snap.total_pnl_pct >= 0 ? 'text-green-600' : 'text-red-600'
              }`}
            >
              {snap.total_pnl_pct >= 0 ? '+' : ''}
              {snap.total_pnl_pct.toFixed(2)}%
            </div>
          </div>
          <div className="card">
            <div className="text-xs uppercase text-gray-500">Realized</div>
            <div
              className={`text-2xl font-bold mt-1 ${
                snap.realized_pnl >= 0 ? 'text-green-600' : 'text-red-600'
              }`}
            >
              {snap.realized_pnl >= 0 ? '+' : ''}
              {fmtInr(snap.realized_pnl)}
            </div>
          </div>
          <div className="card">
            <div className="text-xs uppercase text-gray-500">Open positions</div>
            <div className="text-2xl font-bold mt-1">{snap.open_positions_count}</div>
          </div>
        </div>
      )}

      {/* Variant decay numbers — P1 only */}
      {cfg.showVariantHealth &&
        Object.keys(snap.live_3m_return_by_variant).length > 0 && (
          <div className="card">
            <h3 className="card-header">Live 3M P&L by variant (feeds guardrails)</h3>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              {Object.entries(snap.live_3m_return_by_variant).map(([v, pct]) => (
                <div key={v} className="p-3 bg-gray-50 rounded-lg">
                  <div className="text-sm font-medium">{v}</div>
                  <div
                    className={`text-2xl font-bold ${
                      pct >= 0 ? 'text-green-600' : 'text-red-600'
                    }`}
                  >
                    {pct >= 0 ? '+' : ''}
                    {pct.toFixed(2)}%
                  </div>
                </div>
              ))}
            </div>
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
                    <td className="py-2 text-xs text-gray-500">
                      {new Date(t.timestamp).toLocaleString('en-IN')}
                    </td>
                    <td>
                      <span
                        className={`text-xs font-semibold px-2 py-0.5 rounded ${
                          t.action === 'OPEN'
                            ? 'bg-blue-100 text-blue-700'
                            : 'bg-purple-100 text-purple-700'
                        }`}
                      >
                        {t.action}
                      </span>
                    </td>
                    <td className="font-medium">{t.symbol}</td>
                    <td className="text-gray-600">{t.variant}</td>
                    <td className="text-right">₹{t.price.toFixed(2)}</td>
                    <td className="text-right">{t.qty}</td>
                    <td
                      className={`text-right font-medium ${
                        (t.pnl_inr ?? 0) > 0
                          ? 'text-green-600'
                          : (t.pnl_inr ?? 0) < 0
                          ? 'text-red-600'
                          : 'text-gray-400'
                      }`}
                    >
                      {t.pnl_inr == null || t.action === 'OPEN' ? '—' : fmtInr(t.pnl_inr)}
                    </td>
                    <td
                      className={`text-right ${
                        (t.pnl_pct ?? 0) > 0
                          ? 'text-green-600'
                          : (t.pnl_pct ?? 0) < 0
                          ? 'text-red-600'
                          : 'text-gray-400'
                      }`}
                    >
                      {t.pnl_pct == null || t.action === 'OPEN'
                        ? '—'
                        : `${t.pnl_pct >= 0 ? '+' : ''}${t.pnl_pct.toFixed(2)}%`}
                    </td>
                    <td className="pl-4 text-xs text-gray-500">{t.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Real-money fees breakdown — only meaningful for P1 (F&O) right now */}
      {portfolioKey === 'p1' && <RealFeesCard />}
    </div>
  )
}
