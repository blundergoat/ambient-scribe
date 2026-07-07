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
        // Live segments carry the stable row ID minted at emission (M20).
        segment_id: `seg-000${i + 1}`,
      });
    }
  }, count);
}

/**
 * Injects rows shaped like the consult-03 failure: two consecutive rows from
 * one speaker (coalesced into one card) followed by the other speaker, so
 * row-level correction inside a multi-row card is testable.
 */
async function injectCoalescedCardSegments(page) {
  await page.evaluate(() => {
    const rows = [
      { speaker_id: "spk_0", text: "How long have the headaches lasted?", segment_id: "seg-0001" },
      { speaker_id: "spk_0", text: "About two weeks now, mostly mornings.", segment_id: "seg-0002" },
      { speaker_id: "spk_1", text: "Any visual changes with them?", segment_id: "seg-0003" },
    ];
    rows.forEach((row, index) => {
      handleRawSegment({
        type: "segment",
        start: index * 2.0,
        end: index * 2.0 + 1.5,
        ...row,
      });
    });
  });
}

// Helper: wait for the page to be fully loaded with CONFIG available
async function loadScribePage(page) {
  await page.goto(`${APP_URL}/scribe`, { waitUntil: "networkidle" });
  await page.waitForSelector("#startBtn");
}

/**
 * Stubs the correction route so summary tests can verify request order.
 * The response can be success or failure; either way the UI should continue
 * to the summary request unless the summary itself fails.
 */
async function stubCorrectionRoute(page, calls, order, responseOptions = {}) {
  await page.route("**/session/*/correction", async (route) => {
    calls.push(route.request().postDataJSON());
    order.push("correction");
    await route.fulfill({
      status: responseOptions.status ?? 200,
      contentType: "application/json",
      body: JSON.stringify(responseOptions.body ?? { status: "ready", segments: 3 }),
    });
  });
}

/**
 * Stubs the summary route so tests count generated note requests locally.
 * Use when a stop, retry, or manual click should prove request order because the model
 * response is not what this browser flow is testing.
 */
async function stubSummaryRoute(page, calls, order = []) {
  await page.route("**/session/*/summary", async (route) => {
    calls.push(route.request().postDataJSON());
    order.push("summary");
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        title: "Stub Summary",
        sections: [{ heading: "Subjective", content: "Patient reports symptoms." }],
        key_points: [],
      }),
    });
  });
}

test.describe("Transcript controls", () => {
  test("download button is not rendered after segments", async ({ page }) => {
    await loadScribePage(page);

    // Download is intentionally removed from the post-consult controls.
    await expect(page.locator("#downloadBtn")).toHaveCount(0);
    await expect(page.locator("#summaryBtn")).toHaveCount(0);

    // Inject 3 fake segments
    await injectFakeSegments(page, 3);

    // Transcript rows still render for summary/review without manual export or summary controls.
    await expect(page.locator(".segment")).toHaveCount(3);
    await expect(page.locator("#downloadBtn")).toHaveCount(0);
    await expect(page.locator("#summaryBtn")).toHaveCount(0);
  });

  test("quality event updates the dev state snapshot", async ({ page }) => {
    await loadScribePage(page);

    const savedRecord = await page.evaluate(() => {
      const qualityRecord = {
        session_id: "quality-browser-session",
        chunks: 7,
        error_count: 0,
        final_confidence: 0.91,
      };

      // Mercure dispatches this after finalize so the dev State tab can show run health.
      handleQualityRecord({ type: "quality", quality: qualityRecord });
      devPanel.switchTab("state");
      devPanel.refreshState();
      return latestQualityRecord;
    });

    expect(savedRecord.session_id).toBe("quality-browser-session");
    await expect(page.locator("#devStateSnapshot")).toContainText(
      "latestQualityRecord"
    );
    await expect(page.locator("#devStateSnapshot")).toContainText(
      "quality-browser-session"
    );
  });

  test("manual role override stays visible and reaches FastAPI state", async ({
    page,
  }) => {
    await loadScribePage(page);
    await injectFakeSegments(page, 1);

    // A real visit always has a live recording socket; only live visits pin a
    // speaker override into role state (finished visits must not resurrect it).
    await page.evaluate(async () => {
      window.__liveSocket = new WebSocket(
        `${CONFIG.wsUrl}/ws/transcribe/${CONFIG.sessionId}`
      );
      await new Promise((resolve) =>
        window.__liveSocket.addEventListener("open", resolve)
      );
    });

    const sessionId = await page.evaluate(() => CONFIG.sessionId);
    const firstSpeakerLabel = page.locator(".segment__speaker").first();
    const overrideSaved = page.waitForResponse(
      (response) =>
        response.url().includes(`/scribe/${sessionId}/roles/override`) &&
        response.request().method() === "POST" &&
        response.ok()
    );

    // Clinician clicks an unknown speaker label once to mark it as Doctor.
    await firstSpeakerLabel.click();
    await overrideSaved;

    await expect(firstSpeakerLabel).toContainText("Doctor");
    await expect(firstSpeakerLabel.locator(".segment__override-icon")).toHaveCount(
      1
    );

    const roleSnapshot = await page.evaluate(async (visibleSessionId) => {
      const response = await fetch(`/scribe/${visibleSessionId}/roles`);
      return response.json();
    }, sessionId);

    expect(roleSnapshot.mapping.spk_0).toBe("DOCTOR");
  });
});

