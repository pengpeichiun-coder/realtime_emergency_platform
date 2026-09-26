import type { OperationsSnapshot } from '../types/domain.ts'
import { applyServerEvent, parseServerEvent } from './events.ts'
import type { ServerEvent } from './events.ts'

export type ConnectionStatus =
  | 'connecting'
  | 'syncing'
  | 'live'
  | 'reconnecting'
  | 'stopped'
type Socket = Pick<
  WebSocket,
  'onopen' | 'onmessage' | 'onclose' | 'onerror' | 'close'
> &
  Partial<Pick<WebSocket, 'send'>>
interface Options {
  url: string
  authenticate?: () => Promise<string>
  fetchSnapshot: (signal: AbortSignal) => Promise<OperationsSnapshot>
  onSnapshot: (snapshot: OperationsSnapshot) => void
  onStatus: (status: ConnectionStatus) => void
  onError: (message: string | null) => void
  onReset: () => void
  createSocket?: (url: string) => Socket
  retryBaseMs?: number
  retryMaxMs?: number
}

/** Synchronizes REST snapshots with one ordered WebSocket stream. No domain simulation. */
export class OperationsStream {
  private socket: Socket | null = null
  private current: OperationsSnapshot | null = null
  private buffer: ServerEvent[] = []
  private controller: AbortController | null = null
  private retry: ReturnType<typeof setTimeout> | null = null
  private handshake: ReturnType<typeof setTimeout> | null = null
  private disposed = false
  private syncing = false
  private syncVersion = 0
  private streamId: string | null = null
  private attempts = 0
  private options: Options

  constructor(options: Options) {
    this.options = options
  }

  start() {
    if (this.socket || this.disposed) return
    this.options.onStatus(this.attempts ? 'reconnecting' : 'connecting')
    const socket = (this.options.createSocket ?? ((url) => new WebSocket(url)))(
      this.options.url,
    )
    this.socket = socket
    this.streamId = null
    this.handshake = setTimeout(() => {
      if (this.socket === socket && !this.streamId) {
        this.fail('Realtime handshake timed out. Reconnecting…')
      }
    }, 5000)
    socket.onopen = () => {
      if (this.disposed || this.socket !== socket || !this.options.authenticate) return
      void this.options
        .authenticate()
        .then((token) => {
          if (!this.disposed && this.socket === socket)
            socket.send?.(
              JSON.stringify({ type: 'authenticate', access_token: token }),
            )
        })
        .catch(() => {
          if (!this.disposed && this.socket === socket)
            this.fail('Session expired. Please sign in again.')
        })
    }
    socket.onmessage = (event) => {
      if (this.disposed || this.socket !== socket) return
      try {
        if (typeof event.data !== 'string') {
          throw new Error('Unexpected realtime message format.')
        }
        const message = parseServerEvent(event.data)
        if (message.type === 'connection.ready') {
          if (this.handshake) clearTimeout(this.handshake)
          this.handshake = null
          this.streamId = message.stream_id
          void this.refresh()
        } else if (this.syncing || !this.current || !this.streamId) {
          this.buffer.push(message)
          if (this.buffer.length > 1000) {
            this.fail(
              'Realtime backlog exceeded the client limit. Resynchronizing…',
            )
          }
        } else {
          try {
            const previous = this.current
            this.current = applyServerEvent(previous, message)
            if (this.current !== previous) {
              if (message.type === 'state.reset') this.options.onReset()
              this.options.onSnapshot(this.current)
            }
          } catch {
            // A gap or server restart is repaired with a fresh REST snapshot.
            this.buffer.push(message)
            void this.refresh()
          }
        }
      } catch (error) {
        this.fail(
          error instanceof Error
            ? error.message
            : 'Invalid realtime event. Reconnecting…',
        )
      }
    }
    socket.onerror = () => {
      if (!this.disposed && this.socket === socket) {
        this.fail('Cannot reach the realtime server. Reconnecting…')
      }
    }
    socket.onclose = () => {
      if (!this.disposed && this.socket === socket) this.reconnect()
    }
  }

  async refresh(): Promise<boolean> {
    if (this.disposed || !this.streamId) return false
    const version = ++this.syncVersion
    this.controller?.abort()
    const controller = new AbortController()
    this.controller = controller
    this.syncing = true
    this.options.onStatus('syncing')
    try {
      let snapshot = await this.options.fetchSnapshot(controller.signal)
      if (
        this.disposed ||
        controller.signal.aborted ||
        version !== this.syncVersion
      ) {
        return false
      }
      if (snapshot.streamId !== this.streamId) {
        throw new Error(
          'Server restarted during synchronization. Reconnecting…',
        )
      }
      const reset = this.buffer.some(
        (event) =>
          event.type === 'state.reset' &&
          event.stream_id === snapshot.streamId &&
          event.sequence > (this.current?.sequence ?? -1),
      )
      for (const event of this.buffer) {
        snapshot = applyServerEvent(snapshot, event)
      }
      this.buffer = []
      this.current = snapshot
      this.syncing = false
      this.attempts = 0
      if (reset) this.options.onReset()
      this.options.onSnapshot(snapshot)
      this.options.onError(null)
      this.options.onStatus('live')
      return true
    } catch (error) {
      if (
        !this.disposed &&
        !controller.signal.aborted &&
        version === this.syncVersion
      ) {
        this.fail(
          error instanceof Error
            ? error.message
            : 'State synchronization failed. Reconnecting…',
        )
      }
      return false
    }
  }

  private fail(message: string) {
    if (this.disposed) return
    this.options.onError(message)
    const socket = this.socket
    // Schedule independently of close: a broken transport may not emit onclose.
    this.reconnect()
    socket?.close(4000, 'Resynchronize')
  }

  private reconnect() {
    this.socket = null
    this.streamId = null
    ++this.syncVersion
    this.controller?.abort()
    this.buffer = []
    this.syncing = false
    if (this.handshake) clearTimeout(this.handshake)
    this.handshake = null
    if (this.disposed || this.retry) return
    this.options.onStatus('reconnecting')
    const delay = Math.min(
      (this.options.retryBaseMs ?? 500) * 2 ** Math.min(this.attempts++, 10),
      this.options.retryMaxMs ?? 10000,
    )
    this.retry = setTimeout(() => {
      this.retry = null
      this.start()
    }, delay)
  }

  stop() {
    this.disposed = true
    ++this.syncVersion
    this.controller?.abort()
    if (this.retry) clearTimeout(this.retry)
    if (this.handshake) clearTimeout(this.handshake)
    this.retry = this.handshake = null
    const socket = this.socket
    this.socket = null
    this.buffer = []
    if (socket) {
      socket.onmessage = null
      socket.onclose = null
      socket.onerror = null
      socket.close(1000, 'Workspace closed')
    }
    this.options.onStatus('stopped')
  }
}
