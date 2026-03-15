// @ts-check
const { defineConfig } = require("@playwright/test");

module.exports = defineConfig({
  testDir: "./tests/e2e",
  testMatch: "**/*.spec.js",
  timeout: 30000,
  retries: 0,
  use: {
    headless: true,
    browserName: "chromium",
    // Allow insecure localhost connections
    ignoreHTTPSErrors: true,
    // Fake media devices so getUserMedia doesn't hard-fail
    launchOptions: {
      args: [
        "--use-fake-device-for-media-stream",
        "--use-fake-ui-for-media-stream",
        "--no-sandbox",
      ],
    },
  },
});
