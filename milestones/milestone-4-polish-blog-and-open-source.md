# Milestone 4 — Polish, Blog, and Open Source

**Timeline:** Weekend 4 (~4-5 hours, optional/stretch)
**Status:** Not Started
**Dependencies:** Milestone 3 complete (DOCTOR/PATIENT attribution working end-to-end)

---

## Objective

Make the project demo-ready, produce the BlunderGOAT blog article, and prepare for open source release. This milestone is optional — the core PoC is complete after Milestone 3.

---

## Tasks

### 4.1 Replay Demo Mode (~1 hour)

> Pre-recorded consultation audio plays through the pipeline so you can demo without needing two live speakers.

- [ ] Add a "Demo Mode" button to the UI
- [ ] Store a **self-recorded** consultation WAV in `public/demo/` (not YouTube-extracted — see legal note below)
- [ ] On demo start:
  - Python reads the WAV file and feeds it through the pipeline in simulated real-time (5-second chunks with delays)
  - Publishes segments to Mercure as if they were live
  - Browser displays the transcript building in real-time
- [ ] Add FastAPI endpoint: `POST /demo/start/{session_id}` triggers the replay
- [ ] Include a progress indicator showing playback position

### 4.2 Clinical Summary Generation (~1 hour)

- [ ] At consultation end (user clicks "End Consultation"), trigger a Strands agent to produce a brief clinical summary:
  - Chief complaint
  - History of presenting illness (key points)
  - Key findings mentioned
  - Plan / next steps discussed
- [ ] Display summary in a collapsible panel below the transcript
- [ ] Summary agent system prompt:
  ```
  You are a clinical documentation assistant. Given a doctor-patient consultation
  transcript, produce a concise clinical summary. Use standard medical documentation
  format. Do not infer information not present in the transcript.
  ```
- [ ] Publish summary to a dedicated Mercure topic: `scribe/session/{id}/summary`

### 4.3 UI Polish (~1 hour)

- [ ] Session timer showing duration and segment count
- [ ] Mobile-responsive layout (ambient scribe is often used on a tablet in-room)
- [ ] Improved dark mode with accessible colour contrast
- [ ] Loading states and error messages for:
  - Microphone permission denied
  - WebSocket connection failure
  - NeMo pipeline error
  - Agent timeout
- [ ] Export transcript as plain text or JSON (download button)
- [ ] Keyboard shortcuts:
  - `Space` or `R` to start/stop recording
  - `Esc` to end consultation

### 4.4 Production Deployment Considerations

- [ ] **HTTPS/WSS requirement:**
  - `getUserMedia` requires secure context in production
  - ALB needs TLS termination (ACM certificate)
  - WebSocket upgrade support at ALB (configure target group with stickiness + WebSocket protocol)
  - Browser must use `wss://` not `ws://`
- [ ] Add TLS configuration to Terraform (ACM cert, ALB HTTPS listener)
- [ ] Add Route53 record for `scribe.blundergoat.com`
- [ ] Rate limiting on WebSocket connections (WAF or application-level)
- [ ] WebSocket authentication (token-based, passed as query param or first message)

### 4.5 Session Persistence

> **Currently, transcripts exist only in Python's in-memory session store.** If the process restarts or the user navigates away, the transcript is lost. This is acceptable for a PoC but worth documenting.

- [ ] Document as known limitation in README
- [ ] If time permits: persist completed transcripts to DynamoDB (same pattern as The Summit's production session store)
- [ ] If time permits: add session recovery — on page reload, fetch transcript history from Python/DynamoDB and re-render

### 4.6 BlunderGOAT Article

**Title:** "Building a Real-Time Ambient Medical Scribe in 4 Weekends"
**Subtitle:** "How I turned a multi-agent chatroom into a speaker-aware medical transcription system"
**Tagline:** "Make the right way the easy way"

- [ ] **Structure:**
  1. **The Problem** — ambient scribe needs real-time streaming + speaker diarization + role attribution. Most solutions are expensive SaaS or complex custom builds.
  2. **The Blunder** — naive approach: pipe audio to a transcription API and regex match speakers. Falls apart with overlapping speech, label flips, cold start.
  3. **The Framework** — agent-based architecture where transcription is a tool and role inference is a reasoning task. NeMo does the ML, Strands agent does the thinking.
  4. **The Build** — weekend-by-weekend walkthrough with architecture diagrams, code snippets, and gotchas.
  5. **The Payoff** — Strands tool pattern means swapping NeMo -> Riva -> Deepgram -> Whisper is a one-file change. Agent intelligence is backend-agnostic.
- [ ] Include architecture diagrams (Mermaid or ASCII)
- [ ] Include 2-minute demo video (screen recording of live transcription)
- [ ] Include VRAM usage screenshots and latency measurements
- [ ] Link to GitHub repo

### 4.7 Open Source Preparation (~1 hour)

- [ ] Strip any employer-specific or personal references
- [ ] **Audit test fixtures for licensing:**
  - Only self-recorded audio or explicitly licensed files may be committed
  - YouTube-extracted OSCE audio must NOT be in the repo (add to `.gitignore`)
  - Add a `tests/fixtures/audio/README.md` explaining how to obtain test audio locally
- [x] Comprehensive README with architecture diagram, prerequisites, quick start, tech stack (created during scaffold)
- [ ] Add `CONTRIBUTING.md`
- [ ] Add `LICENSE` (MIT or Apache 2.0)
- [ ] Verify `docker compose up --build` works from a clean clone (GPU required)
- [x] Add `.env.example` with all required environment variables documented (created during scaffold)
- [ ] Tag as `v0.1.0-poc`

---

## Exit Criteria

### Demo-Ready (minimum for this milestone)
- [ ] Pre-recorded demo replay works end-to-end (using self-recorded audio)
- [ ] Clinical summary generated at consultation end
- [ ] UI is polished and mobile-responsive
- [ ] Can record a compelling 2-minute demo video

### Open Source (stretch)
- [ ] Repo is clean, documented, and reproducible from clone
- [ ] **No unlicensed third-party audio in the repo**
- [ ] README covers setup, architecture, and known limitations
- [ ] Tagged `v0.1.0-poc`
- [ ] Blog article published on BlunderGOAT

---

## Success Metrics (Full Project)

| Level | Criteria |
|---|---|
| **Minimum Viable Demo** (Milestone 2) | Live mic -> real-time transcript with speaker labels in browser. Latency < 5s. Works on 2-person conversation. |
| **Full Demo** (Milestone 3) | DOCTOR/PATIENT attribution with progressive confidence. Agent self-corrects label flips. Colour-coded transcript UI. Compelling enough for a 2-minute demo video. |
| **Stretch** (Milestone 4) | Pre-recorded demo replay. Clinical summary generation. Open source repo with working Docker Compose. Published BlunderGOAT article. |
