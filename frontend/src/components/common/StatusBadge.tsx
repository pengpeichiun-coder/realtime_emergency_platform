import type {
  IncidentSeverity,
  IncidentStatus,
  UnitStatus,
} from '../../types/domain'
import { formatLabel } from '../../utils/format'

type BadgeValue = IncidentSeverity | IncidentStatus | UnitStatus

const badgeStyles: Record<BadgeValue, string> = {
  critical: 'border-red-200 bg-red-50 text-red-700',
  high: 'border-orange-200 bg-orange-50 text-orange-800',
  medium: 'border-amber-200 bg-amber-50 text-amber-800',
  low: 'border-slate-200 bg-slate-50 text-slate-600',
  active: 'border-red-200 bg-red-50 text-red-700',
  dispatched: 'border-indigo-200 bg-indigo-50 text-indigo-700',
  en_route: 'border-blue-200 bg-blue-50 text-blue-700',
  on_scene: 'border-teal-200 bg-teal-50 text-teal-800',
  resolved: 'border-slate-200 bg-slate-50 text-slate-600',
  available: 'border-emerald-200 bg-emerald-50 text-emerald-800',
  offline: 'border-slate-200 bg-slate-100 text-slate-600',
}

export function StatusBadge({ value }: { value: BadgeValue }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-md border px-2 py-1 text-[11px] font-semibold leading-none ${badgeStyles[value]}`}
    >
      <span className="size-1.5 rounded-full bg-current" aria-hidden="true" />
      {formatLabel(value)}
    </span>
  )
}
