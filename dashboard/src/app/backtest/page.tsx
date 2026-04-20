import { getBlob } from '@/lib/blob'
import { FlaskConical, AlertTriangle, TrendingUp, TrendingDown, ArrowRight } from 'lucide-react'

export const revalidate = 600

interface BacktestResult {
  generated_at: string
  version?: string
  config: Record<string, unknown>
  metrics: {
    total_trades: number
    winners: number
    losers: number
    win_rate_pct: number
    avg_winner_pct: number
    avg_loser_pct: number
    expectancy_pct: number
    profit_factor: number | null
    total_return_pct: number
    cagr_pct?: number
    max_drawdown_pct: number
    nifty_buyhold_total_pct?: number
    nifty_buyhold_cagr_pct?: number
    alpha_vs_nifty_cagr_pct?: number
    years?: number
  }
  trades: {
    symbol: string
    entry_date: string
    exit_date: string
    entry: number
    exit: number
    net_return_pct: number
    exit_reason?: string
  }[]
  monthly_log?: { date: string; regime_deploy_pct: number; regime_reason: string; equity: number }[]
  caveats: string[]
}

async function load(key: string): Promise<BacktestResult | null> {
  return await getBlob<BacktestResult>(key)
}

function MetricCell({ label, v1, v2, format = (n: number) => n.toFixed(2) + '%', betterIs = 'higher' }: {
  label: string
  v1: number | null | undefined
  v2: number | null | undefined
  format?: (n: number) => string
  betterIs?: 'higher' | 'lower'
}) {
  const v1n = v1 ?? null
  const v2n = v2 ?? null
  const better = v1n != null && v2n != null
    ? (betterIs === 'higher' ? v2n > v1n : v2n < v1n)
    : null
  return (
    <tr className="border-b border-gray-100">
      <td className="py-2 text-sm text-gray-600">{label}</td>
      <td className="py-2 text-sm text-right text-gray-500">
        {v1n == null ? '—' : format(v1n)}
      </td>
      <td className={`py-2 text-sm text-right font-semibold ${
        better === true ? 'text-green-600' : better === false ? 'text-red-600' : 'text-gray-700'
      }`}>
        {v2n == null ? '—' : format(v2n)}
      </td>
    </tr>
  )
}

