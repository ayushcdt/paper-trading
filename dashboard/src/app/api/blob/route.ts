import { NextRequest, NextResponse } from 'next/server'
import { Redis } from '@upstash/redis'

const redis = new Redis({
  url: process.env.KV_REST_API_URL || '',
  token: process.env.KV_REST_API_TOKEN || '',
})

const ALLOWED_KEYS = new Set([
  'paper_portfolio',
  'backtest_v1',
  'backtest_v2',
  'backtest_v3',
  'backtest_v3_oos',
  'variant_health',
  'variant_params',
  'target_state',
  'news',
  'alerts',
  'news_shadow_log',
  'live_ticks',
])

function unauthorized() {
  return NextResponse.json({ error: 'unauthorized' }, { status: 401 })
}

// POST /api/blob?key=paper_portfolio   body: any JSON  -> stores under blob:<key>
export async function POST(request: NextRequest) {
  const expected = process.env.UPDATE_SECRET
  if (!expected) return NextResponse.json({ error: 'server misconfigured' }, { status: 500 })
  if (request.headers.get('x-api-key') !== expected) return unauthorized()

  const key = new URL(request.url).searchParams.get('key') || ''
  if (!ALLOWED_KEYS.has(key)) {
    return NextResponse.json({ error: `key not allowed: ${key}` }, { status: 400 })
  }
  try {
    const body = await request.json()
    await redis.set(`blob:${key}`, body)
    return NextResponse.json({ success: true, key })
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 500 })
  }
}

// GET /api/blob?key=paper_portfolio -> returns stored JSON
export async function GET(request: NextRequest) {
  const key = new URL(request.url).searchParams.get('key') || ''
  if (!ALLOWED_KEYS.has(key)) {
    return NextResponse.json({ error: `key not allowed: ${key}` }, { status: 400 })
  }
  try {
    const data = await redis.get(`blob:${key}`)
    if (!data) return NextResponse.json({ error: 'not found' }, { status: 404 })
    return NextResponse.json(data)
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 500 })
  }
}
