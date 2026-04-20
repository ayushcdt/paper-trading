import { getAnalysisData } from '@/lib/data'
import MacroSection from '@/components/MacroSection'
import {
  TrendingUp,
  TrendingDown,
  Activity,
  Target,
  BarChart3,
  CheckCircle,
  XCircle
} from 'lucide-react'

export const revalidate = 60

export default async function MarketPage() {
  const data = await getAnalysisData()
  const { market } = data
  const { nifty, banknifty, vix, sectors, stance } = market

  return (
    <div className="space-y-8">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Market Analysis</h1>
        <p className="text-gray-500 mt-1">
          Comprehensive market overview and trading strategy
        </p>
      </div>

      {/* Indices */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        {/* Nifty 50 */}
        <div className="card">
          <h3 className="text-sm font-medium text-gray-500 mb-2">Nifty 50</h3>
          <div className="flex items-end gap-3 mb-4">
            <span className="text-3xl font-bold">{nifty.value.toLocaleString('en-IN')}</span>
            <span className={`flex items-center text-sm font-medium pb-1 ${
              nifty.change_pct >= 0 ? 'text-green-600' : 'text-red-600'
            }`}>
              {nifty.change_pct >= 0 ? '+' : ''}{nifty.change_pct.toFixed(2)}%
            </span>
          </div>
          <div className={`inline-block badge ${
            nifty.trend.includes('BULLISH') ? 'badge-green' :
            nifty.trend.includes('BEARISH') ? 'badge-red' : 'badge-yellow'
          }`}>
            {nifty.trend}
          </div>
        </div>

        {/* Bank Nifty */}
        <div className="card">
          <h3 className="text-sm font-medium text-gray-500 mb-2">Bank Nifty</h3>
          <div className="flex items-end gap-3 mb-4">
            <span className="text-3xl font-bold">{banknifty.value.toLocaleString('en-IN')}</span>
            <span className={`flex items-center text-sm font-medium pb-1 ${
              banknifty.change_pct >= 0 ? 'text-green-600' : 'text-red-600'
            }`}>
              {banknifty.change_pct >= 0 ? '+' : ''}{banknifty.change_pct.toFixed(2)}%
            </span>
          </div>
          <div className={`inline-block badge ${
            banknifty.trend.includes('BULLISH') ? 'badge-green' :
            banknifty.trend.includes('BEARISH') ? 'badge-red' : 'badge-yellow'
          }`}>
            {banknifty.trend}
          </div>
        </div>

        {/* VIX */}
        <div className="card">
          <h3 className="text-sm font-medium text-gray-500 mb-2">India VIX</h3>
          <div className="flex items-end gap-3 mb-4">
            <span className="text-3xl font-bold">{vix.value.toFixed(2)}</span>
            <Activity className={`w-5 h-5 pb-1 ${
              vix.value < 15 ? 'text-green-500' :
              vix.value < 20 ? 'text-yellow-500' : 'text-red-500'
            }`} />
          </div>
          <p className="text-sm text-gray-600">{vix.message}</p>
        </div>
      </div>

      {/* Technical Status */}
      <div className="card">
        <h3 className="card-header">Nifty Technical Status</h3>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-6">
          <div className="flex items-center gap-3">
            {nifty.above_20ema ? (
              <CheckCircle className="w-6 h-6 text-green-500" />
            ) : (
              <XCircle className="w-6 h-6 text-red-500" />
            )}
            <div>
              <p className="font-medium">Above 20 EMA</p>
              <p className="text-sm text-gray-500">{nifty.above_20ema ? 'Yes' : 'No'}</p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            {nifty.above_50ema ? (
              <CheckCircle className="w-6 h-6 text-green-500" />
            ) : (
              <XCircle className="w-6 h-6 text-red-500" />
            )}
            <div>
              <p className="font-medium">Above 50 EMA</p>
              <p className="text-sm text-gray-500">{nifty.above_50ema ? 'Yes' : 'No'}</p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            {nifty.above_200ema ? (
              <CheckCircle className="w-6 h-6 text-green-500" />
            ) : (
              <XCircle className="w-6 h-6 text-red-500" />
            )}
            <div>
              <p className="font-medium">Above 200 EMA</p>
              <p className="text-sm text-gray-500">{nifty.above_200ema ? 'Yes' : 'No'}</p>
            </div>
          </div>
          <div>
            <p className="font-medium">RSI (14)</p>
            <p className={`text-lg font-bold ${
              nifty.rsi > 70 ? 'text-red-600' :
              nifty.rsi < 30 ? 'text-green-600' : 'text-gray-900'
            }`}>{nifty.rsi}</p>
          </div>
        </div>

        <div className="mt-6 pt-6 border-t border-gray-100">
          <div className="grid grid-cols-2 gap-6">
            <div>
              <h4 className="text-sm font-medium text-gray-500 mb-2">Support Levels</h4>
              <div className="flex gap-3">
                {nifty.support.map((level, idx) => (
                  <span key={idx} className="px-3 py-1 bg-green-100 text-green-800 rounded-lg font-medium">
                    {level.toLocaleString('en-IN')}
                  </span>
                ))}
              </div>
            </div>
            <div>
              <h4 className="text-sm font-medium text-gray-500 mb-2">Resistance Levels</h4>
              <div className="flex gap-3">
                {nifty.resistance.map((level, idx) => (
                  <span key={idx} className="px-3 py-1 bg-red-100 text-red-800 rounded-lg font-medium">
                    {level.toLocaleString('en-IN')}
                  </span>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Market Stance */}
      <div className={`card ${
        stance.stance.includes('BULLISH') ? 'bg-gradient-to-r from-green-50 to-emerald-50 border-green-200' :
        stance.stance.includes('BEARISH') ? 'bg-gradient-to-r from-red-50 to-orange-50 border-red-200' :
        'bg-gradient-to-r from-yellow-50 to-amber-50 border-yellow-200'
      }`}>
        <div className="flex items-center gap-4 mb-4">
          <div className={`w-12 h-12 rounded-xl flex items-center justify-center ${
            stance.stance.includes('BULLISH') ? 'bg-green-500' :
            stance.stance.includes('BEARISH') ? 'bg-red-500' : 'bg-yellow-500'
          }`}>
            <Target className="w-6 h-6 text-white" />
          </div>
          <div>
            <h3 className="text-xl font-bold text-gray-900">
              {stance.stance.replace(/_/g, ' ')}
            </h3>
            <p className="text-gray-600">Overall Market Stance</p>
          </div>
        </div>
        <div className="flex items-center gap-6">
          <div>
            <p className="text-sm text-gray-500">Stance Score</p>
            <p className="text-2xl font-bold">{stance.score}</p>
          </div>
          <div>
            <p className="text-sm text-gray-500">Recommended Cash</p>
            <p className="text-2xl font-bold">{stance.cash_recommendation}%</p>
          </div>
        </div>
      </div>

      {/* Sector Performance */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="card">
          <h3 className="card-header flex items-center gap-2">
            <TrendingUp className="w-5 h-5 text-green-500" />
            Sector Leaders (1 Week)
          </h3>
          <div className="space-y-4">
            {sectors.leaders.map((sector, idx) => (
              <div key={idx} className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <span className="w-8 h-8 bg-green-100 rounded-lg flex items-center justify-center text-green-700 font-bold text-sm">
                    {idx + 1}
                  </span>
                  <span className="font-medium">{sector.name}</span>
                </div>
                <div className="flex items-center gap-2">
                  <div className="w-24 h-2 bg-gray-200 rounded-full overflow-hidden">
                    <div
                      className="h-full bg-green-500 rounded-full"
                      style={{ width: `${Math.min(Math.abs(sector.return_1w) * 20, 100)}%` }}
                    ></div>
                  </div>
                  <span className="text-green-600 font-bold w-16 text-right">
                    +{sector.return_1w}%
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="card">
          <h3 className="card-header flex items-center gap-2">
            <TrendingDown className="w-5 h-5 text-red-500" />
            Sector Laggards (1 Week)
          </h3>
          <div className="space-y-4">
            {sectors.laggards.map((sector, idx) => (
              <div key={idx} className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <span className="w-8 h-8 bg-red-100 rounded-lg flex items-center justify-center text-red-700 font-bold text-sm">
                    {idx + 1}
                  </span>
                  <span className="font-medium">{sector.name}</span>
                </div>
                <div className="flex items-center gap-2">
                  <div className="w-24 h-2 bg-gray-200 rounded-full overflow-hidden">
                    <div
                      className="h-full bg-red-500 rounded-full"
                      style={{ width: `${Math.min(Math.abs(sector.return_1w) * 20, 100)}%` }}
                    ></div>
                  </div>
                  <span className="text-red-600 font-bold w-16 text-right">
                    {sector.return_1w}%
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Macro */}
      <MacroSection macro={market.macro} />

      {/* Outlook */}
      <div className="card">
        <h3 className="card-header flex items-center gap-2">
          <BarChart3 className="w-5 h-5 text-blue-500" />
          Market Outlook
        </h3>
        <p className="text-gray-700 leading-relaxed">{market.outlook}</p>
      </div>

      {/* Strategy */}
      <div className="card bg-gradient-to-r from-blue-50 to-indigo-50 border-blue-200">
        <h3 className="card-header flex items-center gap-2">
          <Target className="w-5 h-5 text-blue-600" />
          Trading Strategy
        </h3>
        <p className="text-gray-700 leading-relaxed">{market.strategy}</p>
      </div>
    </div>
  )
}
