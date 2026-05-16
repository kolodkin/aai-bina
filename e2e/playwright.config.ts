import { defineConfig, devices } from '@playwright/test'

const FRONTEND_URL = process.env.FRONTEND_URL ?? 'http://localhost:5173'

export default defineConfig({
  testDir: './tests',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? 'github' : 'list',
  use: {
    baseURL: FRONTEND_URL,
    trace: 'on-first-retry',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  // When MANAGE_SERVERS=1 Playwright will start both backend and frontend
  // itself. Otherwise we assume the dev server is already running.
  webServer: process.env.MANAGE_SERVERS
    ? [
        {
          command: 'deno task --cwd ../backend start',
          url: 'http://localhost:8000/api/health',
          reuseExistingServer: !process.env.CI,
          timeout: 30_000,
        },
        {
          command: 'deno task --cwd ../frontend dev',
          url: FRONTEND_URL,
          reuseExistingServer: !process.env.CI,
          timeout: 60_000,
        },
      ]
    : undefined,
})
