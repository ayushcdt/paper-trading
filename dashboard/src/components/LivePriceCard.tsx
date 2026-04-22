'use client'

import { useLiveTick } from './LiveDataProvider'
import { TrendingUp, TrendingDown } from 'lucide-react'
import { ReactNode } from 'react'

/**
 * A live-updating price card. Uses server-rendered fallback until live data arrives.
 * Pass through any extra children (e.g., sector leader/laggard badges, S/R levels).
 */
export function LivePriceCard({
  symbol,
  label,
  fallbackLtp,
  trendBadge,
  suffixLine,
}: {
  symbol: string
  label: string
  fallbackLtp: number
  trendBadge?: ReactNode
  suffixLine?: ReactNode
}) {
  const { ltp, changePct, isLive } = useLiveTick(symbol, fallbackLtp)
  const up = changePct >= 0

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-3">
        <span className="text-sm font-medium text-gray-500">
          {label}
          {isLive && <span className="ml-2 text-[10px] text-green-600 font-semibold">● LIVE</span>}
        </span>
        {trendBadge}
      </div>
      <div className="flex items-end gap-2">
        <span className="text-3xl font-bold text-gray-900 tabular-nums">
          {ltp.toLocaleString('en-IN', { maximumFractionDigits: 2 })}
        </span>
        <span className={`flex items-center text-sm font-medium ${up ? 'text-green-600' : 'text-red-600'}`}>
          {up ? <TrendingUp className="w-4 h-4 mr-1" /> : <TrendingDown className="w-4 h-4 mr-1" />}
          {up ? '+' : ''}
          {changePct.toFixed(2)}%
        </span>
      </div>
      {suffixLine && <div className="mt-3 text-sm text-gray-500">{suffixLine}</div>}
    </div>
  )
}
