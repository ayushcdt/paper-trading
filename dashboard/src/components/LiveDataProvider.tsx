'use client'

import { createContext, useContext, useEffect, useState, ReactNode } from 'react'
import type { PaperSnapshot } from '@/lib/paper'

export type { PaperSnapshot, PaperPosition, PaperTrade } from '@/lib/paper'

export interface Tick {
  ltp: number
  open?: number
  high?: number
  low?: number
  close?: number
  volume?: number
  received_at?: string
}

export interface LiveData {
  ticks: Record<string, Tick>
  paper: PaperSnapshot | null
  status: 'live' | 'stale' | 'down' | 'idle' | 'error'
  age_seconds: number
  last_fetched: number
}

const EMPTY: LiveData = {
  ticks: {},
  paper: null,
  status: 'idle',
  age_seconds: 0,
  last_fetched: 0,
}

const LiveDataContext = createContext<LiveData>(EMPTY)

const POLL_INTERVAL_MS = 10_000

// Mirrors backend/common/market_hours.py — keep in sync.
// NSE: Mon-Fri, 09:15-15:30 IST.
function isMarketOpen(): boolean {
  const now = new Date()
  // Shift UTC -> IST (UTC+5:30) by adding 330 min, then read day/time from that.
  const istMs = now.getTime() + 330 * 60 * 1000
  const ist = new Date(istMs)
  const day = ist.getUTCDay()  // 0=Sun, 6=Sat
  if (day === 0 || day === 6) return false
  const minutes = ist.getUTCHours() * 60 + ist.getUTCMinutes()
  return minutes >= 9 * 60 + 15 && minutes <= 15 * 60 + 30
}

export function LiveDataProvider({ children }: { children: ReactNode }) {
  const [data, setData] = useState<LiveData>(EMPTY)

  useEffect(() => {
    let cancelled = false

    const fetchLive = async () => {
      if (cancelled) return
      const visible = typeof document === 'undefined' || document.visibilityState === 'visible'
      const market = isMarketOpen()
      if (!visible) {
        console.log('[LiveData] skip: tab hidden')
        return
      }
      if (!market) {
        console.log('[LiveData] skip: market closed (IST check)')
        return
      }

      try {
        const r = await fetch('/api/live', { cache: 'no-store' })
        if (!r.ok) {
          console.warn('[LiveData] fetch not ok:', r.status)
          return
        }
        const j = await r.json()
        if (!cancelled) {
          const tickKeys = Object.keys(j.ticks ?? {})
          console.log(`[LiveData] fetched ${tickKeys.length} ticks, paper=${!!j.paper}, status=${j.status}, age=${j.age_seconds}s`)
          setData({
            ticks: j.ticks ?? {},
            paper: j.paper ?? null,
            status: j.status ?? 'idle',
            age_seconds: j.age_seconds ?? 0,
            last_fetched: Date.now(),
          })
        }
      } catch (e) {
        console.warn('[LiveData] fetch error:', e)
      }
    }

    fetchLive()
    const id = setInterval(fetchLive, POLL_INTERVAL_MS)
    // Refetch whenever tab becomes visible
    const onVis = () => fetchLive()
    document.addEventListener('visibilitychange', onVis)

    return () => {
      cancelled = true
      clearInterval(id)
      document.removeEventListener('visibilitychange', onVis)
    }
  }, [])

  return <LiveDataContext.Provider value={data}>{children}</LiveDataContext.Provider>
}

export function useLiveData() {
  return useContext(LiveDataContext)
}

/**
 * Helper: get live LTP for a symbol with fallback to a server-provided value.
 * Returns { ltp, changePct } computed from close if available.
 */
export function useLiveTick(symbol: string, fallbackLtp?: number) {
  const live = useLiveData()
  const t = live.ticks[symbol]
  if (!t?.ltp) {
    return { ltp: fallbackLtp ?? 0, changePct: 0, isLive: false }
  }
  const prev = t.close ?? t.open ?? t.ltp
  const changePct = prev ? ((t.ltp - prev) / prev) * 100 : 0
  return { ltp: t.ltp, changePct, isLive: live.status === 'live' }
}
