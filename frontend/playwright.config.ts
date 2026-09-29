import { defineConfig } from "playwright/test";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  retries: 0,
  workers: 1,
  timeout: 30_000,
  expect: { timeout: 5_000 },
  outputDir: "e2e-results/artifacts",
  reporter: [
    ["list"],
    ["json", { outputFile: "e2e-results/results.json" }],
  ],
  use: {
    baseURL: "http://127.0.0.1:5173",
    timezoneId: "Asia/Shanghai",
    // The app defaults to English; the suites assert on the Chinese UI copy, so pin the stored language to zh.
    storageState: {
      cookies: [],
      origins: [{ origin: "http://127.0.0.1:5173", localStorage: [{ name: "unibot.language", value: "zh" }] }],
    },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    launchOptions: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH
      ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH }
      : undefined,
  },
  webServer: {
    command: "node ./node_modules/vite/bin/vite.js --host 127.0.0.1",
    url: "http://127.0.0.1:5173",
    reuseExistingServer: true,
    timeout: 30_000,
  },
});
