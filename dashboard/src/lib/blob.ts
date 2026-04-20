import { Redis } from '@upstash/redis'

const redis = new Redis({
  url: process.env.KV_REST_API_URL || '',
  token: process.env.KV_REST_API_TOKEN || '',
})

export async function getBlob<T = unknown>(key: string): Promise<T | null> {
  try {
    const data = await redis.get<T>(`blob:${key}`)
    return data ?? null
  } catch {
    return null
  }
}