export default async function BacktestPage() {
  const [v1, v2] = await Promise.all([
    load('backtest_v1'),
    load('backtest_v2'),
  ])

  if (!v1 && !v2) {
    return (
      <div className="space-y-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Backtest Results</h1>
          <p className="text-gray-500 mt-1">Validate the strategy on historical data before risking capital.</p>
        </div>
        <div className="card bg-yellow-50 border-yellow-200">
          <div className="flex items-start gap-3">
            <AlertTriangle className="w-5 h-5 text-yellow-600 mt-0.5 flex-shrink-0" />
            <div>
              <p className="font-medium text-yellow-900">No backtest results yet.</p>
              <p className="text-sm text-yellow-800 mt-1">
                Run on your local machine:
                <code className="block mt-1 bg-yellow-100 px-2 py-1 rounded font-mono text-xs">
                  cd c:\trading\backend && python scripts/backtest_v2.py
                </code>
              </p>
            </div>
          </div>
        </div>
      </div>
    )
  }

  const verdict = v2
    ? v2.metrics.profit_factor != null && v2.metrics.profit_factor >= 1.4 && (v2.metrics.alpha_vs_nifty_cagr_pct ?? 0) >= 5
      ? { color: 'green', label: 'PASSES validation criteria' }
      : v2.metrics.profit_factor != null && v2.metrics.profit_factor >= 1.0
        ? { color: 'yellow', label: 'PROFITABLE but below alpha target (need PF≥1.4 AND alpha≥+5%)' }
        : { color: 'red', label: 'FAILS — do not deploy real capital' }
    : null

  return (
    <div className="space-y-8">
      <div>
        <div className="flex items-center gap-2">
          <FlaskConical className="w-6 h-6 text-purple-600" />
          <h1 className="text-2xl font-bold text-gray-900">Backtest Results — V1 vs V2</h1>
        </div>
        <p className="text-gray-500 mt-1">
          V1 = original (lost 32%). V2 = Artha-redesigned (vol-adj 12-1 momentum + regime gates + trailing exits).
        </p>
      </div>

      {verdict && (
        <div className={`card ${
          verdict.color === 'green' ? 'bg-green-50 border-green-200' :
          verdict.color === 'yellow' ? 'bg-yellow-50 border-yellow-200' :
          'bg-red-50 border-red-200'
        }`}>
          <div className="flex items-center gap-3">
            <ArrowRight className={`w-5 h-5 ${
              verdict.color === 'green' ? 'text-green-700' :
              verdict.color === 'yellow' ? 'text-yellow-700' :
              'text-red-700'
            }`} />
            <p className={`font-semibold ${
              verdict.color === 'green' ? 'text-green-900' :
              verdict.color === 'yellow' ? 'text-yellow-900' :
              'text-red-900'
            }`}>V2 verdict: {verdict.label}</p>
          </div>
        </div>
      )}

      <div className="card">
        <h3 className="card-header">Metrics comparison</h3>
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead className="text-xs uppercase text-gray-500 border-b border-gray-200">
              <tr>
                <th className="text-left py-2">Metric</th>
                <th className="text-right">V1 (original)</th>
                <th className="text-right">V2 (Artha redesign)</th>
              </tr>
            </thead>
            <tbody>
              <MetricCell label="Total return" v1={v1?.metrics.total_return_pct} v2={v2?.metrics.total_return_pct} betterIs="higher" />
              <MetricCell label="CAGR" v1={v1?.metrics.cagr_pct} v2={v2?.metrics.cagr_pct} betterIs="higher" />
              <MetricCell label="Nifty buy-hold CAGR (same period)" v1={v1?.metrics.nifty_buyhold_cagr_pct} v2={v2?.metrics.nifty_buyhold_cagr_pct} betterIs="higher" />
              <MetricCell label="Alpha vs Nifty (CAGR)" v1={v1?.metrics.alpha_vs_nifty_cagr_pct} v2={v2?.metrics.alpha_vs_nifty_cagr_pct} betterIs="higher" />
              <MetricCell label="Win rate" v1={v1?.metrics.win_rate_pct} v2={v2?.metrics.win_rate_pct} betterIs="higher" />
              <MetricCell label="Avg winner" v1={v1?.metrics.avg_winner_pct} v2={v2?.metrics.avg_winner_pct} betterIs="higher" />
              <MetricCell label="Avg loser" v1={v1?.metrics.avg_loser_pct} v2={v2?.metrics.avg_loser_pct} betterIs="higher" />
              <MetricCell label="Expectancy / trade" v1={v1?.metrics.expectancy_pct} v2={v2?.metrics.expectancy_pct} betterIs="higher" />
              <MetricCell label="Profit factor" v1={v1?.metrics.profit_factor} v2={v2?.metrics.profit_factor} format={n => n.toFixed(2)} betterIs="higher" />
              <MetricCell label="Max drawdown" v1={v1?.metrics.max_drawdown_pct} v2={v2?.metrics.max_drawdown_pct} betterIs="higher" />
              <MetricCell label="Total trades" v1={v1?.metrics.total_trades} v2={v2?.metrics.total_trades} format={n => n.toFixed(0)} betterIs="lower" />
              <MetricCell label="Test years" v1={v1?.metrics.years} v2={v2?.metrics.years} format={n => `${n.toFixed(1)}y`} betterIs="higher" />
            </tbody>
          </table>
        </div>
      </div>

      {v2?.monthly_log && v2.monthly_log.length > 0 && (
        <div className="card">
          <h3 className="card-header">V2 regime log (last 12 rebalances)</h3>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="uppercase text-gray-500 border-b border-gray-200">
                <tr>
                  <th className="text-left py-2">Date</th>
                  <th className="text-right">Deploy</th>
                  <th className="text-left pl-4">Regime reason</th>
                  <th className="text-right">Equity (₹)</th>
                </tr>
              </thead>
              <tbody>
                {v2.monthly_log.slice(-12).reverse().map((m, i) => (
                  <tr key={i} className="border-b border-gray-50">
                    <td className="py-1.5 text-gray-600">{m.date}</td>
                    <td className={`text-right font-medium ${m.regime_deploy_pct === 100 ? 'text-green-600' : m.regime_deploy_pct >= 50 ? 'text-yellow-600' : 'text-red-600'}`}>
                      {m.regime_deploy_pct}%
                    </td>
                    <td className="pl-4 text-gray-500">{m.regime_reason}</td>
                    <td className="text-right text-gray-600">{m.equity.toLocaleString('en-IN')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {v2 && (
        <div className="card bg-yellow-50 border-yellow-200">
          <h3 className="text-sm font-semibold text-yellow-900 mb-2 flex items-center gap-2">
            <AlertTriangle className="w-4 h-4" />
            V2 caveats
          </h3>
          <ul className="text-xs text-yellow-800 space-y-1">
            {v2.caveats.map((c, i) => (
              <li key={i}>- {c}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
