import { getBlob } from '@/lib/blob'
import { Activity, ArrowRightLeft, Newspaper, TrendingUp, TrendingDown } from 'lucide-react'
import type { PaperSnapshot } from '@/lib/paper'

/** Pulls today's intraday activity from paper portfolio's recent_trades. */
export async function IntradayActivityFeed() {
  const paper = (await getBlob<PaperSnapshot>('paper_portfolio')) || null
  if (!paper) return null
  const trades = paper.recent_trades ?? []
  const today = new Date()
  const todayStr = today.getFullYear() + '-' + String(today.getMonth() + 1).padStart(2, '0') + '-' + String(today.getDate()).padStart(2, '0')

  // Today's intraday activity: trades whose date is today AND reason indicates intraday/swap/catalyst
  const intradayToday = trades.filter(t => {
    const ts = String(t.timestamp || '')
    if (!ts.startsWith(todayStr)) return false
    const r = String(t.reason || '').toLowerCase()
    return r.includes('intraday') || r.includes('swap') || r.includes('catalyst')
  })

  if (intradayToday.length === 0) {
    return (
      <div className="card">
        <h3 className="card-header flex items-center gap-2">
          <Activity className="w-5 h-5 text-indigo-600" />
          Intraday activity (today)
        </h3>
        <p className="text-sm text-gray-500">No intraday swaps or catalyst opens yet today.</p>
        <p className="text-xs text-gray-400 mt-2">
          Intraday rebalance + catalyst injection run every 15 min during market hours.
          Activity fires when picker shifts, intraday strength gap exceeds 8 composite points,
          or news catalyst meets thresholds (5+ articles, 3+ sources, M&amp;A/results/FDA keyword).
        </p>
      </div>
    )
  }

  return (
    <div className="card">
      <h3 className="card-header flex items-center gap-2">
        <Activity className="w-5 h-5 text-indigo-600" />
        Intraday activity (today, {intradayToday.length} events)
      </h3>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-xs uppercase text-gray-500 border-b border-gray-200">
            <tr>
              <th className="text-left py-2">Time</th>
              <th className="text-left">Action</th>
              <th className="text-left">Symbol</th>
              <th className="text-right">Qty</th>
              <th className="text-right">Price</th>
              <th className="text-right">P&amp;L</th>
              <th className="text-left pl-4">Reason</th>
            </tr>
          </thead>
          <tbody>
            {intradayToday.map((t, i) => {
              const ts = String(t.timestamp || '')
              const time = ts.slice(11, 16)  // HH:MM
              const action = String(t.action || '')
              const reason = String(t.reason || '')
              const isCatalyst = reason.toLowerCase().includes('catalyst')
              const isSwapOut = action === 'CLOSE'
              const pnl = (t as any).pnl_inr || 0
              return (
                <tr key={i} className="border-b border-gray-50">
                  <td className="py-2 text-gray-500 tabular-nums">{time}</td>
                  <td className="text-xs">
                    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full ${
                      isCatalyst ? 'bg-amber-50 text-amber-700' :
                      isSwapOut ? 'bg-red-50 text-red-700' : 'bg-green-50 text-green-700'
                    }`}>
                      {isCatalyst ? <Newspaper className="w-3 h-3" /> :
                       isSwapOut ? <TrendingDown className="w-3 h-3" /> :
                                   <TrendingUp className="w-3 h-3" />}
                      {isCatalyst ? 'CATALYST' : isSwapOut ? 'SWAP_OUT' : 'SWAP_IN'}
                    </span>
                  </td>
                  <td className="font-medium">{t.symbol}</td>
                  <td className="text-right tabular-nums">{t.qty}</td>
                  <td className="text-right tabular-nums">Rs {Number(t.price).toFixed(2)}</td>
                  <td className={`text-right tabular-nums ${pnl > 0 ? 'text-green-700' : pnl < 0 ? 'text-red-700' : 'text-gray-500'}`}>
                    {pnl ? `Rs ${pnl >= 0 ? '+' : ''}${Number(pnl).toFixed(0)}` : '-'}
                  </td>
                  <td className="pl-4 text-xs text-gray-600 truncate max-w-md">{reason}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
