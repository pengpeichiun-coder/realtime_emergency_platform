import { Link } from 'react-router-dom'
import type { Incident } from '../../types/domain'
import { StatusBadge } from '../common/StatusBadge'
import { Icon } from '../common/Icon'
import { formatTime } from '../../utils/format'

export function IncidentList({
  incidents,
  selectedIncidentId,
  onSelect,
}: {
  incidents: Incident[]
  selectedIncidentId: string | null
  onSelect: (id: string) => void
}) {
  const activeIncidents = incidents.filter(
    (incident) => incident.status !== 'resolved',
  )
  return (
    <section
      className="overflow-hidden rounded-xl border border-slate-200 bg-white"
      aria-label="Active incidents"
    >
      <div className="flex items-center justify-between border-b border-slate-100 px-5 py-4">
        <h2 className="text-sm font-semibold text-slate-800">
          Active incidents{' '}
          <span className="ml-2 rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-500">
            {activeIncidents.length}
          </span>
        </h2>
        <Link to="/incidents" className="text-xs font-medium text-teal-700">
          View all →
        </Link>
      </div>
      <div className="max-h-[260px] xl:max-h-[385px] divide-y divide-slate-100 overflow-y-auto">
        {activeIncidents.map((incident) => (
          <button
            key={incident.id}
            type="button"
            aria-pressed={selectedIncidentId === incident.id}
            onClick={() => onSelect(incident.id)}
            className={`block w-full border-l-2 px-5 py-4 text-left transition-colors ${selectedIncidentId === incident.id ? 'border-teal-600 bg-teal-50/50' : 'border-transparent hover:bg-slate-50'}`}
          >
            <div className="mb-2 flex items-center justify-between gap-2">
              <span className="font-mono text-[10px] text-slate-400">
                {incident.id}
              </span>
              <StatusBadge value={incident.severity} />
            </div>
            <h3 className="text-sm font-semibold text-slate-800">
              {incident.title}
            </h3>
            <p className="mt-1 flex items-center gap-1 text-xs text-slate-500">
              <Icon name="pin" className="h-3 w-3" />
              {incident.area}
            </p>
            <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
              <StatusBadge value={incident.status} />
              <span className="text-[10px] text-slate-400">
                {incident.assignedUnitId ??
                  `${formatTime(incident.createdAt)} SGT`}
              </span>
            </div>
          </button>
        ))}
      </div>
    </section>
  )
}
