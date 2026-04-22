/**
 * Per-symbol mini badge for /stocks pick rows.
 * Reads news block already loaded by the page (no extra fetch).
 *
 * Surfaces:
 *  - "Results soon" if there's a pending board-meeting intimation matching this symbol
 *  - "Results today" if a results filing landed today for this symbol
 *  - 24h sentiment color dot (green > +1, red < -1, neutral otherwise)
 *  - Story buzz count (number of distinct stories last 24h)
 */
import type { NewsBlock, ResultsFiling } from '@/lib/news'
import { Calendar, FileText, Activity } from 'lucide-react'

function matchesSymbol(filing: ResultsFiling, symbolToken: string): boolean {
  if (!symbolToken || symbolToken.length < 4) return false
  const tok = symbolToken.toLowerCase()
  const hay = ((filing.company || '') + ' ' + (filing.title || '')).toLowerCase()
  return hay.includes(tok)
}

export function NewsBadge({ symbol, news }: { symbol: string; news?: NewsBlock | null }) {
  if (!news) return null
  const tok = symbol.replace(/[-&].*$/, '')
  const filedToday = (news.today_results ?? []).find(f => matchesSymbol(f, tok))
  const intimation = (news.pending_results ?? []).find(f => matchesSymbol(f, tok))
  const sym = news.symbol_mentions?.[symbol]
  const sent = sym?.sentiment_24h ?? 0
  const buzz = sym?.story_buzz_24h ?? 0
  const c24 = sym?.c24 ?? 0

  if (!filedToday && !intimation && !sent && !buzz && !c24) return null

  return (
    <div className="flex items-center gap-2 flex-wrap text-xs mt-2">
      {filedToday && (
        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-200">
          <FileText className="w-3 h-3" />
          Results filed today
        </span>
      )}
      {!filedToday && intimation && (
        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-blue-50 text-blue-700 border border-blue-200">
          <Calendar className="w-3 h-3" />
          Results expected soon
        </span>
      )}
      {(c24 > 0 || sent !== 0) && (
        <span
          className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full border ${
            sent <= -1.5 ? 'bg-red-50 text-red-700 border-red-200'
            : sent >=  1.5 ? 'bg-green-50 text-green-700 border-green-200'
            : 'bg-gray-50 text-gray-600 border-gray-200'
          }`}
          title="Decay-weighted Loughran-McDonald sentiment over last 24h"
        >
          <Activity className="w-3 h-3" />
          {c24} arts · sent {sent >= 0 ? '+' : ''}{sent.toFixed(1)}
        </span>
      )}
      {buzz > 0 && (
        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-amber-50 text-amber-700 border border-amber-200" title="Distinct story clusters mentioning this symbol last 24h">
          buzz {buzz}
        </span>
      )}
    </div>
  )
}
