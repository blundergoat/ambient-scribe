---
category: verification
last_reviewed: 2026-07-06
---

# READ / SCOPE / VERIFY Lessons

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

## Lesson: Semantic anchors should prefer function names over escaped route strings (2026-07-04)

`./scripts/context-validate.sh` rejected a generated footgun citation that used an escaped decorator string for the WebSocket route in `strands_agents/api/server.py`. The route existed, but the checker did not accept the escaped quote form.

**Lesson:** For learning-loop citations, prefer stable function-name anchors such as `(search: "async def transcribe_stream")` over quoted decorator or route literals that require escaping.

## Lesson: Goat-flow installed skill edits can drift from package templates (2026-07-04)

Updating installed goat-plan skill copies under `.agents/skills/`, `.claude/skills/`, and `.github/skills/` removed stale project path text, but `goat-flow audit` still compares those files to the package template in `node_modules/@blundergoat/goat-flow/workflow/skills/goat-plan/SKILL.md`.

**Lesson:** When changing installed goat-flow skill text for project policy, run `goat-flow audit` and either accept/report template drift or make the change upstream in the package before claiming the audit is clean.

## Lesson: Gruff context docs need marker vocabulary (2026-07-04)

During M05, comments clearly described user-visible error handling but still failed `docs.missing-error-behavior-doc` because gruff's context-doc rule looks for marker words such as `reports`, `fallback`, `recover`, or `throws`.

**Lesson:** When fixing gruff context-doc findings, read the rule vocabulary and include the expected marker word in plain English instead of relying on semantically similar prose.

## Lesson: Gruff env placeholders are exact-token sensitive (2026-07-04)

**Created:** 2026-07-04
**Evidence:** `.env.example` (search: "APP_SECRET=changeme"), `.env.example` (search: "MERCURE_JWT_SECRET=changemechangemechangemechangeme"), `strands_agents/.venv/lib/python3.12/site-packages/gruffpy/rule/sensitive_data/hardcoded_env_value_rule.py` (search: "_PLACEHOLDER_VALUES").

During M06, intuitive placeholders such as `<generate-app-secret>` and `allowlists.secretPreviews` still left `sensitive-data.hardcoded-env-value` findings. Reading the rule showed the env-secret detector only skips exact placeholder tokens or low-entropy values, and the high-entropy detector in gruff-py 0.4.1 does not consult `secretPreviews`.

**Lesson:** When gruff-py flags `.env.example`, read the sensitive-data rule before tuning config; prefer exact known placeholders such as `changeme` or low-entropy repeated local placeholders, then rerun JSON output to prove the warning disappeared.

## Lesson: Gruff PHP display filters do not lower the exit threshold (2026-07-04)

**Created:** 2026-07-04
**Evidence:** `composer.json` (search: "vendor/bin/gruff-php analyse"), `.goat-flow/plans/0.3.0/M07-fix-gruff-php-findings.md` (search: "rewired").

During M07, a complexity-only gruff-php command was initially considered for the retired cyclomatic alias. The command still failed while unrelated advisory findings existed, because gruff-php's report selection changes displayed findings but the configured `minimumSeverity.analyse` threshold still controls the process exit.

**Lesson:** When replacing a legacy quality gate with gruff-php, use the full `gruff-php analyse` command unless the tool documentation explicitly says a selector changes exit semantics; prove the alias with a failing and then clean run before marking the plan checkbox complete.

## Lesson: Generic Gruff baseline filenames collide across tool lanes (2026-07-05)

**Created:** 2026-07-05
**Evidence:** `gruff-php-baseline.json` (search: "gruff.baseline.v2"), `composer.json` (search: "--baseline=gruff-php-baseline.json"), `scripts/preflight-checks.sh` (search: "--baseline=gruff-php-baseline.json").

During M14 review, a PHP accepted-debt baseline was written as `gruff-baseline.json`. `gruff-py` also auto-loads that filename, rejected the PHP `gruff.baseline.v2` schema, and exited with a baseline error even though the Python findings were clean.

