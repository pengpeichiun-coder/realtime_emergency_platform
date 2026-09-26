import { useId, useState } from 'react'
import type { FormEvent } from 'react'
import type {
  CreateIncidentInput,
  IncidentSeverity,
  IncidentType,
} from '../../types/domain'
import { formatLabel, incidentTypeLabels } from '../../utils/format'

interface CreateIncidentFormProps {
  onCreate: (input: CreateIncidentInput) => Promise<boolean>
  onCancel: () => void
  pending: boolean
}

const incidentTypes: IncidentType[] = ['fire', 'medical', 'traffic', 'security']
const severityLevels: IncidentSeverity[] = ['low', 'medium', 'high', 'critical']

export function CreateIncidentForm({
  onCreate,
  onCancel,
  pending,
}: CreateIncidentFormProps) {
  const formId = useId()
  const [validationError, setValidationError] = useState<string | null>(null)

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (pending) return

    const data = new FormData(event.currentTarget)
    const title = String(data.get('title') ?? '').trim()
    const area = String(data.get('area') ?? '').trim()
    const description = String(data.get('description') ?? '').trim()
    const type = incidentTypes.find((value) => value === data.get('type'))
    const severity = severityLevels.find((value) => value === data.get('severity'))
    const latitudeText = String(data.get('latitude') ?? '').trim()
    const longitudeText = String(data.get('longitude') ?? '').trim()
    const latitude = Number(latitudeText)
    const longitude = Number(longitudeText)

    if (!title || !area || !description) {
      setValidationError(
        'Enter a title, area, and description. Spaces alone are not accepted.',
      )
      return
    }
    if (title.length > 120 || area.length > 120 || description.length > 2000) {
      setValidationError(
        'Keep the title and area within 120 characters and the description within 2,000 characters.',
      )
      return
    }
    if (!type || !severity) {
      setValidationError('Choose an incident type and severity.')
      return
    }
    if (
      !latitudeText ||
      !longitudeText ||
      !Number.isFinite(latitude) ||
      !Number.isFinite(longitude) ||
      latitude < -90 ||
      latitude > 90 ||
      longitude < -180 ||
      longitude > 180
    ) {
      setValidationError(
        'Enter a latitude from −90 to 90 and a longitude from −180 to 180.',
      )
      return
    }

    setValidationError(null)
    await onCreate({ type, title, area, severity, description, latitude, longitude })
  }

  return (
    <section
      role="region"
      aria-label="Create incident"
      className="rounded-xl border border-teal-200 bg-white p-5 sm:p-6"
    >
      <p className="kicker">NEW REPORT</p>
      <h2 className="mt-1 text-lg font-semibold text-slate-800">Create incident</h2>
      <p id={`${formId}-help`} className="mt-2 text-sm leading-6 text-slate-500">
        Fictional demo incident. Coordinates are entered manually.
      </p>

      <form
        onSubmit={handleSubmit}
        aria-describedby={`${formId}-help`}
        aria-busy={pending}
        className="mt-5"
      >
        <fieldset disabled={pending} className="min-w-0 space-y-4">
          <legend className="sr-only">Incident details</legend>
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <label
                htmlFor={`${formId}-type`}
                className="mb-2 block text-xs font-semibold text-slate-700"
              >
                Type
              </label>
              <select
                id={`${formId}-type`}
                name="type"
                defaultValue="fire"
                required
                className="form-input"
              >
                {incidentTypes.map((type) => (
                  <option key={type} value={type}>
                    {incidentTypeLabels[type]}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label
                htmlFor={`${formId}-severity`}
                className="mb-2 block text-xs font-semibold text-slate-700"
              >
                Severity
              </label>
              <select
                id={`${formId}-severity`}
                name="severity"
                defaultValue="high"
                required
                className="form-input"
              >
                {severityLevels.map((severity) => (
                  <option key={severity} value={severity}>
                    {formatLabel(severity)}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div>
            <label
              htmlFor={`${formId}-title`}
              className="mb-2 block text-xs font-semibold text-slate-700"
            >
              Title
            </label>
            <input
              id={`${formId}-title`}
              name="title"
              type="text"
              required
              maxLength={120}
              placeholder="Briefly describe the incident"
              className="form-input"
            />
          </div>

          <div>
            <label
              htmlFor={`${formId}-area`}
              className="mb-2 block text-xs font-semibold text-slate-700"
            >
              Area
            </label>
            <input
              id={`${formId}-area`}
              name="area"
              type="text"
              defaultValue="Marina Bay"
              required
              maxLength={120}
              className="form-input"
            />
          </div>

          <div>
            <label
              htmlFor={`${formId}-description`}
              className="mb-2 block text-xs font-semibold text-slate-700"
            >
              Description
            </label>
            <textarea
              id={`${formId}-description`}
              name="description"
              required
              maxLength={2000}
              rows={3}
              placeholder="Add details to help responders assess the situation"
              className="form-input resize-y"
            />
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <label
                htmlFor={`${formId}-latitude`}
                className="mb-2 block text-xs font-semibold text-slate-700"
              >
                Latitude
              </label>
              <input
                id={`${formId}-latitude`}
                name="latitude"
                type="number"
                step="any"
                min={-90}
                max={90}
                defaultValue="1.2834"
                required
                className="form-input"
              />
            </div>
            <div>
              <label
                htmlFor={`${formId}-longitude`}
                className="mb-2 block text-xs font-semibold text-slate-700"
              >
                Longitude
              </label>
              <input
                id={`${formId}-longitude`}
                name="longitude"
                type="number"
                step="any"
                min={-180}
                max={180}
                defaultValue="103.8607"
                required
                className="form-input"
              />
            </div>
          </div>

          {validationError && (
            <p
              role="alert"
              className="rounded-lg border border-red-200 bg-red-50 p-3 text-xs leading-5 text-red-700"
            >
              {validationError}
            </p>
          )}

          <div className="flex flex-wrap justify-end gap-3 border-t border-slate-100 pt-4">
            <button
              type="button"
              disabled={pending}
              onClick={onCancel}
              className="rounded-lg border border-slate-200 px-4 py-3 text-sm font-semibold text-slate-600 hover:bg-slate-50"
            >
              Cancel
            </button>
            <button type="submit" disabled={pending} className="primary-button">
              {pending ? 'Creating…' : 'Create incident'}
            </button>
          </div>
        </fieldset>
      </form>
    </section>
  )
}
