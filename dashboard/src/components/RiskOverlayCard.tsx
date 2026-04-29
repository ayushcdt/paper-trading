import { getAnalysisData } from '@/lib/data'
import { Shield, AlertOctagon, ShieldOff, ShieldCheck } from 'lucide-react'

export async function RiskOverlayCard() {
  const data = (await getAnalysisData()) as any
  const ro = data?.stocks?.risk_overlay
  const killActive = data?.stocks?.kill_switch_active
  const killReason = data?.stocks?.kill_switch_reason
  if (!ro) return null

  const dd = ro.current_dd_pct ?? 0
  const haltActive = ro.halt_active || ro.tail_halt
  const tailHalt = ro.tail_halt
  const vixMult = ro.vix_size_multiplier ?? 1.0

  let statusClass = 'bg-emerald-50 border-emerald-200 text-emerald-800'
  let StatusIcon = ShieldCheck
  let statusText = 'Risk overlay healthy'
  if (tailHalt) {
    statusClass = 'bg-red-100 border-red-400 text-red-900'
    StatusIcon = AlertOctagon
    statusText = 'TAIL HALT — manual reset required'
  } else if (haltActive) {
    statusClass = 'bg-amber-50 border-amber-300 text-amber-900'
    StatusIcon = ShieldOff
    statusText = 'DD circuit breaker engaged'
  } else if (vixMult < 1.0) {
    statusClass = 'bg-yellow-50 border-yellow-300 text-yellow-900'
    StatusIcon = Shield
    statusText = `VIX gate: positions sized at ${(vixMult * 100).toFixed(0)}%`
  }

  return (
    <div className={`card ${statusClass}`}>
      <h3 className="card-header flex items-center gap-2 mb-3">
        <StatusIcon className="w-5 h-5" />
        Risk overlay
        <span className="ml-auto text-xs font-normal opacity-80">{statusText}</span>
      </h3>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
        <Stat label="Current DD" value={`${dd >= 0 ? '+' : ''}${dd.toFixed(2)}%`} accent={dd <= -8 ? 'text-red-700' : dd <= -4 ? 'text-amber-700' : 'text-gray-900'} />
        <Stat label="Peak equity" value={`Rs ${Math.round(ro.peak_equity ?? 0).toLocaleString('en-IN')}`} />
        <Stat label="Current equity" value={`Rs ${Math.round(ro.current_equity ?? 0).toLocaleString('en-IN')}`} />
        <Stat label="Picks (raw -> final)" value={`${ro.raw_pick_count ?? 0} -> ${ro.final_pick_count ?? 0}`} note={`after sector cap: ${ro.after_sector_cap ?? '-'}`} />
        <Stat label="Sector cap" value={`${ro.sector_cap_pct ?? 30}%`} note="per NSE industry" />
        <Stat label="Max positions" value={`${ro.max_positions ?? 10}`} />
        <Stat label="DD halt at" value={`${ro.dd_halt_pct ?? -8}%`} note={`resume at ${ro.dd_resume_pct ?? -4}%`} />
        <Stat label="VIX gate" value={`${(vixMult * 100).toFixed(0)}%`} note={`>${ro.vix_gate_high ?? 25}=half, >${ro.vix_gate_extreme ?? 35}=zero`} />
      </div>
      {killActive && killReason && (
        <p className="mt-3 text-xs opacity-90 border-t border-current/20 pt-2">
          <strong>Kill switch active:</strong> {killReason}
        </p>
      )}
    </div>
  )
}

function Stat({ label, value, note, accent }: { label: string; value: string; note?: string; accent?: string }) {
  return (
    <div className="bg-white/40 rounded p-2.5">
      <div className="text-[11px] uppercase tracking-wide opacity-75">{label}</div>
      <div className={`font-bold tabular-nums mt-0.5 ${accent ?? 'text-gray-900'}`}>{value}</div>
      {note && <div className="text-[11px] opacity-60 mt-0.5">{note}</div>}
    </div>
  )
}