test.describe("Role confidence badge stability gating", () => {
  /**
   * Sends one role update through the same browser function Mercure uses.
   * Tests drive it directly so badge policy is verifiable without live NeMo.
   */
  async function injectRoleUpdate(page, roleUpdateEvent) {
    await page.evaluate((event) => {
      handleRoleUpdate(event);
    }, roleUpdateEvent);
  }

  test("confident mapping over unstable speakers is not shown as identified", async ({
    page,
  }) => {
    await loadScribePage(page);
    await injectFakeSegments(page, 2);

    // The consult-03 failure mode: the role agent is 90% sure of the global
    // mapping while speaker identities churned all session (17 remaps).
    await injectRoleUpdate(page, {
      type: "role_update",
      mapping: { spk_0: "DOCTOR", spk_1: "PATIENT" },
      confidence: 0.9,
      flip_detected: false,
      role_stability: {
        level: "unstable",
        anchor_remap_rate: 0.944,
        anchor_remaps: 17,
        phantom_merges: 6,
        windows: 18,
        mapping_changes: 1,
        pending_contrary_mapping: false,
      },
    });

    const badge = page.locator("#confidenceBadge");
    await expect(badge).toContainText("Roles assigned - verify labels (90%)");
    await expect(badge).not.toContainText("Roles identified");
    await expect(badge).toHaveClass(/bg-amber-100/);
  });

  test("confident mapping with stable speakers earns the identified badge", async ({
    page,
  }) => {
    await loadScribePage(page);
    await injectFakeSegments(page, 2);

    await injectRoleUpdate(page, {
      type: "role_update",
      mapping: { spk_0: "DOCTOR", spk_1: "PATIENT" },
      confidence: 0.9,
      flip_detected: false,
      role_stability: {
        level: "stable",
        anchor_remap_rate: 0.05,
        anchor_remaps: 1,
        phantom_merges: 0,
        windows: 20,
        mapping_changes: 0,
        pending_contrary_mapping: false,
      },
    });

    const badge = page.locator("#confidenceBadge");
    await expect(badge).toContainText("Roles identified (90%)");
    await expect(badge).toHaveClass(/bg-green-100/);
  });

  test("updates without a stability field keep the last known instability", async ({
    page,
  }) => {
    await loadScribePage(page);
    await injectFakeSegments(page, 2);

    // Live update carries the instability evidence...
    await injectRoleUpdate(page, {
      type: "role_update",
      mapping: { spk_0: "DOCTOR", spk_1: "PATIENT" },
      confidence: 0.7,
      role_stability: { level: "unstable", anchor_remaps: 17 },
    });
    // ...then the post-disconnect drain publishes without the field.
    await injectRoleUpdate(page, {
      type: "role_update",
      mapping: { spk_0: "DOCTOR", spk_1: "PATIENT" },
      confidence: 0.9,
    });

    // The badge must not upgrade to green on a drain update with no evidence.
    const badge = page.locator("#confidenceBadge");
    await expect(badge).toContainText("Roles assigned - verify labels (90%)");
    await expect(badge).toHaveClass(/bg-amber-100/);
  });

  test("servers without stability reporting keep the pre-M20 badge behavior", async ({
    page,
  }) => {
    await loadScribePage(page);
    await injectFakeSegments(page, 2);

    // An older server never sends role_stability on any update.
    await injectRoleUpdate(page, {
      type: "role_update",
      mapping: { spk_0: "DOCTOR", spk_1: "PATIENT" },
      confidence: 0.9,
    });

    const badge = page.locator("#confidenceBadge");
    await expect(badge).toContainText("Roles identified (90%)");
    await expect(badge).toHaveClass(/bg-green-100/);
  });

  test("manual override survives a conflicting unstable role update", async ({
    page,
  }) => {
    await loadScribePage(page);
    await injectFakeSegments(page, 1);

    const firstSpeakerLabel = page.locator(".segment__speaker").first();
    // Clinician corrects the first speaker to Doctor by clicking the label.
    await firstSpeakerLabel.click();
    await expect(firstSpeakerLabel).toContainText("Doctor");

    // A later unstable agent update proposes the opposite label for spk_0.
    await injectRoleUpdate(page, {
      type: "role_update",
      mapping: { spk_0: "PATIENT", spk_1: "DOCTOR" },
      confidence: 0.9,
      role_stability: { level: "unstable", anchor_remaps: 12 },
    });

    // The clinician's correction stays visible with its confirmation mark.
    await expect(firstSpeakerLabel).toContainText("Doctor");
    await expect(
      firstSpeakerLabel.locator(".segment__override-icon")
    ).toHaveCount(1);
  });
});

