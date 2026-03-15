# Milestone 6 — Open Source + Content

**Timeline:** ~3-4 hours
**Status:** Not Started
**Dependencies:** Milestone 4 complete (polished product), M5 optional

---

## Objective

Prepare the project for public release and produce the blog article. This is separate from product work so reliability doesn't compete with content creation.

---

## Tasks

### 6.1 Repository Cleanup

- [ ] Strip employer-specific or personal references
- [ ] Audit test fixtures for licensing (only self-recorded audio may be committed)
- [ ] Add `tests/fixtures/audio/README.md` explaining how to obtain test audio locally
- [ ] Verify `docker compose up --build` works from a clean clone
- [x] `.env.example` with all required environment variables documented
- [x] Comprehensive `CLAUDE.md` with architecture, commands, and context router

### 6.2 Open Source Files

- [ ] Add `LICENSE` (MIT or Apache 2.0)
- [ ] Add `CONTRIBUTING.md`
- [ ] Add `README.md` with: architecture diagram, prerequisites, quick start, tech stack, demo screenshot/gif
- [ ] Tag as `v0.1.0`

### 6.3 Blog Article

**Title:** "Building a Real-Time Ambient Scribe in 4 Weekends"

- [ ] Structure:
  1. The Problem — real-time streaming + speaker diarisation + role attribution
  2. The Architecture — NeMo GPU + Strands agent + Mercure SSE
  3. The Build — weekend-by-weekend walkthrough with code snippets
  4. The "Wow Moment" — progressive confidence UX (grey → colour-coded roles)
  5. Multi-Mode — how one architecture serves medical, meetings, interviews, lectures
  6. The Payoff — swapping ASR backends is a one-file change; agent intelligence is backend-agnostic
- [ ] Include architecture diagrams (Mermaid)
- [ ] Include 2-minute demo video (screen recording of live transcription)
- [ ] Include VRAM usage and latency measurements
- [ ] Link to GitHub repo

---

## Exit Criteria

- [ ] Repo is clean, documented, and reproducible from clone
- [ ] No unlicensed third-party audio in the repo
- [ ] README covers setup, architecture, modes, and known limitations
- [ ] Blog article published
- [ ] Tagged release on GitHub
