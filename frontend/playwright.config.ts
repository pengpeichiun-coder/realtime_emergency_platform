import { defineConfig, devices } from '@playwright/test'

const crossBrowser = process.env.E2E_CROSS_BROWSER === '1'

const projects = [
  {
    name: 'chromium',
    use: { ...devices['Desktop Chrome'] },
  },
]

if (crossBrowser) {
  projects.push(
    {
      name: 'firefox',
      grep: /@browser-smoke/,
      use: { ...devices['Desktop Firefox'] },
    },
    {
      name: 'webkit',
      grep: /@browser-smoke/,
      use: { ...devices['Desktop Safari'] },
    },
  )
}

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 100_000,
  expect: { timeout: 12_000 },
  reporter: [
    ['list'],
    ['junit', { outputFile: 'test-results/e2e-junit.xml' }],
  ],
  outputDir: 'test-results/artifacts',
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://127.0.0.1:18082',
    actionTimeout: 12_000,
    navigationTimeout: 20_000,
    screenshot: 'off',
    trace: 'off',
    video: 'off',
  },
  projects,
})
