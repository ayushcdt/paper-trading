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

export async function GET() {
  try {
    const data = await redis.get<LiveTicks>('blob:live_ticks')
    if (!data) {
      return NextResponse.json({ status: 'no_data', ticks: {} })
    }
    const age = (Date.now() - new Date(data.generated_at).getTime()) / 1000
    return NextResponse.json({
      ...data,
      age_seconds: Math.round(age),
      status: age < 30 ? 'live' : age < 300 ? 'stale' : 'down',
    })
  } catch {
    return NextResponse.json({ status: 'error', ticks: {} })
  }
}
