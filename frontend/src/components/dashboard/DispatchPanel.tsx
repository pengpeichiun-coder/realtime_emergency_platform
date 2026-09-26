import { useId, useState } from 'react'
import { useNearbyUnits } from '../../features/nearby/useNearbyUnits'
import type { EmergencyDispatch } from '../../types/domain'
import type { EmergencyUnit, Incident } from '../../types/domain'
import { unitTypeLabels } from '../../utils/format'
import { Icon } from '../common/Icon'
import { StatusBadge } from '../common/StatusBadge'

interface DispatchPanelProps {
  incident: Incident | undefined
  units: EmergencyUnit[]
  assignment: EmergencyDispatch | undefined
  error: string | null
  onDispatch: (unitId: string) => void
  readOnly?: boolean
  pending?: boolean
  onResolve?: () => void
}

const responseSteps = ['Dispatched', 'En route', 'On scene'] as const

export function DispatchPanel({
  incident,
  units,
  assignment,
  error,
  onDispatch,
  pending = false,
  readOnly = false,
  onResolve,
}: DispatchPanelProps) {
  const selectId = useId()
  const [selectedUnitId, setSelectedUnitId] = useState('')
  const availableUnits = units.filter(
    (unit) => unit.status === 'available' && !unit.assignedIncidentId,
  )
  const selectedUnit = availableUnits.find((unit) => unit.id === selectedUnitId)
  const assignedUnit = units.find(
    (unit) => unit.id === (assignment?.unitId ?? incident?.assignedUnitId),
  )
  const canDispatch =
    incident?.status === 'active' && !incident.assignedUnitId && !assignment
  const nearby = useNearbyUnits(incident, units, canDispatch && !pending)
  const responseStep =
    assignedUnit?.status === 'on_scene'
      ? 2
      : assignedUnit?.status === 'en_route'
        ? 1
        : assignedUnit?.status === 'dispatched'
          ? 0
          : -1

  return (
    <section
      aria-label="Dispatch controls"
      className="rounded-xl border border-slate-200 bg-white p-5"
    >
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="kicker">INCIDENT BRIEF</p>
          <h2 className="mt-1 text-sm font-semibold text-slate-800">
            Dispatch controls
          </h2>
        </div>
        <span className="rounded-lg bg-teal-50 p-2 text-teal-700">
          <Icon name="truck" className="h-4 w-4" />
        </span>
      </div>

      {incident ? (
        <>
          <div className="mt-5 border-t border-slate-100 pt-4">
            <p className="font-mono text-[11px] text-slate-500">
              {incident.id}
            </p>
            <h3 className="mt-1 text-base font-semibold text-slate-800">
              {incident.title}
            </h3>
            <p className="mt-2 flex items-center gap-1 text-xs text-slate-500">
              <Icon name="pin" className="h-3 w-3" />
              {incident.area}
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              <StatusBadge value={incident.severity} />
              <StatusBadge value={incident.status} />
            </div>
            <p className="mt-3 text-sm leading-6 text-slate-500">
              {incident.description}
            </p>
          </div>

          {canDispatch && !readOnly ? (
            <form
              className="mt-5 border-t border-slate-100 pt-4"
              onSubmit={(event) => {
                event.preventDefault()
                if (selectedUnit) onDispatch(selectedUnit.id)
              }}
            >
              <div
                aria-label="Nearby available units"
                className="mb-4 space-y-2"
              >
                <h3 className="text-xs font-semibold text-slate-700">
                  Nearby available units
                </h3>
                <p className="text-[11px] text-slate-500">
                  Within 10 km · straight-line distance, not travel time
                </p>
                {nearby.loading && (
                  <p role="status" className="text-xs text-slate-500">
                    Finding nearby units…
                  </p>
                )}
                {nearby.error && (
                  <div role="alert" className="text-xs text-amber-800">
                    <p>{nearby.error}</p>
                    <button
                      type="button"
                      onClick={nearby.retry}
                      disabled={pending}
                      className="mt-1 underline"
                    >
                      Retry nearby search
                    </button>
                  </div>
                )}
                {!nearby.loading &&
                  !nearby.error &&
                  nearby.units.length === 0 && (
                    <p className="text-xs text-slate-500">
                      No available units within this radius. You can still
                      choose from the available fleet below.
                    </p>
                  )}
                <ul className="space-y-1">
                  {nearby.units.map((result) => {
                    const unit = availableUnits.find(
                      (item) => item.id === result.unit_id,
                    )
                    return unit ? (
                      <li key={unit.id}>
                        <button
                          type="button"
                          disabled={pending}
                          onClick={() => setSelectedUnitId(unit.id)}
                          className="flex w-full items-center justify-between gap-2 rounded border border-slate-200 px-2 py-2 text-left text-xs hover:bg-teal-50"
                        >
                          <span>
                            {unit.id} · {unitTypeLabels[unit.type]}
                          </span>
                          <span className="whitespace-nowrap">
                            {(result.distance_meters / 1000).toFixed(1)} km
                          </span>
                        </button>
                      </li>
                    ) : null
                  })}
                </ul>
              </div>
              <label
                htmlFor={selectId}
                className="mb-2 block text-xs font-semibold text-slate-700"
              >
                Available unit
              </label>
              <select
                id={selectId}
                className="form-input"
                value={selectedUnit?.id ?? ''}
                onChange={(event) => setSelectedUnitId(event.target.value)}
                disabled={pending || availableUnits.length === 0}
              >
                <option value="">Choose a unit</option>
                {availableUnits.map((unit) => (
                  <option key={unit.id} value={unit.id}>
                    {unit.id} · {unitTypeLabels[unit.type]} · {unit.area}
                  </option>
                ))}
              </select>
              {availableUnits.length === 0 ? (
                <p className="mt-2 text-xs leading-5 text-slate-500">
                  No units are available. Reset Demo to restore the starting
                  fleet.
                </p>
              ) : (
                <p className="mt-2 text-xs leading-5 text-slate-500">
                  {availableUnits.length} units ready for this demo dispatch.
                </p>
              )}
              <button
                type="submit"
                className="primary-button mt-4 w-full justify-center"
                disabled={pending || !selectedUnit}
              >
                <Icon name="arrow" className="h-4 w-4" />
                {pending ? 'Dispatching…' : 'Confirm dispatch'}
              </button>
            </form>
          ) : assignedUnit && incident.status !== 'resolved' ? (
            <div className="mt-5 rounded-lg border border-slate-200 bg-slate-50 p-4">
              <p className="text-[10px] font-semibold tracking-wider text-slate-500">
                ASSIGNED UNIT
              </p>
              <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
                <p className="text-sm font-semibold text-slate-800">
                  {assignedUnit.id}
                  <span className="ml-2 font-normal text-slate-500">
                    {unitTypeLabels[assignedUnit.type]}
                  </span>
                </p>
                <StatusBadge value={assignedUnit.status} />
              </div>
              <ol
                aria-label="Unit response progress"
                className="mt-5 grid grid-cols-3 gap-2"
              >
                {responseSteps.map((step, index) => (
                  <li
                    key={step}
                    aria-current={responseStep === index ? 'step' : undefined}
                    className={`border-t-2 pt-2 text-[11px] font-medium ${index <= responseStep ? 'border-teal-600 text-teal-800' : 'border-slate-200 text-slate-500'}`}
                  >
                    <span className="mb-1 flex h-5 items-center">
                      {index < responseStep || responseStep === 2 ? (
                        <Icon name="check" className="h-4 w-4" />
                      ) : (
                        <span aria-hidden="true">0{index + 1}</span>
                      )}
                    </span>
                    {step}
                  </li>
                ))}
              </ol>
              <p
                role="status"
                className="mt-4 text-xs leading-5 text-slate-600"
              >
                {assignedUnit.status === 'on_scene'
                  ? `${assignedUnit.id} has arrived. The unit is on scene and movement has stopped.`
                  : assignedUnit.status === 'en_route'
                    ? `${assignedUnit.id} is moving toward ${incident.area}. Follow its marker and route on the map.`
                    : assignedUnit.status === 'dispatched'
                      ? `${assignedUnit.id} is dispatched and preparing to depart.`
                      : `${assignedUnit.id} is assigned to this incident.`}
              </p>
              <a
                href="#operations-map"
                className="mt-3 inline-block text-xs font-semibold text-teal-700"
              >
                View unit on map ↓
              </a>
            </div>
          ) : (
            <p className="mt-4 rounded-lg bg-slate-50 p-3 text-xs leading-5 text-slate-600">
              {incident.status === 'resolved'
                ? 'This incident is resolved and is closed for dispatch.'
                : readOnly
                  ? 'Your responder role has view access to this incident.'
                  : 'This incident already has a response in progress.'}
            </p>
          )}
        </>
      ) : (
        <div className="mt-5 rounded-lg border border-dashed border-slate-200 px-4 py-8 text-center">
          <Icon name="pin" className="mx-auto h-6 w-6 text-slate-400" />
          <p className="mt-3 text-sm font-medium text-slate-700">
            Select an incident
          </p>
          <p className="mt-2 text-xs leading-5 text-slate-500">
            Choose an incident on the map or in the active list to review its
            details and dispatch an available unit.
          </p>
        </div>
      )}

      {!readOnly && incident?.status === 'on_scene' && onResolve && (
        <button
          type="button"
          disabled={pending}
          onClick={onResolve}
          className="primary-button mt-4 w-full justify-center"
        >
          Resolve incident
        </button>
      )}
      {error && (
        <p
          role="alert"
          className="mt-4 rounded-lg border border-red-200 bg-red-50 p-3 text-xs leading-5 text-red-700"
        >
          {error}
        </p>
      )}
      <p className="mt-4 text-[10px] leading-5 text-slate-400">
        Straight-line demo movement · no real dispatch
      </p>
    </section>
  )
}