**Lesson:** When multiple Gruff implementations share a repo, do not put implementation-specific accepted debt in the generic `gruff-baseline.json`; use tool-specific baseline filenames and pass them explicitly in that tool's Composer/script gate.

## Lesson: Validation wrappers must check exit codes before success text (2026-07-05)

**Created:** 2026-07-05
**Evidence:** `scripts/preflight-checks.sh` (search: "composer_validate_exit"), `scripts/validate-composer.sh` (search: "ALLOWED_STRANDS_CLIENT_WARNING").

During M14 review, direct `composer validate --strict` exited 1 for the intentionally commit-pinned Strands PHP client, but preflight had been grepping for "is valid" and therefore reported the step green despite the non-zero exit. The fix moved the exception into a wrapper that checks the exit code and allows only the reviewed warning.

**Lesson:** Validation steps should key off the command exit code first; if one warning is intentionally accepted, encode that exact exception in a wrapper instead of grepping for success text in mixed success/warning output.

## Lesson: SDK observability plans must match installed vendor contracts (2026-07-04)

**Created:** 2026-07-04
**Evidence:** `.goat-flow/plans/0.3.0/M08-observability-and-eval.md` (search: "ResponseObserver"), `vendor/blundergoat/strands-php-client/src/Http/RequestMiddleware.php` (search: "interface RequestMiddleware"), `src/Observability/StrandsClientTelemetry.php` (search: "implements RequestMiddleware").

During M08, the plan described PHP client 1.5.x `ResponseObserver` hooks, but the installed 1.4.0 client only exposes `RequestMiddleware` with `beforeRequest()` and `afterResponse()`. Implementing from the plan text alone would have created a class against an absent interface.

**Lesson:** Before implementing SDK instrumentation from a plan, verify the installed vendor interface and lockfile version, then update the plan with the actual contract used.

## Lesson: Goat-flow setup-green can still hide cross-agent drift (2026-07-05)

**Created:** 2026-07-05
**Evidence:** `.claude/skills/goat/SKILL.md` (search: "goat-flow-skill-version"), `.github/skills/goat/SKILL.md` (search: "goat-flow-skill-version"), `.github/hooks/hooks.json` (search: "\"postToolUse\"").

During a Codex goat-flow 1.13.1 repair, `goat-flow setup . --agent codex` reported `0 audit checks failed` after codex config, hooks, and skills were synced. The exact requested `goat-flow audit . --harness --agent codex` still exited non-zero because the audit drift section also compared installed `.claude/skills/`, `.github/skills/`, and `.github/hooks/hooks.json` copies against package templates.

**Lesson:** When the exact audit command is the acceptance gate, trust the audit exit code and its top-level `drift.status`, not only the setup prompt's numbered checks. If drift remains, sync every named installed agent copy before declaring the audit clean.

## Lesson: Logging-only edits can still break hot role logic (2026-07-05)

**Created:** 2026-07-05
**Evidence:** `strands_agents/tools/assign_roles.py` (search: "previous_mapping = self.current_mapping"), `tests/python/test_role_inference.py` (search: "test_detects_full_speaker_flip").

During M18 Phase 0, a plain-log message was added to `RoleMappingState.did_update_mapping_detect_flip()` using `changed_speakers`, but that variable existed only inside `_is_role_label_flip()`. The focused observability tests passed, while the full Python suite caught six role-flip failures with `NameError`.

**Lesson:** Treat observability-only patches as executable code in the hot path. When a log line needs derived values, compute them locally before mutating state and rerun the domain tests for that path, not only the observability tests.

## Lesson: Eval history fetch must wait out post-disconnect role churn (2026-07-05)

**Created:** 2026-07-05
**Evidence:** `scripts/eval-fixtures.sh` (search: "Fetching earlier made attribution depend on a fetch-vs-flip race"), `.goat-flow/plans/0.3.0/M20-improve-doctor-patient-detection.md` (search: "fetch-vs-flip race").

