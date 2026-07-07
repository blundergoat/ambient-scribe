---
category: verification
last_reviewed: 2026-07-07
---

# READ / SCOPE / VERIFY Lessons

## Lesson: Post-visit correction smoke tests must stay inside reconnect grace

**Created:** 2026-07-06
**What happened:** A consult-03 browser smoke replay reached `02:57` and generated a
summary, but the corrected transcript artifact was empty. The app had behaved correctly:
the summary click happened after `session_lifecycle.grace_period_expired`, so `/correction`
could no longer access retained audio and fell back to live rows.
**Prevention:** When testing the post-visit correction lane manually, click Summarise
immediately after Stop/finalized and verify logs include both `correction.completed` and
`summary.requested source=corrected_segments`. A generated summary alone only proves the
fallback path when correction grace has expired.

## Lesson: Editing agent source evicts in-memory sessions via uvicorn reload

**Created:** 2026-07-07
**What happened:** A summary-prompt edit to `strands_agents/agents/summary_agent.py` triggered
the nemo-agent's uvicorn `--reload`, which restarted the process and wiped the in-memory
`SessionStore` - the captured user sessions' stored rows vanished mid-validation and
`POST /session/{id}/summary` started returning 404 "No transcript found".
**Prevention:** With `SESSION_STORAGE=memory`, treat ANY edit under `strands_agents/` as a
session-destroying restart. Capture `/history` and `/corrected-transcript` artifacts to
`var/quality/` BEFORE editing agent code. If server state is already gone, the summary route
accepts the captured rows directly as `SummaryRequest.segments` under a fresh session UUID -
that replays the full prompt+model path from on-disk artifacts.

## Lesson: Full-clip proportional word timings drift - timing splits need an internal coherence guard

**Created:** 2026-07-07
**What happened:** The first runtime M08 eval run split the consult-08 age echo at 14.32-15.12
while the true doctor echo sits at ~16.1-16.6: the token-timestamps-proportional-to-words
estimate that was ~200ms accurate on the 20s probe drifted ~1.6s on the full 60s correction
clip (token density varies across pauses, and the proportional word-to-token index mapping
ignores that). The mis-timed DOCTOR row landed inside patient-only truth and scored
confidently-wrong: A/B on the same artifact measured 83.3% strict without the split vs 78.9%
with it - the split itself created the regression the M04 naive split had, just via drift
instead of row-share times.
**Prevention:** Never trust a full-clip proportional estimate as an absolute time. Gate timing
splits on internal coherence between independent time sources: the echo tail's timed end must
reach or pass the live row's end (`corrected_role_cues.py`, search: "echo_end <
safe_source_time"). When the sources disagree, keep the row whole. Probe-scale accuracy
(short clips) does not generalize to session-length clips - re-verify timing accuracy at the
real clip length before widening any timing-based rule.

## Lesson: Graceful client recovery can disguise a server crash as a clean stop (2026-07-05)

A demo replay "stopped by itself" at ~70s and was signed off as the socket-drop recovery path working: the browser drained, showed "Replay stopped", and even produced good ground-truth metrics. The server had actually CRASHED (`ValueError: buffer size must be a multiple of element size` in the new windowed emission) - the drop handler turned the crash into exactly the UI a deliberate stop produces. Two layers hid it: `logger.error("websocket.error", extra={...})` carried the exception only in invisible `extra` fields and logged no traceback (no `exc_info`), so even a reproduction showed a bare ERROR line until the logger was instrumented first (`strands_agents/api/streaming_session.py`, search: "websocket.error %s").

**Lesson:** After any session that ends earlier than expected - even one that looks like a clean stop - grep the server logs for ERROR before declaring the run healthy; resilient client recovery converts crashes into normal-looking endings. And when a swallowed exception needs diagnosing, instrument the logger FIRST (`exc_info=error`, error text in the message string, not only `extra`), then reproduce once; reproducing before instrumenting wastes the reproduction.

