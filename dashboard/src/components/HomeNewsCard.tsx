import Link from 'next/link'
import { getNewsBlock } from '@/lib/news'
import { Newspaper, FileText, Calendar, Flame, Scale } from 'lucide-react'

export async function HomeNewsCard() {
  const news = await getNewsBlock()
  if (!news || news.status === 'skipped' || news.status === 'error') {
    return null
  }
  const today = news.today_results?.length ?? 0
  const pending = news.pending_results?.length ?? 0
  const legal = news.legal_today?.length ?? 0
  const hot = news.hot_stories?.length ?? 0

  return (
    <Link href="/news" className="card block hover:shadow-md transition">
      <div className="flex items-center justify-between mb-3">
        <h3 className="card-header flex items-center gap-2 mb-0">
          <Newspaper className="w-5 h-5 text-blue-500" />
          News flow
        </h3>
        <span className="text-xs text-gray-500">view all -&gt;</span>
      </div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Stat icon={<FileText className="w-4 h-4" />} label="Results filed today" value={today} accent="text-emerald-600" />
        <Stat icon={<Calendar className="w-4 h-4" />} label="Upcoming intimations" value={pending} accent="text-blue-600" />
        <Stat icon={<Scale className="w-4 h-4" />} label="Legal today" value={legal} accent={legal > 0 ? 'text-amber-600' : 'text-gray-500'} />
        <Stat icon={<Flame className="w-4 h-4" />} label="Hot stories" value={hot} accent={hot > 0 ? 'text-red-600' : 'text-gray-500'} />
      </div>
      {news.status === 'cached' && (
        <p className="text-xs text-gray-400 mt-3">Cached at {new Date((news as any).fetched_at ?? Date.now()).toLocaleTimeString('en-IN')}</p>
      )}
    </Link>
  )
}

function Stat({ icon, label, value, accent }: { icon: React.ReactNode; label: string; value: number; accent: string }) {
  return (
    <div className="bg-gray-50 rounded-lg p-3">
      <div className={`flex items-center gap-1 text-xs uppercase tracking-wide ${accent}`}>
        {icon}
        <span>{label}</span>
      </div>
      <div className="text-2xl font-bold text-gray-900 mt-1 tabular-nums">{value}</div>
    </div>
  )
}
