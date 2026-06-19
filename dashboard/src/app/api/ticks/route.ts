import { NextResponse } from 'next/server'
import { Redis } from '@upstash/redis'

export const revalidate = 0
export const dynamic = 'force-dynamic'

const redis = new Redis({
  url: process.env.KV_REST_API_URL || '',
  token: process.env.KV_REST_API_TOKEN || '',
})

interface LiveTicks {
  generated_at: string
  tick_count: number
  ticks: Record<string, {
    ltp: number
    open?: number
    high?: number
    low?: number
    close?: number
    volume?: number
    received_at?: string
  }>
}

// NSE cash + F&O regular session is 09:15-15:30 IST, Mon-Fri.
// Outside this window we never expect fresh ticks, so we report
// `market_closed` instead of `down` (the WS is not actually broken).
function isMarketOpenIST(now: Date = new Date()): boolean {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'Asia/Kolkata',
    weekday: 'short',
    hour: 'numeric',
    minute: 'numeric',
    hour12: false,
  }).formatToParts(now)
  const weekday = parts.find(p => p.type === 'weekday')?.value || ''
  const hour = parseInt(parts.find(p => p.type === 'hour')?.value || '0', 10)
  const minute = parseInt(parts.find(p => p.type === 'minute')?.value || '0', 10)
  const isWeekday = !['Sat', 'Sun'].includes(weekday)
  const minutesOfDay = hour * 60 + minute
  return isWeekday && minutesOfDay >= 9 * 60 + 15 && minutesOfDay < 15 * 60 + 30
}

export async function GET() {
  try {
    const data = await redis.get<LiveTicks>('blob:live_ticks')
    if (!data) {
      return NextResponse.json({ status: 'no_data', ticks: {} })
    }
    const age = (Date.now() - new Date(data.generated_at).getTime()) / 1000
    const marketOpen = isMarketOpenIST()
    let status: 'live' | 'stale' | 'down' | 'market_closed'
    if (age < 30) {
      status = 'live'
    } else if (age < 300) {
      status = marketOpen ? 'stale' : 'market_closed'
    } else {
      status = marketOpen ? 'down' : 'market_closed'
    }
    return NextResponse.json({
      ...data,
      age_seconds: Math.round(age),
      market_open: marketOpen,
      status,
    })
  } catch {
    return NextResponse.json({ status: 'error', ticks: {} })
  }
}
