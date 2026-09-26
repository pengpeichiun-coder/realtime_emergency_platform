export type Role = 'ADMIN' | 'DISPATCHER' | 'RESPONDER'
export interface User {
  id: string
  organization_id: string
  email: string
  display_name: string
  role: Role
  is_active: boolean
  can_reset_demo?: boolean
}
export interface Tokens {
  access_token: string
  expires_in: number
}
export interface Transport {
  login: (body: Record<string, string>) => Promise<Tokens>
  register: (body: Record<string, string>) => Promise<Tokens>
  refresh: () => Promise<Tokens>
  logout: () => Promise<void>
  me: (token: string) => Promise<User>
}
export interface AuthState {
  status: 'loading' | 'anonymous' | 'authenticated'
  user: User | null
  notice?: string
}
export const canOperate = (user: User | null) =>
  user?.role === 'ADMIN' || user?.role === 'DISPATCHER'
export const canAdmin = (user: User | null) => user?.role === 'ADMIN'
export const protectedDestination = (state: AuthState) =>
  state.status === 'anonymous' ? '/login' : null

/** Memory-only credentials; one shared refresh, including session restoration. */
export class AuthSession {
  private token: string | null = null
  private expires = 0
  private generation = 0
  private pending: Promise<string> | null = null
  private listeners = new Set<() => void>()
  private state: AuthState = { status: 'loading', user: null }
  private transport: Transport
  constructor(transport: Transport) {
    this.transport = transport
  }
  snapshot = () => this.state
  subscribe = (callback: () => void) => {
    this.listeners.add(callback)
    return () => {
      this.listeners.delete(callback)
    }
  }
  private publish(state: AuthState) {
    this.state = state
    this.listeners.forEach((callback) => callback())
  }
  accessToken = () => this.token
  revision = () => this.generation
  clear() {
    ++this.generation
    this.token = null
    this.expires = 0
    this.publish({ status: 'anonymous', user: null })
  }
  private async accept(tokens: Tokens, generation: number) {
    const user = await this.transport.me(tokens.access_token)
    if (generation !== this.generation)
      throw new Error('Session changed during authentication.')
    this.token = tokens.access_token
    this.expires = Date.now() + tokens.expires_in * 1000
    this.publish({ status: 'authenticated', user })
    return this.token
  }
  async signIn(body: Record<string, string>, register = false) {
    const generation = ++this.generation
    const tokens = await (register
      ? this.transport.register(body)
      : this.transport.login(body))
    return this.accept(tokens, generation)
  }
  refresh() {
    if (this.pending) return this.pending
    const generation = this.generation
    this.pending = this.transport
      .refresh()
      .then((tokens) => this.accept(tokens, generation))
      .catch((error) => {
        if (generation === this.generation) this.clear()
        throw error
      })
      .finally(() => {
        this.pending = null
      })
    return this.pending
  }
  async restore() {
    try {
      await this.refresh()
    } catch {
      /* refresh clears unavailable sessions */
    }
  }
  async validToken() {
    if (this.token && Date.now() < this.expires - 15000) return this.token
    return this.refresh()
  }
  async logout() {
    this.clear()
    // Settle an already-running rotation before revoking the resulting cookie.
    await this.pending?.catch(() => undefined)
    try {
      await this.transport.logout()
    } catch {
      this.publish({
        status: 'anonymous',
        user: null,
        notice:
          'Local session cleared, but server sign-out could not be confirmed. Reconnect and sign out again before using a shared device.',
      })
    }
  }
}
