'use client'

import { useEffect, useState } from 'react'
import { Radio, Circle, AlertTriangle } from 'lucide-react'

interface Tick {
  ltp: number
  open?: number
  high?: number
  low?: number
  close?: number
  volume?: number
  received_at?: string
}

interface TickPayload {
  status: 'live' | 'stale' | 'down' | 'no_data' | 'error'
  age_seconds?: number
  tick_count?: number
  ticks?: Record<string, Tick>
  generated_at?: string
}

const POLL_INTERVAL_MS = 5000  // 5 sec client-side poll

export default function LiveTickBadge({ symbols = ['NIFTY', 'BANKNIFTY', 'INDIAVIX'] as string[] }: { symbols?: string[] }) {
  const [data, setData] = useState<TickPayload | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let alive = true
    const fetchTicks = async () => {
      try {
        const r = await fetch('/api/ticks', { cache: 'no-store' })
        const j = await r.json()
        if (alive) {
          setData(j)
          setLoading(false)
        }
      } catch {
        if (alive) setLoading(false)
      }
    }
    fetchTicks()
    const id = setInterval(fetchTicks, POLL_INTERVAL_MS)
    return () => {
      alive = false
      clearInterval(id)
    }
  }, [])

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-xs text-gray-400">
        <Circle className="w-3 h-3 animate-pulse" /> connecting...
      </div>
    )
  }

  const status = data?.status ?? 'no_data'
  const age = data?.age_seconds ?? 0
  const ticks = data?.ticks ?? {}

  const indicator = {
    live:    { cls: 'text-green-600',  label: 'LIVE',  pulse: true  },
    stale:   { cls: 'text-yellow-600', label: 'STALE', pulse: false },
    down:    { cls: 'text-red-600',    label: 'DOWN',  pulse: false },
    no_data: { cls: 'text-gray-400',   label: 'IDLE',  pulse: false },
    error:   { cls: 'text-red-600',    label: 'ERROR', pulse: false },
  }[status]

  return (
    <div className="flex flex-wrap items-center gap-3 bg-white rounded-lg border border-gray-200 px-3 py-2 text-xs">
      <div className={`flex items-center gap-1.5 ${indicator.cls} font-semibold`}>
        <Radio className={`w-3.5 h-3.5 ${indicator.pulse ? 'animate-pulse' : ''}`} />
        {indicator.label}
        <span className="text-gray-400 font-normal">
          {status === 'live' ? `${age}s` : status === 'stale' ? `${Math.round(age/60)}m` : ''}
        </span>
      </div>

      {symbols.map(sym => {
        const t = ticks[sym]
        if (!t || !t.ltp) {
          return (
            <div key={sym} className="flex items-center gap-1 text-gray-400">
              <span className="font-medium">{sym}</span>
              <span>—</span>
            </div>
          )
        }
        const prev = t.close ?? t.open ?? t.ltp
        const changePct = prev ? ((t.ltp - prev) / prev) * 100 : 0
        const up = changePct >= 0
        return (
          <div key={sym} className="flex items-center gap-1">
            <span className="font-medium text-gray-600">{sym === 'INDIAVIX' ? 'VIX' : sym}</span>
            <span className="font-bold text-gray-900">{t.ltp.toLocaleString('en-IN', { maximumFractionDigits: 2 })}</span>
            <span className={up ? 'text-green-600' : 'text-red-600'}>
              {up ? '+' : ''}{changePct.toFixed(2)}%
            </span>
          </div>
        )
      })}

      {status === 'down' && (
        <div className="flex items-center gap-1 text-red-600">
          <AlertTriangle className="w-3 h-3" />
          WebSocket down — showing last cached data
        </div>
      )}
    </div>
  )
}
