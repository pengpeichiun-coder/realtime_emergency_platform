import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { Navigate } from 'react-router-dom'
import { useAuth } from '../auth/context'
import { canAdmin } from '../auth/session'
import type { User } from '../auth/session'
import { apiErrorMessage, client } from '../api/client'
export function UsersPage() {
  const { user } = useAuth()
  const [users, setUsers] = useState<User[]>([])
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)
  const allowed = canAdmin(user)
  async function load() {
    setUsers((await client.get<User[]>('/users')).data)
  }
  useEffect(() => {
    if (!allowed) return
    const controller = new AbortController()
    void client
      .get<User[]>('/users', { signal: controller.signal })
      .then((response) => setUsers(response.data))
      .catch((e) => {
        if (!controller.signal.aborted) setError(apiErrorMessage(e))
      })
    return () => controller.abort()
  }, [allowed])
  if (!allowed) return <Navigate to="/dashboard" replace />
  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = event.currentTarget
    setPending(true)
    setError(null)
    try {
      await client.post('/users', Object.fromEntries(new FormData(form)))
      form.reset()
      await load()
    } catch (e) {
      setError(apiErrorMessage(e))
    } finally {
      setPending(false)
    }
  }
  async function change(
    id: string,
    field: 'role' | 'active',
    value: string | boolean,
  ) {
    setPending(true)
    setError(null)
    try {
      await client.patch(
        `/users/${id}/${field}`,
        field === 'role' ? { role: value } : { is_active: value },
      )
      await load()
    } catch (e) {
      setError(apiErrorMessage(e))
    } finally {
      setPending(false)
    }
  }
  return (
    <>
      <p className="kicker">ORGANIZATION ADMINISTRATION</p>
      <h1 className="page-title">Manage users</h1>
      {error && (
        <p role="alert" className="my-4 text-sm text-red-700">
          {error}
        </p>
      )}
      <form
        aria-label="Create user"
        onSubmit={create}
        className="my-6 grid gap-4 rounded-xl bg-white p-5 sm:grid-cols-2"
      >
        <label>
          Display name
          <input
            name="display_name"
            required
            maxLength={120}
            className="form-input"
          />
        </label>
        <label>
          Email
          <input name="email" type="email" required className="form-input" />
        </label>
        <label>
          Initial password
          <input
            name="password"
            type="password"
            required
            minLength={12}
            maxLength={128}
            autoComplete="new-password"
            className="form-input"
          />
        </label>
        <label>
          Role
          <select name="role" className="form-input" defaultValue="RESPONDER">
            <option>RESPONDER</option>
            <option>DISPATCHER</option>
            <option>ADMIN</option>
          </select>
        </label>
        <button className="primary-button" disabled={pending}>
          Create user
        </button>
      </form>
      <ul aria-label="Organization users" className="space-y-3">
        {users.map((item) => (
          <li
            key={item.id}
            className="flex flex-wrap items-center justify-between gap-4 rounded-xl bg-white p-5"
          >
            <div>
              <p className="font-semibold">{item.display_name}</p>
              <p className="text-sm text-slate-500">{item.email}</p>
            </div>
            <div className="flex items-center gap-4">
              <select
                aria-label={`Role for ${item.email}`}
                value={item.role}
                disabled={pending}
                onChange={(event) =>
                  void change(item.id, 'role', event.target.value)
                }
                className="form-input"
              >
                <option>ADMIN</option>
                <option>DISPATCHER</option>
                <option>RESPONDER</option>
              </select>
              <button
                className="text-sm text-teal-700"
                disabled={pending}
                onClick={() => void change(item.id, 'active', !item.is_active)}
              >
                {item.is_active ? 'Deactivate' : 'Activate'}
              </button>
            </div>
          </li>
        ))}
      </ul>
    </>
  )
}
