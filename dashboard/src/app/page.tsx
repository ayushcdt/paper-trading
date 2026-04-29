import { getAnalysisData } from '@/lib/data'
import { LivePriceCard } from '@/components/LivePriceCard'
import { HomeNewsCard } from '@/components/HomeNewsCard'
import { RiskOverlayCard } from '@/components/RiskOverlayCard'
import {
  TrendingUp,
  TrendingDown,
  Activity,
  Target,
  AlertTriangle,
  Clock
} from 'lucide-react'

export const revalidate = 60 // Revalidate every 60 seconds

export default async function Dashboard() {
  const data = await getAnalysisData()
  const { market, stocks } = data

  const nifty = market.nifty
  const vix = market.vix
  const stance = market.stance

  // Format time
  const lastUpdated = new Date(data.generated_at).toLocaleString('en-IN', {
    dateStyle: 'medium',
    timeStyle: 'short'
  })

  return (
    <div className="space-y-8">
      {/* Header */}
      <div className="flex justify-between items-start">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Dashboard</h1>
          <p className="text-gray-500 mt-1">AI-powered market analysis and stock picks</p>
        </div>
        <div className="flex items-center gap-2 text-sm text-gray-500">
          <Clock className="w-4 h-4" />
          <span>Updated: {lastUpdated}</span>
        </div>
      </div>

      {/* Market Overview Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
        {/* Nifty 50 -- LIVE */}
        <LivePriceCard
          symbol="NIFTY"
          label="Nifty 50"
          fallbackLtp={nifty.value}
          trendBadge={
            <span className={`badge ${nifty.change_pct >= 0 ? 'badge-green' : 'badge-red'}`}>
              {nifty.trend}
            </span>
          }
          suffixLine={`Support: ${nifty.support[0]?.toLocaleString()} | Resistance: ${nifty.resistance[0]?.toLocaleString()}`}
        />

        {/* VIX -- LIVE */}
        <LivePriceCard
          symbol="INDIAVIX"
          label="India VIX"
          fallbackLtp={vix.value}
          suffixLine={
            <span className={`badge ${
              vix.value < 15 ? 'badge-green' :
              vix.value < 20 ? 'badge-yellow' : 'badge-red'
            }`}>
              {vix.interpretation.replace('_', ' ')}
            </span>
          }
        />


        {/* Market Stance */}
        <div className="card">
          <div className="flex items-center justify-between mb-3">
            <span className="text-sm font-medium text-gray-500">Market Stance</span>
            <Target className="w-5 h-5 text-gray-400" />
          </div>
          <div className="text-2xl font-bold text-gray-900">
            {stance.stance.replace('_', ' ')}
          </div>
          <div className="mt-3 text-sm text-gray-500">
            Recommended cash: {stance.cash_recommendation}%
          </div>
        </div>

        {/* Stock Picks Count */}
        <div className="card">
          <div className="flex items-center justify-between mb-3">
            <span className="text-sm font-medium text-gray-500">Stock Picks</span>
            <TrendingUp className="w-5 h-5 text-green-500" />
          </div>
          <div className="text-3xl font-bold text-gray-900">
            {stocks.picks.length}
          </div>
          <div className="mt-3 text-sm text-gray-500">
            {stocks.picks.filter(p => p.conviction === 'HIGH').length} high conviction
          </div>
        </div>
      </div>

      {/* Risk overlay status */}
      <RiskOverlayCard />

      {/* News flow card */}
      <HomeNewsCard />

      {/* Sectors */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Sector Leaders */}
        <div className="card">
          <h3 className="card-header flex items-center gap-2">
            <TrendingUp className="w-5 h-5 text-green-500" />
            Sector Leaders
          </h3>
          <div className="space-y-3">
            {market.sectors.leaders.map((sector, idx) => (
              <div key={idx} className="flex items-center justify-between py-2 border-b border-gray-50 last:border-0">
                <span className="font-medium text-gray-700">{sector.name}</span>
                <span className="text-green-600 font-medium">+{sector.return_1w}%</span>
              </div>
            ))}
          </div>
        </div>

        {/* Sector Laggards */}
        <div className="card">
          <h3 className="card-header flex items-center gap-2">
            <TrendingDown className="w-5 h-5 text-red-500" />
            Sector Laggards
          </h3>
          <div className="space-y-3">
            {market.sectors.laggards.map((sector, idx) => (
              <div key={idx} className="flex items-center justify-between py-2 border-b border-gray-50 last:border-0">
                <span className="font-medium text-gray-700">{sector.name}</span>
                <span className="text-red-600 font-medium">{sector.return_1w}%</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Market Outlook */}
      <div className="card">
        <h3 className="card-header">Market Outlook</h3>
        <p className="text-gray-600 leading-relaxed">{market.outlook}</p>
      </div>

      {/* Strategy */}
      <div className="card bg-gradient-to-r from-green-50 to-emerald-50 border-green-100">
        <h3 className="card-header flex items-center gap-2">
          <Target className="w-5 h-5 text-green-600" />
          Strategy Recommendation
        </h3>
        <p className="text-gray-700 leading-relaxed">{market.strategy}</p>
      </div>

      {/* Top 3 Stock Picks Preview */}
      <div className="card">
        <div className="flex items-center justify-between mb-6">
          <h3 className="text-lg font-semibold text-gray-900">Top Stock Picks</h3>
          <a href="/stocks" className="text-green-600 hover:text-green-700 text-sm font-medium">
            View all {stocks.picks.length} picks →
          </a>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {stocks.picks.slice(0, 3).map((stock) => (
            <div key={stock.symbol} className="p-4 bg-gray-50 rounded-lg">
              <div className="flex items-center justify-between mb-2">
                <span className="font-bold text-gray-900">{stock.symbol}</span>
                <span className={`badge ${
                  stock.conviction === 'HIGH' ? 'badge-green' :
                  stock.conviction === 'MEDIUM' ? 'badge-yellow' : 'badge-blue'
                }`}>
                  {stock.conviction}
                </span>
              </div>
              <div className="text-sm text-gray-500 mb-3">{stock.sector}</div>
              <div className="flex items-center justify-between text-sm">
                <span className="text-gray-600">CMP: ₹{stock.cmp.toLocaleString()}</span>
                <span className="text-green-600 font-medium">+{stock.upside_pct}%</span>
              </div>
              <div className="mt-2 text-sm">
                <span className="text-gray-500">Target: </span>
                <span className="font-medium">₹{stock.target.toLocaleString()}</span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
