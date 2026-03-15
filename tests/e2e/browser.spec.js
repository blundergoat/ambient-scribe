/**
 * Browser-based E2E tests for the Ambient Scribe frontend.
 *
 * Tests reconnect logic, download functionality, and accessibility
 * using Playwright with headless Chromium.
 *
 * Services must be running (use scripts/e2e-test.sh --no-start to skip service startup):
 *   - Python agent on AGENT_PORT (default 48201)
 *   - PHP app on APP_PORT (default 48202)
 *   - Mercure on MERCURE_PORT (default 48203)
 *
 * Run: npx playwright test tests/e2e/browser.spec.js
 */

const { test, expect } = require("@playwright/test");

const APP_PORT = process.env.APP_PORT || "48202";
const AGENT_PORT = process.env.AGENT_PORT || "48201";
const APP_URL = `http://localhost:${APP_PORT}`;

// Helper: inject fake segments into the page (simulates Mercure SSE delivery)
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

test.describe("Download functionality", () => {
  test("download button appears after segments and produces valid files", async ({
    page,
  }) => {
    await loadScribePage(page);

    // Download button should be hidden initially
    const downloadBtn = page.locator("#downloadBtn");
    await expect(downloadBtn).toBeHidden();

    // Inject 3 fake segments
    await injectFakeSegments(page, 3);

    // Download button should now be visible
    await expect(downloadBtn).toBeVisible();

    // Click download and capture all downloads
    const downloads = [];
    page.on("download", (d) => downloads.push(d));
    await downloadBtn.click();

    // Wait for both downloads to arrive
    await page.waitForTimeout(2000);
    expect(downloads.length).toBeGreaterThanOrEqual(1);

    // Find JSON download
    const jsonDownload = downloads.find((d) =>
      d.suggestedFilename().endsWith(".json")
    );
    expect(jsonDownload).toBeTruthy();

    // Verify JSON content
    const jsonPath = await jsonDownload.path();
    const fs = require("fs");
    const jsonContent = JSON.parse(fs.readFileSync(jsonPath, "utf-8"));
    expect(jsonContent.session_id).toBeTruthy();
    expect(jsonContent.exported_at).toBeTruthy();
    expect(jsonContent.segments).toHaveLength(3);
    expect(jsonContent.segments[0].text).toBe("Test segment number 1");

    // Find TXT download (if present — some browsers may merge rapid downloads)
    const txtDownload = downloads.find((d) =>
      d.suggestedFilename().endsWith(".txt")
    );
    if (txtDownload) {
      const txtPath = await txtDownload.path();
      const txtContent = fs.readFileSync(txtPath, "utf-8");
      expect(txtContent).toContain("Test segment number 1");
      expect(txtContent).toContain("Test segment number 2");
      expect(txtContent).toContain("Test segment number 3");
    }
  });
});

test.describe("Reconnect functionality", () => {
  test("shows reconnect button after WebSocket failure", async ({ page }) => {
    await loadScribePage(page);

    // Simulate: set state as if recording was active and WS dropped
    await page.evaluate(() => {
      isRecording = true;
      userInitiatedStop = false;
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

    // Download button should be visible (segments exist)
    await expect(page.locator("#downloadBtn")).toBeVisible();
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
      userInitiatedStop = false;
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
      userInitiatedStop = false;
      reconnectAttempts = 3;
      handleUnexpectedDisconnect(1006);
    });

    await expect(page.locator("#reconnectBtn")).toBeVisible();

    // Click reconnect — it will try to start recording (will fail without mic,
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
      /stop recording/i
    );
    await expect(page.locator("#downloadBtn")).toHaveAttribute(
      "aria-label",
      /download/i
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
