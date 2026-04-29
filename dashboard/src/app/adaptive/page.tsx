import { getBlob } from '@/lib/blob'
import { Brain, Shield, AlertTriangle, Activity, CheckCircle, XCircle } from 'lucide-react'

export const revalidate = 60

interface BacktestV3 {
  generated_at: string
  config: Record<string, unknown>
  metrics: {
    total_trades: number
    win_rate_pct: number
    cagr_pct: number
    nifty_buyhold_cagr_pct: number
    alpha_vs_nifty_cagr_pct: number
    max_drawdown_pct: number
    profit_factor: number | null
    expectancy_pct: number
    years: number
  }
  regime_attribution: Record<string, { trades: number; win_rate_pct: number; avg_return_pct: number; total_pnl_inr: number }>
  monthly_log?: { date: string; regime: string; variant: string; deploy_pct: number; reason: string; equity: number }[]
}

interface VariantHealth {
  generated_at: string
  windows: Record<string, Record<string, {
    name: string; trades: number; avg_return_pct: number; stdev_pct: number;
    sharpe_proxy: number; win_rate_pct: number
  }>>
  guardrail_state?: {
    portfolio_peak: number; portfolio_current: number; drawdown_pct: number;
    kill_switch_active: boolean; kill_switch_reason: string | null;
    variants: Record<string, { suspended: boolean; consecutive_decay_flags: number; live_3m_return_pct: number | null }>
  }
}

interface VariantParams {
  generated_at: string
  window_start: string
  window_end: string
  variants: Record<string, { params: Record<string, number> | null; sharpe: number }>
}

async function readJson<T>(key: string): Promise<T | null> {
  return await getBlob<T>(key)
}

function regimeColor(regime: string) {
  if (regime === 'BULL_LOW_VOL') return 'bg-green-100 text-green-800'
  if (regime === 'BULL_HIGH_VOL') return 'bg-emerald-100 text-emerald-800'
  if (regime === 'RANGE') return 'bg-yellow-100 text-yellow-800'
  return 'bg-red-100 text-red-800'
}

