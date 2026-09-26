import { ReportAction } from '../components/incidents/ReportAction'
import { useAuth } from '../auth/context'
import { canOperate, canAdmin } from '../auth/session'
import { lazy, Suspense, useState } from 'react'
import { CreateIncidentForm } from '../components/incidents/CreateIncidentForm'
import { Metrics } from '../components/dashboard/Metrics'
import { Icon } from '../components/common/Icon'
import { IncidentList } from '../components/dashboard/IncidentList'
import { DispatchPanel } from '../components/dashboard/DispatchPanel'
import { ActivityFeed } from '../components/dashboard/ActivityFeed'
import { useOperations } from '../features/operations/context'

const OperationsMap = lazy(() =>
  import('../components/map/OperationsMap').then((module) => ({
    default: module.OperationsMap,
  })),
)

export function DashboardPage() {
  const { user } = useAuth()
  const writable = canOperate(user)
  const operations = useOperations()
  const { snapshot, selectedIncidentId, selectIncident } = operations
  const pending = operations.pending || operations.connection !== 'live'
  const { incidents, units, dispatches, activity: events } = snapshot
  const assignments = dispatches.filter(
    (item) => item.status === 'created' || item.status === 'en_route',
  )
  const [creating, setCreating] = useState(false)
  const selected = incidents.find(
    (incident) => incident.id === selectedIncidentId,
  )
  const dispatchRoutes = assignments.flatMap((assignment) => {
    const destination = incidents.find(
      (incident) => incident.id === assignment.incidentId,
    )
    return destination
      ? [
          {
            unitId: assignment.unitId,
            origin: [
              assignment.origin.latitude,
              assignment.origin.longitude,
            ] as [number, number],
            destination: [destination.latitude, destination.longitude] as [
              number,
              number,
            ],
          },
        ]
      : []
  })
  return (
    <>
      <div className="mb-7 flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="kicker">COMMAND OVERVIEW</p>
          <h1 className="page-title">Operations overview</h1>
          <p className="page-description">
            A clear view of every incident. A coordinated response.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <span className="flex items-center gap-2 text-xs text-teal-700">
            <span className="h-1.5 w-1.5 rounded-full bg-teal-600" />
            {operations.connection === 'live'
              ? 'Realtime connected'
              : 'Reconnecting…'}
          </span>
          {writable && (
            <button
              type="button"
              className="primary-button"
              onClick={() => setCreating(true)}
              disabled={pending || creating}
            >
              Create incident
            </button>
          )}
          <button
            type="button"
            onClick={() => void operations.refresh()}
            disabled={pending || operations.loading}
            className="text-xs font-semibold text-teal-700"
          >
            Refresh
          </button>
          {canAdmin(user) && user?.can_reset_demo && (
            <button
              type="button"
              disabled={pending}
              onClick={() => {
                void operations.resetDemo().then((success) => {
                  if (success) setCreating(false)
                })
              }}
              className="flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-medium text-slate-600 hover:bg-slate-50"
            >
              <Icon name="reset" className="h-3.5 w-3.5" />
              Reset demo
            </button>
          )}
        </div>
      </div>
      {creating && writable && (
        <div className="mb-6">
          <CreateIncidentForm
            pending={pending}
            onCancel={() => setCreating(false)}
            onCreate={async (input) => {
              const success = await operations.createIncident(input)
              if (success) setCreating(false)
              return success
            }}
          />
        </div>
      )}
      <Metrics incidents={incidents} units={units} />
      <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_340px]">
        <div className="order-2 min-w-0 space-y-5 xl:order-1">
          <Suspense
            fallback={
              <p
                role="status"
                className="rounded-xl bg-white p-8 text-sm text-slate-500"
              >
                Loading map…
              </p>
            }
          >
            <OperationsMap
              incidents={incidents}
              units={units}
              selectedIncidentId={selectedIncidentId}
              onSelectIncident={selectIncident}
              dispatchRoutes={dispatchRoutes}
            />
          </Suspense>
          <ActivityFeed events={events} />
        </div>
        <div className="order-1 flex flex-col gap-5 xl:order-2">
          <div className="order-2 xl:order-1">
            {selected && <ReportAction key={selected.id} incidentId={selected.id}/>}
            <DispatchPanel
              key={selected?.id ?? 'empty'}
              incident={selected}
              units={units}
              assignment={assignments.find(
                (assignment) => assignment.incidentId === selectedIncidentId,
              )}
              error={null}
              pending={pending}
              readOnly={!writable}
              onResolve={() => {
                if (selected) void operations.resolveIncident(selected.id)
              }}
              onDispatch={(unitId) => {
                if (selected) void operations.dispatchUnit(selected.id, unitId)
              }}
            />
          </div>
          <div className="order-1 xl:order-2">
            <IncidentList
              incidents={incidents}
              selectedIncidentId={selectedIncidentId}
              onSelect={selectIncident}
            />
          </div>
        </div>
      </div>
    </>
  )
}
