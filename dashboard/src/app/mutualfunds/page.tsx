import { getAnalysisData } from '@/lib/data'
import { PieChart, TrendingUp, Info, AlertTriangle } from 'lucide-react'

export const revalidate = 60

export default async function MutualFundsPage() {
  const data = await getAnalysisData()
  const { mutualfunds } = data

  // Calculate total allocation
  const allFunds = mutualfunds.recommendations.flatMap(cat => cat.funds)
  const totalAllocation = allFunds.reduce((sum, fund) => sum + fund.allocation_pct, 0)

  return (
    <div className="space-y-8">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Mutual Fund Recommendations</h1>
        <p className="text-gray-500 mt-1">
          Curated portfolio allocation based on long-term wealth building principles
        </p>
      </div>

      {/* Allocation Summary */}
      <div className="card">
        <h3 className="card-header flex items-center gap-2">
          <PieChart className="w-5 h-5 text-blue-500" />
          Suggested Allocation
        </h3>
        <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-4">
          {mutualfunds.recommendations.map((category, idx) => {
            const catAllocation = category.funds.reduce((sum, f) => sum + f.allocation_pct, 0)
            if (catAllocation === 0) return null
            return (
              <div key={idx} className="text-center p-4 bg-gray-50 rounded-lg">
                <div className="text-2xl font-bold text-gray-900">{catAllocation}%</div>
                <div className="text-sm text-gray-500">{category.category}</div>
              </div>
            )
          })}
        </div>
        <p className="mt-4 text-sm text-gray-500">
          {mutualfunds.allocation_note}
        </p>
      </div>

      {/* Fund Categories */}
      <div className="space-y-6">
        {mutualfunds.recommendations.map((category, catIdx) => (
          <div key={catIdx} className="card">
            <h3 className="card-header">{category.category}</h3>
            <div className="space-y-4">
              {category.funds.map((fund, fundIdx) => (
                <div
                  key={fundIdx}
                  className={`p-4 rounded-lg border ${
                    fund.recommendation === 'CORE_HOLDING' ? 'bg-green-50 border-green-200' :
                    fund.recommendation === 'HIGH_CONVICTION' ? 'bg-blue-50 border-blue-200' :
                    fund.recommendation === 'SATELLITE' ? 'bg-purple-50 border-purple-200' :
                    'bg-gray-50 border-gray-200'
                  }`}
                >
                  <div className="flex items-start justify-between mb-3">
                    <div>
                      <h4 className="font-semibold text-gray-900">{fund.name}</h4>
                      <p className="text-sm text-gray-500">{fund.amc}</p>
                    </div>
                    <div className="text-right">
                      {fund.allocation_pct > 0 && (
                        <div className="text-2xl font-bold text-gray-900">{fund.allocation_pct}%</div>
                      )}
                      <span className={`badge ${
                        fund.recommendation === 'CORE_HOLDING' ? 'badge-green' :
                        fund.recommendation === 'HIGH_CONVICTION' ? 'badge-blue' :
                        fund.recommendation === 'SATELLITE' ? 'bg-purple-100 text-purple-800' :
                        fund.recommendation === 'STABILITY' ? 'bg-gray-200 text-gray-800' :
                        'badge-yellow'
                      }`}>
                        {fund.recommendation.replace('_', ' ')}
                      </span>
                    </div>
                  </div>

                  <div className="flex items-center gap-4 mb-3 text-sm">
                    <div>
                      <span className="text-gray-500">Expense Ratio: </span>
                      <span className="font-medium">{fund.expense_ratio}%</span>
                    </div>
                  </div>

                  <div className="flex items-start gap-2 text-sm">
                    <Info className="w-4 h-4 text-gray-400 mt-0.5 flex-shrink-0" />
                    <p className="text-gray-600">{fund.reasoning}</p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>

      {/* Investment Tips */}
      <div className="card">
        <h3 className="card-header flex items-center gap-2">
          <TrendingUp className="w-5 h-5 text-green-500" />
          Investment Guidelines
        </h3>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          <div className="space-y-3">
            <h4 className="font-medium text-gray-900">Do's</h4>
            <ul className="space-y-2 text-sm text-gray-600">
              <li className="flex items-start gap-2">
                <span className="text-green-500 mt-1">✓</span>
                Always choose Direct plans (not Regular)
              </li>
              <li className="flex items-start gap-2">
                <span className="text-green-500 mt-1">✓</span>
                Start SIPs and stay consistent
              </li>
              <li className="flex items-start gap-2">
                <span className="text-green-500 mt-1">✓</span>
                Rebalance annually to target allocation
              </li>
              <li className="flex items-start gap-2">
                <span className="text-green-500 mt-1">✓</span>
                Stay invested for 7+ years minimum
              </li>
              <li className="flex items-start gap-2">
                <span className="text-green-500 mt-1">✓</span>
                Increase SIP amount annually (10-15%)
              </li>
            </ul>
          </div>
          <div className="space-y-3">
            <h4 className="font-medium text-gray-900">Don'ts</h4>
            <ul className="space-y-2 text-sm text-gray-600">
              <li className="flex items-start gap-2">
                <span className="text-red-500 mt-1">✗</span>
                Don't stop SIPs during market crashes
              </li>
              <li className="flex items-start gap-2">
                <span className="text-red-500 mt-1">✗</span>
                Don't chase past returns blindly
              </li>
              <li className="flex items-start gap-2">
                <span className="text-red-500 mt-1">✗</span>
                Don't over-diversify (5-7 funds max)
              </li>
              <li className="flex items-start gap-2">
                <span className="text-red-500 mt-1">✗</span>
                Don't time the market with lump sum
              </li>
              <li className="flex items-start gap-2">
                <span className="text-red-500 mt-1">✗</span>
                Don't invest without emergency fund
              </li>
            </ul>
          </div>
        </div>
      </div>

      {/* Disclaimer */}
      <div className="card bg-yellow-50 border-yellow-200">
        <div className="flex items-start gap-3">
          <AlertTriangle className="w-5 h-5 text-yellow-600 flex-shrink-0 mt-0.5" />
          <div className="text-sm text-yellow-800">
            <p className="font-medium mb-1">Disclaimer</p>
            <p>
              Mutual fund investments are subject to market risks. Read all scheme related
              documents carefully before investing. Past performance is not indicative of
              future returns. These are general recommendations and may not suit your
              specific financial situation. Consult a SEBI-registered investment advisor
              for personalized advice.
            </p>
          </div>
        </div>
      </div>
    </div>
  )
}