export default async function AdaptivePage() {
  const [bt, health, params] = await Promise.all([
    readJson<BacktestV3>('backtest_v3'),
    readJson<VariantHealth>('variant_health'),
    readJson<VariantParams>('variant_params'),
  ])

  const latestLog = bt?.monthly_log?.[bt.monthly_log.length - 1]
  const gs = health?.guardrail_state

  return (
    <div className="space-y-8">
      <div>
        <div className="flex items-center gap-2">
          <Brain className="w-6 h-6 text-indigo-600" />
          <h1 className="text-2xl font-bold text-gray-900">Strategy — Momentum Base + Risk Overlay</h1>
        </div>
        <p className="text-gray-500 mt-1">
          momentum_agg variant + portfolio risk overlay (sector cap, DD circuit breaker, VIX gate, tail halt).
          Replaced V3 adaptive on 2026-04-28 after backtest showed V3 had -3.78% alpha vs Nifty.
          See <code className="bg-gray-100 px-1.5 py-0.5 rounded text-xs">data/research/sustainability/</code> for the audit trail.
        </p>
      </div>

      {/* Strategy change banner */}
      <div className="card bg-amber-50 border-amber-200">
        <div className="flex items-start gap-3">
          <Activity className="w-5 h-5 text-amber-600 mt-0.5 flex-shrink-0" />
          <div className="text-sm text-amber-900">
            <strong className="block mb-1">Strategy rewrite — 2026-04-28</strong>
            <p>
              V3 retired (live backtest: +3.50% CAGR, Sharpe 0.45, WR 31.9%, alpha -3.78% vs Nifty).
              Replaced with momentum_agg + risk overlay (backtest: +21.69% CAGR, Sharpe 1.35, MaxDD -16%, alpha +14.36%).
            </p>
            <p className="mt-2">
              <strong>Real money go-live BLOCKED</strong> until walk-forward validation passes.
              Current verdict: FAIL (2/5 folds positive alpha, need 3). See <code className="bg-amber-100 px-1.5 py-0.5 rounded text-xs">data/research/walk_forward.json</code>.
            </p>
            <p className="mt-2 text-amber-700">
              The historical V3 backtest stats below are RETIRED — kept for audit. Live system is now the new picker.
            </p>
          </div>
        </div>
      </div>

      {/* Current regime / variant */}
      {latestLog && (
        <div className="card">
          <h3 className="card-header flex items-center gap-2"><Activity className="w-5 h-5 text-blue-500" />Current state</h3>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div className="p-4 bg-gray-50 rounded-lg">
              <div className="text-sm text-gray-500 mb-1">Regime</div>
              <span className={`inline-block px-3 py-1 rounded text-sm font-semibold ${regimeColor(latestLog.regime)}`}>
                {latestLog.regime.replace(/_/g, ' ')}
              </span>
              <p className="text-xs text-gray-500 mt-2">{latestLog.reason}</p>
            </div>
            <div className="p-4 bg-gray-50 rounded-lg">
              <div className="text-sm text-gray-500 mb-1">Active variant</div>
              <div className="text-xl font-bold text-gray-900">{latestLog.variant}</div>
              <div className="text-xs text-gray-500 mt-2">Deploy: {latestLog.deploy_pct}%</div>
            </div>
            <div className="p-4 bg-gray-50 rounded-lg">
              <div className="text-sm text-gray-500 mb-1">As of</div>
              <div className="text-xl font-bold text-gray-900">{latestLog.date}</div>
              <div className="text-xs text-gray-500 mt-2">Equity: ₹{latestLog.equity.toLocaleString('en-IN')}</div>
            </div>
          </div>
        </div>
      )}

      {/* Guardrails */}
      {gs && (
        <div className={`card ${gs.kill_switch_active ? 'bg-red-50 border-red-200' : 'bg-green-50 border-green-200'}`}>
          <h3 className="card-header flex items-center gap-2">
            <Shield className={`w-5 h-5 ${gs.kill_switch_active ? 'text-red-600' : 'text-green-600'}`} />
            Guardrails
          </h3>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div>
              <div className="text-xs text-gray-500">Kill switch</div>
              <div className={`font-semibold ${gs.kill_switch_active ? 'text-red-700' : 'text-green-700'}`}>
                {gs.kill_switch_active ? 'ACTIVE' : 'armed'}
              </div>
            </div>
            <div>
              <div className="text-xs text-gray-500">Current drawdown</div>
              <div className="font-semibold">{gs.drawdown_pct.toFixed(1)}%</div>
            </div>
            <div>
              <div className="text-xs text-gray-500">Peak equity</div>
              <div className="font-semibold">₹{gs.portfolio_peak.toLocaleString('en-IN')}</div>
            </div>
            <div>
              <div className="text-xs text-gray-500">Current equity</div>
              <div className="font-semibold">₹{gs.portfolio_current.toLocaleString('en-IN')}</div>
            </div>
          </div>
          {gs.kill_switch_reason && (
            <p className="text-xs text-red-700 mt-3">{gs.kill_switch_reason}</p>
          )}
        </div>
      )}

      {/* Variant health */}
      {health?.windows && (
        <div className="card">
          <h3 className="card-header flex items-center gap-2"><Activity className="w-5 h-5 text-purple-500" />Variant health (nightly)</h3>
          <p className="text-xs text-gray-500 mb-3">Last check: {new Date(health.generated_at).toLocaleString('en-IN')}</p>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-xs uppercase text-gray-500 border-b border-gray-200">
                <tr>
                  <th className="text-left py-2">Variant</th>
                  <th className="text-right">1y trades</th>
                  <th className="text-right">1y avg</th>
                  <th className="text-right">1y Sharpe</th>
                  <th className="text-right">3y Sharpe</th>
                  <th className="text-left pl-4">Status</th>
                </tr>
              </thead>
              <tbody>
                {Object.keys(health.windows.trailing_1y || {}).map(name => {
                  const w1 = health.windows.trailing_1y[name]
                  const w3 = health.windows.trailing_3y?.[name]
                  const suspended = gs?.variants?.[name]?.suspended
                  return (
                    <tr key={name} className="border-b border-gray-50">
                      <td className="py-2 font-medium">{name}</td>
                      <td className="text-right">{w1.trades}</td>
                      <td className={`text-right ${w1.avg_return_pct >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                        {w1.avg_return_pct >= 0 ? '+' : ''}{w1.avg_return_pct.toFixed(2)}%
                      </td>
                      <td className="text-right">{w1.sharpe_proxy.toFixed(2)}</td>
                      <td className="text-right">{w3 ? w3.sharpe_proxy.toFixed(2) : '—'}</td>
                      <td className="pl-4">
                        {suspended ? (
                          <span className="inline-flex items-center gap-1 text-red-600 text-xs">
                            <XCircle className="w-3 h-3" />suspended
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 text-green-600 text-xs">
                            <CheckCircle className="w-3 h-3" />active
                          </span>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Backtest summary */}
      {bt && (
        <div className="card">
          <h3 className="card-header">Backtest — V3 adaptive, {bt.metrics.years.toFixed(1)}y window</h3>
          <div className="grid grid-cols-2 md:grid-cols-5 gap-4 mb-6">
            <div><div className="text-xs text-gray-500">CAGR</div><div className={`text-2xl font-bold ${bt.metrics.cagr_pct >= 0 ? 'text-green-600' : 'text-red-600'}`}>{bt.metrics.cagr_pct >= 0 ? '+' : ''}{bt.metrics.cagr_pct.toFixed(1)}%</div></div>
            <div><div className="text-xs text-gray-500">Nifty CAGR</div><div className="text-2xl font-bold text-gray-700">+{bt.metrics.nifty_buyhold_cagr_pct.toFixed(1)}%</div></div>
            <div><div className="text-xs text-gray-500">Alpha</div><div className={`text-2xl font-bold ${bt.metrics.alpha_vs_nifty_cagr_pct >= 0 ? 'text-green-600' : 'text-red-600'}`}>{bt.metrics.alpha_vs_nifty_cagr_pct >= 0 ? '+' : ''}{bt.metrics.alpha_vs_nifty_cagr_pct.toFixed(1)}%</div></div>
            <div><div className="text-xs text-gray-500">Max DD</div><div className="text-2xl font-bold text-red-600">{bt.metrics.max_drawdown_pct.toFixed(1)}%</div></div>
            <div><div className="text-xs text-gray-500">Profit factor</div><div className="text-2xl font-bold">{bt.metrics.profit_factor?.toFixed(2) ?? '—'}</div></div>
          </div>

          <h4 className="text-sm font-semibold text-gray-700 mb-2">Regime attribution</h4>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-xs uppercase text-gray-500 border-b border-gray-200">
                <tr>
                  <th className="text-left py-2">Regime</th>
                  <th className="text-right">Trades</th>
                  <th className="text-right">Win rate</th>
                  <th className="text-right">Avg return</th>
                  <th className="text-right">P&L</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(bt.regime_attribution).map(([reg, a]) => (
                  <tr key={reg} className="border-b border-gray-50">
                    <td className="py-2"><span className={`inline-block px-2 py-0.5 rounded text-xs ${regimeColor(reg)}`}>{reg.replace(/_/g, ' ')}</span></td>
                    <td className="text-right">{a.trades}</td>
                    <td className="text-right">{a.trades ? `${a.win_rate_pct.toFixed(0)}%` : '—'}</td>
                    <td className={`text-right ${a.avg_return_pct >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                      {a.trades ? `${a.avg_return_pct >= 0 ? '+' : ''}${a.avg_return_pct.toFixed(2)}%` : '—'}
                    </td>
                    <td className={`text-right font-medium ${a.total_pnl_inr >= 0 ? 'text-green-700' : 'text-red-700'}`}>
                      {a.trades ? `₹${a.total_pnl_inr.toLocaleString('en-IN')}` : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Calibrated params */}
      {params && (
        <div className="card">
          <h3 className="card-header">Self-tuned parameters (Phase 3)</h3>
          <p className="text-xs text-gray-500 mb-3">
            Recalibration window: {params.window_start} → {params.window_end}. Each variant's params are tuned on trailing 1y within hard bounds.
          </p>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {Object.entries(params.variants).map(([name, v]) => (
              <div key={name} className="p-3 bg-gray-50 rounded-lg">
                <div className="font-medium text-sm">{name}</div>
                <div className="text-xs text-gray-500 mb-2">Sharpe: {v.sharpe.toFixed(2)}</div>
                {v.params ? (
                  <div className="space-y-0.5">
                    {Object.entries(v.params).map(([k, val]) => (
                      <div key={k} className="text-xs font-mono">
                        <span className="text-gray-500">{k}:</span> <span className="text-gray-900">{val}</span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="text-xs text-gray-400">using defaults</div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {!bt && (
        <div className="card bg-yellow-50 border-yellow-200">
          <div className="flex items-start gap-3">
            <AlertTriangle className="w-5 h-5 text-yellow-600 mt-0.5 flex-shrink-0" />
            <div className="text-sm text-yellow-800">
              <p className="font-medium">No V3 backtest results yet.</p>
              <p className="mt-1">
                Run: <code className="bg-yellow-100 px-2 py-0.5 rounded font-mono text-xs">cd c:\trading\backend && python scripts/backtest_v3.py</code>
              </p>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
