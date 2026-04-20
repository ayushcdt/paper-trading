import { getBlob } from '@/lib/blob'
import { Bell, AlertTriangle, Info, AlertCircle } from 'lucide-react'

export const revalidate = 60

interface Alert {
  timestamp: string
  severity: 'info' | 'warning' | 'critical'
  message: string
}

export default async function AlertsPage() {
  const alerts = (await getBlob<Alert[]>('alerts')) ?? []
  const reversed = [...alerts].reverse()  // newest first

  const countBy = {
    critical: alerts.filter(a => a.severity === 'critical').length,
    warning:  alerts.filter(a => a.severity === 'warning').length,
    info:     alerts.filter(a => a.severity === 'info').length,
  }

  const sevStyle = {
    critical: { bg: 'bg-red-50 border-red-200',       Icon: AlertCircle,   color: 'text-red-700' },
    warning:  { bg: 'bg-yellow-50 border-yellow-200', Icon: AlertTriangle, color: 'text-yellow-700' },
    info:     { bg: 'bg-blue-50 border-blue-200',     Icon: Info,          color: 'text-blue-700' },
  }

  return (
    <div className="space-y-6">
      <div>
        <div className="flex items-center gap-2">
          <Bell className="w-6 h-6 text-indigo-600" />
          <h1 className="text-2xl font-bold text-gray-900">Alerts</h1>
        </div>
        <p className="text-gray-500 mt-1">
          System-generated notifications: regime changes, positions opened/closed, guardrails, bad news on held names.
          Last 100 events. Also delivered via Telegram if configured.
        </p>
      </div>

      <div className="grid grid-cols-3 gap-4">
        <div className="card">
          <div className="text-sm text-gray-500 mb-1 flex items-center gap-2">
            <AlertCircle className="w-4 h-4 text-red-600" />Critical
          </div>
          <div className="text-3xl font-bold text-red-700">{countBy.critical}</div>
        </div>
        <div className="card">
          <div className="text-sm text-gray-500 mb-1 flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 text-yellow-600" />Warnings
          </div>
          <div className="text-3xl font-bold text-yellow-700">{countBy.warning}</div>
        </div>
        <div className="card">
          <div className="text-sm text-gray-500 mb-1 flex items-center gap-2">
            <Info className="w-4 h-4 text-blue-600" />Info
          </div>
          <div className="text-3xl font-bold text-blue-700">{countBy.info}</div>
        </div>
      </div>

      {reversed.length === 0 ? (
        <div className="card bg-gray-50">
          <p className="text-gray-500 text-sm">No alerts yet. First ones will appear after the system detects a state change (regime shift, position opened, etc.).</p>
        </div>
      ) : (
        <div className="space-y-2">
          {reversed.map((a, i) => {
            const { bg, Icon, color } = sevStyle[a.severity]
            const lines = a.message.split(' | ')
            return (
              <div key={i} className={`card ${bg} py-3`}>
                <div className="flex items-start gap-3">
                  <Icon className={`w-5 h-5 mt-0.5 flex-shrink-0 ${color}`} />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center justify-between gap-2">
                      <span className={`font-medium text-sm ${color}`}>{lines[0]}</span>
                      <span className="text-xs text-gray-500 flex-shrink-0">
                        {new Date(a.timestamp).toLocaleString('en-IN')}
                      </span>
                    </div>
                    {lines.length > 1 && (
                      <p className="text-xs text-gray-600 mt-1">{lines.slice(1).join(' | ')}</p>
                    )}
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
