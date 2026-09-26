import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { Navigate } from 'react-router-dom'
import { useAuth } from '../auth/context'
import { canAdmin } from '../auth/session'
import { client, apiErrorMessage } from '../api/client'
const events = [
  'incident.created',
  'incident.updated',
  'incident.resolved',
  'unit.dispatched',
  'unit.arrived',
  'dispatch.completed',
]
interface Subscription {
  id: string
  name: string
  url: string
  event_types: string[]
  is_active: boolean
}
interface Delivery {
  id: string
  event_type: string
  status: string
  attempt_count: number
  last_http_status: number | null
  last_error: string | null
  created_at: string
}
export function WebhooksPage() {
  const { user } = useAuth()
  const allowed = canAdmin(user)
  const [subscriptions, setSubscriptions] = useState<Subscription[]>([])
  const [deliveries, setDeliveries] = useState<Delivery[]>([])
  const [error, setError] = useState<string | null>(null)
  const [secret, setSecret] = useState<string | null>(null)
  const [pending, setPending] = useState(false)
  useEffect(() => {
    if (!allowed) return
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    async function load() {
      try {
        const [subs, history] = await Promise.all([
          client.get<Subscription[]>('/webhooks', {
            signal: controller.signal,
          }),
          client.get<Delivery[]>('/webhook-deliveries', {
            signal: controller.signal,
          }),
        ])
        setSubscriptions(subs.data)
        setDeliveries(history.data)
      } catch (e) {
        if (!controller.signal.aborted) setError(apiErrorMessage(e))
      }
      if (!controller.signal.aborted)
        timer = setTimeout(() => void load(), 2000)
    }
    void load()
    return () => {
      controller.abort()
      clearTimeout(timer)
    }
  }, [allowed])
  if (!allowed) return <Navigate to="/dashboard" replace />
  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = event.currentTarget
    const data = new FormData(form)
    setPending(true)
    setError(null)
    setSecret(null)
    try {
      const result = await client.post<
        Subscription & { signing_secret: string }
      >('/webhooks', {
        name: data.get('name'),
        url: data.get('url'),
        event_types: data.getAll('events'),
        is_active: data.has('active'),
      })
      setSecret(result.data.signing_secret)
      setSubscriptions((items) => [result.data, ...items])
      form.reset()
    } catch (e) {
      setError(apiErrorMessage(e))
    } finally {
      setPending(false)
    }
  }
  async function change(sub: Subscription, remove = false) {
    setPending(true)
    setError(null)
    try {
      if (remove) {
        await client.delete(`/webhooks/${sub.id}`)
        setSubscriptions((items) => items.filter((item) => item.id !== sub.id))
      } else {
        const result = await client.patch<Subscription>(`/webhooks/${sub.id}`, {
          is_active: !sub.is_active,
        })
        setSubscriptions((items) =>
          items.map((item) => (item.id === sub.id ? result.data : item)),
        )
      }
    } catch (e) {
      setError(apiErrorMessage(e))
    } finally {
      setPending(false)
    }
  }
  return (
    <>
      <p className="kicker">ADMIN INTEGRATIONS</p>
      <h1 className="page-title">Webhooks</h1>
      {error && (
        <p role="alert" className="my-4 text-red-700">
          {error}
        </p>
      )}
      {secret && (
        <div role="status" className="my-4 rounded-lg bg-amber-50 p-4">
          <strong>Signing secret — shown once</strong>
          <p>
            Save this secret in your receiver. It cannot be retrieved later.
          </p>
          <code className="block break-all">{secret}</code>
          <button className="mt-2 underline" onClick={() => setSecret(null)}>
            Dismiss secret
          </button>
        </div>
      )}
      <form
        onSubmit={(event) => void create(event)}
        className="my-6 grid max-w-2xl gap-4 rounded-xl bg-white p-5"
      >
        <h2 className="font-semibold">Create webhook</h2>
        <label>
          Name
          <input
            name="name"
            required
            maxLength={120}
            className="mt-1 block w-full rounded border p-2"
          />
        </label>
        <label>
          URL
          <input
            name="url"
            type="url"
            required
            maxLength={2048}
            placeholder="https://receiver.example.com/events"
            className="mt-1 block w-full rounded border p-2"
          />
        </label>
        <fieldset>
          <legend>Events (select at least one)</legend>
          {events.map((event) => (
            <label key={event} className="mt-2 block text-sm">
              <input
                type="checkbox"
                name="events"
                value={event}
                defaultChecked={event === 'incident.created'}
                className="mr-2"
              />
              {event}
            </label>
          ))}
        </fieldset>
        <label>
          <input
            type="checkbox"
            name="active"
            defaultChecked
            className="mr-2"
          />
          Active
        </label>
        <button
          disabled={pending}
          className="rounded bg-teal-700 p-2 text-white disabled:opacity-50"
        >
          Create webhook
        </button>
      </form>
      <h2 className="text-lg font-semibold">Subscriptions</h2>
      <ul className="my-4 space-y-3">
        {subscriptions.map((sub) => (
          <li key={sub.id} className="rounded-xl bg-white p-4">
            <strong>{sub.name}</strong>
            <p className="break-all text-sm">{sub.url}</p>
            <p className="text-sm">{sub.event_types.join(', ')}</p>
            <p>{sub.is_active ? 'Active' : 'Inactive'}</p>
            <button
              disabled={pending}
              className="mr-4 text-teal-700"
              onClick={() => void change(sub)}
            >
              {sub.is_active ? 'Disable' : 'Enable'}
            </button>
            <button
              disabled={pending}
              className="text-red-700"
              onClick={() => void change(sub, true)}
            >
              Delete
            </button>
          </li>
        ))}
      </ul>
      <h2 className="text-lg font-semibold">Delivery history</h2>
      <p className="text-sm text-slate-500">
        Latest 100 deliveries. Receivers must deduplicate the delivery ID.
      </p>
      <div className="mt-4 overflow-auto">
        <table className="w-full bg-white text-left text-sm">
          <thead>
            <tr>
              {[
                'Event',
                'Status',
                'Attempts',
                'HTTP status',
                'Last error',
                'Time',
              ].map((label) => (
                <th key={label} className="p-3">
                  {label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {deliveries.map((item) => (
              <tr key={item.id} className="border-t">
                <td className="p-3">
                  {item.event_type}
                  <small className="block">{item.id}</small>
                </td>
                <td className="p-3">{item.status}</td>
                <td className="p-3">{item.attempt_count}</td>
                <td className="p-3">{item.last_http_status ?? '—'}</td>
                <td className="p-3">{item.last_error ?? '—'}</td>
                <td className="p-3">
                  {new Date(item.created_at).toLocaleString()}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}