test.describe("Row-level role correction", () => {
  test("row correction survives a conflicting role update and reaches the summary body", async ({
    page,
  }) => {
    await loadScribePage(page);
    await injectCoalescedCardSegments(page);

    // The consult-03 shape: one card coalesces two same-speaker rows.
    await expect(page.locator(".segment")).toHaveCount(2);
    const secondRow = page.locator('.segment__text[data-segment-id="seg-0002"]');
    const overridePosted = page.waitForRequest(
      (request) =>
        request.url().includes("/roles/override") &&
        request.method() === "POST" &&
        request.postDataJSON()?.segment_id === "seg-0002"
    );

    // Clinician clicks the one wrong line inside the card (Unknown -> Doctor).
    await secondRow.click();
    await overridePosted;
    await expect(secondRow.locator(".segment__row-role")).toContainText("Dr ✓");

    // A later conflicting unstable agent update relabels both speakers.
    await page.evaluate(() => {
      handleRoleUpdate({
        type: "role_update",
        mapping: { spk_0: "PATIENT", spk_1: "DOCTOR" },
        confidence: 0.9,
        role_stability: { level: "unstable", anchor_remaps: 12 },
      });
    });

    // The corrected row keeps its pinned label chip inside the relabeled card.
    await expect(secondRow.locator(".segment__row-role")).toContainText("Dr ✓");

    // The summary body stays row-preserving: three records, the corrected row
    // keeps DOCTOR while its card's speaker mapping says PATIENT, and the
    // chip text never leaks into the spoken text.
    const summaryRows = await page.evaluate(() => readVisibleTranscriptSegments());
    expect(summaryRows).toHaveLength(3);
    expect(summaryRows[0].segment_id).toBe("seg-0001");
    expect(summaryRows[0].role).toBe("PATIENT");
    expect(summaryRows[1].segment_id).toBe("seg-0002");
    expect(summaryRows[1].role).toBe("DOCTOR");
    expect(summaryRows[1].text).toBe("About two weeks now, mostly mornings.");
    expect(summaryRows[2].role).toBe("DOCTOR");
  });

  test("automatic row exceptions render tentatively and yield to the clinician", async ({
    page,
  }) => {
    await loadScribePage(page);
    await injectCoalescedCardSegments(page);

    // The server's cue lane flags row 2 as a doctor line inside a Patient card.
    await page.evaluate(() => {
      handleRoleUpdate({
        type: "role_update",
        mapping: { spk_0: "PATIENT", spk_1: "DOCTOR" },
        confidence: 0.7,
        row_exceptions: { "seg-0002": "DOCTOR" },
      });
    });

    const flaggedRow = page.locator('.segment__text[data-segment-id="seg-0002"]');
    await expect(flaggedRow.locator(".segment__row-role--auto")).toContainText(
      "Dr auto"
    );

    const mixedRoleCard = page.locator(".segment").first();
    await expect(mixedRoleCard.locator(".segment__speaker")).toContainText(
      "Review labels"
    );
    await expect(mixedRoleCard.locator(".segment__speaker")).not.toContainText(
      "Patient"
    );

    // The summary body uses the row-level judgment, not the card's mapping.
    let summaryRows = await page.evaluate(() => readVisibleTranscriptSegments());
    expect(summaryRows[1].role).toBe("DOCTOR");
    expect(summaryRows[0].role).toBe("PATIENT");

    // A new mapping that explains the row clears the tentative marker.
    await page.evaluate(() => {
      handleRoleUpdate({
        type: "role_update",
        mapping: { spk_0: "DOCTOR", spk_1: "PATIENT" },
        confidence: 0.7,
        row_exceptions: {},
      });
    });
    await expect(flaggedRow.locator(".segment__row-role")).toHaveCount(0);
    await expect(mixedRoleCard.locator(".segment__speaker")).toContainText(
      "Doctor"
    );

    // When the exception returns, the clinician's click still outranks it.
    await page.evaluate(() => {
      handleRoleUpdate({
        type: "role_update",
        mapping: { spk_0: "PATIENT", spk_1: "DOCTOR" },
        confidence: 0.7,
        row_exceptions: { "seg-0002": "UNKNOWN" },
      });
    });
    await flaggedRow.click();
    await expect(flaggedRow.locator(".segment__row-role")).toContainText("✓");

    // Later automatic judgments cannot displace the clinician's chip.
    await page.evaluate(() => {
      handleRoleUpdate({
        type: "role_update",
        mapping: { spk_0: "PATIENT", spk_1: "DOCTOR" },
        confidence: 0.7,
        row_exceptions: { "seg-0002": "DOCTOR" },
      });
    });
    await expect(flaggedRow.locator(".segment__row-role")).toContainText("✓");

    summaryRows = await page.evaluate(() => readVisibleTranscriptSegments());
    // The clicked correction (Unknown -> Doctor cycle landed on DOCTOR after
    // the auto UNKNOWN) stays the row's reported role.
    expect(summaryRows[1].role).toBe("DOCTOR");
  });

  test("row corrections from another tab render through the roles topic", async ({
    page,
  }) => {
    await loadScribePage(page);
    await injectCoalescedCardSegments(page);

    // Another tab corrected row 1; this tab receives it via Mercure.
    await page.evaluate(() => {
      handleRoleUpdate({
        type: "role_update",
        mapping: {},
        row_overrides: { "seg-0001": "PATIENT" },
        confidence: 0.5,
        manual_override: true,
      });
    });

    const firstRow = page.locator('.segment__text[data-segment-id="seg-0001"]');
    await expect(firstRow.locator(".segment__row-role")).toContainText("Pt ✓");

    const summaryRows = await page.evaluate(() => readVisibleTranscriptSegments());
    expect(summaryRows[0].role).toBe("PATIENT");
  });

  test("post-visit row correction without role state keeps the earned badge", async ({
    page,
  }) => {
    await loadScribePage(page);
    await injectCoalescedCardSegments(page);

    // The live visit earned a green badge before Stop.
    await page.evaluate(() => {
      handleRoleUpdate({
        type: "role_update",
        mapping: { spk_0: "PATIENT", spk_1: "DOCTOR" },
        confidence: 0.9,
      });
    });
    const badge = page.locator("#confidenceBadge");
    await expect(badge).toContainText("Roles identified (90%)");

    // A row fix after grace teardown broadcasts only the row signal - the
    // server no longer includes mapping or confidence for a finished visit.
    await page.evaluate(() => {
      handleRoleUpdate({
        type: "role_update",
        row_overrides: { "seg-0001": "DOCTOR" },
        flip_detected: false,
        manual_override: true,
      });
    });

    // The row chip applies while the badge keeps its earned confidence.
    const firstRow = page.locator('.segment__text[data-segment-id="seg-0001"]');
    await expect(firstRow.locator(".segment__row-role")).toContainText("Dr ✓");
    await expect(badge).toContainText("Roles identified (90%)");
  });

  test("a fabricated empty-mapping override cannot wipe the badge to zero", async ({
    page,
  }) => {
    await loadScribePage(page);
    await injectCoalescedCardSegments(page);

    await page.evaluate(() => {
      handleRoleUpdate({
        type: "role_update",
        mapping: { spk_0: "PATIENT", spk_1: "DOCTOR" },
        confidence: 0.9,
      });
    });

    // The consult-03 regression shape: a role_update fabricated after grace
    // teardown carried an empty mapping and zero confidence with the row fix.
    await page.evaluate(() => {
      handleRoleUpdate({
        type: "role_update",
        mapping: {},
        row_overrides: { "seg-0001": "DOCTOR" },
        confidence: 0,
        manual_override: true,
      });
    });

    const badge = page.locator("#confidenceBadge");
    await expect(badge).toContainText("Roles identified (90%)");
    await expect(badge).not.toContainText("Speakers unclear");
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
    const countContainer = page.locator("#segmentCount").locator("..");
    await expect(countContainer).toHaveAttribute("role", "status");
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

test.describe("Summary provenance (M5)", () => {
  test("cited section shows a superscript affordance whose popover holds the stitched source", async ({
    page,
  }) => {
    await loadScribePage(page);

    await page.evaluate(() => {
      renderSummary({
        type: "summary",
        title: "Cited summary",
        sections: [
          {
            heading: "Plan",
            content: "Doctor advised topical treatment.",
            citations: [
              {
                segment_id: "corrected-0001",
                start: 0,
                end: 1.5,
                role: "DOCTOR",
                text: "Use the cream twice",
              },
              {
                segment_id: "corrected-0002",
                start: 1.9,
                end: 3.0,
                role: "DOCTOR",
                text: "daily after washing.",
              },
            ],
          },
        ],
        key_points: [],
      });
    });

    // Two adjacent same-speaker rows stitch into one utterance for the label.
    const toggle = page.locator(".summary-provenance__toggle");
    await expect(toggle).toHaveText("1");
    await expect(toggle).toHaveAttribute("aria-label", "View source, 1 utterance");
    await expect(toggle).toHaveAttribute("aria-expanded", "false");

    await toggle.click();
    const popover = page.locator(".summary-provenance__popover");
    await expect(popover).toBeVisible();
    await expect(toggle).toHaveAttribute("aria-expanded", "true");
    await expect(popover).toContainText("00:00");
    await expect(popover).toContainText("DOCTOR");
    await expect(popover).toContainText("Use the cream twice daily after washing.");

    // Escape closes the dialog and anchors keyboard focus back on the toggle.
    await page.keyboard.press("Escape");
    await expect(popover).toBeHidden();
    await expect(toggle).toBeFocused();
  });

  test("open in transcript closes the popover and switches tabs", async ({ page }) => {
    await loadScribePage(page);

    await page.route("**/session/*/corrected-transcript", (route) =>
      route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          session_id: "stub",
          source: "corrected_segments",
          segments: [
            {
              segment_id: "corrected-0001",
              role: "DOCTOR",
              text: "Use the cream twice daily.",
              start: 0,
              end: 1.5,
            },
          ],
        }),
      })
    );

    await page.evaluate(() => {
      renderSummary({
        type: "summary",
        title: "Cited summary",
        sections: [
          {
            heading: "Plan",
            content: "Doctor advised topical treatment.",
            citations: [
              {
                segment_id: "corrected-0001",
                start: 0,
                end: 1.5,
                role: "DOCTOR",
                text: "Use the cream twice daily.",
              },
            ],
          },
        ],
        key_points: [],
      });
    });

    await page.locator(".summary-provenance__toggle").click();
    await page.locator(".summary-provenance__open").click();

    await expect(page.locator(".summary-provenance__popover")).toBeHidden();
    await expect(page.locator("#summaryTabTranscript")).toHaveAttribute("aria-selected", "true");
    await expect(page.locator("#summaryTranscriptView")).toBeVisible();
    // The popover reuses the same block class, so scope to the transcript tab body.
    await expect(page.locator("#summaryTranscript .summary-transcript__block")).toContainText(
      "Use the cream twice daily."
    );
  });

  test("deep link highlights all cited blocks, fades, and skips uncited rows (M6)", async ({
    page,
  }) => {
    await loadScribePage(page);

    await page.route("**/session/*/corrected-transcript", (route) =>
      route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          session_id: "stub",
          source: "corrected_segments",
          segments: [
            {
              segment_id: "corrected-0001",
              role: "DOCTOR",
              text: "How can I help you today?",
              start: 0,
              end: 1.5,
            },
            {
              segment_id: "corrected-0002",
              role: "PATIENT",
              text: "My skin is red.",
              start: 8.0,
              end: 9.0,
            },
            {
              segment_id: "corrected-0003",
              role: "PATIENT",
              text: "It itches at night.",
              start: 9.4,
              end: 10.5,
            },
          ],
        }),
      })
    );

    await page.evaluate(() => {
      renderSummary({
        type: "summary",
        title: "Cited summary",
        sections: [
          {
            heading: "Subjective",
            content: "Skin redness with nocturnal itch.",
            citations: [
              {
                segment_id: "corrected-0002",
                start: 8.0,
                end: 9.0,
                role: "PATIENT",
                text: "My skin is red.",
              },
              {
                segment_id: "corrected-0003",
                start: 9.4,
                end: 10.5,
                role: "PATIENT",
                text: "It itches at night.",
              },
            ],
          },
        ],
        key_points: [],
      });
    });

    await page.locator(".summary-provenance__toggle").click();
    await page.locator(".summary-provenance__open").click();

    // The two cited rows stitch into one block, which carries the highlight;
    // the uncited doctor block stays plain.
    const citedBlocks = page.locator("#summaryTranscript .summary-transcript__block--cited");
    await expect(citedBlocks).toHaveCount(1);
    await expect(citedBlocks.first()).toContainText("My skin is red. It itches at night.");
    await expect(
      page.locator("#summaryTranscript .summary-transcript__block").first()
    ).not.toHaveClass(/--cited/);
    await expect(page.locator("#summaryTranscriptNotice")).toBeHidden();

    // The highlight is temporary: it clears after the fade window (~3s).
    await expect(citedBlocks).toHaveCount(0, { timeout: 5000 });
  });

  test("deep link shows a non-blocking notice when cited rows are missing (M6)", async ({
    page,
  }) => {
    await loadScribePage(page);

    await page.route("**/session/*/corrected-transcript", (route) =>
      route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          session_id: "stub",
          source: "corrected_segments",
          segments: [
            {
              segment_id: "corrected-0001",
              role: "DOCTOR",
              text: "How can I help you today?",
              start: 0,
              end: 1.5,
            },
          ],
        }),
      })
    );

    await page.evaluate(() => {
      renderSummary({
        type: "summary",
        title: "Cited summary",
        sections: [
          {
            heading: "Plan",
            content: "Emollient plan.",
            citations: [
              {
                segment_id: "corrected-9999",
                start: 30.0,
                end: 31.0,
                role: "DOCTOR",
                text: "Use the emollient.",
              },
            ],
          },
        ],
        key_points: [],
      });
    });

    await page.locator(".summary-provenance__toggle").click();
    await page.locator(".summary-provenance__open").click();

    // The tab still opens, nothing highlights, and the notice self-reports.
    await expect(page.locator("#summaryTabTranscript")).toHaveAttribute("aria-selected", "true");
    const missNotice = page.locator("#summaryTranscriptNotice");
    await expect(missNotice).toBeVisible();
    await expect(missNotice).toContainText("The cited rows are not in this transcript view.");
    await expect(
      page.locator("#summaryTranscript .summary-transcript__block--cited")
    ).toHaveCount(0);

    // The notice is temporary, so it never blocks reading the transcript.
    await expect(missNotice).toBeHidden({ timeout: 6000 });
  });

  test("an open popover does not trap the next section's toggle", async ({ page }) => {
    await loadScribePage(page);

    await page.evaluate(() => {
      renderSummary({
        type: "summary",
        title: "Two cited sections",
        sections: [
          {
            heading: "Subjective",
            content: "Patient reports dry, itchy skin.",
            citations: [
              {
                segment_id: "corrected-0001",
                start: 0,
                end: 1.5,
                role: "PATIENT",
                text: "My skin is dry and itchy.",
              },
            ],
          },
          {
            heading: "Plan",
            content: "Doctor advised topical treatment.",
            citations: [
              {
                segment_id: "corrected-0002",
                start: 5,
                end: 6.5,
                role: "DOCTOR",
                text: "Use the cream twice daily.",
              },
            ],
          },
        ],
        key_points: [],
      });
    });

    const toggles = page.locator(".summary-provenance__toggle");
    const popovers = page.locator(".summary-provenance__popover");

    // Section A's open popover overlays the full section width below it -
    // exactly where section B's superscript sits.
    await toggles.nth(0).click();
    await expect(popovers.nth(0)).toBeVisible();

    // One DIRECT mouse click on section B's toggle must open B (not be
    // swallowed by A's popover surface) and close A in the same click.
    await toggles.nth(1).click();
    await expect(popovers.nth(1)).toBeVisible();
    await expect(popovers.nth(0)).toBeHidden();
    await expect(toggles.nth(1)).toHaveAttribute("aria-expanded", "true");
    await expect(toggles.nth(0)).toHaveAttribute("aria-expanded", "false");

    // Escape recovery is unchanged by the stacking fix.
    await page.keyboard.press("Escape");
    await expect(popovers.nth(1)).toBeHidden();
  });

  test("a click outside the popover dismisses it", async ({ page }) => {
    await loadScribePage(page);

    await page.evaluate(() => {
      renderSummary({
        type: "summary",
        title: "Cited summary",
        sections: [
          {
            heading: "Plan",
            content: "Doctor advised topical treatment.",
            citations: [
              {
                segment_id: "corrected-0001",
                start: 0,
                end: 1.5,
                role: "DOCTOR",
                text: "Use the cream twice daily.",
              },
            ],
          },
        ],
        key_points: [],
      });
    });

    await page.locator(".summary-provenance__toggle").click();
    await expect(page.locator(".summary-provenance__popover")).toBeVisible();

    await page.locator("#summaryTitle").click();
    await expect(page.locator(".summary-provenance__popover")).toBeHidden();
  });

  test("renders uncited summary sections without provenance affordances", async ({ page }) => {
    await loadScribePage(page);

    await page.evaluate(() => {
      renderSummary({
        type: "summary",
        title: "Legacy summary",
        sections: [
          {
            heading: "Subjective",
            content: "Patient reports headache.",
          },
        ],
        key_points: ["Headache discussed"],
      });
    });

    await expect(page.locator(".summary-section__content").first()).toContainText(
      "Patient reports headache."
    );
    await expect(page.locator(".summary-provenance__toggle")).toHaveCount(0);
    await expect(page.locator(".summary-provenance__popover")).toHaveCount(0);
  });
});

