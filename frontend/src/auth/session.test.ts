import { test } from 'node:test'
import assert from 'node:assert/strict'
import axios, { AxiosError } from 'axios'
import type { InternalAxiosRequestConfig } from 'axios'
import {
  AuthSession,
  canAdmin,
  canOperate,
  protectedDestination,
} from './session.ts'
import type { Transport, User } from './session.ts'
import { attachAuthentication } from './interceptors.ts'
const user: User = {
  id: 'U1',
  organization_id: 'ORG-A',
  email: 'admin@example.com',
  display_name: 'Admin',
  role: 'ADMIN',
  is_active: true,
}
function setup(overrides: Partial<Transport> = {}) {
  const calls = { refresh: 0, logout: 0, login: 0, me: 0 }
  const session = new AuthSession({
    login: async () => {
      calls.login++
      return { access_token: 'login', expires_in: 600 }
    },
    register: async () => ({ access_token: 'register', expires_in: 600 }),
    refresh: async () => {
      calls.refresh++
      return { access_token: 'refresh-' + calls.refresh, expires_in: 600 }
    },
    logout: async () => {
      calls.logout++
    },
    me: async () => {
      calls.me++
      return user
    },
    ...overrides,
  })
  return { session, calls }
}
test('login loads current identity and logout clears in-memory access', async () => {
  const { session, calls } = setup()
  await session.signIn({ email: user.email, password: 'test-password' })
  assert.equal(session.snapshot().status, 'authenticated')
  assert.equal(session.accessToken(), 'login')
  await session.logout()
  assert.equal(session.accessToken(), null)
  assert.equal(session.snapshot().user, null)
  assert.equal(calls.logout, 1)
})
test('registration loads its new organization identity', async () => {
  const { session } = setup()
  await session.signIn({}, true)
  assert.equal(session.accessToken(), 'register')
  assert.equal(session.snapshot().user?.organization_id, 'ORG-A')
})
test('reload restores through refresh and me; simultaneous restoration is single flight', async () => {
  const { session, calls } = setup()
  await Promise.all([session.restore(), session.restore(), session.restore()])
  assert.equal(calls.refresh, 1)
  assert.equal(calls.me, 1)
  assert.equal(session.snapshot().status, 'authenticated')
})
test('failed refresh clears identity and protects routes', async () => {
  const { session } = setup({
    refresh: async () => {
      throw new Error('Expired cookie')
    },
  })
  await session.signIn({})
  await assert.rejects(session.refresh())
  assert.equal(session.accessToken(), null)
  assert.equal(protectedDestination(session.snapshot()), '/login')
})
test('failed login cannot establish an identity', async () => {
  const { session } = setup({
    login: async () => {
      throw new Error('Invalid credentials')
    },
  })
  await session.restore()
  session.clear()
  await assert.rejects(session.signIn({}))
  assert.equal(session.snapshot().status, 'anonymous')
})
test('logout waits for ongoing rotation and prevents late session resurrection', async () => {
  let resolve!: (value: { access_token: string; expires_in: number }) => void
  const { session, calls } = setup({
    refresh: () =>
      new Promise((done) => {
        resolve = done
      }),
  })
  const refresh = session.refresh()
  const logout = session.logout()
  resolve({ access_token: 'late', expires_in: 600 })
  await assert.rejects(refresh)
  await logout
  assert.equal(session.accessToken(), null)
  assert.equal(session.snapshot().status, 'anonymous')
  assert.equal(calls.logout, 1)
})
test('valid token avoids unnecessary refresh; near-expired token renews', async () => {
  const { session, calls } = setup({
    login: async () => ({ access_token: 'short', expires_in: 0 }),
  })
  await session.signIn({})
  await session.validToken()
  assert.equal(calls.refresh, 1)
  await session.validToken()
  assert.equal(calls.refresh, 1)
})
test('role policies and protected navigation match all three roles', () => {
  for (const role of ['ADMIN', 'DISPATCHER', 'RESPONDER'] as const) {
    const account = { ...user, role }
    assert.equal(canOperate(account), role !== 'RESPONDER')
    assert.equal(canAdmin(account), role === 'ADMIN')
    assert.equal(
      protectedDestination({ status: 'authenticated', user: account }),
      null,
    )
  }
  assert.equal(canOperate(null), false)
  assert.equal(canAdmin(null), false)
  assert.equal(
    protectedDestination({ status: 'anonymous', user: null }),
    '/login',
  )
})
function unauthorized(config: InternalAxiosRequestConfig) {
  return new AxiosError('Unauthorized', 'ERR_BAD_REQUEST', config, undefined, {
    status: 401,
    statusText: 'Unauthorized',
    data: {},
    headers: {},
    config,
  })
}
test('Axios concurrent 401s rotate once and retry each request once with the new token', async () => {
  const { session, calls } = setup()
  await session.signIn({})
  let requests = 0
  const client = axios.create({
    adapter: async (config) => {
      requests++
      if (config.headers.get('Authorization') === 'Bearer login')
        throw unauthorized(config)
      assert.equal(config.headers.get('Authorization'), 'Bearer refresh-1')
      return { status: 200, statusText: 'OK', data: {}, headers: {}, config }
    },
  })
  attachAuthentication(client, session)
  await Promise.all([
    client.get('/one'),
    client.get('/two'),
    client.get('/three'),
  ])
  assert.equal(calls.refresh, 1)
  assert.equal(requests, 6)
})
test('Axios cannot loop on persistent 401 and clears the session', async () => {
  const { session, calls } = setup()
  await session.signIn({})
  let requests = 0
  const client = axios.create({
    adapter: async (config) => {
      requests++
      throw unauthorized(config)
    },
  })
  attachAuthentication(client, session)
  await assert.rejects(client.get('/denied'))
  assert.equal(requests, 2)
  assert.equal(calls.refresh, 1)
  assert.equal(session.accessToken(), null)
})
test('Axios refresh failure does not retry a protected request', async () => {
  const { session } = setup({
    refresh: async () => {
      throw new Error('Revoked')
    },
  })
  await session.signIn({})
  let requests = 0
  const client = axios.create({
    adapter: async (config) => {
      requests++
      throw unauthorized(config)
    },
  })
  attachAuthentication(client, session)
  await assert.rejects(client.get('/denied'))
  assert.equal(requests, 1)
  assert.equal(session.snapshot().status, 'anonymous')
})

test('late 401 after logout cannot silently refresh the cleared session', async () => {
  const { session, calls } = setup()
  await session.signIn({})
  let rejectRequest!: () => void
  const client = axios.create({
    adapter: (config) =>
      new Promise((_, reject) => {
        rejectRequest = () => reject(unauthorized(config))
      }),
  })
  attachAuthentication(client, session)
  const request = client.get('/old-session')
  await new Promise((resolve) => setTimeout(resolve, 0))
  await session.logout()
  rejectRequest()
  await assert.rejects(request)
  assert.equal(calls.refresh, 0)
  assert.equal(session.snapshot().status, 'anonymous')
})

test('failed server logout displays an honest notice without retaining access credentials', async () => {
  const { session } = setup({
    logout: async () => {
      throw new Error('Network unavailable')
    },
  })
  await session.signIn({})
  await session.logout()
  assert.equal(session.accessToken(), null)
  assert.match(session.snapshot().notice ?? '', /could not be confirmed/)
})
