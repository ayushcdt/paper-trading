import { getNewsBlock } from '@/lib/news'
import type { ResultsFiling, LegalArticle, HotStory } from '@/lib/news'
import { FileText, Calendar, Flame, Scale, ExternalLink, AlertTriangle } from 'lucide-react'

export const revalidate = 60

export default async function NewsPage() {
  const news = await getNewsBlock()

  const today = news.today_results ?? []
  const pending = news.pending_results ?? []
  const legal = news.legal_today ?? []
  const hot = news.hot_stories ?? []

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">News flow</h1>
        <p className="text-gray-500 mt-1">
          NSE/BSE corporate filings, regulatory orders, and clustered story buzz
          from newsapp. Article count last fetch: {news.article_count ?? 0}
          {news.status === 'cached' && ' (cached)'}.
        </p>
      </div>

      {news.status === 'unavailable' || news.status === 'error' ? (
        <div className="card border-red-200 bg-red-50">
          <div className="flex items-center gap-2 text-red-800">
            <AlertTriangle className="w-5 h-5" />
            <span className="font-medium">News pipeline unavailable</span>
          </div>
          <p className="text-sm text-red-700 mt-1">
            {news.error ?? 'Backend reported newsapp Supabase fetch failed; trading still works without news enrichment.'}
          </p>
        </div>
      ) : null}

      {/* Today's results filings */}
      <Section icon={<FileText className="w-5 h-5 text-emerald-600" />} title={`Results filed today (${today.length})`}>
        {today.length === 0 ? (
          <Empty hint="No company has filed results yet today. NSE/BSE filings typically arrive 11:00-19:00 IST." />
        ) : (
          <FilingTable filings={today} />
        )}
      </Section>

      {/* Upcoming results (intimations) */}
      <Section icon={<Calendar className="w-5 h-5 text-blue-600" />} title={`Upcoming results — board meeting intimations (${pending.length})`}>
        {pending.length === 0 ? (
          <Empty hint="No board-meeting intimations in the last 7 days." />
        ) : (
          <FilingTable filings={pending} />
        )}
      </Section>

      {/* Legal today */}
      <Section icon={<Scale className="w-5 h-5 text-amber-600" />} title={`Legal / regulatory today (${legal.length})`}>
        {legal.length === 0 ? (
          <Empty hint="No SEBI / court orders or legal news published today." />
        ) : (
          <LegalTable items={legal} />
        )}
      </Section>

      {/* Hot stories */}
      <Section icon={<Flame className="w-5 h-5 text-red-500" />} title={`Hot stories last 24h — ≥3 articles each (${hot.length})`}>
        {hot.length === 0 ? (
          <Empty hint="No story has reached 3+ articles in the last 24 hours yet." />
        ) : (
          <StoryList stories={hot} />
        )}
      </Section>
    </div>
  )
}

function Section({ icon, title, children }: { icon: React.ReactNode; title: string; children: React.ReactNode }) {
  return (
    <div className="card">
      <h3 className="card-header flex items-center gap-2">
        {icon}
        {title}
      </h3>
      {children}
    </div>
  )
}

function Empty({ hint }: { hint: string }) {
  return <p className="text-sm text-gray-500">{hint}</p>
}

function FilingTable({ filings }: { filings: ResultsFiling[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="text-xs uppercase text-gray-500 border-b border-gray-200">
          <tr>
            <th className="text-left py-2">Time (IST)</th>
            <th className="text-left">Source</th>
            <th className="text-left">Company</th>
            <th className="text-left">Subject</th>
            <th className="text-right">Link</th>
          </tr>
        </thead>
        <tbody>
          {filings.map((f, i) => {
            const istTime = istTimeFromIso(f.published_at)
            return (
              <tr key={i} className="border-b border-gray-50">
                <td className="py-2 text-gray-500 tabular-nums">{istTime}</td>
                <td className="text-gray-600 text-xs">{f.source}</td>
                <td className="font-medium text-gray-900">{f.company || '—'}</td>
                <td className="text-gray-700">{stripCompanyPrefix(f.title, f.company)}</td>
                <td className="text-right">
                  {f.url ? (
                    <a href={f.url} target="_blank" rel="noopener noreferrer" className="text-blue-600 hover:text-blue-700 inline-flex items-center gap-1">
                      <ExternalLink className="w-3 h-3" />
                    </a>
                  ) : null}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function LegalTable({ items }: { items: LegalArticle[] }) {
  return (
    <ul className="divide-y divide-gray-100">
      {items.map((a, i) => (
        <li key={i} className="py-3 flex items-start gap-3">
          <span className="text-xs text-gray-500 w-16 flex-shrink-0 tabular-nums">{istTimeFromIso(a.published_at)}</span>
          <div className="flex-1">
            <a href={a.url} target="_blank" rel="noopener noreferrer" className="text-sm font-medium text-gray-900 hover:text-blue-700">
              {a.title}
            </a>
            <div className="text-xs text-gray-500 mt-0.5">
              {a.source}
              {(a.orgs?.length ?? 0) > 0 && (
                <span className="ml-2">· orgs: {a.orgs!.slice(0, 4).join(', ')}</span>
              )}
            </div>
          </div>
        </li>
      ))}
    </ul>
  )
}

function StoryList({ stories }: { stories: HotStory[] }) {
  return (
    <ul className="divide-y divide-gray-100">
      {stories.map((s, i) => (
        <li key={i} className="py-3 flex items-start gap-3">
          <span className="badge badge-red flex-shrink-0">{s.article_count} arts</span>
          <div className="flex-1">
            <p className="text-sm font-medium text-gray-900">{s.sample_title || `Story #${s.story_id}`}</p>
            {s.orgs && s.orgs.length > 0 && (
              <p className="text-xs text-gray-500 mt-0.5">orgs: {s.orgs.slice(0, 5).join(', ')}</p>
            )}
          </div>
        </li>
      ))}
    </ul>
  )
}

function istTimeFromIso(iso: string): string {
  if (!iso) return ''
  try {
    const d = new Date(iso)
    // Display in IST regardless of viewer timezone
    const istMs = d.getTime() + 330 * 60 * 1000
    const ist = new Date(istMs)
    const h = String(ist.getUTCHours()).padStart(2, '0')
    const m = String(ist.getUTCMinutes()).padStart(2, '0')
    return `${h}:${m}`
  } catch {
    return ''
  }
}

function stripCompanyPrefix(title: string, company: string): string {
  if (!title) return ''
  if (!company) return title
  const sep = title.includes(' ? ') ? ' ? ' : title.includes(' — ') ? ' — ' : title.includes(' - ') ? ' - ' : null
  if (!sep) return title
  const idx = title.indexOf(sep)
  return title.slice(idx + sep.length).trim()
}