test.describe("Post-visit correction before summary", () => {
  test("runs correction before sending the summary request", async ({ page }) => {
    const correctionCalls = [];
    const summaryCalls = [];
    const requestOrder = [];
    await loadScribePage(page);
    await stubCorrectionRoute(page, correctionCalls, requestOrder);
    await stubSummaryRoute(page, summaryCalls, requestOrder);
    await injectFakeSegments(page, 2);

    await page.evaluate(() => requestSummary());

    await expect.poll(() => summaryCalls.length, { timeout: 5000 }).toBe(1);
    expect(correctionCalls).toHaveLength(1);
    expect(requestOrder).toEqual(["correction", "summary"]);
    expect(correctionCalls[0].segments.map((row) => row.segment_id)).toEqual([
      "seg-0001",
      "seg-0002",
    ]);
  });

  test("continues to summary when correction is unavailable", async ({ page }) => {
    const correctionCalls = [];
    const summaryCalls = [];
    const requestOrder = [];
    await loadScribePage(page);
    await stubCorrectionRoute(page, correctionCalls, requestOrder, {
      status: 503,
      body: { status: "unavailable", detail: "Correction service unavailable" },
    });
    await stubSummaryRoute(page, summaryCalls, requestOrder);
    await injectFakeSegments(page, 1);

    await page.evaluate(() => requestSummary());

    await expect.poll(() => summaryCalls.length, { timeout: 5000 }).toBe(1);
    expect(correctionCalls).toHaveLength(1);
    expect(requestOrder).toEqual(["correction", "summary"]);
    expect(summaryCalls[0].segments[0].segment_id).toBe("seg-0001");
  });
});

