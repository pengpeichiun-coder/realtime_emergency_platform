import http from 'k6/http'
import { check } from 'k6'
export const base = __ENV.BASE_URL || 'http://127.0.0.1:8080'
export function login() {
  const response = http.post(`${base}/api/auth/login`, JSON.stringify({
    email: __ENV.LOAD_EMAIL || 'admin@demo.example.com',
    password: __ENV.LOAD_PASSWORD || 'Demo-Only-Response-2026!',
  }), { headers: { 'Content-Type': 'application/json' } })
  if (!check(response, { 'login successful': r => r.status === 200 })) throw new Error('Login failed')
  return { token: response.json('access_token') }
}
export function optionsFor(token) {
  return { headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' } }
}