## Lesson: Config-echo checkmarks are not integration checks - invoke the dependency from its runtime (2026-07-05)

`./scripts/check-ai-model.sh` printed `✔ model us.anthropic.claude-haiku-4-5-20251001-v1:0` and `✔ AWS credentials resolve with STS`, so Bedrock looked healthy while every summary failed in ~140ms with the browser blaming "AI model unavailable". Neither checkmark had touched Bedrock: the model line was a plain `echo` of config, and STS only proves the access key authenticates against the identity service. The real error (`ValidationException: The provided model identifier is invalid` - a `us.` geo inference profile does not exist in ap-southeast-2) surfaced only by invoking `bedrock-runtime converse` from inside the nemo-agent container with the container's own env. The "obvious" fix (`apac.` prefix) was also wrong - only `aws bedrock list-inference-profiles` revealed that Sydney exposes Claude 4.5+ under `au.`/`global.` profiles. A second trap compounded the hunt: the exception WAS logged at the failure site, but only into `extra={}` fields that the plain log format silently drops (`strands_agents/api/summary_generation.py`, search: "summary.agent_failed"), so the ERROR line carried no reason.

**Lesson:** When a health check passes but the feature fails: (1) distrust any ✔ that is an echo of config or a probe of an adjacent service (STS is not Bedrock); (2) reproduce the exact failing call from the runtime environment - same container, same env (`docker compose exec <svc> python3 -c ...`); (3) enumerate valid identifiers instead of pattern-guessing them (`list-inference-profiles`, not geo-prefix analogy); (4) remember `logger.error(msg, extra={...})` detail is invisible in plain log format - the answer may already be logged where you cannot see it. The script now probes for real (`scripts/check-ai-model.sh`, search: "model responds to a live invoke"). Related: `.goat-flow/learning-loop/lessons/verification.md` entry "Verify the running container's env, not the compose default".

## Lesson: Guard shared socket handlers against stale-socket events (2026-07-05)

While converting demo replay to stream over the live WebSocket, the shared `onclose` handler gained a replay branch (`public/js/scribe-recording.js`, search: "A drop of the replay's own socket"). Self-review before runtime caught a race: starting a replay while a live recording is active closes the OLD socket, and if that close event fires after `isReplayActive` becomes true, the handler would have stopped the brand-new replay. Fixed by requiring `event.target === transcriptionSocket` before treating a close as the replay's own.

**Lesson:** When one event handler serves sockets that are replaced across session transitions, compare `event.target` against the current socket before acting — flags like `isReplayActive` describe the NEW session, not the socket that emitted the event.

## Lesson: Verify the running container's env, not the compose default (2026-07-04)

After changing the `docker-compose.yml` default to `OLLAMA_HOST=http://ollama:11434`, one `docker compose up -d` showed the agent with the fixed value, so the "agent can't reach Ollama" bug was declared fixed. But the user's own `start-dev.sh` (`dc up -d`) recreated the agent with `OLLAMA_HOST=http://host.docker.internal:11434` - because `.env` sets it and `${OLLAMA_HOST:-…}` lets `.env` override the compose default. Summaries kept returning 502 and role inference kept falling back to the heuristic on every recreate.

**Lesson:** For env-driven behaviour, verify the RUNNING container's effective value (`docker compose exec <svc> sh -c 'echo $VAR'`) after the user's own startup path - not the compose file, and not a single ad-hoc recreate. `.env` overrides `${VAR:-default}`, so a stale `.env` silently reintroduces the bug. When a value must not be overridable, pin the literal in compose. Related footgun: `.goat-flow/learning-loop/footguns/role-agent.md`.

## Lesson: Audio format mismatch - read both pipeline ends (2026-03-21)

AudioBuffer in `strands_agents/nemo_session.py` assumed 16 kHz 16-bit PCM while the browser MediaRecorder sent WebM/Opus. NeMo received garbage audio and produced nonsensical transcriptions with no errors in logs. Root cause was found only after reading both `templates/scribe/index.html.twig` (producer) and `strands_agents/nemo_session.py` (consumer).