test.describe("Live-stop finalize drain (M21)", () => {
  /** Puts the page into a live-recording state without a microphone. */
  async function enterLiveRecordingState(page) {
    // The real UI transition hides Start and shows Stop, so drain assertions
    // observe the same control states a clinician would.
    await page.evaluate(() => showRecordingUi());
  }

  test("stop waits for finalized, renders the tail, then summarizes once", async ({
    page,
  }) => {
    const correctionCalls = [];
    const summaryCalls = [];
    const requestOrder = [];
    await loadScribePage(page);
    await stubCorrectionRoute(page, correctionCalls, requestOrder);
    await stubSummaryRoute(page, summaryCalls, requestOrder);
    await injectFakeSegments(page, 3);
    await enterLiveRecordingState(page);

    await page.evaluate(() => stopRecording());

    // The drain holds the visit open: no start button, waiting status.
    await expect(page.locator("#status")).toContainText("Finishing transcription");
    await expect(page.locator("#startBtn")).toBeHidden();
    expect(summaryCalls).toHaveLength(0);

    // The server's held-back tail arrives while the stream is still open.
    await page.evaluate(() => {
      handleRawSegment({
        type: "segment",
        speaker_id: "spk_0",
        text: "the finalize flush row",
        start: 6.0,
        end: 7.5,
        segment_id: "seg-0004",
      });
      handleRawSegment({ type: "finalized", session_id: CONFIG.sessionId });
    });

    // Drain ends: tail row visible, visit closed, exactly one summary request
    // whose body carries the tail row the pre-M21 race used to lose.
    await expect(page.locator("#status")).toContainText("Session ended");
    await expect(page.locator("#startBtn")).toBeVisible();
    await expect(
      page.locator('.segment__text[data-segment-id="seg-0004"]')
    ).toHaveCount(1);
    await expect
      .poll(() => summaryCalls.length, { timeout: 5000 })
      .toBe(1);
    expect(correctionCalls).toHaveLength(1);
    expect(requestOrder).toEqual(["correction", "summary"]);
    const postedIds = summaryCalls[0].segments.map((row) => row.segment_id);
    expect(postedIds).toContain("seg-0004");
    expect(postedIds).toHaveLength(4);
  });

  test("stop with no visible rows still drains finalize rows into the summary", async ({
    page,
  }) => {
    const summaryCalls = [];
    await loadScribePage(page);
    await stubSummaryRoute(page, summaryCalls);
    await enterLiveRecordingState(page);

    await page.evaluate(() => stopRecording());
    await expect(page.locator("#status")).toContainText("Finishing transcription");

    await page.evaluate(() => {
      handleRawSegment({
        type: "segment",
        speaker_id: "spk_0",
        text: "first words only released by finalize",
        start: 0.0,
        end: 1.5,
        segment_id: "seg-final-0001",
      });
      handleRawSegment({ type: "finalized", session_id: CONFIG.sessionId });
    });

    await expect(page.locator("#status")).toContainText("Session ended");
    await expect(page.locator("#startBtn")).toBeVisible();
    await expect
      .poll(() => summaryCalls.length, { timeout: 5000 })
      .toBe(1);
    expect(summaryCalls[0].segments.map((row) => row.segment_id)).toEqual([
      "seg-final-0001",
    ]);
  });

  test("a dead backend cannot hold the visit open: timeout still summarizes", async ({
    page,
  }) => {
    test.setTimeout(45000);
    const summaryCalls = [];
    await loadScribePage(page);
    await stubSummaryRoute(page, summaryCalls);
    await injectFakeSegments(page, 2);
    await enterLiveRecordingState(page);

    await page.evaluate(() => stopRecording());
    await expect(page.locator("#status")).toContainText("Finishing transcription");

    // No finalized event ever arrives; the bounded timeout finishes the visit.
    await expect(page.locator("#status")).toContainText("Session ended", {
      timeout: 20000,
    });
    await expect.poll(() => summaryCalls.length, { timeout: 5000 }).toBe(1);
  });

  test("reset during the drain tears down immediately without a summary", async ({
    page,
  }) => {
    const summaryCalls = [];
    await loadScribePage(page);
    await stubSummaryRoute(page, summaryCalls);
    await injectFakeSegments(page, 2);
    await enterLiveRecordingState(page);

    await page.evaluate(() => {
      stopRecording();
      resetSession();
    });

    // Reset discards the visit: drain cancelled, stream closed, no summary.
    const state = await page.evaluate(() => ({
      draining: isLiveDraining,
      mercureConnected: streams?.isConnected ?? false,
    }));
    expect(state.draining).toBe(false);
    expect(state.mercureConnected).toBe(false);

    // A stale finalized event after reset must not resurrect the old visit.
    await page.evaluate(() => {
      handleRawSegment({ type: "finalized", session_id: "stale-session" });
    });
    await page.waitForTimeout(500);
    expect(summaryCalls).toHaveLength(0);
  });

  test("a row correction made before Stop survives the drain into the summary body", async ({
    page,
  }) => {
    const summaryCalls = [];
    await loadScribePage(page);
    await stubSummaryRoute(page, summaryCalls);
    await injectCoalescedCardSegments(page);

    // Clinician fixes one wrong row, then stops the visit.
    const secondRow = page.locator('.segment__text[data-segment-id="seg-0002"]');
    const overridePosted = page.waitForRequest(
      (request) =>
        request.url().includes("/roles/override") && request.method() === "POST"
    );
    await secondRow.click();
    await overridePosted;

    await enterLiveRecordingState(page);
    await page.evaluate(() => {
      stopRecording();
      handleRawSegment({ type: "finalized", session_id: CONFIG.sessionId });
    });

    await expect.poll(() => summaryCalls.length, { timeout: 5000 }).toBe(1);
    const correctedRow = summaryCalls[0].segments.find(
      (row) => row.segment_id === "seg-0002"
    );
    expect(correctedRow.role).toBe("DOCTOR");
  });
});

