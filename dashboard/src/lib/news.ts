/**
 * News block in the analysis blob (produced by backend/news/feed.py).
 * Source of truth for these shapes is generate_analysis.py's news_block dict.
 */

import { getAnalysisData } from './data'

export type ResultsKind = 'filed' | 'intimation'

export interface ResultsFiling {
  kind: ResultsKind
  company: string
  title: string
  source: string         // e.g. "NSE", "BSE - Board Meeting"
  published_at: string
  url: string
}

export interface LegalArticle {
  title: string
  source: string         // e.g. "LiveLaw", "SCC Online Blog"
  published_at: string
  url: string
  story_id?: number | null
  orgs?: string[]
}

export interface HotStory {
  story_id: number
  article_count: number
  sample_title: string
  orgs: string[]
}

export interface SymbolNews {
  c24: number
  c7d: number
  sentiment_24h: number
  sentiment_7d: number
  story_buzz_24h: number
}

export interface NewsBlock {
  status: 'ok' | 'unavailable' | 'cached' | 'skipped' | 'error'
  article_count?: number
  macro?: { counts_7d?: Record<string, number>; sentiment_7d?: Record<string, number> }
  symbol_mentions?: Record<string, SymbolNews>
  earnings_titles?: string[]
  today_results?: ResultsFiling[]
  pending_results?: ResultsFiling[]
  legal_today?: LegalArticle[]
  hot_stories?: HotStory[]
  error?: string
}

export async function getNewsBlock(): Promise<NewsBlock> {
  const data = await getAnalysisData() as any
  const n = (data?.news ?? {}) as NewsBlock
  return n
}

/** Keep only the filings whose company string contains the given symbol's
 *  name variants. Used by per-pick badges to count results-soon for a stock. */
export function filingsForSymbol(
  filings: ResultsFiling[] | undefined,
  symbolNames: string[],
): ResultsFiling[] {
  if (!filings || filings.length === 0) return []
  const lowered = symbolNames.map(n => n.toLowerCase())
  return filings.filter(f => {
    const hay = (f.company + ' ' + f.title).toLowerCase()
    return lowered.some(name => name.length >= 4 && hay.includes(name))
  })
}
