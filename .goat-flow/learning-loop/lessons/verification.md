---
category: verification
last_reviewed: 2026-07-16
---

# READ / SCOPE / VERIFY Lessons

## Lesson: When a milestone makes timing user-controlled, re-verify every timer it now races

**Added:** 2026-07-16 · **Trigger:** user-reported regression after M11 acceptance testing

M11 replaced the instant auto-summary with an on-demand button, converting
"time between finalize and summarize" from ~0 ms into an unbounded user decision. The
milestone's RISKY investigation proved MID-VISIT pause tolerance (WS idle, `SESSION_TTL`,
audio-time windows) but nobody re-checked POST-FINALIZE timers — and
`session_lifecycle.py:143` destroys the audio 30 s after finalize, so a user who reads the
transcript before clicking silently loses the corrected note source
(session `61747213`: click +112 s → `audio_expired` → live fallback → all claims
mislabelled Absence-based). The first live verification runs also masked it: agent scripts
and quick demo clicks all summarized within 7 s.

**Why:** an investigation scoped to "does the gap during the visit break anything" does not
cover "what expires after the visit ends"; converting any automatic step to manual changes
the reachable timing envelope on BOTH sides of it.

**How to apply:** when a milestone moves a step from automatic to user-triggered, enumerate
every timeout/TTL/grace between the old trigger point and its new latest-possible time
(`rg "sleep|grace|ttl|expire" strands_agents/`) and test at a delay PAST each one; add the
longest-delay case to the milestone's Manual gate.

**Created:** 2026-07-12
**What happened:** M04's first guarded replay correctly kept a dominant speaker visible, but
reported `keep_sustained_voice` because the diagnostic checked the new acoustic guard before the
older emitted-duration rule. The behavior was safe; the label falsely credited the new policy,
so the replay was stopped at 25 seconds and restarted after precedence was pinned.
**Evidence:**
`var/quality/m04-crosstalk-bleed-20260711T193941Z/phase1-attempt1-label-abort-agent.log`
(search: `"window_index": 5`) and `phase1-green-after-label-fix.log`.
**Prevention:** For policy diagnostics, evaluate and test the ordinary decisive branch before a
fallback/guard branch. Pin one control that already passes the old rule and one specimen whose
outcome changes only because of the new rule before spending a full replay.

## Lesson: A detached eval is not running until its sentinel path advances

**Created:** 2026-07-12
**What happened:** M04 Phase 0's first nested `nohup` launch returned without an active replay or
log progress. No audio ran, but relying on the launch command alone would have left a silent,
incomplete evidence directory.
**Evidence:** `var/quality/m04-crosstalk-bleed-20260711T193941Z/phase0-eval.log` and the later
managed runner's `M04_PHASE0_EVAL_EXIT=0` sentinel show the difference.
**Prevention:** Put the exit sentinel inside an evidence-owned runner, keep long commands in a
managed process/session (or a proven new session), and verify the first fixture line plus live
process state immediately. A successful shell launch is not an eval start signal.

## Lesson: CSS pseudo-content does not preserve copied or accessible text

**Created:** 2026-07-11
**What happened:** M03 split corrected stitched utterances into row-local confidence spans and
used `::before { content: ' ' }` to separate them visually. The full Playwright lane failed
two provenance contracts because DOM text concatenated adjacent rows (`twicedaily`,
`red.Itches`) even though the browser looked spaced correctly.
**Evidence:**
`var/quality/m03-confidence-styling-20260711T100804Z/full-playwright-first-failure.log`
records 46/48 passing and both exact text mismatches.
**Prevention:** When a separator belongs to copied, searched, or screen-reader text, insert a
real text node. Use pseudo-content only for decoration, and keep an assertion over the owning
component's combined DOM text whenever display rows are split into local spans.

## Lesson: Register dynamic modules before executing dataclass definitions

**Created:** 2026-07-11
**What happened:** M03's corpus-mining tool loaded `scripts/transcript-quality.py` through
`importlib`, but executed it before adding the module to `sys.modules`. Python's dataclass
annotation lookup then dereferenced a missing module and the first evidence run failed.
**Evidence:** `var/quality/m03-confidence-styling-phase0-20260711T093609Z/mining-run-first-failure.log`
(search: "AttributeError: 'NoneType' object has no attribute '__dict__'").
**Prevention:** After `module_from_spec`, assign the module under `spec.name` in `sys.modules`
before `exec_module` whenever dynamically loaded code defines dataclasses or resolves annotations.

## Lesson: Re-run formatting after the last regression pin

