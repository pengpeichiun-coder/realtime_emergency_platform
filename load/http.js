import http from 'k6/http'
import { check, sleep } from 'k6'
import { base, login, optionsFor } from './common.js'
export const options = { vus: 5, duration: '15s', thresholds: { http_req_failed: ['rate<0.01'], checks: ['rate==1'] } }
export const setup = login
export default function ({ token }) {
  for (const path of ['/api/state', '/api/incidents', '/api/units', '/api/activity', '/api/jobs']) {
    check(http.get(base + path, optionsFor(token)), { 'read succeeds': r => r.status === 200 })
  }
  sleep(1)
}
