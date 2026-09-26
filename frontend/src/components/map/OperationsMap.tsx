import { useEffect, useState } from 'react'
import { divIcon } from 'leaflet'
import {
  MapContainer,
  Marker,
  Polyline,
  Popup,
  TileLayer,
  Tooltip,
  useMap,
} from 'react-leaflet'
import type { EmergencyUnit, Incident, UnitType } from '../../types/domain'
import { formatLabel, formatTime, unitTypeLabels } from '../../utils/format'
import { StatusBadge } from '../common/StatusBadge'

const incidentIcon = divIcon({
  className: 'entity-marker',
  html: '<span class="map-marker map-marker--incident">!</span>',
  iconSize: [30, 30],
  iconAnchor: [15, 15],
})
const unitSymbols: Record<UnitType, string> = {
  ambulance: 'A',
  fire_truck: 'F',
  police: 'P',
}
const unitIcons = Object.fromEntries(
  Object.entries(unitSymbols).map(([type, symbol]) => [
    type,
    divIcon({
      className: 'entity-marker',
      html: `<span class="map-marker map-marker--${type}">${symbol}</span>`,
      iconSize: [28, 28],
      iconAnchor: [14, 14],
    }),
  ]),
) as Record<UnitType, ReturnType<typeof divIcon>>

// Keep colocated incident and on-scene unit markers individually clickable.
const onSceneIcons = Object.fromEntries(
  Object.entries(unitIcons).map(([type, icon]) => [
    type,
    divIcon({ ...icon.options, iconAnchor: [-18, 14] }),
  ]),
) as Record<UnitType, ReturnType<typeof divIcon>>

export interface DispatchRoute {
  unitId: string
  origin: [number, number]
  destination: [number, number]
}

const movingIcons = Object.fromEntries(
  Object.entries(unitIcons).map(([type, icon]) => [
    type,
    divIcon({ ...icon.options, className: 'entity-marker moving-unit' }),
  ]),
) as Record<UnitType, ReturnType<typeof divIcon>>

function MapFocus({
  incident,
  route,
}: {
  incident?: Incident
  route?: DispatchRoute
}) {
  const map = useMap()
  const latitude = incident?.latitude
  const longitude = incident?.longitude
  const originLatitude = route?.origin[0]
  const originLongitude = route?.origin[1]
  useEffect(() => {
    if (latitude === undefined || longitude === undefined) {
      map.setView([1.298, 103.846], 13, { animate: false })
    } else {
      map.setView([latitude, longitude], 14, { animate: false })
    }
  }, [map, latitude, longitude])

  useEffect(() => {
    // Fit a new dispatch once; keep the view still when the journey completes.
    if (
      latitude !== undefined &&
      longitude !== undefined &&
      originLatitude !== undefined &&
      originLongitude !== undefined
    ) {
      map.stop()
      map.fitBounds(
        [
          [latitude, longitude],
          [originLatitude, originLongitude],
        ],
        { padding: [65, 65], maxZoom: 15, animate: false },
      )
    }
  }, [map, latitude, longitude, originLatitude, originLongitude])
  return null
}

