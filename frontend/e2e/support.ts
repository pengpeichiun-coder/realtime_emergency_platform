import { execFileSync } from 'node:child_process'
import { mkdirSync, readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, request, type APIRequestContext, type BrowserContext, type Locator, type Page, type Request, type Response } from '@playwright/test'

export const baseURL = process.env.E2E_BASE_URL ?? 'http://127.0.0.1:18082'
export const api2URL = process.env.E2E_API2_URL ?? 'http://127.0.0.1:18004'
export const composeRoot = process.env.E2E_COMPOSE_ROOT
export const composeProject = process.env.E2E_COMPOSE_PROJECT ?? 'emergency-platform-e2e'
export const demoEmail = process.env.E2E_ADMIN_EMAIL
export const demoPassword = process.env.E2E_DEMO_PASSWORD
const here = dirname(fileURLToPath(import.meta.url))
const screenshotRoot = resolve(here, '../../docs/screenshots')
const webhookReceiverPath = '/tmp/emergency-platform-e2e-webhook.py'
let webhookReceiverInstalled = false

export interface Session {
  accessToken: string
  email: string
}

export function requireDemoCredentials() {
  if (!demoEmail || !demoPassword) {
    throw new Error('Set E2E_ADMIN_EMAIL and E2E_DEMO_PASSWORD to the seeded fictional demo account.')
  }
  return { email: demoEmail, password: demoPassword }
}

export async function login(page: Page, email = requireDemoCredentials().email, password = requireDemoCredentials().password): Promise<Session> {
  await page.goto('/login')
  const loginPath = /\/api\/auth\/login(?:\?|$)/
  const responsePromise = page.waitForResponse(
    (response) => loginPath.test(response.url()) && response.request().method() === 'POST',
    { timeout: 45_000 },
  )
  let resolveRequestFailure!: (failure: { url: string; error: string }) => void
  const requestFailurePromise = new Promise<{ url: string; error: string }>((resolve) => {
    resolveRequestFailure = resolve
  })
  const onRequestFailed = (request: Request) => {
    if (loginPath.test(request.url()) && request.method() === 'POST') {
      resolveRequestFailure({
        url: new URL(request.url()).pathname,
        error: request.failure()?.errorText ?? 'unknown browser transport error',
      })
    }
  }
  page.on('requestfailed', onRequestFailed)
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password').fill(password)
  await page.getByRole('button', { name: 'Sign in' }).click()
  let response: Response
  try {
    const result = await Promise.race([
      responsePromise.then((value) => ({ kind: 'response' as const, value })),
      requestFailurePromise.then((value) => ({ kind: 'failure' as const, value })),
    ])
    if (result.kind === 'failure') {
      throw new Error(`Browser could not complete POST ${result.value.url}: ${result.value.error}`)
    }
    response = result.value
  } finally {
    page.off('requestfailed', onRequestFailed)
  }
  if (!response.ok()) {
    const body = (await response.json().catch(() => ({}))) as { detail?: unknown }
    const detail = typeof body.detail === 'string' ? body.detail : 'no safe API detail provided'
    throw new Error(`Login failed with HTTP ${response.status()}: ${detail}`)
  }
  const body = (await response.json()) as { access_token: string }
  await expect(page.getByRole('heading', { name: 'Operations overview' })).toBeVisible({ timeout: 60_000 })
  await expect(page.getByText('Realtime connected')).toBeVisible({ timeout: 60_000 })
  return { accessToken: body.access_token, email }
}

export function emailForRole(role: 'dispatcher' | 'responder') {
  const { email } = requireDemoCredentials()
  return `${role}@${email.slice(email.indexOf('@') + 1)}`
}

export async function apiFor(token: string): Promise<APIRequestContext> {
  return request.newContext({
    baseURL,
    extraHTTPHeaders: { Authorization: `Bearer ${token}` },
  })
}

export async function capturePortfolio(
  page: Page,
  name: string,
  options: { scrollTo?: Locator; scrollOffsetY?: number } = {},
) {
  if (process.env.E2E_CAPTURE_PORTFOLIO !== '1') return
  if (options.scrollTo) await options.scrollTo.scrollIntoViewIfNeeded()
  else await page.evaluate(() => window.scrollTo(0, 0))
  if (options.scrollOffsetY) {
    await page.evaluate((offset) => window.scrollBy(0, offset), options.scrollOffsetY)
  }
  mkdirSync(screenshotRoot, { recursive: true })
  await page.screenshot({
    path: resolve(screenshotRoot, name),
    fullPage: false,
    animations: 'disabled',
  })
}

export async function resetDemo(page: Page) {
  const snapshotRefresh = page.waitForResponse(
    (response) => response.url().endsWith('/api/state') && response.request().method() === 'GET',
  )
  const responsePromise = page.waitForResponse(
    (response) => response.url().endsWith('/api/demo/reset') && response.request().method() === 'POST',
  )
  await page.getByRole('button', { name: 'Reset demo' }).click()
  const response = await responsePromise
  expect(response.ok()).toBeTruthy()
  await snapshotRefresh
  await expect(page.getByRole('region', { name: 'Dispatch controls' })).toContainText('Select an incident')
  await expect(page.getByRole('region', { name: 'Active incidents' }).getByRole('button').filter({ hasText: 'INC-001' }).filter({ hasText: 'Building fire' }).first()).toBeVisible()
}

