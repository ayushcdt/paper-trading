'use client'

import { useLiveData } from './LiveDataProvider'
import { TrendingUp, TrendingDown, Activity } from 'lucide-react'
import type { PaperSnapshot } from '@/lib/paper'

function fmtInr(n: number): string {
  return '₹' + Math.round(n).toLocaleString('en-IN')
}

export function LivePaperSnapshot({ fallback }: { fallback: PaperSnapshot }) {
  const live = useLiveData()
  const snap = live.paper ?? fallback
  const up = (snap.total_pnl_pct ?? 0) >= 0
  const isLive = !!live.paper && live.status === 'live'

  return (
    <>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div className="card">
          <div className="text-sm text-gray-500 mb-1 flex items-center gap-2">
            Current equity
            {isLive && <span className="text-[10px] text-green-600 font-semibold">● LIVE</span>}
          </div>
          <div className="text-3xl font-bold text-gray-900 tabular-nums">{fmtInr(snap.current_equity)}</div>
        </div>
        <div className="card">
          <div className="text-sm text-gray-500 mb-1">Total P&L</div>
          <div className={`text-3xl font-bold tabular-nums ${up ? 'text-green-600' : 'text-red-600'}`}>
            {up ? '+' : ''}
            {snap.total_pnl_pct.toFixed(2)}%
          </div>
          <div className="text-xs text-gray-400 mt-1 tabular-nums">
            {fmtInr(snap.current_equity - (snap.current_equity - (snap.realized_pnl + snap.unrealized_pnl)))}
          </div>
        </div>
        <div className="card">
          <div className="text-sm text-gray-500 mb-1">Realized</div>
          <div className={`text-2xl font-bold tabular-nums ${snap.realized_pnl >= 0 ? 'text-green-600' : 'text-red-600'}`}>
            {fmtInr(snap.realized_pnl)}
          </div>
        </div>
        <div className="card">
          <div className="text-sm text-gray-500 mb-1">Unrealized</div>
          <div className={`text-2xl font-bold tabular-nums ${snap.unrealized_pnl >= 0 ? 'text-green-600' : 'text-red-600'}`}>
            {fmtInr(snap.unrealized_pnl)}
          </div>
          <div className="text-xs text-gray-400 mt-1">{snap.open_positions_count} open positions</div>
        </div>
      </div>

      <div className="card">
        <h3 className="card-header flex items-center gap-2">
          <Activity className="w-5 h-5 text-blue-500" />
          Open positions ({snap.open_positions_count})
          {isLive && <span className="ml-2 text-[10px] text-green-600 font-semibold">● LIVE</span>}
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
                    <td className="text-right tabular-nums">₹{p.entry_price.toFixed(2)}</td>
                    <td className="text-right tabular-nums">₹{p.current_price.toFixed(2)}</td>
                    <td className="text-right">{p.qty}</td>
                    <td className={`text-right font-medium tabular-nums ${p.unrealized_pnl_inr >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                      {fmtInr(p.unrealized_pnl_inr)}
                    </td>
                    <td className={`text-right tabular-nums ${p.unrealized_pnl_pct >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                      {p.unrealized_pnl_pct >= 0 ? '+' : ''}
                      {p.unrealized_pnl_pct.toFixed(2)}%
                    </td>
                    <td className="text-right text-gray-500 tabular-nums">₹{p.stop_at_entry.toFixed(2)}</td>
                    <td className="pl-4 text-xs text-gray-500">{new Date(p.entry_date).toLocaleDateString('en-IN')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  )
}