During M20 Phase 1, three c03 @83s runs after a publish-only payload change all scored
45.0% strict attribution against a 55.0% Phase 0 median, which looked like the kill
criterion firing on the new code. The run-invariant diagnostics (dyadic ceiling, window
counts, remaps) were identical, and two runs with byte-identical role-decision timelines
had scored 45% vs 55% across phases - one Phase 0 history even contradicted its own
timeline's final mapping. The real cause: the role queue keeps accepting tail-batch
flips for seconds after WebSocket disconnect, and `fetch_history` ran before the settle
sleep, so the scored labels depended on a fetch-vs-flip race, not on the change under
test. Reordering the eval to fetch history after the role-timeline settle made the gate
deterministic (55.0/55.0/55.0). Churn can still straddle any fixed settle window, so
median-of-3 remains mandatory.

**Lesson:** when a gate metric moves right after a change that cannot mechanically
affect it, first check the gate's own sampling timing against asynchronous state
updates before blaming the change. Compare run-invariant diagnostics and look for
artifacts that contradict each other within one run (here: history role labels vs the
role timeline's final mapping) - a self-contradicting run proves a measurement race.

## Lesson: Free-assignment oracle metrics overstate reachable accuracy - constrain the assignment (2026-07-05)

**Created:** 2026-07-05
**Evidence:** `scripts/transcript-quality.py` (search: "def score_best_dyadic_mapping"), `.goat-flow/plans/0.3.0/M16-diarization-stability-role-confidence.md` (search: "Deep diagnosis (2026-07-05, second pass").

The M16 "speaker oracle accuracy" gave each emitted speaker ID its majority reference role
independently, so on fixtures where diarization mixed one voice across BOTH IDs the oracle
assigned DOCTOR to both (c03/c06/c08) or PATIENT to both (c07) - mappings no real
one-DOCTOR/one-PATIENT product can ship. The derived "role mapping gap" (+35.3pp on c07)
was read as role-mapping headroom, when the best VALID dyadic mapping could only recover
+13.7pp; the rest was diarization purity loss wearing a role-mapping costume.

**Lesson:** when an oracle/ceiling metric drives a which-layer-is-at-fault decision,
constrain the oracle to assignments the product can actually make (here: a role
bijection for dyads) and report both numbers. An unconstrained per-ID oracle is an upper
bound on a different system than the one being tuned.

## Lesson: Prompt-only role establishment hints need median replay proof (2026-07-05)

**Created:** 2026-07-05
**Evidence:** `strands_agents/api/role_agent_runtime.py` (search: "establishment_hint_guard"), `strands_agents/api/role_inference_queue.py` (search: "establishment_hint").

During M20 Phase 4, removing the role agent's `current_mapping` echo and adding cue-rich
representative rows looked mechanically correct, but c03 @83s immediately scored 45/45
strict on two runs. Adding opener cue counts and a prompt-level `establishment_hint` still
scored 50/50 on two of three runs because the model accepted the exact inverse mapping
from later seam-mixed rows.

**Lesson:** For sampled role-establishment work, do not accept prompt/evidence wording from
static tests or one replay. Use median-of-3 on the contested fixture, inspect the role
timeline when a run regresses, and make high-precision establishment evidence enforceable
when it is meant to prevent an exact dyadic inversion.

## Lesson: Region WER buckets need word-level allocation, not whole-interval exclusion (2026-07-05)

**Created:** 2026-07-05
**Evidence:** `scripts/transcript-quality.py` (search: "def timed_words_for_region"), `.goat-flow/plans/0.3.0/M17-transcript-accuracy-and-readability.md` (search: "Phase 0 baseline table").

During M17 Phase 0, the first clean-vs-overlap WER split assigned an entire TextGrid
interval or transcript row to the overlap bucket if it touched cross-talk at all. A full
fixture run made c07 clean WER print as `298.3%`: long reference intervals that barely
touched overlap were removed from the clean denominator while nearby transcript rows stayed
clean. The metric would have made later readability/transcription changes look worse or
better for bucket-boundary reasons instead of real word accuracy.

**Lesson:** For WER or other word-count metrics split by time regions, allocate words by
word timestamps when available, or by an explicit approximation such as token-center time.
Do not reuse segment-level "touches overlap" exclusion unless the numerator and denominator
are guaranteed to be bucketed the same way.

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

## Lesson: Log-derived timelines must count state changes, not echoed decisions (2026-07-05)

**Created:** 2026-07-05
**Evidence:** `scripts/role-timeline.py` (search: "Count the state-change log only"), `tests/python/test_role_timeline.py` (search: "role_mapping.flip_detected speakers=speaker_0,speaker_1").

During M16 diagnostics, the first role-timeline summary counted accepted flips whenever a
timeline row had `decision == "accepted_flip"`. A single visible flip appears twice in the
logs: once as the state-change row (`role_mapping.flip_detected`) and once as the downstream
published role-call row (`role_inference.completed` with `flip_detected: true`). The artifact
therefore doubled accepted-flip counts until the eval-run summaries were compared with
`session.quality`.

**Lesson:** For log-derived counters, identify the ownership event that mutates the state and
count only that event. Downstream publish/completed logs can repeat the decision for context,
but they should not increment the same summary counter unless the owning event is absent and
the fallback is documented in code and tests.

## Lesson: Original fixture metrics outrank plausible seam fixes (2026-07-05)

**Created:** 2026-07-05
**Evidence:** `scripts/eval-fixtures.sh` (search: "scripts/transcript-quality.py"), `scripts/transcript-quality.py` (search: "best dyadic mapping accuracy"), `tests/python/test_nemo_session.py` (search: "test_window_speaker_ids_follow_the_anchor").

A conservative speaker-anchor change for consultation-03 passed the focused
`tests/python/test_nemo_session.py` suite and matched the local theory that tiny context-tail
overlaps can trigger bad whole-visit speaker swaps. The original fixture replay rejected it:
consultation-03 at 83 seconds fell to 45.0% non-overlap attribution, while the reverted
runtime scored 55.0%. The unit test proved only one synthetic seam behavior, not the full
doctor/patient transcript users see.

**Lesson:** For transcription-quality work, keep the original fixture replay as the
acceptance gate. A mechanism that passes unit tests but worsens `scripts/transcript-quality.py`
on the reported fixture must be reverted or marked diagnostic-only, even when the hypothesis
still sounds mechanically plausible.

## Lesson: Held-tail speaker anchors need replay proof before adoption (2026-07-05)

**Created:** 2026-07-05
**Evidence:** `strands_agents/nemo_session.py` (search: "_overlap_speaker_map"), `.goat-flow/plans/0.3.0/M20-improve-doctor-patient-detection.md` (search: "held-tail anchor voting").

During M20 Phase 5, the per-window artifacts made held-tail speaker anchoring look like the
small seam mechanism M16 had left open: c03 @83s had 18 held rows and wrong rows clustered at
window starts, so using the prior window's canonicalized held rows as extra overlap-vote
references seemed safer than widening the NeMo/ASR window. The focused unit test passed and
the mechanism changed no audio slicing, no GPU model, and no timestamp mode. Runtime replay
rejected it anyway: c03 @83s strict attribution fell from the accepted 70.0 median to
65.0/65.0/65.0, with the same 18 windows, 18 remaps, 6 merges, 5 confidently wrong rows, and
2 uncertain rows each run. The patch was reverted and the restore smoke returned to 70.0.

**Lesson:** Treat held-tail or un-emitted transcript rows as an unproven speaker-identity
source, not a free continuity improvement. Even when a seam mechanism does not widen GPU
audio and passes a synthetic anchor test, run the reported fixture median before keeping it;
if it lowers strict attribution, revert rather than tuning a second seam rule in the same
phase.

## Lesson: Dataclass script imports need sys.modules registration (2026-07-04)

**Created:** 2026-07-04
**Evidence:** `scripts/analyze-logs.py` (search: "class ProcessQualityStats"), `tests/python/test_observability.py` (search: "Dataclasses resolve postponed annotations through sys.modules during script import").

Full pytest caught that the test helper loaded `scripts/analyze-logs.py` with `importlib.util.module_from_spec()` but did not register it in `sys.modules` before executing the module. Python dataclasses resolving postponed annotations then failed during import.

**Lesson:** When test-loading a hyphenated Python script that defines dataclasses or postponed annotations, insert the module into `sys.modules` before `spec.loader.exec_module(module)`, then rerun the full test gate that found the issue.

## Lesson: GPU image import gates need a local/pending split (2026-07-04)

**Created:** 2026-07-04
**Evidence:** `.goat-flow/plans/0.3.0/M09-dependency-upgrades.md` (search: "Phase-0 import one-liner"), `docker/nemo/Dockerfile` (search: "nvcr.io/nvidia/nemo:26.02").

During M09, the plan required a `docker run nvcr.io/nvidia/nemo:26.02 ...` import check before dependency edits. The image pull is multi-GB and was stopped locally, so claiming the import passed would have been false while blocking all GPU-free package and contract checks would have stalled useful work.

**Lesson:** For heavyweight GPU images, split verification into local manifest/package-manager gates and an explicit GPU-host build/import gate. Mark the GPU gate human-pending unless the container actually builds and imports in the current session.

## Lesson: Name GPU-pending fallbacks as fallbacks (2026-07-04)

**Created:** 2026-07-04
**Evidence:** `.goat-flow/plans/0.3.0/M11-medical-phrase-boosting.md` (search: "decode-time GPU spike pending"), `strands_agents/medical_lexicon.py` (search: "normaliser for common clinical terms").

During M11, the plan targeted NeMo decode-time phrase boosting, but the exact multitalker transducer API still needs a pinned-container GPU spike. Shipping the useful local path as "phrase boosting" without naming that distinction would make reviewers think the GPU decoding contract had been proven.

**Lesson:** When a plan's ideal implementation depends on unrun GPU/provider proof, label the shipped local path as a fallback in code, docs, changelog, and plan status. Leave the original proof checkbox unchecked and add a focused unit test for the fallback's real contract.

## Lesson: Hidden sidebars need layout-state smoke tests (2026-07-04)

**Created:** 2026-07-04
**Evidence:** `templates/scribe/index.html.twig` (search: "consultation-workspace:has(.clinical-hints:not(.hidden))"), `public/js/scribe-output.js` (search: "function renderClinicalHints").

During M12, the first clinical-hints UI pass hid the sidebar element but left a dedicated desktop grid column in the base layout. Static analyzers and API tests stayed green, but the clinician page would have opened with blank right-side space until hints arrived.

**Lesson:** When adding a hidden/dismissible panel that changes page columns, verify both empty and populated layout states with a DOM or browser smoke test. The hidden state must remove reserved layout space, not only hide panel contents.

## Lesson: Optional JSON knowledge files need malformed-file coverage (2026-07-04)

**Created:** 2026-07-04
**Evidence:** `strands_agents/clinical_hints.py` (search: "except json.JSONDecodeError"), `tests/python/test_clinical_hints.py` (search: "test_load_clinical_knowledge_ignores_invalid_json").

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

## Lesson: Eval-generated session IDs must use route-valid UUIDs and avoid retry collisions (2026-07-05)

**Created:** 2026-07-05
**Evidence:** `scripts/eval-channel-ceiling.py` (search: "def channel_session_id"), `strands_agents/api/server.py` (search: "_validate_session_id").

During M17 channel-ceiling work, the first eval script posted human-readable session IDs such as `primock57-...-doctor` to `/transcribe/file`; FastAPI rejected them with `400 Bad Request` because the route validates caller-supplied session IDs. The next fix made deterministic UUIDs from fixture/role, but retries reused active in-memory session state during the reconnect grace window.

**Lesson:** Eval tooling that creates server sessions must either omit session IDs and capture the generated one, or generate valid UUIDs with a run-specific salt. After changing session identity behavior, run the server path that validates the ID rather than only testing local helper formatting.

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