export async function createIncident(page: Page, title: string) {
  await page.getByRole('button', { name: 'Create incident', exact: true }).first().click()
  const form = page.getByRole('region', { name: 'Create incident' })
  await form.getByLabel('Title').fill(title)
  await form.getByLabel('Description').fill('Fictional browser validation exercise.')
  const responsePromise = page.waitForResponse(
    (response) => response.url().endsWith('/api/incidents') && response.request().method() === 'POST',
  )
  await form.getByRole('button', { name: 'Create incident', exact: true }).click()
  const response = await responsePromise
  expect(response.status()).toBe(201)
  const incident = (await response.json()) as { id: string; title: string }
  await expect(page.getByRole('button', { name: new RegExp(title) })).toBeVisible()
  return incident
}

function compose(args: string[], input?: string) {
  if (!composeRoot) {
    throw new Error('E2E_COMPOSE_ROOT is required for real worker/receiver validation.')
  }
  const composeFiles = (process.env.E2E_COMPOSE_FILES ?? 'compose.yaml,compose.e2e.yaml')
    .split(',')
    .map((file) => resolve(composeRoot, file.trim()))
  return execFileSync(
    'docker',
    [
      'compose',
      '--project-directory', composeRoot,
      '--project-name', composeProject,
      ...composeFiles.flatMap((file) => ['-f', file]),
      ...args,
    ],
    { cwd: composeRoot, input, encoding: 'utf8', stdio: ['pipe', 'pipe', 'pipe'] },
  ).trim()
}

function installWebhookReceiver() {
  if (!composeRoot) {
    throw new Error('E2E_COMPOSE_ROOT is required for real worker/receiver validation.')
  }
  const source = readFileSync(resolve(composeRoot, 'scripts/e2e_webhook_receiver.py'), 'utf8')
  compose(
    ['exec', '-T', 'worker', 'sh', '-c', `umask 077 && cat > ${webhookReceiverPath}`],
    source,
  )
  webhookReceiverInstalled = true
}

export async function startWebhookReceiver(secret: string) {
  installWebhookReceiver()
  compose(['exec', '-T', 'worker', 'python', webhookReceiverPath, 'cleanup'])
  compose(['exec', '-T', 'worker', 'python', webhookReceiverPath, 'prepare'], secret)
  compose(['exec', '-d', '-T', 'worker', 'python', webhookReceiverPath, 'serve'])
  try {
    await expect.poll(
      () => {
        try {
          return compose([
            'exec', '-T', 'worker', 'python', '-c',
            "from pathlib import Path; p=Path('/tmp/emergency-platform-e2e-webhook/ready'); print('ready') if p.exists() else exit(1)",
          ])
        } catch {
          return false
        }
      },
      {
        timeout: 20_000,
        intervals: [100, 250, 500, 1_000],
        message: 'Worker-local HMAC receiver should be listening',
      },
    ).toBeTruthy()
  } catch (error) {
    cleanupWebhookReceiver()
    throw error
  }
}

export function webhookReceiverResult() {
  const value = compose([
    'exec', '-T', 'worker', 'python', webhookReceiverPath, 'result',
  ])
  return JSON.parse(value) as {
    signature_valid: boolean
    delivery_id: string
    event_type: string
    event_id: string
  }
}

export function cleanupWebhookReceiver() {
  if (!composeRoot) return
  if (!webhookReceiverInstalled) installWebhookReceiver()
  try {
    compose(['exec', '-T', 'worker', 'python', webhookReceiverPath, 'cleanup'])
  } finally {
    compose(['exec', '-T', 'worker', 'rm', '-f', webhookReceiverPath])
    webhookReceiverInstalled = false
  }
}

export async function routeContextThroughApi2(context: BrowserContext) {
  await context.route('**/api/**', async (route) => {
    const incoming = new URL(route.request().url())
    const destination = new URL(api2URL)
    destination.pathname = incoming.pathname
    destination.search = incoming.search
    await route.continue({ url: destination.toString() })
  })
  await context.addInitScript((api2Address) => {
    const NativeWebSocket = window.WebSocket
    window.WebSocket = new Proxy(NativeWebSocket, {
      construct(target, args) {
        const socketURL = new URL(String(args[0]))
        if (socketURL.pathname === '/ws/events') {
          const destination = new URL(api2Address)
          destination.protocol = socketURL.protocol
          destination.pathname = socketURL.pathname
          destination.search = socketURL.search
          return Reflect.construct(target, [destination.toString(), ...args.slice(1)])
        }
        return Reflect.construct(target, args)
      },
    })
  }, api2URL)
}

export async function expectNoHorizontalOverflow(page: Page) {
  await expect.poll(async () => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
}
