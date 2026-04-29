import { getBlob } from '@/lib/blob'
import { Receipt, TrendingDown, TrendingUp } from 'lucide-react'
import type { PaperSnapshot } from '@/lib/paper'

/**
 * Real-money fees breakdown.
 * Shows what brokerage/STT/exchange/SEBI/stamp/GST these trades would have
 * cost as real money under a Zerodha-style discount-broker fee schedule,
 * and compares to the flat 0.4% cost the paper system applies internally.
 */
export async function RealFeesCard() {
  const paper = (await getBlob<PaperSnapshot>('paper_portfolio')) || null
  const fs = paper?.fees_summary
  if (!paper || !fs) return null

  const fmt = (n: number) => 'Rs ' + Math.round(n).toLocaleString('en-IN')
  const fmt2 = (n: number) => 'Rs ' + Number(n).toFixed(2)
  const realLowerThanFlat = fs.real_vs_flat_inr < 0

  // Aggregate breakdown across recent trades
  const agg = fs.recent_trades.reduce(
    (acc, t) => {
      const b = t.breakdown
      if (!b) return acc
      acc.brokerage += b.brokerage || 0
      acc.stt += b.stt || 0
      acc.exchange += b.exchange || 0
      acc.sebi += b.sebi || 0
      acc.stamp += b.stamp || 0
      acc.gst += b.gst || 0
      return acc
    },
    { brokerage: 0, stt: 0, exchange: 0, sebi: 0, stamp: 0, gst: 0 },
  )

  return (
    <div className="card">
      <h3 className="card-header flex items-center gap-2">
        <Receipt className="w-5 h-5 text-purple-600" />
        Real-money fees (if these were real trades)
      </h3>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
        <div className="bg-purple-50 rounded p-3">
          <div className="text-xs text-gray-600 mb-1">Total real fees paid</div>
          <div className="text-2xl font-bold text-purple-700 tabular-nums">{fmt(fs.total_real_fees_inr)}</div>
          <div className="text-xs text-gray-500 mt-1">across {fs.n_closes} closes</div>
        </div>
        <div className="bg-gray-50 rounded p-3">
          <div className="text-xs text-gray-600 mb-1">System estimate (0.4% flat)</div>
          <div className="text-2xl font-bold text-gray-700 tabular-nums">{fmt(fs.flat_estimate_inr)}</div>
          <div className="text-xs text-gray-500 mt-1">already deducted from P&amp;L</div>
        </div>
        <div className={`rounded p-3 ${realLowerThanFlat ? 'bg-green-50' : 'bg-red-50'}`}>
          <div className="text-xs text-gray-600 mb-1 flex items-center gap-1">
            Real vs flat
            {realLowerThanFlat ? (
              <TrendingDown className="w-3 h-3 text-green-700" />
            ) : (
              <TrendingUp className="w-3 h-3 text-red-700" />
            )}
          </div>
          <div className={`text-2xl font-bold tabular-nums ${realLowerThanFlat ? 'text-green-700' : 'text-red-700'}`}>
            {realLowerThanFlat ? '' : '+'}
            {fmt(fs.real_vs_flat_inr)}
          </div>
          <div className="text-xs text-gray-500 mt-1">
            {realLowerThanFlat ? 'real money would pay LESS' : 'real money would pay MORE'}
          </div>
        </div>
        <div className="bg-gray-50 rounded p-3">
          <div className="text-xs text-gray-600 mb-1">Avg fee per close</div>
          <div className="text-2xl font-bold text-gray-700 tabular-nums">{fmt2(fs.avg_fee_per_close_inr)}</div>
          <div className="text-xs text-gray-500 mt-1">
            {fs.n_intraday_closes} intraday + {fs.n_delivery_closes} delivery
          </div>
        </div>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-6 gap-2 mb-4 text-xs">
        <FeeChip label="Brokerage" value={agg.brokerage} />
        <FeeChip label="STT" value={agg.stt} />
        <FeeChip label="Exchange" value={agg.exchange} />
        <FeeChip label="SEBI" value={agg.sebi} />
        <FeeChip label="Stamp" value={agg.stamp} />
        <FeeChip label="GST" value={agg.gst} />
      </div>

      {fs.recent_trades.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="text-gray-500 border-b border-gray-200">
              <tr>
                <th className="text-left py-1">Date</th>
                <th className="text-left">Symbol</th>
                <th className="text-left">Type</th>
                <th className="text-right">Notional</th>
                <th className="text-right">Real fee</th>
                <th className="text-right">% of notional</th>
                <th className="text-right">P&amp;L (gross)</th>
              </tr>
            </thead>
            <tbody>
              {fs.recent_trades.slice(0, 10).map((t, i) => {
                const pctOfNotional = t.exit_notional > 0 ? (t.real_fee_inr / t.exit_notional) * 100 : 0
                return (
                  <tr key={i} className="border-b border-gray-50">
                    <td className="py-1 text-gray-500 tabular-nums">{t.date}</td>
                    <td className="font-medium">{t.symbol}</td>
                    <td>
                      <span
                        className={`inline-block px-2 py-0.5 rounded-full text-[10px] ${
                          t.is_intraday ? 'bg-blue-50 text-blue-700' : 'bg-amber-50 text-amber-700'
                        }`}
                      >
                        {t.is_intraday ? 'INTRADAY' : 'DELIVERY'}
                      </span>
                    </td>
                    <td className="text-right tabular-nums">{fmt(t.exit_notional)}</td>
                    <td className="text-right tabular-nums">{fmt2(t.real_fee_inr)}</td>
                    <td className="text-right tabular-nums text-gray-500">{pctOfNotional.toFixed(3)}%</td>
                    <td
                      className={`text-right tabular-nums ${
                        t.pnl_inr > 0 ? 'text-green-700' : t.pnl_inr < 0 ? 'text-red-700' : 'text-gray-500'
                      }`}
                    >
                      {t.pnl_inr ? `${t.pnl_inr >= 0 ? '+' : ''}${fmt(t.pnl_inr)}` : '-'}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      <p className="text-[10px] text-gray-400 mt-3">
        Fee model: Zerodha discount broker. Brokerage Rs 0 delivery / min(0.03%, Rs 20) intraday. STT 0.1% both legs
        (delivery) or 0.025% sell only (intraday). Exchange 0.00322%, SEBI 0.0001%, stamp 0.015% buy
        (delivery) / 0.003% buy (intraday). GST 18% on (brokerage+exchange+SEBI). System P&amp;L still uses flat 0.4%
        for backtest parity; this card is informational.
      </p>
    </div>
  )
}

function FeeChip({ label, value }: { label: string; value: number }) {
  return (
    <div className="bg-white border border-gray-200 rounded p-2 text-center">
      <div className="text-[10px] text-gray-500">{label}</div>
      <div className="font-semibold text-gray-800 tabular-nums">Rs {value.toFixed(2)}</div>
    </div>
  )
}
