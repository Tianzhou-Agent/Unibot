import { defineConfig } from "playwright/test";
import config from "./playwright.config";

export default defineConfig({
  ...config,
  testMatch: "base-path.spec.ts",
  outputDir: "e2e-results/subpath/artifacts",
  reporter: [
    ["list"],
    ["json", { outputFile: "e2e-results/subpath/results.json" }],
  ],
  use: {
    ...config.use,
    baseURL: "http://127.0.0.1:5174/superbot/",
    storageState: {
      cookies: [],
      origins: [{ origin: "http://127.0.0.1:5174", localStorage: [{ name: "unibot.language", value: "zh" }] }],
    },
  },
  webServer: {
    command: "node ./node_modules/vite/bin/vite.js --config vite.config.ts --host 127.0.0.1 --port 5174",
    env: { VITE_BASE_PATH: "/superbot/", VITE_API_BASE_URL: "" },
    url: "http://127.0.0.1:5174/superbot/",
    reuseExistingServer: false,
    timeout: 30_000,
  },
});