**Lesson:** Always read both ends of a data pipeline before diagnosing silent failures. Related footgun: `.goat-flow/learning-loop/footguns/audio.md`.

## Lesson: Stale references after rename (2026-03-21)

After renaming `mercure_topic_raw` to `mercure_topic_segments`, stale references remained in config and docs.

**Lesson:** Always run `rg <old_symbol>` after renames and confirm zero remaining refs (DoD gate #6).

## Lesson: Browser stream state ordering needs a focused regression check (2026-07-04)

**Source:** git history (auto-seeded)
**Evidence:** `templates/scribe/index.html.twig` + commit `0125a6b` fixed the StreamOrchestrator `_active` ordering bug.

**Lesson:** When changing EventSource subscription setup or stream lifecycle state, run a browser or contract regression that proves subscriptions can connect, reconnect, and shut down in that order.

## Lesson: Live transcription fixes need cross-layer verification, not one-service checks (2026-07-04)

**Source:** git history (auto-seeded)
**Evidence:** `docker-compose.yml`, `strands_agents/api/server.py`, `strands_agents/nemo_pipeline.py`, `strands_agents/nemo_session.py`, `templates/scribe/index.html.twig`, and `tests/python/test_api.py` + commit `35bceb4` restored live transcription and healthcheck contracts.

**Lesson:** For live transcription regressions, verify Docker wiring, FastAPI health/WebSocket behavior, NeMo session handling, and browser config together before declaring the fix complete.

## Lesson: Logging-only edits can still break hot role logic (2026-07-05)

**Created:** 2026-07-05
**Evidence:** `strands_agents/tools/assign_roles.py` (search: "previous_mapping = self.current_mapping"), `tests/python/test_role_inference.py` (search: "test_detects_full_speaker_flip").

During M18 Phase 0, a plain-log message was added to `RoleMappingState.did_update_mapping_detect_flip()` using `changed_speakers`, but that variable existed only inside `_is_role_label_flip()`. The focused observability tests passed, while the full Python suite caught six role-flip failures with `NameError`.

**Lesson:** Treat observability-only patches as executable code in the hot path. When a log line needs derived values, compute them locally before mutating state and rerun the domain tests for that path, not only the observability tests.

## Lesson: Windowed-emission tests must clear the minimum-new-audio floor (2026-07-05)

**Created:** 2026-07-05
**Evidence:** `strands_agents/nemo_session.py` (search: "_MIN_NEW_AUDIO_SECONDS"), `tests/python/test_nemo_session.py` (search: "test_alternating_speaker_fragments_stay_separate").

During M17 fragment-policy work, a regression test intended to prove alternating-speaker
fragments stayed separate put the first fragment's end exactly at the
`_MIN_NEW_AUDIO_SECONDS` boundary. The existing context-replay filter correctly dropped
that segment before the new merge policy ran, so the failure tested the old emission floor
instead of the new fragment behavior.

**Lesson:** When writing windowed-emission tests, place new segments clearly beyond
`_MIN_NEW_AUDIO_SECONDS` unless the floor itself is under test. Boundary-equal timestamps
exercise replay filtering, not downstream segment cleanup.

## Lesson: Emission floors need inclusive boundary tests (2026-07-05)

**Created:** 2026-07-05
**Evidence:** `strands_agents/nemo_session.py` (search: "_MIN_NEW_AUDIO_SECONDS"), `tests/python/test_nemo_session.py` (search: "test_emitted_audio_is_not_transcribed_again").

During M17 seam fallback work, raising the context-replay floor from 0.3s to 0.5s used a
strict `>` comparison. The existing emit-once regression then hid a segment with exactly
0.5s of new post-mark audio, contradicting the intended "at least half a second" rule.

**Lesson:** When changing an emission threshold, include boundary-equal coverage and make
the comparison match the product wording. "At least N seconds of new audio" is inclusive;
strict comparisons can silently drop short user utterances at the acceptance boundary.

## Lesson: Hidden sidebars need layout-state smoke tests (2026-07-04)

**Created:** 2026-07-04
**Evidence:** `templates/scribe/index.html.twig` (search: ".summary-column:not(:has(.summary-panel:not(.hidden)))"), `public/js/scribe-output.js` (search: "function renderSummary").

During M12, the first clinical-hints UI pass hid the sidebar element but left a dedicated desktop grid column in the base layout. Static analyzers and API tests stayed green, but the clinician page would have opened with blank right-side space until hints arrived.

**Lesson:** When adding a hidden/dismissible panel that changes page columns, verify both empty and populated layout states with a DOM or browser smoke test. The hidden state must remove reserved layout space, not only hide panel contents.

## Lesson: Optional JSON knowledge files need malformed-file coverage (2026-07-04)

**Created:** 2026-07-04
**Evidence:** `strands_agents/clinical_context.py` (search: "except json.JSONDecodeError"), `tests/python/test_clinical_context.py` (search: "test_load_clinical_knowledge_ignores_invalid_json").

During M12, the clinical KB loader handled missing files and invalid shapes but did not handle malformed JSON. A bad PoC knowledge file would have turned an assistive hint/grounding feature into a summary-generation failure for the user.

**Lesson:** For optional local JSON/fixture inputs used by a user-facing path, cover malformed JSON as well as missing files and empty data. Optional assistive data should degrade to no context, not block the primary workflow.

## Lesson: Model stack docs need env, Compose, and code defaults checked together (2026-07-04)

**Created:** 2026-07-04
**Evidence:** `README_STACK.md` (search: "Compose still has a no-"), `.env.example` (search: "ROLE_AGENT_MODEL_PROVIDER=ollama"), `strands_agents/agents/transcription_agent.py` (search: "ROLE_AGENT_MODEL_PROVIDER").

While creating the stack inventory, the root README still described Bedrock as the role-inference default, `.env.example` described Ollama as the local default, Compose passed Ollama by default, and the Python agent retained Bedrock defaults for missing env vars. Reading only one source would have produced another stale model summary.

**Lesson:** For docs that name model providers or IDs, verify `.env.example`, `docker-compose.yml`, agent factory code, and any existing README before writing the final wording. Call out intentional fallback differences instead of flattening them into one default.

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

## Lesson: Delivery-race claims need receiver-side evidence (2026-07-06)

**Created:** 2026-07-06
**Evidence:** `strands_agents/api/summary_request.py` (search: "Blank text is not useful"), `public/js/scribe-output.js` (search: "enterReplayDrain"), `.goat-flow/plans/0.3.0/M21-stop-race-finalize-delivery.md` (search: "corrected 2026-07-06").

Analyzing a manual replay from server logs alone, the agent saw the browser summary
carry 38 segments while the server stored 40, saw the WebSocket disconnect timestamp
precede the finalize-flush publish, and concluded the browser's SSE teardown raced the
flush - writing that into a milestone plan as the headline evidence. The user's dev
panel later showed the browser had received the flush row, the `finalized` event, and
two post-finalize role updates; the 2-row delta was blank-text segments intentionally
filtered by `normalise_browser_visible_segments`. The race is real but lives only in the
live-recording stop path (`stopRecording`), while the replay path already drains
correctly via `enterReplayDrain` - reading one stop path and assuming the other matched
compounded the error.

**Lesson:** Before attributing a data gap to a delivery race, verify the receiver
actually missed the data (payload contents via SSE tap, dev panel, or browser state) and
check for intentional filters on the counting path. When a browser flow has sibling
paths (live vs replay, stop vs reset), read every sibling before generalizing evidence
from one of them.

## Lesson: Agent-only QA artifacts must be fetched from the agent origin (2026-07-06)

**Created:** 2026-07-06
**Evidence:** `strands_agents/api/server.py` (search: "/session/{session_id}/history"), `strands_agents/api/server.py` (search: "/session/{session_id}/corrected-transcript"), `src/Controller/ScribeController.php` (search: "public function correction").

During corrected-transcript manual QA, the browser replay, correction, and summary all
succeeded, but the Playwright artifact script failed with `Unexpected token '<'` after
fetching `/session/{id}/history` from the Symfony app origin. Symfony correctly returned
HTML 404 because only summary/correction have same-origin proxy routes; history and
corrected-transcript are FastAPI QA routes on the agent port.

**Lesson:** Before fetching a manual QA artifact, verify which service owns the route.
Use the FastAPI agent origin for `/session/{id}/history` unless a Symfony proxy exists.
Treat JSON parse errors from HTML as route-origin mistakes before treating the product
flow as failed.
**Update (2026-07-07):** `/session/{id}/corrected-transcript` now HAS a same-origin
Symfony proxy (summary UX M4, `src/Controller/ScribeController.php`, search:
"public function correctedTranscript"), so the browser fetches it app-origin; `/history`
remains agent-only.

## Lesson: Correction allocators need ASR-drop replay proof (2026-07-06)

**Created:** 2026-07-06
**Evidence:** `strands_agents/post_visit_correction.py` (search: "append_gap_after_missing_anchor"), `tests/python/test_post_visit_correction.py` (search: "test_build_corrected_segments_preserves_live_row_when_asr_drops_patient_text").

The first anchor-allocation pass fixed opener/empathy rows and passed focused tests, but a
fresh consult-03 browser replay exposed a dropped patient utterance: second-pass ASR omitted
"and I just want you to do something", so the allocator placed the next doctor words into
patient-timed rows and role cleanup labelled them as doctor. Offline rebuild after preserving
missing-anchor live rows improved corrected strict attribution from 90.3% to 96.8%.

**Lesson:** For post-ASR correction, test the failure mode where the second-pass model drops
an utterance that live preview captured. Missing anchors should preserve live text or remain
empty; they must not consume neighboring corrected words just to keep every row populated.

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

## Lesson: pytest owns extra root log handlers; assert on the app's handler only

**Created:** 2026-07-07
**What happened:** While regression-testing the correlation-ID fix (filter moved from the
root logger to the root handler, `strands_agents/api/server.py`, search: "CorrelationIdFilter"),
a test asserted that EVERY `logging.getLogger().handlers` entry carries the filter. It failed:
pytest's logging plugin injects its own capture handlers at the root, and those legitimately
lack app filters.
**Prevention:** Tests about app logging configuration must quantify existentially
("at least one root handler carries the filter") or pin the specific dictConfig handler,
never iterate all root handlers - the set is polluted under pytest. Pair the config assertion
with a behavioral test: log via a child logger through a scratch handler+filter and assert the
JSON output (search in `tests/python/test_observability.py`: "corr-child-123").

## Lesson: Dev-tooling transitives mask undeclared runtime imports

**Created:** 2026-07-07
**What happened:** A post-hardening Codex P1 caught what the local test suite could not:
`strands_agents/api/mercure_publisher.py` (search: "import jwt as pyjwt") minted the
publisher token on the DEFAULT compose path, but `strands_agents/requirements.txt` never
declared PyJWT. Every local test passed because the venv carried PyJWT transitively via
`mcp` dev tooling (`pip show PyJWT` -> `Required-by: mcp`); a fresh container built from
requirements.txt would import-fail, cache an empty token, and silently skip every
raw/role/summary publish.
**Lesson:** When verifying a runtime `import X` claim, checking `import X` in the local
venv proves nothing about the deployed image. Check the declaration chain instead:
`pip show <pkg>` and inspect `Required-by` - if only dev tools require it, treat it as
undeclared. Regression now pins the declaration (`tests/python/test_mercure_failures.py`,
search: "pyjwt_is_declared_in_runtime_requirements").
