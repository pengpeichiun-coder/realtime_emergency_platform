import type { IncidentType, UnitType } from '../types/domain'

const singaporeTime = new Intl.DateTimeFormat('en-SG', {
  timeZone: 'Asia/Singapore',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
})

export function formatTime(iso: string): string {
  return singaporeTime.format(new Date(iso))
}

export function formatLabel(value: string): string {
  return value
    .replaceAll('_', ' ')
    .replace(/\b\w/g, (letter) => letter.toUpperCase())
}

export const unitTypeLabels: Record<UnitType, string> = {
  ambulance: 'Ambulance',
  fire_truck: 'Fire truck',
  police: 'Police',
}

export const incidentTypeLabels: Record<IncidentType, string> = {
  fire: 'Fire',
  medical: 'Medical',
  traffic: 'Traffic',
  security: 'Security',
}