**Created:** 2026-07-11
**What happened:** M02's late same-row denial regression passed focused and full Python tests,
but the final Ruff format gate still found one test file requiring mechanical formatting.
**Evidence:** `var/quality/m02-fidelity-denial-precision-20260711T083957Z/ruff-format-check.log`
first recorded `Would reformat: tests/python/test_summary_fidelity.py` before the clean rerun.
**Prevention:** Treat formatting as a final-code gate: rerun it after the last test edit, then
rerun affected tests so the formatted file—not the pre-format version—is the verified artifact.
**Follow-up (M03):** A final exception-comment audit again left three otherwise green Python files
needing Ruff formatting. The formatter and all 130 affected summary/fidelity tests were rerun
before the broad suite, confirming the final edited bytes rather than the earlier focused pass.

## Lesson: Verification wrappers must preserve the producer's exit status

**Created:** 2026-07-11
**What happened:** M02's first focused pytest run printed `2 failed, 6 passed`, but a trailing
`sed` made the shell command exit 0. The first clean-campaign resume then crashed after saving
generation 1, while `python ... | tee ...` again returned 0 because pipefail was absent. The
final Playwright monitor also kept waiting after all 43 tests passed because its `pgrep -f`
pattern matched the polling shell itself.
**Evidence:** `var/quality/m02-fidelity-denial-precision-20260711T083957Z/phase1-focused-first-green.log`
(search: "2 failed, 6 passed") and
`var/quality/m02-fidelity-denial-precision-20260711T083957Z/c03-campaign-final-resume.log`
(search: "requests_remaining=4"), and
`var/quality/m02-fidelity-denial-precision-20260711T083957Z/playwright.log`
(search: "43 passed") - literal output and saved responses exposed each masked or stale
wrapper status.
**Prevention:** Capture the producer status before any display command (`status=$?; sed ...;
exit "$status"`), and use `set -o pipefail` whenever `tee` records a test/eval run. Read the
pass/fail line even when the wrapper reports success. Poll a captured PID, or use a bracketed
process pattern such as `[p]laywright test`, so the monitor cannot match its own command line.

## Lesson: A compound-question repro needs the complete bounded exchange

**Created:** 2026-07-11
**What happened:** The first day5 respiratory fixture copied `cold symptoms` onward but
omitted the earlier retained fragment `health, any persistent`. VERIFY correctly stayed red,
but the missing test evidence briefly looked like a checker failure.
**Evidence:** `tests/python/test_summary_fidelity.py` (search:
"DAY5_SPLIT_SCREENING_ROWS") - the fixture now includes the full clinician context before
the patient's short `no`.
**Prevention:** Before changing row-shape logic, compare the unit fixture with the persisted
row IDs and retain the complete locality window. A summary sentence's compound adjective may
live several UI cards before the final question mark. When older fragments complete a
compound topic, require the immediate follow-up question to overlap that same topic first;
otherwise a later `No` can overwrite an earlier affirmative answer.

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

## Lesson: Optional JSON knowledge files need malformed-file coverage (2026-07-04)

**Created:** 2026-07-04
**Evidence:** `strands_agents/clinical_context.py` (search: "except json.JSONDecodeError"), `tests/python/test_clinical_context.py` (search: "test_load_clinical_knowledge_ignores_invalid_json").

During M12, the clinical KB loader handled missing files and invalid shapes but did not handle malformed JSON. A bad PoC knowledge file would have turned an assistive hint/grounding feature into a summary-generation failure for the user.

**Lesson:** For optional local JSON/fixture inputs used by a user-facing path, cover malformed JSON as well as missing files and empty data. Optional assistive data should degrade to no context, not block the primary workflow.

## Lesson: Model stack docs need env, Compose, and code defaults checked together (2026-07-04)

**Created:** 2026-07-04
**Evidence:** `README_STACK.md` (search: "bare Compose falls back to"), `.env.example` (search: "ROLE_AGENT_MODEL_PROVIDER=bedrock"), `strands_agents/agents/transcription_agent.py` (search: "ROLE_AGENT_MODEL_PROVIDER").

While creating the stack inventory, the root README still described Bedrock as the role-inference default, `.env.example` described Ollama as the local default, Compose passed Ollama by default, and the Python agent retained Bedrock defaults for missing env vars. Reading only one source would have produced another stale model summary.

**Lesson:** For docs that name model providers or IDs, verify `.env.example`, `docker-compose.yml`, agent factory code, and any existing README before writing the final wording. Call out intentional fallback differences instead of flattening them into one default.

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
**Follow-up (same session):** the root enabler was that `tests/python/requirements-dev.txt`
never chained the runtime manifest, so the documented venv setup could not even import
fastapi and every runtime dep in the working venv was installation history. It now includes
`-r ../../strands_agents/requirements.txt` (proven by building a throwaway venv from the
single documented command), and the publisher imports pyjwt at module load so a broken
image fails at startup instead of silently skipping publishes.

