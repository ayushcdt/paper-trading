'use client'

import { useEffect, useState } from 'react'
import { useLiveData } from './LiveDataProvider'

/**
 * Tiny badge showing provider state. Visible on every page.
 * If something breaks, user can read this badge verbatim -- no screenshots needed.
 */
export function LiveDebugBadge() {
  const live = useLiveData()
  const [secSinceFetch, setSec] = useState(0)

  useEffect(() => {
    if (!live.last_fetched) return
    const id = setInterval(() => {
      setSec(Math.round((Date.now() - live.last_fetched) / 1000))
    }, 1000)
    return () => clearInterval(id)
  }, [live.last_fetched])

  const tickCount = Object.keys(live.ticks).length
  const paperHas = !!live.paper
  const fetchedAgo = live.last_fetched ? `${secSinceFetch}s` : 'never'

  return (
    <div className="text-[10px] text-gray-500 bg-gray-50 rounded px-2 py-1 font-mono whitespace-nowrap">
      Provider: ticks={tickCount} paper={paperHas ? 'Y' : 'N'} last={fetchedAgo} status={live.status}
    </div>
  )
}