test.describe("Replay auto-summary", () => {
  test("replay stop runs correction before generating the summary", async ({
    page,
  }) => {
    const correctionCalls = [];
    const summaryCalls = [];
    const requestOrder = [];
    await loadScribePage(page);
    await stubCorrectionRoute(page, correctionCalls, requestOrder);
    await stubSummaryRoute(page, summaryCalls, requestOrder);
    await injectFakeSegments(page, 2);

    await page.evaluate(() => {
      isReplayActive = true;
      stopReplay();
      handleRawSegment({ type: "finalized", session_id: CONFIG.sessionId });
    });

    await expect
      .poll(() => summaryCalls.length, { timeout: 5000 })
      .toBe(1);
    expect(correctionCalls).toHaveLength(1);
    expect(requestOrder).toEqual(["correction", "summary"]);
  });
});

test.describe("Chronological transcript insertion (M22 refinements)", () => {
  test("a late-arriving old row inserts at its spoken position", async ({ page }) => {
    await loadScribePage(page);
    await page.evaluate(() => {
      const rows = [
        { speaker_id: "spk_0", text: "opening line", start: 2.0, end: 4.0, segment_id: "seg-0001" },
        { speaker_id: "spk_1", text: "a reply", start: 20.0, end: 23.0, segment_id: "seg-0002" },
        { speaker_id: "spk_0", text: "a follow up", start: 40.0, end: 43.0, segment_id: "seg-0003" },
        // The straggler: a dormant slot's drained tail from much earlier.
        { speaker_id: "spk_1", text: "the straggler words", start: 10.0, end: 12.0, segment_id: "seg-0004" },
      ];
      rows.forEach((row) => handleRawSegment({ type: "segment", ...row }));
    });

    const starts = await page.evaluate(() =>
      [...document.querySelectorAll(".segment")].map((c) => parseFloat(c.dataset.start))
    );
    // Chronological on screen despite arrival order.
    expect(starts).toEqual([2.0, 10.0, 20.0, 40.0]);

    // Tail coalescing still works after an insertion: a same-speaker row
    // adjacent to the LAST card must join it, not the straggler's card.
    await page.evaluate(() => {
      handleRawSegment({
        type: "segment",
        speaker_id: "spk_0",
        text: "and more follow up",
        start: 44.0,
        end: 46.0,
        segment_id: "seg-0005",
      });
    });
    const cardCount = await page.evaluate(() => document.querySelectorAll(".segment").length);
    const expectedCardsAfterTailMerge = 4;
    expect(cardCount).toBe(expectedCardsAfterTailMerge);
  });

  test("late rows remain chronological inside and across coalesced cards", async ({ page }) => {
    await loadScribePage(page);
    await page.evaluate(() => {
      const rows = [
        { speaker_id: "spk_0", text: "first", start: 40.0, end: 41.0, segment_id: "seg-a" },
        { speaker_id: "spk_0", text: "third", start: 44.0, end: 45.0, segment_id: "seg-c" },
        { speaker_id: "spk_0", text: "second late", start: 42.0, end: 43.0, segment_id: "seg-b" },
        { speaker_id: "spk_1", text: "between speakers late", start: 43.0, end: 43.5, segment_id: "seg-x" },
      ];
      rows.forEach((row) => handleRawSegment({ type: "segment", ...row }));
    });

    const rowStarts = await page.evaluate(() =>
      [...document.querySelectorAll(".segment__text")].map((row) => parseFloat(row.dataset.start))
    );
    expect(rowStarts).toEqual([40.0, 42.0, 43.0, 44.0]);

    const summaryRows = await page.evaluate(() => readVisibleTranscriptSegments());
    expect(summaryRows.map((row) => row.segment_id)).toEqual(["seg-a", "seg-b", "seg-x", "seg-c"]);
  });
});