export function OperationsMap({
  incidents,
  units,
  selectedIncidentId,
  onSelectIncident,
  dispatchRoutes,
}: {
  incidents: Incident[]
  units: EmergencyUnit[]
  selectedIncidentId: string | null
  onSelectIncident: (id: string) => void
  dispatchRoutes: DispatchRoute[]
}) {
  const [tileError, setTileError] = useState(false)
  return (
    <section
      id="operations-map"
      aria-label="Singapore operations map"
      className="overflow-hidden rounded-xl border border-slate-200 bg-white"
    >
      <div className="flex items-center justify-between gap-3 border-b border-slate-100 px-5 py-4">
        <div>
          <h2 className="text-sm font-semibold text-slate-800">
            Operational map
          </h2>
          <p className="mt-1 text-xs text-slate-500">
            Central Singapore · Incident & unit positions
          </p>
        </div>
        <span className="rounded border border-slate-200 px-2 py-1 font-mono text-[10px] text-slate-500">
          SG / 01
        </span>
      </div>
      {tileError && (
        <p
          role="status"
          className="bg-amber-50 px-4 py-2 text-xs text-amber-900"
        >
          Some map tiles could not load. Check your connection and reload;
          incident and unit information remains available.
        </p>
      )}
      <MapContainer
        center={[1.298, 103.846]}
        zoom={13}
        scrollWheelZoom={false}
        // Avoid pending Leaflet zoom callbacks during reset or route unmount.
        zoomAnimation={false}
        markerZoomAnimation={false}
        className="operations-map"
        aria-label="Singapore map"
      >
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
          url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
          eventHandlers={{
            tileerror: () => setTileError(true),
          }}
        />
        <MapFocus
          incident={incidents.find(
            (incident) => incident.id === selectedIncidentId,
          )}
          route={dispatchRoutes.find(
            (route) =>
              route.unitId ===
              incidents.find((incident) => incident.id === selectedIncidentId)
                ?.assignedUnitId,
          )}
        />
        {dispatchRoutes.map((route) => (
          <Polyline
            key={route.unitId}
            positions={[route.origin, route.destination]}
            pathOptions={{
              color: '#188f86',
              weight: 3,
              dashArray: '6 8',
              opacity: 0.75,
            }}
          >
            <Tooltip>Demo route · {route.unitId}</Tooltip>
          </Polyline>
        ))}
        {incidents
          .filter((incident) => incident.status !== 'resolved')
          .map((incident) => (
            <Marker
              key={incident.id}
              position={[incident.latitude, incident.longitude]}
              icon={incidentIcon}
              title={`Incident ${incident.id}: ${incident.title}`}
              alt={`Incident ${incident.id}`}
              eventHandlers={{ click: () => onSelectIncident(incident.id) }}
            >
              <Popup>
                <div className="map-popup">
                  <p className="font-mono text-xs text-slate-500">
                    {incident.id}
                  </p>
                  <h3 className="mt-1 font-semibold">{incident.title}</h3>
                  <p>{incident.area}</p>
                  <div className="flex flex-wrap gap-2">
                    <StatusBadge value={incident.severity} />
                    <StatusBadge value={incident.status} />
                  </div>
                  <p>{incident.description}</p>
                </div>
              </Popup>
            </Marker>
          ))}
        {units.map((unit) => (
          <Marker
            key={unit.id}
            position={[unit.latitude, unit.longitude]}
            icon={
              unit.status === 'on_scene'
                ? onSceneIcons[unit.type]
                : unit.status === 'en_route'
                  ? movingIcons[unit.type]
                  : unitIcons[unit.type]
            }
            opacity={unit.status === 'offline' ? 0.5 : 1}
            title={`Unit ${unit.id}: ${unitTypeLabels[unit.type]}`}
            alt={`Unit ${unit.id}`}
            zIndexOffset={100}
          >
            {(unit.status === 'en_route' || unit.status === 'dispatched') && (
              <Tooltip permanent direction="top" offset={[0, -16]}>
                {unit.id} · {formatLabel(unit.status)}
              </Tooltip>
            )}
            <Popup>
              <div className="map-popup">
                <p className="font-mono text-xs text-slate-500">{unit.id}</p>
                <h3 className="mt-1 font-semibold">{unit.name}</h3>
                <p>
                  {unitTypeLabels[unit.type]} · {unit.area}
                </p>
                <StatusBadge value={unit.status} />
                <p>Last updated: {formatTime(unit.lastUpdatedAt)} SGT</p>
              </div>
            </Popup>
          </Marker>
        ))}
      </MapContainer>
      <div className="flex flex-wrap gap-x-5 gap-y-2 border-t border-slate-100 px-5 py-3 text-[11px] text-slate-500">
        <span>
          <b className="mr-1 text-rose-600">●</b> Incident
        </span>
        <span>
          <b className="mr-1 text-teal-700">A</b> Ambulance
        </span>
        <span>
          <b className="mr-1 text-orange-600">F</b> Fire truck
        </span>
        <span>
          <b className="mr-1 text-blue-600">P</b> Police
        </span>
        <span className="ml-auto text-slate-400">
          Scroll to zoom disabled · Use + / −
        </span>
      </div>
    </section>
  )
}
