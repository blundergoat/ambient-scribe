---
category: browser-verification
last_reviewed: 2026-07-15
---

# Browser verification lessons

## Lesson: Transcript card grouping needs arrival-order reproduction

**Created:** 2026-07-14
**What happened:** A manual consult showed adjacent Patient cards, making role relabeling or two
raw speaker IDs plausible causes. Saved history proved the fragments shared one speaker ID, and a
three-event browser repro with stable roles isolated the real branch: a delayed continuation at a
card boundary was always inserted as a new card.
**Evidence:** `var/quality/transcript-card-grouping-20260713T192500Z/diagnosis.md` and
`reproduce-card-split.js` retain the same-speaker rows, arrival order, and failing DOM shape.
**Prevention:** Reproduce transcript grouping with raw speaker IDs, spoken timestamps, and event
arrival order before changing role policy. Assert both visible card count and row-preserving summary
order; final labels alone cannot reveal which chronological insertion branch created a card.
**Follow-up:** The first append pushed the broad `verification.md` bucket over its 39 KB gate.
Start browser-specific lessons in this narrower bucket and run index/stats immediately.

## Lesson: Capture a UI mock's normal and interaction states separately

**Created:** 2026-07-11
**What happened:** M03's first component capture forced its confidence tooltip open, which
covered the adjacent `Review wording` chip and made the recommended default state impossible
to judge even though both elements rendered correctly.
**Evidence:** `var/quality/m03-confidence-styling-phase0-20260711T093609Z/light-theme-component-before-tooltip-fix.png`
shows the overlap; the final `light-theme-component.png` and `light-theme-tooltip-focus.png`
separate the default and keyboard-focused states.
**Prevention:** Capture the untouched component first, then trigger hover/focus and save a
second image; an interaction overlay must never replace evidence of the default UI hierarchy.
**Follow-up (M03 product):** A focus assertion sampled the first frame of a 140ms tooltip
transition and read opacity 0, then a locator screenshot clipped the tooltip outside the row's
box even after opacity reached 1. Poll the settled pseudo-element style and use a padded page
clip when the interaction overlay extends beyond the captured element.

## Lesson: Give browser-use screenshots an absolute evidence path

**Created:** 2026-07-11
**What happened:** M03 invoked `browser-use screenshot` with a relative filename from an
evidence-directory shell, but the persistent browser daemon resolved it at the repository root.
The final status gate caught the untracked PNG before the design handoff.
**Evidence:** `var/quality/m03-confidence-styling-phase0-20260711T093609Z/browser-use-path-routing.log`
records the requested, observed, and corrected paths.
**Prevention:** Pass an absolute evidence path to `browser-use screenshot`; do not assume the
daemon shares the invoking shell's working directory, and always finish with `git status`.

## Lesson: Browser stream state ordering needs a focused regression check (2026-07-04)

**Source:** git history (auto-seeded)
**Evidence:** `templates/scribe/index.html.twig` + commit `0125a6b` fixed the StreamOrchestrator `_active` ordering bug.

**Lesson:** When changing EventSource subscription setup or stream lifecycle state, run a browser or contract regression that proves subscriptions can connect, reconnect, and shut down in that order.

## Lesson: Hidden sidebars need layout-state smoke tests (2026-07-04)

**Created:** 2026-07-04
**Evidence:** `templates/scribe/index.html.twig` (search: ".summary-column:not(:has(.summary-panel:not(.hidden)))"), `public/js/scribe-output.js` (search: "function renderSummary").

During M12, the first clinical-hints UI pass hid the sidebar element but left a dedicated desktop grid column in the base layout. Static analyzers and API tests stayed green, but the clinician page would have opened with blank right-side space until hints arrived.

**Lesson:** When adding a hidden/dismissible panel that changes page columns, verify both empty and populated layout states with a DOM or browser smoke test. The hidden state must remove reserved layout space, not only hide panel contents.

## Lesson: Browser replay smokes need a trustworthy origin (2026-07-04)

**Created:** 2026-07-04
**Evidence:** `public/js/scribe-recording.js` (search: "crypto.randomUUID"), `public/js/scribe-fixtures.js` (search: "startReplay(replayFile, { audioUrl").

While testing the Demo Audio picker, a Playwright smoke loaded the page on `http://app.test/`. The replay flow called `resetSession()`, which uses `crypto.randomUUID()`, and Chromium denied that API on the non-trustworthy fake origin. The same smoke passed when routed through `http://localhost/`, matching local app behavior.

**Lesson:** Browser smokes that exercise recording or replay session reset should run on `localhost` or HTTPS, not arbitrary fake HTTP hosts. Otherwise secure-context browser APIs can fail before the app flow is actually tested.

## Lesson: Populated transcript layouts need populated browser smokes (2026-07-04)

**Created:** 2026-07-04
**Evidence:** `templates/scribe/index.html.twig` (search: "consultation-workspace"), `public/js/scribe-transcript.js` (search: "Any visible transcript text means the start prompt is no longer useful.").

During the 0.3.0 mockup refresh, an empty-state screenshot made the new workspace layout look clean, but a populated transcript/summary browser smoke exposed that direct transcript events could leave the start prompt visible above real rows.

**Lesson:** For transcript, summary, or hidden-panel layout changes, capture both empty and populated browser states. Include DOM assertions for card count, empty-state visibility, overlap, and removed controls so visual verification covers the state users actually review.
**Follow-up (M03 product):** The first quiet `Review wording` chip increased an otherwise
identical transcript card by 3.33px. Compare equal-content marked/unmarked card bounding boxes;
keeping the chip within the existing header line-height removed the shift while preserving the
visible label.

## Lesson: Full-page screenshot compression can misreport theme rendering

**Created:** 2026-07-07
**What happened:** During summary UX M4, a downscaled full-page dark-mode screenshot made
the summary panel look white-on-dark, suggesting the new tab CSS ignored the dark theme.
Computed-style probes (`getComputedStyle(...).backgroundColor`) showed the panel at the
correct dark token (#16202b), and an element-level screenshot of `#summaryPanel` rendered
plainly dark - the "white panel" was a rendering/compression artifact of the 1280px
full-page capture.
**Prevention:** For theme verification, assert computed styles for the changed elements
and screenshot the component (`locator(...).screenshot()`), not only the page. Do not
file or fix a theme bug from a downscaled full-page PNG alone.

## Lesson: A block class rendered in two views breaks strict-mode e2e locators

**Created:** 2026-07-07
**What happened:** The M5 provenance popover deliberately reuses the Transcript tab's
`.summary-transcript__block` builder so cited utterances look identical in both places.
The first migrated e2e test located `.summary-transcript__block` bare and failed with a
Playwright strict-mode violation: the class now resolves in BOTH the popover and the tab.
**Evidence:** `tests/e2e/browser.spec.js` (search: "scope to the transcript tab body").
**Prevention:** When a component builder is reused across views, scope e2e locators to
the owning container (`#summaryTranscript .summary-transcript__block`). Audit existing
locators for a class the moment a second consumer of its builder lands.
