import { NextResponse } from 'next/server'
import { Redis } from '@upstash/redis'

export const revalidate = 0
export const dynamic = 'force-dynamic'

const redis = new Redis({
  url: process.env.KV_REST_API_URL || '',
  token: process.env.KV_REST_API_TOKEN || '',
})

/**
 * Unified live-data endpoint. Polled by LiveDataProvider every 10s when
 * market is open + tab is visible. Returns everything a "live-moving"
 * dashboard card might need in a single Redis round-trip.
 */
export async function GET() {
  try {
    const [ticksBlob, paperBlob] = await Promise.all([
      redis.get<{ generated_at?: string; ticks?: Record<string, any>; tick_count?: number }>('blob:live_ticks'),
      redis.get<any>('blob:paper_portfolio'),
    ])

    const now = Date.now()
    const tickAge = ticksBlob?.generated_at
      ? (now - new Date(ticksBlob.generated_at).getTime()) / 1000
      : 99999

    const status =
      tickAge < 30 ? 'live' :
      tickAge < 300 ? 'stale' :
      tickAge < 99999 ? 'down' :
      'idle'

    return NextResponse.json({
      ticks: ticksBlob?.ticks ?? {},
      tick_count: ticksBlob?.tick_count ?? 0,
      tick_generated_at: ticksBlob?.generated_at ?? null,
      paper: paperBlob ?? null,
      status,
      age_seconds: Math.round(tickAge),
    })
  } catch (e) {
    return NextResponse.json({ status: 'error', error: String(e) }, { status: 500 })
  }
}
