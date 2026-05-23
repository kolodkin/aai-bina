import { defineConfig } from "@playwright/test"

// The app under test is started separately (Vite dev server, or the FastAPI
// backend serving the built SPA); point at it with BASE_URL.
const BASE_URL = process.env.BASE_URL ?? "http://localhost:5173"

export default defineConfig({
  testDir: "./e2e",
  timeout: 90_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  reporter: process.env.CI
    ? [["list"], ["junit", {
      outputFile: process.env.PLAYWRIGHT_JUNIT_OUTPUT_NAME ?? "blob-report/junit.xml",
    }]]
    : [["list"]],
  use: {
    baseURL: BASE_URL,
    viewport: { width: 1280, height: 900 },
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: {
        browserName: "chromium",
        launchOptions: { args: ["--no-sandbox"] },
      },
    },
  ],
})