## Lesson: "Peek instead of create" needs a liveness signal when the call is a write (2026-07-07)

Replacing `get_or_create_state` with `peek_state` in the speaker-scope role override
(0.4.0-slice-1 M04) silently broke a live-visit contract: a clinician can click a speaker label BEFORE the
role worker has created any state, and peek-only meant that early override never became a
confirmed override - the next agent update could undo the clinician. An existing regression
(`tests/python/test_api.py`, search: "survives_later_agent_update") caught it immediately.
The fix gates on lifecycle (`is_active` or `has_pending_destroy` -> create; finished visit ->
peek). The row-scope path never had this problem because row corrections persist in
transcript storage, not role state. When removing a state-creation side effect, first
enumerate who legitimately relies on the creation: "reads must not create" is right for
reads (`roles_snapshot`), but an override is a write.

## Lesson: Environment-variable checks are not device-liveness checks (2026-07-08)

The M06 phase-2 gate followed the standing rule "verify the RUNNING container env, not
compose defaults" - `NEMO_SESSION_ENGINE=streaming` and `MEDICAL_BOOST_ENABLED=1` were
confirmed in the container before the eval - and the eval STILL ran on the wrong hardware:
WSL had silently dropped the GPU adapter, a hot-reload had loaded both models on CPU, and
env vars said nothing about it. The false byte-identity failure cost two attribution evals
and one control run to unwind (footguns/runtime.md, search: "silently move NeMo to CPU").
Env inspection proves CONFIGURATION; it never proves the RESOURCE is attached. Before a
baseline-gated run, also assert the resource itself: `torch.cuda.is_available()` in the
serving container for GPU gates, and generally one probe of the physical dependency any
byte-identity claim rides on. Attribution order when a gate diff appears: (1) device parity
with the baseline, (2) same-code same-device rerun for run-to-run stability, (3) only then
suspect the code.

## Lesson: Styling-carried UI state does not survive text paste - read fidelity and correction outcomes from logs

**Created:** 2026-07-09
**What happened:** During the 2026-07-08 (UTC) manual round, a pasted c03 note showed no
"Unverified against transcript" markers and the review concluded "zero flags fired". The agent
log showed `summary.fidelity_flagged flagged=3` for that exact session - the amber marker is
styling, and a text copy strips it. The same analysis initially reported "no errors in the test
window" because the sweep grepped `ERROR|Traceback|CUDA error`; the evening's only real failure
was logged at WARNING as `correction.unavailable ... CUDA driver error: device not ready`, which
none of those patterns match. Both wrong claims were corrected only by reading the logs.
**Prevention:** Pasted UI text is evidence of CONTENT, never of styling-carried state (fidelity
flags, confidence tinting, badges) and never of absence. Before claiming anything about a
session's fidelity or correction outcome, grep the agent log for `summary.fidelity_` and
`correction.` with the session id. Error sweeps over this stack must match loosely
(`-i "unavailable|failed|cuda"`), not exact phrases or level names.

## Lesson: Field-session fixture replays must preserve the original stop time

