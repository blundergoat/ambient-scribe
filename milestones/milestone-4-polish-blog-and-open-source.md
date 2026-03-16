# Milestone 4 — Product Polish + Demo

**Timeline:** Weekend 4 (~5-6 hours)
**Status:** Code-complete (all tasks done; demo video recording is the only remaining exit criterion)
**Dependencies:** Milestone 3 + 3.5 complete

---

## Objective

Make the application demo-ready with a compelling user experience. Audio quality feedback, session summaries, replay mode, and frontend cleanup. Prepare for the blog article and open source release.

---

## Tasks

### 4.1 Audio Quality Feedback

- [x] RMS energy check on incoming PCM chunks in `PcmStreamer`
- [x] If average amplitude below threshold for 3+ consecutive chunks: show "Low audio level — move closer to the microphone"
- [x] Clipping detection: if samples hit max int16 value frequently, show "Audio clipping detected"
- [x] Visual mic level indicator (waveform or simple bar) confirming the mic is active even during silence

### 4.2 Session Summary Generation

> **Why:** The summary is what transforms a transcription tool into a productivity tool. It's the highest-value feature for every mode, not just medical.

- [x] Triggered on "End Session" — Strands agent produces a structured summary
- [x] Summary format adapts to the selected mode:
  - **Medical:** SOAP note (Subjective, Objective, Assessment, Plan)
  - **Meeting:** Action items, decisions, attendees, next steps
  - **Interview:** Key topics discussed, candidate strengths/concerns, follow-up items
  - **General:** Key points, speaker contributions, topics covered
- [x] Summary displayed in a collapsible panel below the transcript
- [x] Summary cites transcript spans (e.g., "Patient reported chest pain [00:03-00:08]")
- [x] Summary published to Mercure topic: `scribe/session/{id}/summary`
- [x] Downloadable as part of the JSON/text export

### 4.3 Replay Demo Mode

- [x] "Demo Mode" button in the UI — file picker for WAV upload
- [x] Replays a WAV through the NeMo pipeline, then publishes segments to Mercure with real-time pacing (adjustable speed)
- [x] Segments publish to Mercure as if they were live — role inference runs automatically
- [x] Progress indicator showing playback position (progress bar + timer)
- [x] Useful for demos, testing, and development without a live microphone

### 4.4 Frontend Cleanup

- [x] Extract all JS into `public/js/scribe.js` (core) and `public/js/scribe-dev.js` (dev panel, zero prod bytes)
- [x] Separate dev panel / scenario runner from production template (conditional `<script>` loading)
- [x] Fix: `pcmStreamer` variable is an implicit global (never declared with `let`/`const`)
- [x] `relabelSegments()` performance: track segments by `speaker_id` in a Map (`segmentsBySpeaker`), only update changed roles
- [x] Accessibility: `aria-live` on transcript container, keyboard shortcuts (Space=start/stop, Esc=end)
- [x] Timer, segment counter, download button already exist
- [x] Light/dark theme with persistence already exists
- [x] Mobile viewport meta tag already exists

### 4.5 Scenario Runner Improvements

- [x] 8 scenarios: happy path, role flip, reconnect, high-volume stress, empty, single speaker, late role, permanent disconnect
- [x] Batch execution, progress bar, JSON export
- [x] Add timing assertions (high-volume stress must complete within 5s budget — `maxDurationMs`)
- [x] Add segment content assertions (verify first/last segment text matches — `contentCheck`)
- [x] Add 3+ speaker scenario (+ 4 more multi-mode scenarios: meeting, interview, TV, lecture)
- [x] Promote scenario runner as a required local gate — `test_scenarios.py` validates fixture integrity (12 tests)

---

## Exit Criteria

- [x] Audio quality feedback visible when mic level is too low
- [x] Mode-appropriate summary generated on session end
- [x] Demo replay works end-to-end without live microphone
- [x] JS extracted from monolithic template (3+ separate files — scribe.js, scribe-dev.js, inline config)
- [x] Scenario runner passes as part of quality gate (fixture validation in pytest)
- [ ] Can record a compelling 2-minute demo video showing the full flow

---

## Already Done (from earlier milestones)

- [x] Session timer and segment count
- [x] Download as JSON and plain text
- [x] Mobile viewport meta tag + Tailwind responsive utilities
- [x] Mercure failure banner + system error handling
- [x] 6 interaction modes with role labels and avatars
- [x] Dev panel with inspector tabs (Segments, Pipeline, Mercure, WebSocket, State, Raw)
