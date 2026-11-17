import { defineConfig, devices } from '@playwright/test'
export default defineConfig({
 testDir: './e2e', fullyParallel: false, workers: 1,
 timeout: 45000, expect: { timeout: 10000 }, retries: process.env.CI ? 1 : 0,
 outputDir: '../output/playwright/results',
 reporter: [['list'], ['html', { outputFolder: '../output/playwright/report', open: 'never' }]],
 use: { baseURL: 'http://127.0.0.1:8096', trace: 'retain-on-failure', screenshot: 'only-on-failure' },
 projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
 webServer: {
  command: 'python3 scripts/verification/serve_browser_tests.py', cwd: '..',
  url: 'http://127.0.0.1:8096/api/bootstrap', reuseExistingServer: false, timeout: 60000,
 },
})
