import { getAnalysisData } from '@/lib/data'
import { Clock, AlertTriangle, XCircle, CheckCircle } from 'lucide-react'

function timeAgo(iso: string): { hours: number; label: string } {
  const then = new Date(iso).getTime()
  const now = Date.now()
  const ms = Math.max(0, now - then)
  const hours = ms / (1000 * 60 * 60)
  if (hours < 1) return { hours, label: `${Math.round(hours * 60)}m ago` }
  if (hours < 24) return { hours, label: `${Math.round(hours)}h ago` }
  return { hours, label: `${Math.round(hours / 24)}d ago` }
}

type Issue = { field: string; problem: string }

function auditDataQuality(data: Awaited<ReturnType<typeof getAnalysisData>>): Issue[] {
  const issues: Issue[] = []
  const m = data.market
  if (!m?.nifty?.value) issues.push({ field: 'Nifty', problem: 'value missing' })
  if (!m?.banknifty?.value) issues.push({ field: 'Bank Nifty', problem: 'value missing' })
  if (m?.banknifty && (m.banknifty.trend === 'INSUFFICIENT_DATA' || m.banknifty.trend === 'UNKNOWN')) {
    issues.push({ field: 'Bank Nifty', problem: 'trend not computed' })
  }
  if (!m?.vix?.value) issues.push({ field: 'VIX', problem: 'value missing' })
  if (!m?.sectors?.leaders?.length) issues.push({ field: 'Sectors', problem: 'no data' })

  const picks = data.stocks?.picks ?? []
  const degraded = picks.filter((p: any) => p.fundamentals_status === 'unavailable' || p.fundamentals_status === 'stale')
  if (degraded.length) {
    issues.push({
      field: 'Stock fundamentals',
      problem: `${degraded.length}/${picks.length} picks have stale or unavailable fundamentals`,
    })
  }
  return issues
}

export default async function FreshnessBanner() {
  const data = await getAnalysisData()
  const { hours, label } = timeAgo(data.generated_at)
  const issues = auditDataQuality(data)

  let level: 'fresh' | 'ok' | 'stale' | 'critical' = 'fresh'
  if (hours >= 48) level = 'critical'
  else if (hours >= 24) level = 'stale'
  else if (hours >= 6) level = 'ok'

  const styles = {
    fresh:    { bg: 'bg-green-50 border-green-200 text-green-800',   Icon: CheckCircle,    note: 'Data is fresh.' },
    ok:       { bg: 'bg-blue-50 border-blue-200 text-blue-800',      Icon: Clock,          note: 'Data is current.' },
    stale:    { bg: 'bg-yellow-50 border-yellow-300 text-yellow-900',Icon: AlertTriangle,  note: 'Data is stale -- re-run analysis before acting.' },
    critical: { bg: 'bg-red-50 border-red-300 text-red-800',         Icon: XCircle,        note: 'Data is very stale -- DO NOT trade on this.' },
  }[level]

  const { Icon } = styles

  return (
    <div className={`mb-6 rounded-lg border px-4 py-3 ${styles.bg}`}>
      <div className="flex items-start gap-3">
        <Icon className="w-5 h-5 mt-0.5 flex-shrink-0" />
        <div className="flex-1">
          <div className="flex items-center justify-between gap-3">
            <span className="font-medium text-sm">
              Last updated {label}
              <span className="ml-2 text-xs opacity-75">
                ({new Date(data.generated_at).toLocaleString('en-IN', { dateStyle: 'medium', timeStyle: 'short' })})
              </span>
            </span>
            <span className="text-xs uppercase tracking-wide font-semibold opacity-80">
              {level === 'critical' ? 'do not trade' : level}
            </span>
          </div>
          <p className="text-xs mt-1 opacity-90">{styles.note}</p>
          {issues.length > 0 && (
            <div className="mt-2 pt-2 border-t border-current/20">
              <p className="text-xs font-semibold mb-1">Data quality issues ({issues.length}):</p>
              <ul className="text-xs space-y-0.5 opacity-90">
                {issues.map((i, idx) => (
                  <li key={idx}>- {i.field}: {i.problem}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
