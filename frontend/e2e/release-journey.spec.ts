import { randomUUID } from 'node:crypto'
import { expect, test } from '@playwright/test'
import {
  apiFor,
  capturePortfolio,
  cleanupWebhookReceiver,
  createIncident,
  emailForRole,
  login,
  resetDemo,
  routeContextThroughApi2,
  startWebhookReceiver,
  webhookReceiverResult,
} from './support'

test('authentication, session restoration, and logout work in the browser', async ({ page }) => {
  test.setTimeout(120_000)
  await page.goto('/dashboard')
  await expect(page).toHaveURL(/\/login$/)
  await capturePortfolio(page, '01-login.png')

  await page.getByLabel('Email').fill(`unknown-${randomUUID()}@example.invalid`)
  await page.getByLabel('Password').fill('not-a-valid-development-password')
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('alert')).toBeVisible()

  const session = await login(page)
  expect(session.accessToken).toBeTruthy()
  await page.reload()
  await expect(page.getByRole('heading', { name: 'Operations overview' })).toBeVisible({ timeout: 60_000 })
  await expect(page.getByText('Demo Admin · ADMIN')).toBeVisible()
  const logoutResponse = page.waitForResponse(
    (response) => response.url().endsWith('/api/auth/logout') && response.request().method() === 'POST',
  )
  await page.getByRole('button', { name: 'Sign out' }).click()
  expect((await logoutResponse).status()).toBe(204)
  await expect(page).toHaveURL(/\/login$/)
  await page.goto('/dashboard')
  await expect(page).toHaveURL(/\/login$/)
})

test('dispatch lifecycle fans out across API replicas and resyncs after reconnect', async ({ browser }) => {
  test.setTimeout(180_000)
  const adminContext = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
  const adminPage = await adminContext.newPage()
  const admin = await login(adminPage)
  await resetDemo(adminPage)
  const adminApi = await apiFor(admin.accessToken)
  expect((await adminApi.get('/api/users')).status()).toBe(200)
  await capturePortfolio(adminPage, '02-dashboard.png')

  const dispatcherContext = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
  await routeContextThroughApi2(dispatcherContext)
  const dispatcherPage = await dispatcherContext.newPage()
  const dispatcher = await login(dispatcherPage, emailForRole('dispatcher'))
  const dispatcherApi = await apiFor(dispatcher.accessToken)
  try {
    expect((await dispatcherApi.get('/api/users')).status()).toBe(403)
    expect((await dispatcherApi.get('/api/webhooks')).status()).toBe(403)
    await expect(dispatcherPage.getByRole('link', { name: 'Manage users' })).toHaveCount(0)
    await expect(dispatcherPage.getByRole('link', { name: 'Webhooks' })).toHaveCount(0)

    const title = 'E2E dispatch lifecycle'
    const unitMarker = adminPage.locator('#operations-map .leaflet-marker-icon[title^="Unit FIRE-001"]').first()
    await expect(unitMarker).toBeVisible()
    const markerBefore = await unitMarker.getAttribute('style')
    const incident = await createIncident(adminPage, title)
    await expect(dispatcherPage.getByRole('button', { name: new RegExp(title) })).toBeVisible()
    await dispatcherPage.getByRole('region', { name: 'Active incidents' }).getByRole('button').filter({ hasText: incident.id }).click()

    const nearby = dispatcherPage.getByLabel('Nearby available units')
    await expect(nearby).toContainText('FIRE-001', { timeout: 20_000 })
    await nearby.getByRole('button', { name: /FIRE-001/ }).click()
    const dispatchResponse = dispatcherPage.waitForResponse(
      (response) => response.url().endsWith('/api/dispatches') && response.request().method() === 'POST',
    )
    await dispatcherPage.getByRole('button', { name: 'Confirm dispatch' }).click()
    expect((await dispatchResponse).status()).toBe(201)

    const dispatchPanel = dispatcherPage.getByRole('region', { name: 'Dispatch controls' })
    const progress = dispatchPanel.getByLabel('Unit response progress')
    await expect(progress.locator('[aria-current="step"]')).toContainText('En route', { timeout: 30_000 })
    await expect.poll(() => unitMarker.getAttribute('style'), { timeout: 30_000 }).not.toBe(markerBefore)
    await capturePortfolio(dispatcherPage, '03-dispatch-map.png', {
      scrollTo: dispatcherPage.getByRole('region', { name: 'Singapore operations map' }),
    })

    await expect(progress.locator('[aria-current="step"]')).toContainText('On scene', { timeout: 90_000 })
    await dispatcherPage.reload()
    await expect(dispatcherPage.getByText('Realtime connected')).toBeVisible({ timeout: 20_000 })
    await dispatcherPage.getByRole('button', { name: new RegExp(title) }).click()
    await expect(dispatcherPage.getByRole('region', { name: 'Dispatch controls' }).getByLabel('Unit response progress').locator('[aria-current="step"]')).toContainText('On scene')

    const resolveResponse = dispatcherPage.waitForResponse(
      (response) => response.url().endsWith(`/api/incidents/${incident.id}/resolve`) && response.request().method() === 'POST',
    )
    await dispatcherPage.getByRole('button', { name: 'Resolve incident' }).click()
    expect((await resolveResponse).status()).toBe(200)
    await expect(adminPage.getByRole('region', { name: 'Activity feed' })).toContainText(incident.id)
    await expect(adminPage.getByRole('region', { name: 'Activity feed' })).toContainText(/resolved/i)
    await capturePortfolio(adminPage, '04-activity.png', {
      scrollTo: adminPage.getByRole('region', { name: 'Activity feed' }),
      scrollOffsetY: -100,
    })
    expect((await adminApi.get('/api/incidents/' + incident.id)).status()).toBe(200)
  } finally {
    await adminApi.dispose()
    await dispatcherApi.dispose()
    await adminContext.close()
    await dispatcherContext.close()
  }
})

