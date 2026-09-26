import { useState } from 'react'
import { useAuth } from '../auth/context'
import { canOperate } from '../auth/session'
import { ReportAction } from '../components/incidents/ReportAction'
import { StatusBadge } from '../components/common/StatusBadge'
import type { Incident } from '../types/domain'
import { formatTime, incidentTypeLabels } from '../utils/format'

export function IncidentsPage({ incidents }: { incidents: Incident[] }) {
  const { user } = useAuth()
  const [reportIncidentId, setReportIncidentId] = useState('')
  const reportIncident = incidents.find(
    (incident) => incident.id === reportIncidentId,
  )
  const activeCount = incidents.filter(
    (incident) => incident.status === 'active',
  ).length

  return (
    <div className="space-y-7">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="mb-2 text-[11px] font-bold uppercase tracking-[0.18em] text-slate-500">
            Operations / Incident register
          </p>
          <h1 className="text-3xl font-semibold tracking-tight text-slate-950">
            Every incident. In view.
          </h1>
          <p className="mt-2 text-sm text-slate-500">
            Monitor priorities, assigned response, and incident status across
            Singapore.
          </p>
        </div>
        <span className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs font-semibold text-red-700">
          {activeCount} awaiting dispatch
        </span>
      </header>

      {canOperate(user) && (
        <section
          aria-label="Incident reports"
          className="rounded-xl border border-slate-200 bg-white p-5"
        >
          <label className="block text-sm font-semibold">
            Incident for reports and AI summary
            <select
              aria-label="Incident for reports and AI summary"
              className="mt-2 block w-full rounded border p-2"
              value={reportIncident?.id ?? ''}
              onChange={(event) => setReportIncidentId(event.target.value)}
            >
              <option value="">
                Select any incident, including resolved incidents
              </option>
              {incidents.map((incident) => (
                <option key={incident.id} value={incident.id}>
                  {incident.id} · {incident.title} · {incident.status}
                </option>
              ))}
            </select>
          </label>
          {reportIncident && (
            <ReportAction
              key={reportIncident.id}
              incidentId={reportIncident.id}
            />
          )}
        </section>
      )}
      <section
        className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm"
        aria-labelledby="incident-register-title"
      >
        <div className="flex items-center justify-between gap-3 border-b border-slate-200 px-5 py-4">
          <h2
            id="incident-register-title"
            className="text-sm font-semibold text-slate-900"
          >
            Incident register{' '}
            <span className="ml-2 text-slate-400">{incidents.length}</span>
          </h2>
          <span className="text-xs text-slate-500">All times SGT</span>
        </div>
        <div className="hidden overflow-x-auto md:block">
          <table className="w-full text-left text-sm">
            <caption className="sr-only">
              Singapore incidents, severity, response status, and reported time
            </caption>
            <thead className="border-b border-slate-200 bg-slate-50 text-[10px] uppercase tracking-wider text-slate-500">
              <tr>
                <th scope="col" className="px-5 py-3 font-semibold">
                  Incident
                </th>
                <th scope="col" className="px-5 py-3 font-semibold">
                  Location / Description
                </th>
                <th scope="col" className="px-5 py-3 font-semibold">
                  Severity
                </th>
                <th scope="col" className="px-5 py-3 font-semibold">
                  Status / Unit
                </th>
                <th scope="col" className="px-5 py-3 font-semibold">
                  Reported
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {incidents.map((incident) => (
                <tr
                  key={incident.id}
                  className="transition-colors hover:bg-slate-50/70"
                >
                  <th scope="row" className="px-5 py-5 align-top font-normal">
                    <span className="block whitespace-nowrap font-mono text-xs font-semibold text-slate-700">
                      {incident.id}
                    </span>
                    <span className="mt-1.5 block text-xs text-slate-500">
                      {incidentTypeLabels[incident.type]}
                    </span>
                  </th>
                  <td className="min-w-64 max-w-md px-5 py-5 align-top">
                    <p className="font-semibold text-slate-900">
                      {incident.title}{' '}
                      <span className="font-normal text-slate-500">
                        · {incident.area}
                      </span>
                    </p>
                    <p className="mt-1 text-xs leading-relaxed text-slate-500">
                      {incident.description}
                    </p>
                  </td>
                  <td className="px-5 py-5 align-top">
                    <StatusBadge value={incident.severity} />
                  </td>
                  <td className="px-5 py-5 align-top">
                    <StatusBadge value={incident.status} />
                    {incident.assignedUnitId && (
                      <p className="mt-2 font-mono text-[11px] text-slate-500">
                        {incident.assignedUnitId}
                      </p>
                    )}
                  </td>
                  <td className="whitespace-nowrap px-5 py-5 align-top font-mono text-xs text-slate-600">
                    <time dateTime={incident.createdAt}>
                      {formatTime(incident.createdAt)}
                    </time>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <ul className="divide-y divide-slate-100 md:hidden">
          {incidents.map((incident) => (
            <li key={incident.id} className="space-y-3 p-5">
              <div className="flex items-center justify-between gap-2">
                <span className="font-mono text-xs font-semibold text-slate-600">
                  {incident.id} · {incidentTypeLabels[incident.type]}
                </span>
                <StatusBadge value={incident.severity} />
              </div>
              <div>
                <h3 className="font-semibold text-slate-900">
                  {incident.title}
                </h3>
                <p className="mt-1 text-sm text-slate-600">{incident.area}</p>
                <p className="mt-2 text-xs leading-relaxed text-slate-500">
                  {incident.description}
                </p>
              </div>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <StatusBadge value={incident.status} />
                <span className="text-xs text-slate-500">
                  {incident.assignedUnitId && `${incident.assignedUnitId} · `}
                  <time dateTime={incident.createdAt}>
                    {formatTime(incident.createdAt)} SGT
                  </time>
                </span>
              </div>
            </li>
          ))}
        </ul>
      </section>
    </div>
  )
}
