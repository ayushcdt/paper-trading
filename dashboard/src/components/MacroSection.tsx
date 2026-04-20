import { Globe, AlertTriangle, TrendingUp, TrendingDown } from 'lucide-react'
import type { MarketData } from '@/lib/data'

function Indicator({
  label,
  value,
  unit,
  changePct,
  trend30d,
  interpretation,
  status,
}: {
  label: string
  value?: number
  unit?: string
  changePct?: number
  trend30d?: number
  interpretation?: string
  status: 'ok' | 'unavailable'
}) {
  if (status !== 'ok' || value == null) {
    return (
      <div className="p-4 bg-gray-50 rounded-lg border border-gray-200">
        <div className="flex items-center justify-between mb-1">
          <span className="text-sm font-medium text-gray-500">{label}</span>
          <AlertTriangle className="w-4 h-4 text-yellow-500" />
        </div>
        <p className="text-xs text-gray-500">Data unavailable</p>
      </div>
    )
  }

  const up = (changePct ?? 0) > 0
  const trendUp = (trend30d ?? 0) > 0

  return (
    <div className="p-4 bg-gray-50 rounded-lg">
      <div className="flex items-center justify-between mb-1">
        <span className="text-sm font-medium text-gray-500">{label}</span>
        <span className="text-xs text-gray-400">{unit}</span>
      </div>
      <div className="flex items-baseline gap-2">
        <span className="text-2xl font-bold text-gray-900">
          {value.toLocaleString('en-IN', { maximumFractionDigits: 2 })}
        </span>
        <span className={`text-sm font-medium ${up ? 'text-red-600' : 'text-green-600'}`}>
          {up ? '+' : ''}
          {changePct?.toFixed(2)}%
        </span>
      </div>
      <div className="mt-2 flex items-center gap-1 text-xs">
        {trendUp ? (
          <TrendingUp className="w-3 h-3 text-red-500" />
        ) : (
          <TrendingDown className="w-3 h-3 text-green-500" />
        )}
        <span className="text-gray-500">
          30d: {trendUp ? '+' : ''}
          {trend30d?.toFixed(1)}%
        </span>
      </div>
      {interpretation && (
        <p className="mt-2 text-xs text-gray-600 leading-snug">{interpretation}</p>
      )}
    </div>
  )
}

export default function MacroSection({ macro }: { macro?: MarketData['macro'] }) {
  if (!macro) {
    return (
      <div className="card">
        <h3 className="card-header flex items-center gap-2">
          <Globe className="w-5 h-5 text-blue-500" />
          Macro Context
        </h3>
        <p className="text-sm text-gray-500">Macro analysis not available in this run.</p>
      </div>
    )
  }

  const fd = macro.fii_dii
  const fii = fd.fii_net_cr ?? 0
  const dii = fd.dii_net_cr ?? 0

  return (
    <div className="card">
      <h3 className="card-header flex items-center justify-between">
        <span className="flex items-center gap-2">
          <Globe className="w-5 h-5 text-blue-500" />
          Macro Context
        </span>
        {macro.from_cache && (
          <span className="text-xs text-gray-400">cached</span>
        )}
      </h3>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
        <Indicator
          label="USD/INR"
          value={macro.usd_inr.value}
          unit="₹/$"
          changePct={macro.usd_inr.change_pct}
          trend30d={macro.usd_inr.trend_30d_pct}
          interpretation={macro.usd_inr.interpretation}
          status={macro.usd_inr.status}
        />
        <Indicator
          label="US 10-Year Yield"
          value={macro.us_10y.value}
          unit={macro.us_10y.unit}
          changePct={macro.us_10y.change_pct}
          trend30d={macro.us_10y.trend_30d_pct}
          interpretation={macro.us_10y.interpretation}
          status={macro.us_10y.status}
        />
        <Indicator
          label="Brent Crude"
          value={macro.brent_crude.value}
          unit={macro.brent_crude.unit}
          changePct={macro.brent_crude.change_pct}
          trend30d={macro.brent_crude.trend_30d_pct}
          interpretation={macro.brent_crude.interpretation}
          status={macro.brent_crude.status}
        />
      </div>

      <div className="border-t border-gray-100 pt-4">
        <div className="flex items-center justify-between mb-3">
          <h4 className="text-sm font-medium text-gray-700">FII / DII Net Flows (₹ Cr)</h4>
          {fd.status === 'ok' && fd.date && (
            <span className="text-xs text-gray-400">{fd.date}</span>
          )}
        </div>
        {fd.status !== 'ok' ? (
          <div className="flex items-center gap-2 text-sm text-yellow-700">
            <AlertTriangle className="w-4 h-4" />
            FII/DII data unavailable (NSE blocks scrapers).
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-4">
            <div className={`p-3 rounded-lg ${fii >= 0 ? 'bg-green-50' : 'bg-red-50'}`}>
              <p className="text-xs text-gray-500">FII Net</p>
              <p className={`text-xl font-bold ${fii >= 0 ? 'text-green-700' : 'text-red-700'}`}>
                {fii >= 0 ? '+' : ''}
                {fii.toLocaleString('en-IN')}
              </p>
            </div>
            <div className={`p-3 rounded-lg ${dii >= 0 ? 'bg-green-50' : 'bg-red-50'}`}>
              <p className="text-xs text-gray-500">DII Net</p>
              <p className={`text-xl font-bold ${dii >= 0 ? 'text-green-700' : 'text-red-700'}`}>
                {dii >= 0 ? '+' : ''}
                {dii.toLocaleString('en-IN')}
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
