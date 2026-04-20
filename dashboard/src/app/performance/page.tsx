import { getAnalysisData } from '@/lib/data'
import { buildHistoryTimeline, buildPickPerformance } from '@/lib/history'
import { TrendingUp, TrendingDown, Calendar, Award, AlertCircle } from 'lucide-react'

export const revalidate = 300 // 5 min

function stanceColor(stance: string) {
  if (stance.includes('BULLISH')) return 'text-green-700 bg-green-50'
  if (stance.includes('BEARISH')) return 'text-red-700 bg-red-50'
  return 'text-yellow-700 bg-yellow-50'
}

export default async function PerformancePage() {
  const [latest, timeline, performance] = await Promise.all([
    getAnalysisData(),
    buildHistoryTimeline(30),
    getAnalysisData().then(d => buildPickPerformance(d, 30)),
  ])

  const winners = performance.filter(p => (p.return_pct ?? 0) > 0)
  const losers = performance.filter(p => (p.return_pct ?? 0) < 0)
  const winRate = performance.length ? (winners.length / performance.length) * 100 : 0
  const avgReturn = performance.length
    ? performance.reduce((s, p) => s + (p.return_pct ?? 0), 0) / performance.length
    : 0

  if (timeline.length === 0) {
    return (
      <div className="space-y-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Track Record</h1>
          <p className="text-gray-500 mt-1">Build trust before you bet capital.</p>
        </div>
        <div className="card bg-yellow-50 border-yellow-200">
          <div className="flex items-start gap-3">
            <AlertCircle className="w-5 h-5 text-yellow-600 mt-0.5 flex-shrink-0" />
            <div>
              <p className="font-medium text-yellow-900">No history yet.</p>
              <p className="text-sm text-yellow-800 mt-1">
                Historical snapshots build over time. Once the scheduled jobs run for a few days, this page will show
                stance accuracy and pick performance.
              </p>
            </div>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Track Record</h1>
        <p className="text-gray-500 mt-1">{timeline.length} days of history. Latest: {latest.market.stance.stance}.</p>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <div className="card">
          <div className="text-sm text-gray-500 mb-1">Picks tracked</div>
          <div className="text-3xl font-bold">{performance.length}</div>
        </div>
        <div className="card">
          <div className="text-sm text-gray-500 mb-1">Win rate</div>
          <div className={`text-3xl font-bold ${winRate >= 50 ? 'text-green-600' : 'text-red-600'}`}>
            {winRate.toFixed(0)}%
          </div>
          <div className="text-xs text-gray-400 mt-1">{winners.length}W / {losers.length}L</div>
        </div>
        <div className="card">
          <div className="text-sm text-gray-500 mb-1">Avg return per pick</div>
          <div className={`text-3xl font-bold ${avgReturn >= 0 ? 'text-green-600' : 'text-red-600'}`}>
            {avgReturn >= 0 ? '+' : ''}
            {avgReturn.toFixed(2)}%
          </div>
        </div>
        <div className="card">
          <div className="text-sm text-gray-500 mb-1">History depth</div>
          <div className="text-3xl font-bold">{timeline.length}d</div>
        </div>
      </div>

      {/* Stance timeline */}
      <div className="card">
        <h3 className="card-header flex items-center gap-2">
          <Calendar className="w-5 h-5 text-blue-500" />
          Stance Timeline (most recent first)
        </h3>
        <div className="space-y-2">
          {timeline.map(entry => (
            <div key={entry.date} className="flex items-center justify-between py-2 border-b border-gray-100 last:border-0">
              <div className="flex items-center gap-3">
                <span className="text-sm text-gray-500 w-20">{entry.date}</span>
                <span className={`text-xs px-2 py-1 rounded ${stanceColor(entry.stance)}`}>
                  {entry.stance.replace(/_/g, ' ')}
                </span>
              </div>
              <div className="flex items-center gap-6 text-sm">
                <span className="text-gray-500">Score: <span className="font-medium text-gray-900">{entry.score}</span></span>
                <span className="text-gray-500">Nifty: <span className="font-medium text-gray-900">{entry.nifty.toLocaleString('en-IN')}</span></span>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Pick performance */}
      <div className="card">
        <h3 className="card-header flex items-center gap-2">
          <Award className="w-5 h-5 text-green-500" />
          Pick Performance (entry vs current)
        </h3>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-xs text-gray-500 uppercase border-b border-gray-200">
              <tr>
                <th className="text-left py-2">Symbol</th>
                <th className="text-left">Picked on</th>
                <th className="text-right">Entry</th>
                <th className="text-right">Current</th>
                <th className="text-right">Return</th>
                <th className="text-left pl-4">Conviction</th>
              </tr>
            </thead>
            <tbody>
              {performance.map(p => (
                <tr key={p.symbol} className="border-b border-gray-50 hover:bg-gray-50">
                  <td className="py-2 font-medium">{p.symbol}</td>
                  <td className="text-gray-500">{p.pick_date}</td>
                  <td className="text-right">₹{p.entry_cmp.toLocaleString('en-IN')}</td>
                  <td className="text-right">{p.current_cmp ? `₹${p.current_cmp.toLocaleString('en-IN')}` : '—'}</td>
                  <td className={`text-right font-medium ${
                    (p.return_pct ?? 0) > 0 ? 'text-green-600' : (p.return_pct ?? 0) < 0 ? 'text-red-600' : 'text-gray-400'
                  }`}>
                    {p.return_pct == null ? (
                      <span className="inline-flex items-center gap-1 text-gray-400">
                        <AlertCircle className="w-3 h-3" />
                        n/a
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1">
                        {p.return_pct > 0 ? <TrendingUp className="w-3 h-3" /> : <TrendingDown className="w-3 h-3" />}
                        {p.return_pct > 0 ? '+' : ''}
                        {p.return_pct.toFixed(2)}%
                      </span>
                    )}
                  </td>
                  <td className="pl-4 text-xs text-gray-500">{p.conviction}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
