import ws from 'k6/ws'
import http from 'k6/http'
import { check } from 'k6'
import { base, login, optionsFor } from './common.js'
export const options = { vus: 3, duration: '12s', thresholds: { checks: ['rate==1'] } }
export const setup = login
export default function ({ token }) {
  let ready = false
  let event = false
  const response = ws.connect(base.replace(/^http/, 'ws') + '/ws/events', { headers: { Origin: base } }, socket => {
    socket.on('open', () => socket.send(JSON.stringify({ type: 'authenticate', access_token: token })))
    socket.on('message', raw => {
      const message = JSON.parse(raw)
      ready ||= message.type === 'connection.ready'
      event ||= message.type === 'incident.updated'
    })
    socket.setTimeout(() => {
      // A bounded update of one fictional demo incident; no unbounded record creation.
      check(http.patch(base + '/api/incidents/INC-001', JSON.stringify({ description: 'Fictional k6 realtime validation' }), optionsFor(token)), {
        'bounded update accepted': r => r.status === 200,
      })
    }, 500)
    socket.setTimeout(() => socket.close(), 2500)
  })
  check(response, { 'websocket upgraded': r => r.status === 101 })
  check(ready, { 'authenticated ready': value => value })
  check(event, { 'domain event received': value => value })
}
