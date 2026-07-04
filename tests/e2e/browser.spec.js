/**
 * Browser-based E2E tests for the Ambient Scribe frontend.
 *
 * Tests reconnect logic, transcript rendering, and accessibility
 * using Playwright with headless Chromium.
 *
 * Services must be running (use scripts/e2e-test.sh --no-start to skip service startup):
 *   - Python agent on AGENT_PORT (default 48101)
 *   - PHP app on APP_PORT (default 48082)
 *   - Mercure on MERCURE_PORT (default 48137)
 *
 * Run: npx playwright test tests/e2e/browser.spec.js
 */

const { test, expect } = require("@playwright/test");

const APP_PORT = process.env.APP_PORT ?? "48082";
const AGENT_PORT = process.env.AGENT_PORT ?? "48101";
const APP_URL = `http://localhost:${APP_PORT}`;

/**
 * Injects fake segments through the same browser function Mercure uses.
 * The loop alternates speakers because tests must cover grouping and reconnect
 * preservation without needing live Mercure or microphone services.
 */
async function injectFakeSegments(page, count = 3) {
  await page.evaluate((n) => {
    for (let i = 0; i < n; i++) {
      // Call the global handleRawSegment function directly
      handleRawSegment({
        type: "segment",
        speaker_id: i % 2 === 0 ? "spk_0" : "spk_1",
        text: `Test segment number ${i + 1}`,
        start: i * 2.0,
        end: i * 2.0 + 1.5,
      });
    }
  }, count);
}

// Helper: wait for the page to be fully loaded with CONFIG available
async function loadScribePage(page) {
  await page.goto(`${APP_URL}/scribe`, { waitUntil: "networkidle" });
  await page.waitForSelector("#startBtn");
}

test.describe("Transcript controls", () => {
  test("download button is not rendered after segments", async ({ page }) => {
    await loadScribePage(page);

    // Download is intentionally removed from the post-consult controls.
    await expect(page.locator("#downloadBtn")).toHaveCount(0);

    // Inject 3 fake segments
    await injectFakeSegments(page, 3);

    // Transcript rows still render for summary/review even without export controls.
    await expect(page.locator(".segment")).toHaveCount(3);
    await expect(page.locator("#downloadBtn")).toHaveCount(0);
  });
});

test.describe("Reconnect functionality", () => {
  test("shows reconnect button after WebSocket failure", async ({ page }) => {
    await loadScribePage(page);

    // Simulate: set state as if recording was active and WS dropped
    await page.evaluate(() => {
      isRecording = true;
      didUserStopRecording = false;
      reconnectAttempts = 3; // Already exhausted retries
      segmentIndex = 5;

      // Trigger the disconnect handler directly
      handleUnexpectedDisconnect(1006);
    });

    // Reconnect button should be visible
    const reconnectBtn = page.locator("#reconnectBtn");
    await expect(reconnectBtn).toBeVisible();

    // Status should mention preserved segments
    const status = page.locator("#status");
    await expect(status).toContainText("5 segments preserved");

    // Download stays removed even when preserved segments exist.
    await expect(page.locator("#downloadBtn")).toHaveCount(0);
  });

  test("segments preserved in DOM after disconnect", async ({ page }) => {
    await loadScribePage(page);

    // Inject segments first
    await injectFakeSegments(page, 4);

    // Verify segments are in the DOM
    const segments = page.locator(".segment");
    await expect(segments).toHaveCount(4);

    // Simulate disconnect (segments should NOT be cleared)
    await page.evaluate(() => {
      isRecording = true;
      didUserStopRecording = false;
      reconnectAttempts = 3;
      handleUnexpectedDisconnect(1006);
    });

    // Segments should still be in the DOM
    await expect(segments).toHaveCount(4);
    await expect(page.locator(".segment__text").first()).toContainText(
      "Test segment number 1"
    );
  });

  test("reconnect button resets state and hides itself", async ({ page }) => {
    await loadScribePage(page);

    // Show reconnect button
    await page.evaluate(() => {
      isRecording = true;
      didUserStopRecording = false;
      reconnectAttempts = 3;
      handleUnexpectedDisconnect(1006);
    });

    await expect(page.locator("#reconnectBtn")).toBeVisible();

    // Click reconnect - it will try to start recording (will fail without mic,
    // but the button should hide and state should reset)
    // We need to handle the getUserMedia rejection
    await page.evaluate(() => {
      // Mock getUserMedia to reject (no mic available in headless)
      navigator.mediaDevices.getUserMedia = () =>
        Promise.reject(new DOMException("Not allowed", "NotAllowedError"));
    });

    await page.locator("#reconnectBtn").click();

    // Reconnect button should be hidden (reconnect() hides it before calling startRecording)
    await expect(page.locator("#reconnectBtn")).toBeHidden();
  });
});

test.describe("Accessibility", () => {
  test("status region announces state changes", async ({ page }) => {
    await loadScribePage(page);

    // The #status element should have aria-live
    const status = page.locator("#status");
    await expect(status).toHaveAttribute("aria-live", "polite");

    // The transcript container should have role=log
    const transcript = page.locator("#transcript");
    await expect(transcript).toHaveAttribute("role", "log");
    await expect(transcript).toHaveAttribute("aria-live", "polite");

    // Segment count should have role=status
    const countContainer = page.locator("[role='status']");
    await expect(countContainer).toBeVisible();

    // sr-only announcement region should exist
    const srAnnounce = page.locator("#srAnnounce");
    await expect(srAnnounce).toHaveAttribute("aria-live", "assertive");
  });

  test("announce function populates sr-only region", async ({ page }) => {
    await loadScribePage(page);

    // Call announce() and verify the sr-only div gets content
    await page.evaluate(() => {
      announce("Recording started");
    });

    const srText = await page.locator("#srAnnounce").textContent();
    expect(srText).toBe("Recording started");
  });

  test("buttons have accessible labels", async ({ page }) => {
    await loadScribePage(page);

    await expect(page.locator("#startBtn")).toHaveAttribute(
      "aria-label",
      /start recording/i
    );
    await expect(page.locator("#stopBtn")).toHaveAttribute(
      "aria-label",
      /stop current session/i
    );
    await expect(page.locator("#reconnectBtn")).toHaveAttribute(
      "aria-label",
      /reconnect/i
    );
    await expect(page.locator("#timer")).toHaveAttribute(
      "aria-label",
      /recording timer/i
    );
  });
});