test('reports, AI, signed webhook delivery, and tenant isolation work end to end', async ({ browser }) => {
  test.setTimeout(300_000)
  const adminContext = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
  const adminPage = await adminContext.newPage()
  const admin = await login(adminPage)
  const adminApi = await apiFor(admin.accessToken)
  let subscriptionId: string | undefined
  let receiverReady = false
  cleanupWebhookReceiver()
  try {
    await resetDemo(adminPage)
    await adminPage.getByRole('region', { name: 'Active incidents' }).getByRole('button').filter({ hasText: 'INC-001' }).first().click()
    await expect(adminPage.getByRole('region', { name: 'Dispatch controls' }).getByRole('heading', { name: 'Building fire' })).toBeVisible()

    const reportResponse = adminPage.waitForResponse(
      (response) => response.url().endsWith('/api/incidents/INC-001/reports') && response.request().method() === 'POST',
    )
    await adminPage.getByRole('button', { name: 'Generate operational report' }).click()
    const reportResult = await reportResponse
    expect(reportResult.status()).toBe(202)
    const reportJobId = ((await reportResult.json()) as { job_id: string }).job_id

    const aiResponse = adminPage.waitForResponse(
      (response) => response.url().endsWith('/api/incidents/INC-001/ai-summary') && response.request().method() === 'POST',
    )
    await adminPage.getByRole('button', { name: 'Generate AI Summary' }).click()
    const aiResult = await aiResponse
    expect(aiResult.status()).toBe(202)
    const aiJobId = ((await aiResult.json()) as { job_id: string }).job_id
    await adminPage.getByRole('link', { name: 'Jobs and reports', exact: true }).click()
    const reportRow = adminPage.getByRole('listitem').filter({ hasText: reportJobId })
    const aiRow = adminPage.getByRole('listitem').filter({ hasText: aiJobId })
    await Promise.all([
      expect(reportRow).toContainText('Succeeded', { timeout: 180_000 }),
      expect(aiRow).toContainText('Succeeded', { timeout: 180_000 }),
    ])
    await reportRow.getByText('View report').click()
    await expect(reportRow.locator('pre')).toBeVisible()
    await capturePortfolio(adminPage, '05-background-job.png')
    await aiRow.getByText('View AI summary').click()
    await expect(aiRow.getByLabel('AI summary result')).toContainText('AI-generated · Human review required')
    await expect(aiRow.getByLabel('AI summary result')).toContainText('Deterministic demo output')
    await capturePortfolio(adminPage, '07-ai-summary.png')

    const handoverResponse = adminPage.waitForResponse(
      (response) => response.url().endsWith('/api/ai/shift-handover') && response.request().method() === 'POST',
    )
    await adminPage.getByRole('button', { name: 'Generate Shift Handover' }).click()
    const handoverResult = await handoverResponse
    expect(handoverResult.status()).toBe(202)
    const handoverJobId = ((await handoverResult.json()) as { job_id: string }).job_id
    const handoverRow = adminPage.getByRole('listitem').filter({ hasText: handoverJobId })
    await expect(handoverRow).toContainText('Succeeded', { timeout: 120_000 })
    await expect(handoverRow.getByText('View AI summary')).toBeVisible()

    await adminPage.getByRole('link', { name: 'Webhooks' }).click()
    const stale = await adminApi.get('/api/webhooks')
    for (const subscription of (await stale.json()) as Array<{ id: string; name: string }>) {
      if (subscription.name === 'E2E Signed Receiver') {
        await adminApi.delete(`/api/webhooks/${subscription.id}`)
      }
    }
    await adminPage.reload()
    await adminPage.getByLabel('Name').fill('E2E Signed Receiver')
    await adminPage.getByLabel('URL').fill('http://127.0.0.1:8765/events')
    await adminPage.getByLabel('incident.created').check()
    const webhookResponse = adminPage.waitForResponse(
      (response) => response.url().endsWith('/api/webhooks') && response.request().method() === 'POST',
    )
    await adminPage.getByRole('button', { name: 'Create webhook' }).click()
    const subscription = (await (await webhookResponse).json()) as {
      id: string
      signing_secret: string
    }
    subscriptionId = subscription.id
    await expect(adminPage.getByText('Signing secret — shown once')).toBeVisible()
    await startWebhookReceiver(subscription.signing_secret)
    receiverReady = true

    await adminPage.getByRole('link', { name: 'Overview' }).click()
    const webhookIncident = await createIncident(adminPage, 'E2E signed webhook incident')
    await expect.poll(
      () => {
        try {
          return webhookReceiverResult()
        } catch {
          return null
        }
      },
      { timeout: 60_000, message: 'Celery should deliver a signed incident.created webhook to the real receiver' },
    ).toMatchObject({ signature_valid: true, event_type: 'incident.created' })
    const received = webhookReceiverResult()
    expect(received.event_id).toBeTruthy()
    expect(received.delivery_id).toBeTruthy()

    await adminPage.getByRole('link', { name: 'Webhooks' }).click()
    const deliveryRow = adminPage.getByRole('row').filter({ hasText: received.delivery_id })
    await expect(deliveryRow).toContainText('DELIVERED', { timeout: 30_000 })
    await expect(deliveryRow).toContainText('204')
    await adminPage.setViewportSize({ width: 1440, height: 650 })
    await capturePortfolio(adminPage, '06-webhook-delivery.png', {
      scrollTo: deliveryRow,
      scrollOffsetY: -100,
    })
    const disableSubscription = await adminApi.patch(`/api/webhooks/${subscription.id}`, {
      data: { is_active: false },
    })
    expect(disableSubscription.status()).toBe(200)

    const artifactsResponse = await adminApi.get('/api/ai/artifacts?incident_id=INC-001')
    const artifacts = (await artifactsResponse.json()) as Array<{ id: string; job_id: string }>
    const artifact = artifacts.find((item) => item.job_id === aiJobId)
    expect(artifact).toBeTruthy()

    const tenantContext = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
    await routeContextThroughApi2(tenantContext)
    const tenantPage = await tenantContext.newPage()
    await tenantPage.goto('/login')
    await tenantPage.getByRole('button', { name: 'Create an organization' }).click()
    await tenantPage.getByLabel('Organization name').fill(`E2E Tenant ${randomUUID()}`)
    await tenantPage.getByLabel('Display name').fill('Fictional E2E Administrator')
    const tenantEmail = `e2e-${randomUUID()}@demo.example.com`
    await tenantPage.getByLabel('Email').fill(tenantEmail)
    const tenantPassword = `E2E-${randomUUID()}-fictional`
    const registerResponse = tenantPage.waitForResponse(
      (response) => response.url().endsWith('/api/auth/register') && response.request().method() === 'POST',
    )
    await tenantPage.getByLabel('Password').fill(tenantPassword)
    await tenantPage.getByRole('button', { name: 'Create organization', exact: true }).click()
    const registered = await registerResponse
    expect(registered.status()).toBe(201)
    const tenantToken = ((await registered.json()) as { access_token: string }).access_token
    await expect(tenantPage.getByRole('heading', { name: 'Operations overview' })).toBeVisible()
    await expect(tenantPage.getByText('Realtime connected')).toBeVisible({ timeout: 20_000 })
    const tenantApi = await apiFor(tenantToken)
    try {
      const paths = ['/api/incidents', '/api/units', '/api/jobs', '/api/webhooks', '/api/webhook-deliveries', '/api/ai/artifacts']
      for (const path of paths) {
        const response = await tenantApi.get(path)
        expect(response.status(), path).toBe(200)
        expect(await response.json(), path).toEqual([])
      }
      expect((await tenantApi.get(`/api/incidents/${webhookIncident.id}`)).status()).toBe(404)
      expect((await tenantApi.get('/api/units/FIRE-001')).status()).toBe(404)
      expect((await tenantApi.get(`/api/jobs/${reportJobId}`)).status()).toBe(404)
      expect((await tenantApi.get(`/api/jobs/${aiJobId}`)).status()).toBe(404)
      expect((await tenantApi.get(`/api/ai/artifacts/${artifact!.id}`)).status()).toBe(404)
      expect((await tenantApi.patch(`/api/webhooks/${subscription.id}`, { data: { is_active: false } })).status()).toBe(404)
      await expect(tenantPage.getByText('E2E signed webhook incident')).toHaveCount(0)

      const privateEvent = `Private tenant event ${randomUUID()}`
      const createPrivate = await adminApi.post('/api/incidents', {
        data: {
          type: 'medical', severity: 'low', title: privateEvent, area: 'Marina Bay',
          description: 'Fictional cross-tenant socket isolation check.', latitude: 1.2834, longitude: 103.8607,
        },
      })
      expect(createPrivate.status()).toBe(201)
      await createIncident(tenantPage, 'E2E tenant B own event')
      await expect(tenantPage.getByText(privateEvent)).toHaveCount(0)
      await expect(tenantPage.getByRole('heading', { name: 'Operations overview' })).toBeVisible()
    } finally {
      await tenantApi.dispose()
      await tenantContext.close()
    }

    expect((await adminApi.get(`/api/incidents/${webhookIncident.id}`)).status()).toBe(200)
  } finally {
    if (receiverReady) cleanupWebhookReceiver()
    if (subscriptionId) await adminApi.delete(`/api/webhooks/${subscriptionId}`)
    await adminApi.dispose()
    await adminContext.close()
  }
})

