import { StatusBadge } from '../components/common/StatusBadge'
import type { EmergencyUnit, UnitType } from '../types/domain'
import { formatTime, unitTypeLabels } from '../utils/format'

const unitStyles: Record<UnitType, string> = {
  ambulance: 'bg-teal-50 text-teal-700 border-teal-200',
  fire_truck: 'bg-orange-50 text-orange-700 border-orange-200',
  police: 'bg-blue-50 text-blue-700 border-blue-200',
}

const unitSymbols: Record<UnitType, string> = {
  ambulance: '+',
  fire_truck: 'F',
  police: 'P',
}

export function UnitsPage({ units }: { units: EmergencyUnit[] }) {
  const availableCount = units.filter(
    (unit) => unit.status === 'available',
  ).length

  return (
    <div className="space-y-7">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="mb-2 text-[11px] font-bold uppercase tracking-[0.18em] text-slate-500">
            Operations / Response fleet
          </p>
          <h1 className="text-3xl font-semibold tracking-tight text-slate-950">
            Ready to respond.
          </h1>
          <p className="mt-2 text-sm text-slate-500">
            Fleet availability, current assignments, and positions across the
            city.
          </p>
        </div>
        <span className="rounded-lg border border-teal-200 bg-teal-50 px-3 py-2 text-xs font-semibold text-teal-800">
          {availableCount} of {units.length} units available
        </span>
      </header>

      <section
        className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm"
        aria-labelledby="response-fleet-title"
      >
        <div className="flex items-center justify-between gap-3 border-b border-slate-200 px-5 py-4">
          <h2
            id="response-fleet-title"
            className="text-sm font-semibold text-slate-900"
          >
            Response fleet{' '}
            <span className="ml-2 text-slate-400">{units.length}</span>
          </h2>
          <span className="text-xs text-slate-500">All times SGT</span>
        </div>
        <div className="hidden overflow-x-auto md:block">
          <table className="w-full text-left text-sm">
            <caption className="sr-only">
              Emergency units, availability, current area, and latest update
            </caption>
            <thead className="border-b border-slate-200 bg-slate-50 text-[10px] uppercase tracking-wider text-slate-500">
              <tr>
                <th scope="col" className="px-5 py-3 font-semibold">
                  Unit
                </th>
                <th scope="col" className="px-5 py-3 font-semibold">
                  Type
                </th>
                <th scope="col" className="px-5 py-3 font-semibold">
                  Status / Assignment
                </th>
                <th scope="col" className="px-5 py-3 font-semibold">
                  Current Area
                </th>
                <th scope="col" className="px-5 py-3 font-semibold">
                  Last Update
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {units.map((unit) => (
                <tr
                  key={unit.id}
                  className="transition-colors hover:bg-slate-50/70"
                >
                  <th scope="row" className="px-5 py-5 font-normal">
                    <div className="flex items-center gap-3">
                      <span
                        aria-hidden="true"
                        className={`flex size-9 shrink-0 items-center justify-center rounded-lg border text-sm font-bold ${unitStyles[unit.type]}`}
                      >
                        {unitSymbols[unit.type]}
                      </span>
                      <span>
                        <span className="block whitespace-nowrap font-mono text-xs font-semibold text-slate-800">
                          {unit.id}
                        </span>
                        <span className="mt-1 block whitespace-nowrap text-xs text-slate-500">
                          {unit.name}
                        </span>
                      </span>
                    </div>
                  </th>
                  <td className="whitespace-nowrap px-5 py-5 text-xs text-slate-600">
                    {unitTypeLabels[unit.type]}
                  </td>
                  <td className="px-5 py-5">
                    <StatusBadge value={unit.status} />
                    {unit.assignedIncidentId && (
                      <p className="mt-2 font-mono text-[11px] text-slate-500">
                        {unit.assignedIncidentId}
                      </p>
                    )}
                  </td>
                  <td className="whitespace-nowrap px-5 py-5 text-xs text-slate-600">
                    {unit.area}
                  </td>
                  <td className="whitespace-nowrap px-5 py-5 font-mono text-xs text-slate-600">
                    <time dateTime={unit.lastUpdatedAt}>
                      {formatTime(unit.lastUpdatedAt)}
                    </time>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <ul className="divide-y divide-slate-100 md:hidden">
          {units.map((unit) => (
            <li key={unit.id} className="space-y-3 p-5">
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-3">
                  <span
                    aria-hidden="true"
                    className={`flex size-9 shrink-0 items-center justify-center rounded-lg border font-bold ${unitStyles[unit.type]}`}
                  >
                    {unitSymbols[unit.type]}
                  </span>
                  <div>
                    <h3 className="font-mono text-xs font-semibold text-slate-800">
                      {unit.id}
                    </h3>
                    <p className="mt-1 text-xs text-slate-500">
                      {unitTypeLabels[unit.type]}
                    </p>
                  </div>
                </div>
                <StatusBadge value={unit.status} />
              </div>
              <p className="text-sm font-medium text-slate-800">{unit.name}</p>
              <div className="flex flex-wrap justify-between gap-2 text-xs text-slate-500">
                <span>{unit.area}</span>
                <time dateTime={unit.lastUpdatedAt}>
                  Updated {formatTime(unit.lastUpdatedAt)} SGT
                </time>
              </div>
              {unit.assignedIncidentId && (
                <p className="text-xs text-slate-500">
                  Assigned to{' '}
                  <span className="font-mono font-semibold text-slate-700">
                    {unit.assignedIncidentId}
                  </span>
                </p>
              )}
            </li>
          ))}
        </ul>
      </section>
    </div>
  )
}
