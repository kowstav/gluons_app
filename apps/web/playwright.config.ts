import { defineConfig, devices } from "@playwright/test";
import path from "node:path";

const python = process.env.GLUONS_E2E_PYTHON || path.resolve("../../.venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");

export default defineConfig({
  testDir: "./tests",
  timeout: 90_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"], ["html", { open: "never" }]],
  use: { baseURL: "http://localhost:3001", trace: "retain-on-failure", screenshot: "only-on-failure" },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 1000 } } },
    { name: "mobile", use: { ...devices["Pixel 7"] } },
  ],
  webServer: [
    { command: `"${python}" tests/start_api.py`, url: "http://localhost:8001/ready", timeout: 120_000, reuseExistingServer: false },
    { command: "npm run dev -- --port 3001", url: "http://localhost:3001", timeout: 120_000, reuseExistingServer: false, env: { NEXT_PUBLIC_API_URL: "http://localhost:8001", NEXT_TELEMETRY_DISABLED: "1" } },
  ],
});
