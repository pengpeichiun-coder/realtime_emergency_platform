import type { EmergencyUnit, Incident } from '../../types/domain'
import { Icon } from '../common/Icon'

export function Metrics({
  incidents,
  units,
}: {
  incidents: Incident[]
  units: EmergencyUnit[]
}) {
  const active = incidents.filter((incident) => incident.status !== 'resolved')
  const responses = incidents.flatMap((incident) =>
    incident.responseTimeSeconds === undefined
      ? []
      : [incident.responseTimeSeconds],
  )
  const average = responses.length
    ? Math.round(
        responses.reduce((sum, seconds) => sum + seconds, 0) / responses.length,
      )
    : null
  const metrics = [
    {
      label: 'Active incidents',
      value: active.length,
      detail: `${active.filter((incident) => incident.severity === 'critical').length} critical priority`,
      icon: 'alert',
      color: 'bg-rose-50 text-rose-600',
    },
    {
      label: 'Available units',
      value: units
        .filter((unit) => unit.status === 'available')
        .length.toString()
        .padStart(2, '0'),
      detail: 'Ready for dispatch',
      icon: 'truck',
      color: 'bg-teal-50 text-teal-700',
    },
    {
      label: 'Dispatched units',
      value: units
        .filter((unit) => ['dispatched', 'en_route'].includes(unit.status))
        .length.toString()
        .padStart(2, '0'),
      detail: 'Assigned or en route',
      icon: 'arrow',
      color: 'bg-blue-50 text-blue-600',
    },
    {
      label: 'Avg. response time',
      value:
        average === null
          ? '—'
          : `${Math.floor(average / 60)}m ${String(average % 60).padStart(2, '0')}s`,
      detail: 'Mock response history',
      icon: 'clock',
      color: 'bg-amber-50 text-amber-700',
    },
  ] as const
  return (
    <section
      aria-label="Operations metrics"
      className="mb-7 grid grid-cols-2 gap-3 xl:grid-cols-4 xl:gap-5"
    >
      {metrics.map((metric) => (
        <article
          key={metric.label}
          className="rounded-xl border border-slate-200/80 bg-white p-4 sm:p-5"
        >
          <div className="mb-5 flex items-center justify-between gap-2">
            <h2 className="text-xs font-medium text-slate-500 sm:text-sm">
              {metric.label}
            </h2>
            <span className={`rounded-lg p-2 ${metric.color}`}>
              <Icon name={metric.icon} className="h-4 w-4" />
            </span>
          </div>
          <p className="text-3xl font-semibold tracking-tight text-slate-800">
            {metric.value}
          </p>
          <p className="mt-2 text-[11px] text-slate-500 sm:text-xs">
            {metric.detail}
          </p>
        </article>
      ))}
    </section>
  )
}
