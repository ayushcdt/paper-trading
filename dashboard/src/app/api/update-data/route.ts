import { NextRequest, NextResponse } from 'next/server'
import { Redis } from '@upstash/redis'

const redis = new Redis({
  url: process.env.KV_REST_API_URL || '',
  token: process.env.KV_REST_API_TOKEN || '',
})

function unauthorized() {
  return NextResponse.json({ error: 'unauthorized' }, { status: 401 })
}

export async function POST(request: NextRequest) {
  const expected = process.env.UPDATE_SECRET
  if (!expected) {
    console.error('UPDATE_SECRET not configured on server')
    return NextResponse.json({ error: 'server misconfigured' }, { status: 500 })
  }

  const provided = request.headers.get('x-api-key')
  if (!provided || provided !== expected) {
    return unauthorized()
  }

  try {
    const data = await request.json()

    if (!data.generated_at || !data.market || !data.stocks) {
      return NextResponse.json({ error: 'Invalid data structure' }, { status: 400 })
    }

    await redis.set('analysis', data)
    await redis.set('last_updated', data.generated_at)

    return NextResponse.json({
      success: true,
      message: 'Data updated successfully',
      timestamp: data.generated_at,
    })
  } catch (error) {
    console.error('Error updating data:', error)
    return NextResponse.json({ error: 'Internal server error' }, { status: 500 })
  }
}

export async function GET() {
  try {
    const lastUpdated = await redis.get('last_updated')
    return NextResponse.json({
      status: 'ok',
      last_updated: lastUpdated || 'Never',
    })
  } catch {
    return NextResponse.json({
      status: 'ok',
      last_updated: 'Redis not configured',
    })
  }
}
