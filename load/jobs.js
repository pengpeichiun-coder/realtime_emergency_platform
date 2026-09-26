import http from 'k6/http'
import { check, sleep } from 'k6'
import { base, login, optionsFor } from './common.js'
export const options = { scenarios: { reports: { executor: 'per-vu-iterations', vus: 5, iterations: 1, maxDuration: '45s' } }, thresholds: { checks: ['rate==1'], http_req_failed: ['rate<0.01'] } }
export const setup = login
export default function ({ token }) {
  const created = http.post(base + '/api/incidents/INC-001/reports', null, optionsFor(token))
  if (!check(created, { 'report accepted': r => r.status === 202 })) return
  const identifier = created.json('job_id')
  let job
  for (let attempt = 0; attempt < 60; attempt++) {
    const response = http.get(base + '/api/jobs/' + identifier, optionsFor(token))
    job = response.json()
    if (['SUCCEEDED', 'FAILED'].includes(job.status)) break
    sleep(.5)
  }
  check(job, { 'report succeeded': value => value.status === 'SUCCEEDED',
    'one execution per job': value => value.attempt_count === 1,
    'matching durable job': value => value.id === identifier && !!value.result_payload })
}
