import { getBlob } from '@/lib/blob'
import { getAnalysisData } from '@/lib/data'
import { Eye, AlertTriangle, TrendingUp, TrendingDown, Info, Flame, Activity } from 'lucide-react'

interface HybridDecision {
  timestamp: string
  symbol: string
  v3_score: number
  hybrid_score: number
  adjustment_pct: number
  reasons: string[]
  sector_tilts_applied: Record<string, number>
  news_sentiment_24h: number
  article_count_24h: number
  story_buzz_24h: number
  active_theme_ids: string[]
  mode: string
}

interface ActiveTheme {
  theme_id: string
  score: number
  matched_articles: number
  distinct_sources: number
  sample_titles: string[]
  positive_for: string[]
  negative_for: string[]
}

interface HybridOverlayBlock {
  status?: string
  active_themes?: ActiveTheme[]
  decisions?: HybridDecision[]
  decisions_count?: number
  mode?: string
}

// Shadow log is a research artefact updated daily — 600s matches /backtest
// (the other "static research" page) and reduces redundant Redis reads.
export const revalidate = 600

interface ShadowEntry {
  symbol: string
  mode: 'shadow' | 'live'
  action: 'BLACKLIST' | 'ADJUST' | 'HOLD'
  original_score: number
  new_score: number | null
  adjustment_pct: number | null
  sentiment_24h: number
  c24: number
  reason: string
  timestamp: string
}

