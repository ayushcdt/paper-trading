import { Redis } from '@upstash/redis'
import type { AnalysisData } from './data'

const redis = new Redis({
  url: process.env.KV_REST_API_URL || '',
  token: process.env.KV_REST_API_TOKEN || '',
})

const INDEX_KEY = 'history:index'

export interface HistoryEntry {
  date: string
  stance: string
  score: number
  nifty: number
  picks: { symbol: string; cmp: number; conviction: string }[]
}

export async function listHistoryDates(limit = 30): Promise<string[]> {
  try {
    const dates = (await redis.get<string[]>(INDEX_KEY)) ?? []
    return dates.slice(-limit).reverse()
  } catch {
    return []
  }
}

export async function getHistorySnapshot(date: string): Promise<AnalysisData | null> {
  try {
    return (await redis.get<AnalysisData>(`history:${date}`)) ?? null
  } catch {
    return null
  }
}

/**
 * Build a compact timeline of stance + headline numbers for a list of dates.
 */
export async function buildHistoryTimeline(limit = 30): Promise<HistoryEntry[]> {
  const dates = await listHistoryDates(limit)
  const snaps = await Promise.all(dates.map(d => getHistorySnapshot(d)))
  const out: HistoryEntry[] = []
  for (let i = 0; i < dates.length; i++) {
    const snap = snaps[i]
    if (!snap) continue
    out.push({
      date: dates[i],
      stance: snap.market?.stance?.stance ?? 'UNKNOWN',
      score: snap.market?.stance?.score ?? 0,
      nifty: snap.market?.nifty?.value ?? 0,
      picks: (snap.stocks?.picks ?? []).slice(0, 5).map(p => ({
        symbol: p.symbol,
        cmp: p.cmp,
        conviction: p.conviction,
      })),
    })
  }
  return out
}

/**
 * For each historical pick, look at what its CMP was on the next available
 * snapshot to compute realized return. Filters to picks made >=1 day ago.
 */
export interface PickPerformance {
  symbol: string
  pick_date: string
  entry_cmp: number
  current_cmp: number | null
  return_pct: number | null
  conviction: string
}

export async function buildPickPerformance(latestSnapshot: AnalysisData, lookbackDays = 30): Promise<PickPerformance[]> {
  const dates = await listHistoryDates(lookbackDays)
  if (!dates.length) return []

  // Build a map of current prices from the freshest snapshot
  const currentPrices = new Map<string, number>()
  for (const p of latestSnapshot.stocks?.picks ?? []) {
    currentPrices.set(p.symbol, p.cmp)
  }

  const seen = new Set<string>()
  const out: PickPerformance[] = []
  // Walk oldest -> newest so the FIRST appearance of a symbol is its entry
  for (const date of [...dates].reverse()) {
    const snap = await getHistorySnapshot(date)
    if (!snap) continue
    for (const p of snap.stocks?.picks ?? []) {
      if (seen.has(p.symbol)) continue
      seen.add(p.symbol)
      const current = currentPrices.get(p.symbol) ?? null
      out.push({
        symbol: p.symbol,
        pick_date: date,
        entry_cmp: p.cmp,
        current_cmp: current,
        return_pct: current != null && p.cmp ? ((current - p.cmp) / p.cmp) * 100 : null,
        conviction: p.conviction,
      })
    }
  }
  return out.sort((a, b) => (b.return_pct ?? -999) - (a.return_pct ?? -999))
}