test('dispatcher actions remain available on mobile while responder and admin APIs stay restricted', async ({ browser }) => {
  test.setTimeout(240_000)
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 1 })
  const page = await context.newPage()
  const dispatcher = await login(page, emailForRole('dispatcher'))
  const dispatcherApi = await apiFor(dispatcher.accessToken)
  try {
    await expect(page.getByRole('button', { name: 'Create incident' })).toBeVisible()
    await expect(page.getByRole('link', { name: 'Manage users' })).toHaveCount(0)
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
    const incident = await createIncident(page, `E2E mobile dispatcher incident ${randomUUID()}`)
    expect(incident.id).toBeTruthy()
    expect((await dispatcherApi.get('/api/users')).status()).toBe(403)
    expect((await dispatcherApi.get('/api/webhooks')).status()).toBe(403)
  } finally {
    await dispatcherApi.dispose()
    await context.close()
  }

  const responderContext = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 1 })
  const responderPage = await responderContext.newPage()
  const responder = await login(responderPage, emailForRole('responder'))
  const responderApi = await apiFor(responder.accessToken)
  try {
    await expect(responderPage.getByRole('button', { name: 'Create incident' })).toHaveCount(0)
    await responderPage.getByRole('region', { name: 'Active incidents' }).getByRole('button').filter({ hasText: 'INC-001' }).first().click()
    await expect(responderPage.getByRole('region', { name: 'Dispatch controls' })).toContainText('view access')
    await expect.poll(() => responderPage.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
    const incidentBody = {
      type: 'medical', severity: 'low', title: 'Forbidden E2E mutation', area: 'Marina Bay',
      description: 'Fictional role boundary test.', latitude: 1.2834, longitude: 103.8607,
    }
    expect((await responderApi.get('/api/incidents')).status()).toBe(200)
    expect((await responderApi.post('/api/incidents', { data: incidentBody })).status()).toBe(403)
    expect((await responderApi.post('/api/dispatches', { data: { incident_id: 'INC-001', unit_id: 'FIRE-001' } })).status()).toBe(403)
    expect((await responderApi.post('/api/incidents/INC-001/resolve')).status()).toBe(403)
    expect((await responderApi.get('/api/users')).status()).toBe(403)
    expect((await responderApi.get('/api/webhooks')).status()).toBe(403)
    await expect(responderPage.getByText('Realtime connected')).toBeVisible({ timeout: 60_000 })
    await responderPage.goto('/users')
    await expect(responderPage).toHaveURL(/\/dashboard$/)
    await responderPage.goto('/webhooks')
    await expect(responderPage).toHaveURL(/\/dashboard$/)
  } finally {
    await responderApi.dispose()
    await responderContext.close()
  }
})