export default async function NewsShadowPage() {
  const [log, analysis] = await Promise.all([
    getBlob<ShadowEntry[]>('news_shadow_log'),
    getAnalysisData() as any,
  ])
  const safeLog = log ?? []
  const reversed = [...safeLog].reverse()
  const overlay: HybridOverlayBlock = (analysis?.hybrid_overlay ?? {}) as HybridOverlayBlock
  const activeThemes = overlay.active_themes ?? []
  const recentDecisions = overlay.decisions ?? []
  const decisionCount = overlay.decisions_count ?? recentDecisions.length

  // Summary metrics
  const blacklists = safeLog.filter(e => e.action === 'BLACKLIST').length
  const boosts = safeLog.filter(e => e.action === 'ADJUST' && (e.adjustment_pct ?? 0) > 0).length
  const penalties = safeLog.filter(e => e.action === 'ADJUST' && (e.adjustment_pct ?? 0) < 0).length
  const firstTs = safeLog.length ? safeLog[0].timestamp : null
  const lastTs = safeLog.length ? safeLog[safeLog.length - 1].timestamp : null
  const daysRunning = firstTs
    ? Math.max(1, Math.round((Date.now() - new Date(firstTs).getTime()) / (1000 * 60 * 60 * 24)))
    : 0

  // Per-symbol net adjustment
  const bySymbol: Record<string, { adjustments: number[]; blacklists: number; lastSeen: string }> = {}
  for (const e of safeLog) {
    if (!bySymbol[e.symbol]) bySymbol[e.symbol] = { adjustments: [], blacklists: 0, lastSeen: e.timestamp }
    bySymbol[e.symbol].lastSeen = e.timestamp
    if (e.action === 'BLACKLIST') bySymbol[e.symbol].blacklists += 1
    if (e.action === 'ADJUST' && e.adjustment_pct != null) bySymbol[e.symbol].adjustments.push(e.adjustment_pct)
  }
  const symbolSummaries = Object.entries(bySymbol)
    .map(([sym, data]) => ({
      symbol: sym,
      count: data.adjustments.length + data.blacklists,
      avg_adjustment: data.adjustments.length
        ? data.adjustments.reduce((s, v) => s + v, 0) / data.adjustments.length
        : 0,
      blacklists: data.blacklists,
      lastSeen: data.lastSeen,
    }))
    .sort((a, b) => b.count - a.count)
    .slice(0, 30)

  const mode = safeLog[safeLog.length - 1]?.mode ?? 'shadow'

  return (
    <div className="space-y-8">
      <div>
        <div className="flex items-center gap-2">
          <Eye className="w-6 h-6 text-indigo-600" />
          <h1 className="text-2xl font-bold text-gray-900">News overlay — shadow mode</h1>
        </div>
        <p className="text-gray-500 mt-1">
          {mode === 'shadow'
            ? 'Currently SHADOW: logging decisions without applying. Switch to LIVE after 30+ days of validation data.'
            : 'LIVE: overlay is actively adjusting pick scores.'}
        </p>
      </div>

      {/* Active themes today (from hybrid_overlay block) */}
      <div className="card">
        <h3 className="card-header flex items-center gap-2">
          <Flame className="w-5 h-5 text-amber-600" />
          Active themes detected ({activeThemes.length})
          <span className="ml-auto text-xs text-gray-500">overlay mode: {overlay.mode ?? 'shadow'}</span>
        </h3>
        {activeThemes.length === 0 ? (
          <p className="text-sm text-gray-500">No themes met the multi-source confirmation threshold this run.</p>
        ) : (
          <div className="space-y-3">
            {activeThemes.map((t, i) => (
              <div key={i} className="border-l-4 border-amber-400 pl-3 py-1">
                <div className="flex items-baseline gap-3">
                  <span className="font-mono text-sm font-semibold text-gray-900">{t.theme_id}</span>
                  <span className="text-xs text-gray-500">
                    score {t.score} · {t.matched_articles} arts · {t.distinct_sources} sources
                  </span>
                </div>
                <div className="text-xs text-gray-600 mt-1">
                  {t.positive_for.length > 0 && (
                    <span className="mr-2">+ for: <span className="text-green-700">{t.positive_for.join(', ')}</span></span>
                  )}
                  {t.negative_for.length > 0 && (
                    <span>- for: <span className="text-red-700">{t.negative_for.join(', ')}</span></span>
                  )}
                </div>
                {t.sample_titles.slice(0, 2).map((s, j) => (
                  <div key={j} className="text-xs text-gray-500 truncate mt-0.5">{s}</div>
                ))}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Recent shadow decisions table */}
      {recentDecisions.length > 0 && (
        <div className="card">
          <h3 className="card-header flex items-center gap-2">
            <Activity className="w-5 h-5 text-indigo-600" />
            Hybrid score decisions ({decisionCount} total this run)
          </h3>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-xs uppercase text-gray-500 border-b border-gray-200">
                <tr>
                  <th className="text-left py-2">Symbol</th>
                  <th className="text-right">V3 score</th>
                  <th className="text-right">Hybrid</th>
                  <th className="text-right">Δ%</th>
                  <th className="text-right">Sent 24h</th>
                  <th className="text-right">Buzz</th>
                  <th className="text-left pl-4">Reasons</th>
                </tr>
              </thead>
              <tbody>
                {recentDecisions.map((d, i) => (
                  <tr key={i} className="border-b border-gray-50">
                    <td className="py-2 font-medium">{d.symbol}</td>
                    <td className="text-right tabular-nums">{d.v3_score.toFixed(3)}</td>
                    <td className="text-right tabular-nums">{d.hybrid_score.toFixed(3)}</td>
                    <td className={`text-right font-medium tabular-nums ${
                      d.adjustment_pct > 0 ? 'text-green-700' : d.adjustment_pct < 0 ? 'text-red-700' : 'text-gray-500'
                    }`}>
                      {d.adjustment_pct >= 0 ? '+' : ''}{d.adjustment_pct.toFixed(1)}%
                    </td>
                    <td className={`text-right tabular-nums ${
                      d.news_sentiment_24h <= -1.5 ? 'text-red-600' : d.news_sentiment_24h >= 1.5 ? 'text-green-600' : 'text-gray-500'
                    }`}>
                      {d.news_sentiment_24h >= 0 ? '+' : ''}{d.news_sentiment_24h.toFixed(1)}
                    </td>
                    <td className="text-right text-gray-500">{d.story_buzz_24h}</td>
                    <td className="pl-4 text-xs text-gray-600">
                      {d.reasons.length > 0 ? d.reasons.slice(0, 2).join(' · ') : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {safeLog.length === 0 && (
        <div className="card bg-yellow-50 border-yellow-200">
          <div className="flex items-start gap-3">
            <AlertTriangle className="w-5 h-5 text-yellow-600 mt-0.5 flex-shrink-0" />
            <div>
              <p className="font-medium text-yellow-900">No shadow data yet.</p>
              <p className="text-sm text-yellow-800 mt-1">
                Data starts accumulating from the next scheduled run. Every pick that would have been adjusted
                by the news overlay gets logged here. Review after 30+ days to decide whether to enable LIVE mode.
              </p>
            </div>
          </div>
        </div>
      )}

      {safeLog.length > 0 && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div className="card">
              <div className="text-sm text-gray-500 mb-1">Days running</div>
              <div className="text-3xl font-bold text-gray-900">{daysRunning}</div>
              <div className="text-xs text-gray-400 mt-1">need 30+ for decision</div>
            </div>
            <div className="card">
              <div className="text-sm text-gray-500 mb-1 flex items-center gap-1">
                <AlertTriangle className="w-4 h-4 text-red-600" />Blacklists
              </div>
              <div className="text-3xl font-bold text-red-700">{blacklists}</div>
              <div className="text-xs text-gray-400 mt-1">would-be-skipped picks</div>
            </div>
            <div className="card">
              <div className="text-sm text-gray-500 mb-1 flex items-center gap-1">
                <TrendingUp className="w-4 h-4 text-green-600" />Boosts
              </div>
              <div className="text-3xl font-bold text-green-700">{boosts}</div>
            </div>
            <div className="card">
              <div className="text-sm text-gray-500 mb-1 flex items-center gap-1">
                <TrendingDown className="w-4 h-4 text-red-600" />Penalties
              </div>
              <div className="text-3xl font-bold text-red-700">{penalties}</div>
            </div>
          </div>

          <div className="card">
            <h3 className="card-header">Most-affected symbols ({symbolSummaries.length})</h3>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-xs uppercase text-gray-500 border-b border-gray-200">
                  <tr>
                    <th className="text-left py-2">Symbol</th>
                    <th className="text-right">Events</th>
                    <th className="text-right">Avg adjustment</th>
                    <th className="text-right">Blacklists</th>
                    <th className="text-left pl-4">Last seen</th>
                  </tr>
                </thead>
                <tbody>
                  {symbolSummaries.map(s => (
                    <tr key={s.symbol} className="border-b border-gray-50">
                      <td className="py-2 font-medium">{s.symbol}</td>
                      <td className="text-right">{s.count}</td>
                      <td className={`text-right ${s.avg_adjustment > 0 ? 'text-green-600' : s.avg_adjustment < 0 ? 'text-red-600' : 'text-gray-400'}`}>
                        {s.avg_adjustment >= 0 ? '+' : ''}{s.avg_adjustment.toFixed(2)}%
                      </td>
                      <td className={`text-right ${s.blacklists > 0 ? 'text-red-600 font-medium' : 'text-gray-400'}`}>
                        {s.blacklists}
                      </td>
                      <td className="pl-4 text-xs text-gray-500">{new Date(s.lastSeen).toLocaleDateString('en-IN')}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="card">
            <h3 className="card-header">Recent decisions (last 50)</h3>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-xs uppercase text-gray-500 border-b border-gray-200">
                  <tr>
                    <th className="text-left py-2">Time</th>
                    <th className="text-left">Symbol</th>
                    <th className="text-left">Action</th>
                    <th className="text-right">Sent 24h</th>
                    <th className="text-right">c24</th>
                    <th className="text-right">Adjustment</th>
                    <th className="text-left pl-4">Reason</th>
                  </tr>
                </thead>
                <tbody>
                  {reversed.slice(0, 50).map((e, i) => (
                    <tr key={i} className="border-b border-gray-50">
                      <td className="py-2 text-xs text-gray-500">{new Date(e.timestamp).toLocaleString('en-IN')}</td>
                      <td className="font-medium">{e.symbol}</td>
                      <td>
                        <span className={`text-xs font-semibold px-2 py-0.5 rounded ${
                          e.action === 'BLACKLIST' ? 'bg-red-100 text-red-700' :
                          (e.adjustment_pct ?? 0) > 0 ? 'bg-green-100 text-green-700' : 'bg-yellow-100 text-yellow-700'
                        }`}>{e.action}</span>
                      </td>
                      <td className={`text-right ${e.sentiment_24h > 0 ? 'text-green-600' : e.sentiment_24h < 0 ? 'text-red-600' : 'text-gray-400'}`}>
                        {e.sentiment_24h > 0 ? '+' : ''}{e.sentiment_24h.toFixed(1)}
                      </td>
                      <td className="text-right text-gray-500">{e.c24}</td>
                      <td className={`text-right ${
                        e.adjustment_pct == null ? 'text-gray-400' :
                        e.adjustment_pct > 0 ? 'text-green-600' : 'text-red-600'
                      }`}>
                        {e.adjustment_pct == null ? '—' : `${e.adjustment_pct > 0 ? '+' : ''}${e.adjustment_pct.toFixed(2)}%`}
                      </td>
                      <td className="pl-4 text-xs text-gray-500">{e.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="card bg-blue-50 border-blue-200">
            <div className="flex items-start gap-3">
              <Info className="w-5 h-5 text-blue-600 mt-0.5 flex-shrink-0" />
              <div className="text-sm text-blue-900">
                <p className="font-medium">How to activate LIVE mode</p>
                <p className="mt-1">
                  After 30+ days of data, evaluate if the overlay's hypothetical adjustments correlate with picks
                  under/over-performing vs their scores. If the overlay is genuinely informative, set
                  <code className="mx-1 px-1.5 py-0.5 bg-blue-100 rounded font-mono text-xs">NEWS_OVERLAY_MODE=live</code>
                  in config.py and redeploy.
                </p>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