**Created:** 2026-07-10
**What happened:** M11 needed fresh rows for a field note captured 228 seconds into a
510-second day3 WAV. The first recapture started the full fixture at browser cadence, which
would have mixed later consultation facts into a replay meant to reproduce the cut-off note;
it was stopped only after the WAV duration and field cutoff were compared. M05 repeated the
cutoff error through headless UI timing: a 132.8-second target reached 215.6 seconds while tool
polls lagged the faster audio clock, and the first stale control click hit hidden microphone Start
instead of replay Stop. The eventual Stop also triggered one automatic 6,401-token summary.
**Evidence:** `.goat-flow/plans/0.4.0-slice-1/M11-note-phrasing-fidelity.md` (search: "initial
uncapped replay") and
`var/quality/m05-dual-identity-duplicates-20260712T060345Z/instrumented-browser-20260712T081246Z/mechanism-verdict.md`.
**Prevention:** Before a real-time fixture recapture, record both the WAV duration and the
field session's stop time. Pass that stop time explicitly with `--seconds` and verify the
saved row duration before using the artifact for prompt or fidelity acceptance. For UI-driven
replays, poll the audio timer at one-second cadence near cutoff and use Escape rather than a stale
element index. Count the Stop flow's automatic correction/summary generation in approval and cost.

## Lesson: Long evals must capture rotating service logs during the run

**Created:** 2026-07-11
**What happened:** The first 20-fixture full-corpus sweep waited until the end to inspect Docker
logs, but rotation had already discarded 13 of 15 early `correction.completed` lines. The
operator's live ledger was the only surviving duration/chunk evidence for those fixtures.
**Evidence:** `var/quality/full-corpus-20260710T2328Z/run-manifest.md` (search:
"Correction-health ledger") records the rotation loss and the operator-captured replacement.
**Prevention:** For long detached evals, append `correction.completed`,
`correction.unavailable`, and milestone instrumentation lines to the run directory while each
fixture executes. Do not treat an end-of-run `docker compose logs` read as durable evidence.

## Lesson: Source-based shell smokes need the main guard before the first run

**Created:** 2026-07-11
**What happened:** M01's first GPU-free corpus smoke sourced the existing eval runner before a
library guard existed, so sourcing immediately entered the real fixture main path. The run was
terminated, health/CUDA/error checks stayed clean, and the smoke was rerun only after the guard.
**Evidence:** `scripts/eval-corrected-fixtures.sh` (search: "fixture_summary_line") and
`tests/eval-corrected-fixtures-smoke.sh` (search: "append_failed_report_row") pin the
corrected order.
**Prevention:** When a shell smoke will source an executable runner, land and syntax-check the
`BASH_SOURCE[0] == $0` main guard before the first source attempt; then add the source-based red
assertion. Never assume an executable script is already library-safe.

## Lesson: Guard both ends of long-running work, not just its entry

**Created:** 2026-07-15
**What happened:** M02's stale-role gate checked `_closed_role_revisions` only at the TOP of
`_infer_and_publish_role_update`. A role inference already inside the executor when settlement
froze the visit (`failed_frozen`) still applied its mapping and published after closure — the
exact kill-criterion behavior ("a role result silently changes an already rendered/copied
artifact"). The automated suite passed because its late-result test started the worker AFTER
closure (hits the entry gate); nothing covered in-flight-at-closure. The gap was found while
designing the acceptance round's slow-provider injection, before running it.
**Evidence:** `strands_agents/api/role_inference_queue.py` (search: "visit can freeze while")
and `tests/python/test_terminal_source_integrity.py` (search: "in_flight_at_close") — the test
was red against commit `890c7e1`, green after the post-executor recheck.
**Prevention:** Any revoke/close/freeze flag raced by long-running work (executor calls, provider
awaits) must be rechecked AFTER the work returns, immediately before applying results — an entry
gate alone only rejects work that has not started. When writing the "late result rejected" test,
always add the sibling case where the result is already in flight when the door closes.

## Lesson: Build acceptance specimens from real artifacts before the human gate

**Created:** 2026-07-15
**What happened:** M03's `Review required (<n>)` badge counted reason CATEGORIES, and the unit
test agreed with the implementation ("(2)") because its expectation was written from the code.
Generating the acceptance specimens from the REAL consult-1.2 note (M02 replay artifacts run
through the production serializer) rendered "(2)" directly above a breakdown listing 1 + 3
flagged items - a contradiction no synthetic fixture had encoded.
**Evidence:** `var/quality/m03-copy-specimens-20260715T/` (search: "Review required") and
`M03-design-proposal.md` amendment 4; fix in `public/js/scribe-copy.js` (search:
"flaggedItemCount").
**Prevention:** Before any human acceptance gate, run the shipped code over real captured
artifacts and read the output as the reviewer would. Synthetic fixtures inherit the author's
assumptions; real data contradicts them. Writing a unit expectation by observing the
implementation's current output is recording, not testing.

## Lesson: An adjacent answer does not prove which question it resolves

**Created:** 2026-07-16
**What happened:** While reviewing consult 5.3, the explicit doctor question "Have you ever
had a panic attack?" followed by the patient's "No I wouldn't say so" was initially treated
as a proven negative screen. Time-aligned cross-examination showed that the doctor had first
asked whether anxiety had ever prevented the patient entering the office, the panic question
overlapped only an unintelligible patient filler, and the later answer continued "I've always
managed to make it to work." Its semantics therefore fit the earlier functional question more
directly; at most, the panic response is ambiguous.
**Evidence:**
`tests/fixtures/audio/primock57-day5-consultation03-im-feeling-very-anxious.doctor.TextGrid`
(search: `Have you ever <UNSURE>had a</UNSURE> panic attack?`) and
`tests/fixtures/audio/primock57-day5-consultation03-im-feeling-very-anxious.patient.TextGrid`
(search: `No I wouldn't say so, I think, I've always managed to make it to work.`).
**Prevention:** Align answers using timing, overlap, and semantic fit across the complete
bounded exchange. When consecutive questions compete for one delayed response, record
"asked; response ambiguous" unless the answer directly identifies the topic; adjacency alone
must not become a positive or negative clinical fact.
