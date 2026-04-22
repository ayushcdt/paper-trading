import { getAnalysisData } from '@/lib/data'
import { getNewsBlock } from '@/lib/news'
import { NewsBadge } from '@/components/NewsBadge'
import { TrendingUp, Target, Shield, AlertTriangle } from 'lucide-react'

export const revalidate = 60

export default async function StocksPage() {
  const [data, news] = await Promise.all([getAnalysisData(), getNewsBlock()])
  const { stocks } = data

  return (
    <div className="space-y-8">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Stock Picks</h1>
        <p className="text-gray-500 mt-1">
          AI-selected stocks based on quality, momentum, and technical analysis
        </p>
      </div>

      {/* Market Condition Banner */}
      <div className={`p-4 rounded-lg ${
        stocks.market_condition.includes('BULLISH') ? 'bg-green-50 border border-green-200' :
        stocks.market_condition.includes('BEARISH') ? 'bg-red-50 border border-red-200' :
        'bg-yellow-50 border border-yellow-200'
      }`}>
        <div className="flex items-center gap-2">
          <Target className={`w-5 h-5 ${
            stocks.market_condition.includes('BULLISH') ? 'text-green-600' :
            stocks.market_condition.includes('BEARISH') ? 'text-red-600' :
            'text-yellow-600'
          }`} />
          <span className="font-medium">
            Market Condition: {stocks.market_condition.replace('_', ' ')}
          </span>
        </div>
      </div>

      {/* Stock Cards */}
      <div className="space-y-6">
        {stocks.picks.map((stock) => (
          <div key={stock.symbol} className="card">
            {/* Header */}
            <div className="flex items-start justify-between mb-4">
              <div>
                <div className="flex items-center gap-3">
                  <span className="w-8 h-8 bg-gray-100 rounded-lg flex items-center justify-center text-sm font-bold text-gray-600">
                    #{stock.rank}
                  </span>
                  <div>
                    <h3 className="text-xl font-bold text-gray-900">{stock.symbol}</h3>
                    <p className="text-sm text-gray-500">{stock.name}</p>
                    <NewsBadge symbol={stock.symbol} news={news} />
                  </div>
                </div>
              </div>
              <div className="text-right">
                <span className={`badge ${
                  stock.conviction === 'HIGH' ? 'badge-green' :
                  stock.conviction === 'MEDIUM' ? 'badge-yellow' : 'badge-blue'
                }`}>
                  {stock.conviction} CONVICTION
                </span>
                <p className="text-sm text-gray-500 mt-1">{stock.sector}</p>
              </div>
            </div>

            {/* Price Info */}
            <div className="grid grid-cols-2 md:grid-cols-5 gap-4 mb-6 p-4 bg-gray-50 rounded-lg">
              <div>
                <p className="text-xs text-gray-500 uppercase">CMP</p>
                <p className="text-lg font-bold text-gray-900">₹{stock.cmp.toLocaleString()}</p>
              </div>
              <div>
                <p className="text-xs text-gray-500 uppercase">Target</p>
                <p className="text-lg font-bold text-green-600">₹{stock.target.toLocaleString()}</p>
              </div>
              <div>
                <p className="text-xs text-gray-500 uppercase">Stop Loss</p>
                <p className="text-lg font-bold text-red-600">₹{stock.stop_loss.toLocaleString()}</p>
              </div>
              <div>
                <p className="text-xs text-gray-500 uppercase">Upside</p>
                <p className="text-lg font-bold text-green-600">+{stock.upside_pct}%</p>
              </div>
              <div>
                <p className="text-xs text-gray-500 uppercase">Risk</p>
                <p className="text-lg font-bold text-red-600">-{stock.risk_pct}%</p>
              </div>
            </div>

            {/* Scores */}
            <div className="mb-6">
              <h4 className="text-sm font-medium text-gray-700 mb-3">Scores</h4>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <div>
                  <div className="flex justify-between text-sm mb-1">
                    <span className="text-gray-500">Quality</span>
                    <span className="font-medium">
                      {stock.scores.quality === null ? 'N/A' : `${stock.scores.quality}/100`}
                    </span>
                  </div>
                  <div className="score-bar">
                    <div className="score-fill" style={{ width: `${stock.scores.quality ?? 0}%` }}></div>
                  </div>
                  {(stock.fundamentals_status === 'unavailable' || stock.fundamentals_status === 'stale') && (
                    <p className="mt-1 text-xs text-yellow-700">
                      Fundamentals {stock.fundamentals_status} -- score is momentum/technical only.
                    </p>
                  )}
                </div>
                <div>
                  <div className="flex justify-between text-sm mb-1">
                    <span className="text-gray-500">Momentum</span>
                    <span className="font-medium">{stock.scores.momentum}/100</span>
                  </div>
                  <div className="score-bar">
                    <div className="score-fill" style={{ width: `${stock.scores.momentum}%` }}></div>
                  </div>
                </div>
                <div>
                  <div className="flex justify-between text-sm mb-1">
                    <span className="text-gray-500">Technical</span>
                    <span className="font-medium">{stock.scores.technical}/100</span>
                  </div>
                  <div className="score-bar">
                    <div className="score-fill" style={{ width: `${stock.scores.technical}%` }}></div>
                  </div>
                </div>
                <div>
                  <div className="flex justify-between text-sm mb-1">
                    <span className="text-gray-500">Overall</span>
                    <span className="font-bold">{stock.scores.overall}/100</span>
                  </div>
                  <div className="score-bar">
                    <div className="score-fill bg-gradient-to-r from-blue-500 to-purple-500" style={{ width: `${stock.scores.overall}%` }}></div>
                  </div>
                </div>
              </div>
            </div>

            {/* Technical Details */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6 text-sm">
              <div className="p-3 bg-gray-50 rounded-lg">
                <p className="text-gray-500">Trend</p>
                <p className={`font-medium ${
                  stock.technicals.trend.includes('BULLISH') ? 'text-green-600' : 'text-red-600'
                }`}>{stock.technicals.trend}</p>
              </div>
              <div className="p-3 bg-gray-50 rounded-lg">
                <p className="text-gray-500">RSI</p>
                <p className="font-medium">{stock.technicals.rsi}</p>
              </div>
              <div className="p-3 bg-gray-50 rounded-lg">
                <p className="text-gray-500">6M RS vs Nifty</p>
                <p className={`font-medium ${stock.momentum.rs_6m > 1 ? 'text-green-600' : 'text-red-600'}`}>
                  {stock.momentum.rs_6m}x
                </p>
              </div>
              <div className="p-3 bg-gray-50 rounded-lg">
                <p className="text-gray-500">Volume</p>
                <p className="font-medium">{stock.technicals.volume_ratio}x avg</p>
              </div>
            </div>

            {/* Setup */}
            <div className="mb-4 p-3 bg-blue-50 rounded-lg">
              <p className="text-sm text-blue-800">
                <span className="font-medium">Setup: </span>
                {stock.technicals.setup}
              </p>
            </div>

            {/* AI Reasoning */}
            <div className="p-4 bg-gradient-to-r from-green-50 to-emerald-50 rounded-lg border border-green-100">
              <div className="flex items-start gap-2">
                <TrendingUp className="w-5 h-5 text-green-600 mt-0.5 flex-shrink-0" />
                <div>
                  <p className="text-sm font-medium text-green-800 mb-1">AI Analysis</p>
                  <p className="text-sm text-green-700">{stock.reasoning}</p>
                </div>
              </div>
            </div>

            {/* Key Levels */}
            <div className="mt-4 pt-4 border-t border-gray-100">
              <div className="flex flex-wrap gap-4 text-sm">
                <div>
                  <span className="text-gray-500">Support: </span>
                  <span className="font-medium">
                    {stock.levels.support.map(s => `₹${s.toLocaleString()}`).join(' | ')}
                  </span>
                </div>
                <div>
                  <span className="text-gray-500">Resistance: </span>
                  <span className="font-medium">
                    {stock.levels.resistance.map(r => `₹${r.toLocaleString()}`).join(' | ')}
                  </span>
                </div>
                <div>
                  <span className="text-gray-500">ATR: </span>
                  <span className="font-medium">₹{stock.levels.atr}</span>
                </div>
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* Disclaimer */}
      <div className="card bg-yellow-50 border-yellow-200">
        <div className="flex items-start gap-3">
          <AlertTriangle className="w-5 h-5 text-yellow-600 flex-shrink-0 mt-0.5" />
          <div className="text-sm text-yellow-800">
            <p className="font-medium mb-1">Disclaimer</p>
            <p>
              These are AI-generated recommendations for educational purposes only.
              Always do your own research before investing. Past performance does not
              guarantee future results. Consult a SEBI-registered advisor for personalized advice.
            </p>
          </div>
        </div>
      </div>
    </div>
  )
}
