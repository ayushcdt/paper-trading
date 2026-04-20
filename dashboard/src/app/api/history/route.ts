import { NextRequest, NextResponse } from 'next/server'
import { Redis } from '@upstash/redis'

const redis = new Redis({
  url: process.env.KV_REST_API_URL || '',
  token: process.env.KV_REST_API_TOKEN || '',
})

const INDEX_KEY = 'history:index' // sorted list of date keys
const MAX_HISTORY_DAYS = 90

function unauthorized() {
  return NextResponse.json({ error: 'unauthorized' }, { status: 401 })
}

// POST: store a snapshot for a given date (called by the Python pipeline)
export async function POST(request: NextRequest) {
  const expected = process.env.UPDATE_SECRET
  if (!expected) {
    return NextResponse.json({ error: 'server misconfigured' }, { status: 500 })
  }
  if (request.headers.get('x-api-key') !== expected) {
    return unauthorized()
  }

  try {
    const { date, snapshot } = await request.json()
    if (!date || !snapshot) {
      return NextResponse.json({ error: 'missing date or snapshot' }, { status: 400 })
    }

    const key = `history:${date}`
    await redis.set(key, snapshot)

    // Maintain a sorted index of available dates
    const indexRaw = (await redis.get<string[]>(INDEX_KEY)) ?? []
    const index = Array.from(new Set([...indexRaw, date])).sort()
    // Keep only the last MAX_HISTORY_DAYS
    const trimmed = index.slice(-MAX_HISTORY_DAYS)
    const dropped = index.filter(d => !trimmed.includes(d))
    for (const d of dropped) {
      await redis.del(`history:${d}`)
    }
    await redis.set(INDEX_KEY, trimmed)

    return NextResponse.json({ success: true, stored: date, total_days: trimmed.length })
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 500 })
  }
}

// GET: list available dates and (optionally) fetch a specific snapshot
export async function GET(request: NextRequest) {
  const url = new URL(request.url)
  const date = url.searchParams.get('date')

  try {
    if (date) {
      const snap = await redis.get(`history:${date}`)
      if (!snap) return NextResponse.json({ error: 'not found' }, { status: 404 })
      return NextResponse.json({ date, snapshot: snap })
    }
    const index = (await redis.get<string[]>(INDEX_KEY)) ?? []
    return NextResponse.json({ dates: index })
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 500 })
  }
}
