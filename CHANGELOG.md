# Changelog

All notable changes to Ambient Scribe are documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- **Session-long streaming transcription engine (M22, experimental, flag-off)** - a new `NEMO_SESSION_ENGINE=streaming` engine wraps NVIDIA's SpeakerTaggedASR composite so one Sortformer speaker cache owns speaker identity for the whole visit, replacing per-window re-diarization and stitching (the mechanism behind mid-visit Doctor/Patient inversions). Live integrated result on the consult-03 fixture at full length: strict attribution 85.5% vs 53.0% windowed, incorrect-confident rows 14.5% vs 44.7%, duplication 0.6% vs 2.4%, zero errors. Default stays `windowed` (verified byte-identical to the M20 gate) until the M22 Phase 3 acceptance gates pass; rollback is an env flip plus agent restart.
- **Stop-time finalize drain for live recordings (M21)** - pressing Stop on a live-microphone visit now mirrors the demo-replay drain: the WebSocket closes (triggering the server's final NeMo pass) while the Mercure stream stays open until the backend `finalized` event arrives (15s bounded timeout), so the held-back tail utterances render and the summary is generated from the complete transcript. Pre-fix, stop tore the stream down immediately and the finalize flush published to nobody (reproduced: 8 browser rows vs 11 server rows); post-fix the same procedure shows 11/11. New Session still tears down immediately. The dev panel State tab shows `segmentsReceivedVsStored` so delivery gaps are visible at a glance.
- **Post-finalize role-churn artifact (M21)** - role flips landing after the `session.quality` record closes (the tail settle window) are now reported: `role_inference.completed` logs carry a `post_finalize` flag, and when tail churn actually happened the role worker persists one additive `quality_tail` JSONL row (`tail_role_flips_accepted`/`suppressed` plus final counters) beside the session's quality record. Existing `session.quality` fields and cardinality are unchanged.
- **Per-row speaker correction (M20 Phase 2)** - clinicians can now click any transcript line to correct who said exactly that line (Doctor -> Patient -> Unknown), without relabeling the speaker's other rows. Every emitted row carries a stable server-minted `segment_id`; corrections are stored row-scoped (separate from speaker-level confirmed overrides), survive later automatic role updates, the finalize history rebuild, and summary generation, and sync to other open tabs via a backwards-compatible `row_overrides` field on the roles topic. Corrected rows show a small "Dr/Pt ✓" chip. This is an explicit correction feature for rows where mixed audio makes automatic attribution wrong - marking a row Unknown is a valid answer and counts as uncertain in quality metrics.

### Changed

- **Summary requests merge instead of replacing history (M21)** - a summary POST can no longer shrink the server-stored transcript: browser rows are merged by `segment_id` (roles update only rows no correction or automatic exception owns), rows the browser missed or filtered stay stored and still reach the note, rows the server never emitted are skipped, and an empty store falls back to the old restore-from-browser behavior for reconnects. Blank-text rows remain excluded from the note text at read time.
- **Finalize flush window logging (M21)** - `nemo_session.window_continuity` console lines now include the existing `phase` field (`chunk`/`finalize`), so the finalize flush re-logging the last window index is distinguishable outside JSON mode.
- **Rejected held-tail speaker-anchor spike (M20 Phase 5)** - tested the one seam mechanism
  exposed by the Phase 0 window artifacts: using the prior window's canonicalized held rows
  as extra overlap-vote evidence without widening NeMo audio, enabling timestamps, or adding
  a GPU model. The mechanism passed focused unit checks but failed the c03 @83s median gate
  (65.0/65.0/65.0 strict vs the accepted 70.0), so it was reverted; a restore smoke returned
  c03 @83s strict attribution to 70.0. No Phase 5 speaker-identity runtime change remains.
- **Role-agent establishment hardening (M20 Phase 4)** - role inference no longer sends the current automatic mapping or mapping history back into the Strands prompt, so an early wrong UI label cannot anchor later decisions. Bounded role evidence now refreshes representative utterances from cue-rich rows, includes opener-derived doctor/patient cue counts and first-seen position, and caps each evidence row at 120 chars. High-precision clinician self-introduction / consultation-opener cues produce an `establishment_hint`; if the model returns the exact two-speaker inverse and no clinician override exists, the server keeps the opener-derived mapping and logs `role_inference.establishment_hint_guard`. Final gates: c03 @83s strict 70.0 across 3/3 runs, c02 strict 82.4 across 3/3 runs, full-corpus strict avg 61.6 with zero truncations.
- **Automatic row-level role exceptions (M20 Phase 3)** - after every speaker-mapping update, a CPU-only cue lane re-judges each identified transcript row against cheap, explainable wording cues (clinician questions, second-person body references, first-person symptom reports) and either relabels a row that contradicts its speaker's mapped role or marks it explicitly uncertain; blended question+answer rows and quoted/echoed symptom wording go uncertain rather than confidently wrong. Exceptions ride the roles topic as an additive `row_exceptions` field, render as tentative dashed chips ("Dr auto", "?") the clinician can override with one click, never touch user-corrected rows, and count uncertain rows as incorrect in strict attribution so uncertainty cannot inflate quality numbers. Cue thresholds were measured on the full baseline corpus (zero wrong flips); no LLM involvement, `max_tokens truncation` stays untouched by construction.
- **Eval trend report strict columns (M20)** - `scripts/eval-fixtures.sh` per-run and `--report` tables now lead with strict attribution (+delta), uncertainty coverage, incorrect-confident rate, best valid dyadic ceiling, and the diagnostic free oracle, replacing the delta-heavy legacy layout.
- **Row-preserving summary input (M20 Phase 2)** - the browser's summary request now sends one record per transcript row (with `segment_id` and the row-resolved role) instead of per coalesced same-speaker card, so summarising no longer replaces server history with row-losing aggregates; the summary transcript is rebuilt from the server-corrected rows so a stale client can never feed the note an uncorrected role.
- **Honest role-confidence badge (M20 Phase 1)** - role updates now carry a backwards-compatible `role_stability` field computed live from speaker-identity counters (anchor remap rate, phantom merges, pending contrary mapping), and the browser badge only shows green "Roles identified" when mapping confidence is high AND speaker identity stayed stable; a confident mapping over churning identities renders as amber "Roles assigned - verify labels" with a correction hint, so the consult-03 failure mode (90% badge over inverted rows) can no longer render as identified. The dev panel State tab shows the same `roleStability` object.
- **Eval history fetch race fix (M20)** - `scripts/eval-fixtures.sh` now fetches session history after the role-timeline settle window instead of before it, so scored attribution reflects the settled labels a clinician sees rather than a race against post-disconnect role flips (identical role-decision timelines previously scored 45% or 55% depending on fetch timing).
- **Strict doctor/patient attribution metrics (M20)** - `scripts/transcript-quality.py` now reports strict clean attribution (uncertain/UNKNOWN clean rows stay in the denominator as incorrect), labeled-row accuracy, uncertainty coverage, and incorrect-confident-row rate alongside the existing visible attribution and best-valid-dyadic ceiling, and marks the per-ID speaker oracle as free/diagnostic-only in its output. Hiding hard rows behind uncertainty can no longer raise the headline detection number.
- **Per-window speaker-continuity diagnostics (M20)** - live NeMo sessions log one `nemo_session.window_continuity` record per emission window (raw vs canonical speaker IDs, overlap-vote evidence, mapping reasons, remap/phantom-merge counts, emitted spans - never transcript text), and `scripts/eval-fixtures.sh` saves them per fixture as `window-continuity.jsonl` via the new `scripts/window-continuity.py`. Requires `LOG_FORMAT=json` on the agent container; the eval runner warns when no windows are captured.
- **Row-level attribution diagnostics (M20)** - `scripts/transcript-quality.py --row-diagnostics-json` writes a per-row artifact (expected vs visible role, overlap flag, confidently-wrong flag, best-valid-mapping fixability, and window/seam joins against the window artifact) so one wrong Doctor/Patient card can be traced without rescoring; the eval runner stores it as `row-diagnostics.json` beside each fixture's history.
- **Medical term correction safety** - the post-ASR fallback now has a reviewer/eval sidecar and CPU-only evaluator, preserves sentence-initial capitalization, tolerates missing/unreadable lexicon files, and disables risky prior variants (`heart attack`, `thyroid function tests`, `listen april`) unless reviewed.
- **Medical boost evaluator coverage** - the CPU-only medical boost evaluator now fails when active lexicon rows lack reviewer provenance/rationale or when the review table drifts from the active runtime lexicon.
- **Gruff PHP accepted-debt baseline** - added a PHP-specific baseline entry and Composer validation wrapper for the requested `blundergoat/strands-php-client` `dev-dev#98bd6598...` constraint so preflight stays green while the project deliberately tests that unreleased client branch without breaking `gruff-py`'s default baseline loader.
- **Transcript fragment readability** - live NeMo sessions now merge adjacent same-speaker word-sized fragments before publishing them, reducing clean-region fragment rates across PriMock57 without merging alternating-speaker ping-pong fragments or changing the browser payload shape.
- **Transcript punctuation readability** - server-side segment cleanup now inserts missing spaces after glued sentence punctuation before rows reach the browser, so ASR text like `started.My` renders as readable transcript text without changing payload shape.
- **Transcript overlap-ceiling spike** - added an eval-only separated-channel runner for named PriMock57 fixtures, but stopped the full-corpus ceiling path after batch mode exceeded GPU memory and WebSocket mode destabilized NeMo on c04; the runner now blocks accidental full-corpus runs unless explicitly allowed.
- **CI context validation workflow** - removed the GitHub Actions wrapper for context validation; the local `./scripts/context-validate.sh` check remains available for agent/workflow edits.
- **Transcript seam spike results** - recorded and rejected two M17 seam-residue mechanisms: NeMo word timestamps exposed the needed SDK surface but crashed the GPU path during live fixture eval, and raising the emission floor failed to reach zero seam repeats without risking short-utterance loss. The accepted runtime keeps the stable timestamp-free NeMo decode path and the previous 0.3s emission floor.
- **Strands PHP client dev upgrade** - Composer now uses the requested `blundergoat/strands-php-client` `dev-dev` commit `98bd6598...`; Symfony Strands calls use the new response-observer hook to add body-safe response counts to `strands.client.call` logs, and the scribe client retries transient Python proxy failures (`429/502/503/504`) twice with a short backoff.
- **Windowed transcript emission** - `TranscriptionSession` now transcribes only audio past an emission high-water mark (with a short context lead) instead of re-transcribing the whole session every chunk. Each stretch of speech reaches the browser exactly once, segments still forming at the buffer edge wait one chunk, the finalize step drains and publishes the held tail, and window speaker IDs are matched to the previous window so labels stay continuous. Fixes the duplicated/growing live transcript and removes the O(n²) GPU cost; measured on PriMock57 consultation-03 ground truth, 4-gram duplication dropped to 3%.
- **M16 reduced quality scope** - the 0.3.0 diarization milestone now ships phantom containment, confidence observability, flip damping, and diagnostic ceilings while deferring true seam-stable speaker identity to a future milestone; the deep diagnosis showed the original ≥90% attribution target is capped by window-seam identity instability.
- **Dyadic speaker containment** - NeMo sessions now cap visible speaker IDs to the configured consultation limit (`NEMO_SPEAKER_CAP`, default `2`) and merge stray window-local speaker IDs back into an established visible identity, with phantom-merge counts captured in session quality records.
- **Dev workspace layout** - transcript, summary, and dev rail now split the width 5/4/3; Clinical Hints render above a collapsible Dev Panel in the right rail; the Demo Audio picker moved into the header (placeholder "Select demo audio", icon-button height, chevron icon, Upload WAV entry in its menu) and the left fixture panel was removed, as was the "Speaker labels corrected" toast.
- **Ollama behind a compose profile** - the `ollama` service only starts under the `ollama` compose profile; `start-dev.sh` activates it when `ROLE_AGENT_MODEL_PROVIDER=ollama`, and Bedrock setups run a three-service stack. `check-ai-model.sh` gained a real Bedrock probe (inference-profile existence plus a one-token invoke) instead of echoing configuration.
- **Agent log defaults** - the NeMo agent now defaults to JSON logs in Compose, with documented `jq` commands for tracing chunk timing, errors, and Mercure publishes by `session_id`; `LOG_FORMAT=console` remains available for plain interactive logs.
- **Streaming demo replay** - demo audio now streams through the live transcription pipeline instead of a batch upload: the browser decodes the WAV to 16 kHz PCM, sends chunks over the same WebSocket as the microphone paced by the audible replay clock, and transcript rows arrive via Mercure exactly like a live visit. Removed the FastAPI `/session/{id}/replay` and `/session/{id}/replay/stop` endpoints, `replay_session.py`, and the Symfony replay proxy routes; this also retires the PHP upload-size and full-file NeMo GPU-memory footguns for demo audio.
- **Medical-only agent behavior** - removed Python-side mode selection for role inference, summaries, replay, and WebSocket ingest so the agent lane always uses DOCTOR/PATIENT role mapping and medical SOAP summaries.
- **Medical-only scribe UI** - removed the browser mode selector, stored mode preference, and mode query parameters from live WebSocket and replay requests.
- **Medical-only demo scenarios** - removed meeting, interview, TV/media, and lecture scenario fixtures from the developer scenario corpus.
- **Demo audio picker** - replaced the left-side dev scenario runner and duplicate header demo button with generated `tests/fixtures/audio/` WAV options that use a built-in-server-safe replay URL.
- **PriMock57 demo audio set** - excluded consultations 01, 09, and 10 from local fixture generation, manifest output, and the default M2 replay smoke.
- **PriMock57 replay clip length** - generate full-length PriMock57 demo consultations (previously capped to 90 seconds) so replay and transcription-quality checks cover the whole encounter; `NEMO_BUFFER_MAX_DURATION` (default 900s) bounds GPU memory.
- **Gruff TypeScript scope** - excluded the vendored Tailwind runtime from gruff-ts so analyzer findings focus on maintained frontend and workflow source.
- **Frontend structure** - split transcript rendering, replay, summary, and download behavior out of the core recording script for easier gruff-ts verification.
- **Gruff Python scope** - excluded one-off NeMo exploration scripts from gruff-py so Python analyzer findings focus on maintained runtime and test code.
- **Python API structure** - moved live streaming and role-inference queue workflows out of `server.py` while preserving FastAPI routes and browser-visible Mercure behavior.
- **Python quality gates** - scoped gruff-py to maintained runtime code and kept pytest as the behavioral test-quality gate for integration-heavy Python tests.
- **PHP complexity gate** - retired the bespoke cyclomatic checker and rewired Composer/preflight complexity checks to gruff-php.
- **PHP dependency bounds** - pinned the PHP runtime range and moved `blundergoat/strands-php-client` from the moving `dev-dev` branch to the tagged 1.4 series.
- **PHP generated reference scope** - excluded the generated Symfony/Psalm `config/reference.php` from gruff-php instead of hand-editing generated output.
- **PHPUnit strictness** - enabled failure-on-warning, failure-on-deprecation, risky-test, output, and global-state strict flags.
- **Observable process logs** - added JSON-line logging on Python and PHP with `session_id`/`correlation_id` join keys, Strands SDK token/latency metrics, and Mercure delivery outcomes.
- **Pinned NeMo image build** - moved the GPU agent image to `nvcr.io/nvidia/nemo:26.02`, pinned `nemo_toolkit[asr]==2.7.3`, and made the container install the checked-in Python requirements file.
- **Python dependency floors** - raised FastAPI, Uvicorn, Pydantic, HTTPX, websockets, SSE Starlette, soundfile, Strands Agents, pytest, pytest-asyncio, and Ruff floors while keeping numpy at the NeMo-compatible 1.x floor until the GPU image is verified.
- **WebSocket server backend** - set Uvicorn to `websockets-sansio` in both Dockerfile and Compose entrypoints so local browser sessions use the same backend.
- **PHP dependency floors** - raised Mercure, Mercure Bundle, PHPUnit, and Infection within the PHP 8.3/Symfony 6.4 lane, and tightened `symfony/dotenv` back to the Symfony 6.4 series.
- **Clinical summary grounding** - added a CPU-only PoC clinical knowledge helper so generated SOAP summaries can include short documentation reminders without using the NeMo GPU.
- **Scribe workspace design** - refreshed the consultation UI toward the 0.3.0 mockup with a compact left demo-audio/dev rail, softer clinical palette, pill controls, and a transcript/summary split workspace.
- **Session summary panel states** - gave the summary panel explicit pending, generating, generated, and failed states with a status badge (`✓ Generated` / `Summary unavailable`) and a retry control, showed the pending placeholder only once transcript text exists, made the panel a fixed non-collapsible header (removed the toggle and chevron), and guarded against overlapping in-flight summary requests per session.
- **Consultation fonts** - loaded the Libre Franklin (UI) and IBM Plex Mono (dev/log) webfonts so the rendered consultation UI matches the 0.3.0 mockup typography instead of falling back to system fonts.
- **Demo audio dropdown** - replaced the demo-audio card list with a compact dropdown selector ("consultation-0X · complaint" plus a "PriMock57 consultation · doctor / patient" descriptor) matching the 0.3.0 mockup; picking a clip starts its replay and replay status still marks the chosen option.

### Added

- **Session quality records** - finalized WebSocket sessions now emit a `session.quality` log/event with chunk counts, window and inference percentiles, held/emitted segment counts, role confidence/flip counters, error count, and speaker-stability counters; the same JSON row is appended under the agent's gitignored `var/quality/sessions.jsonl`, and the dev State tab exposes the latest record.
- **Fixture eval runner** - added `scripts/eval-fixtures.sh` to stream PriMock57 WAV fixtures through the live WebSocket path, pull history and `session.quality`, run `scripts/transcript-quality.py`, append `var/quality/trend.jsonl`, and print a compact current-vs-previous report for M16/M17/M19 verification.
- **Transcript attribution scoring** - extended `scripts/transcript-quality.py` and the fixture trend rows with TextGrid-based speaker attribution, non-overlap attribution, residual phantom-speaker counts, and role-flip counts for diarization quality decisions.
- **Role attribution diagnostics** - added speaker oracle accuracy, role-mapping gap metrics, and per-session role timeline artifacts so fixture runs show whether wrong transcript labels come from diarization identity mixing, role mapping, fallback, or suppressed flips.
- **Honest role-mapping ceiling and flip-counter cross-check** - `scripts/transcript-quality.py` now reports the best valid one-DOCTOR/one-PATIENT mapping accuracy and the real `role mapping headroom` (the free-role oracle overstates recoverable accuracy when diarization mixes one voice across both speaker IDs), and `scripts/role-timeline.py --quality-json` appends a `role_timeline.quality_check` row because `session.quality` flip counters are snapshotted at disconnect while late role decisions land only in the logs; `scripts/eval-fixtures.sh` records both and warns on counter mismatch.
- **Transcript word-level quality metrics** - `scripts/transcript-quality.py` now reports WER with substitution/insertion/deletion counts, clean-vs-overlap WER, segment density, fragment rates, and seam re-read counts; `scripts/eval-fixtures.sh` records those metrics in trend rows so M17 accuracy/readability changes can be accepted or reverted by corpus numbers.
- **PriMock57 ground-truth transcripts** - added `scripts/download-primock57-transcripts.sh` to fetch the CC BY 4.0 Praat TextGrid transcripts paired by name with each demo consultation WAV, enabling transcription-quality measurement against a reference.
- **Summary failure guidance** - when summary generation fails, the panel now shows an actionable fix note and a page-level warning banner ("AI model unavailable … See README_STACK.md") pointing at Ollama/Bedrock reachability, instead of a bare "Summary generation failed" message; the banner clears once a summary renders.
- **Live model-unavailable warning** - when the role/summary model is unreachable, the agent publishes a one-time `system_error` on the session roles topic so the browser shows the warning banner during the consultation, not only at summary time; it clears once a summary renders.
- **Pre-flight AI-model gate** - a consultation no longer starts (recording or replay) when the off-GPU role/summary model is unreachable. The browser checks a new `GET /agent/model-health` endpoint first and, if unavailable, shows an actionable banner and aborts instead of transcribing with no roles/summary. Added `scripts/check-ai-model.sh` (referenced by the UI) to diagnose and pull the model, replacing the unhelpful "See README_STACK.md" copy.
- **Dev Panel connection indicator** - the Dev Panel header shows a green "● connected" / "○ disconnected" state reflecting the live Mercure feed, matching the 0.3.0 mockup.
- **Settled speaker-confidence badge** - the "Identifying speakers…" badge no longer pulses indefinitely; once inference returns it shows a settled state ("Roles identified" / "Low confidence" / "Speakers unclear (N%)") with no flashing.

- **Log analysis and eval tooling** - added `scripts/analyze-logs.py` for process-quality reports and `scripts/eval-role-heuristic.py` for GPU-free scenario role-attribution evaluation.
- **Stack inventory documentation** - added `README_STACK.md` with the current model, service, runtime, topic, and dependency inventory for the medical scribe stack.
- **Clinical intelligence documentation** - added `README_CLINICAL_INTELLIGENCE.md` to explain the medical phrase normalisation and clinical RAG/hints layers, including toggles, safety boundaries, benefits, and pending GPU/SSE proof.
- **Synthetic demo consultation corpus** - added an FFmpeg/Flite generator, manifest, attribution notes, and documentation for five license-clean replay WAVs, including chest pain, role-flip, three-speaker, drug-vocabulary, and monologue cases.
- **Medical phrase normalisation** - added an opt-in medical lexicon and post-ASR correction fallback behind `MEDICAL_BOOST_ENABLED` while NeMo decode-time phrase boosting remains GPU-pending.
- **Clinical hints sidebar** - added the `scribe/session/{id}/hints` Mercure topic, summary-response hint fallback, browser subscription, and dismissible sidebar for assistive clinician-review suggestions.

### Fixed

- **Role-agent tool payload size** - shrank the Strands `assign_roles` contract so the
  model passes only session ID, mapping, confidence, and terse reasoning while transcript
  rows stay in server-side pending state; role updates still publish the same browser
  `attributed_segments` payload, role-agent input now uses capped per-speaker evidence
  instead of `transcript_so_far`, and `session.quality` records role truncation events.
- **Suppressed role-flip handling** - a damped `assign_roles` flip now counts as a successful
  tool decision, so the role-agent runtime keeps the established DOCTOR/PATIENT mapping
  instead of falling through to the keyword fallback and applying the suppressed relabel.
- **Agent session isolation and role overrides** - role and summary Strands agents are now
  created per call instead of cached as singleton conversation objects, and server-side role
  mapping now preserves a user's manual speaker correction over later agent proposals.
- **Manual role override persistence** - speaker-label clicks now post through the same-origin
  Symfony `/scribe/{sessionId}/roles/override` proxy instead of a browser-to-FastAPI CORS
  request, so the visible correction is also saved in the server role state.
- **Structured role and summary agent contracts** - role inference now accepts only the
  compact `assign_roles` tool path and falls back to the keyword classifier when the tool is
  not invoked; summary generation now uses a Pydantic structured-output schema, drops the
  unused `duration_seconds` summary field, and has independent `SUMMARY_AGENT_*` model and
  token settings.
- **Python diagnostic log lines** - warning and error logs in the agent now put session IDs, error types, and error text into the plain message line, while exception-backed paths include tracebacks. Added an observability guard so `logger.error("event", extra={...})` regressions fail in pytest instead of hiding details in Docker logs.
- **Strands callback noise** - role and summary agents now pass the SDK's explicit null callback handler so model reasoning, tool banners, and streamed summary prose do not print into the container log stream.
- **Ollama host unreachable via stale `.env`** - the agent's `OLLAMA_HOST` is now pinned to the in-network `http://ollama:11434` in `docker-compose.yml` and is no longer overridable by `.env`. A stale `.env` value of `http://host.docker.internal:11434` (unreachable from the agent on WSL2) was silently making every summary 502 and forcing role inference onto the weak keyword heuristic across container recreates.
- **Demo Audio panel gap** - the demo-audio panel is now content-height (grid `auto` row) so the Upload WAV button sits directly under the selector and the Dev Panel fills the remaining rail, instead of a fixed 42vh panel with a large empty gap.
- **Consultation viewport layout** - the scribe page now fits the viewport height with the transcript and summary panels scrolling internally, instead of growing past the viewport and producing a page-level vertical scrollbar.
- **Agent image boto3/botocore conflict** - the NeMo base image's runtime venv (`/opt/venv`) shipped `botocore 1.42.61`, which shadowed the boto3/botocore that `strands-agents` installed into the system site and crashed the FastAPI agent at import (`cannot import name 'DocumentModifiedShape' from 'botocore.docs.utils'`), leaving the container unhealthy and blocking `setup-initial.sh`. The `docker/nemo/Dockerfile` now installs a matched `boto3==1.42.61`/`botocore==1.42.61` pair into `/opt/venv`, which also satisfies the base image's `aiobotocore<1.42.62` pin.
- **Ollama Compose wiring** - the agent now defaults to the bundled `ollama` service (`http://ollama:11434`), which starts with the stack; removed the `local` profile, added a `nemo-agent`→`ollama` dependency, and dropped the host port so it never clashes with a host-side Ollama. Fixes summaries returning 502 and role inference falling back to the heuristic when `host.docker.internal:11434` was unreachable (e.g. on WSL2).
- **Demo audio replay routing** - added same-origin Symfony proxies for replay and summary requests so the browser receives JSON from FastAPI instead of app-origin HTML errors.
- **Audible demo audio stop flow** - made Demo Audio replay attach the selected WAV to a browser audio player, added early replay stop/cancel handling, and exposed the Summarise button after stopped replay text exists.
- **Demo audio transcript pacing** - made replay transcript rows reveal from the browser audio clock and send the visible transcript snapshot to stop/summary routes so text cannot outrun what the user hears.
- **Large demo WAV replay** - raised local PHP upload limits for PriMock fixtures and made replay treat malformed success responses as recoverable UI errors.
- **Transcript empty state** - hid the start prompt as soon as transcript rows render, including dev-injected replay/test events.

### Security

- **Frontend transcript rendering** - moved transcript, summary, status, and dev-panel output away from HTML-string rendering so model and scenario text is inserted as text.
- **Deploy workflow actions** - pinned third-party AWS GitHub Actions to reviewed commit SHAs.
- **Env template placeholders** - replaced realistic-looking committed secret examples with obvious local placeholders and removed copied AWS credential slots from `.env.example`.
- **Remote health-check secret path** - moved the production API key secret path behind a required `SECRET_PATH` override instead of committing the deployed path.

### Removed

- **Multi-mode support** - removed Meeting, Interview, TV/Media, Lecture, and General modes, including the `?mode=` transport parameter, `_session_modes`, mode prompt dictionaries, and the browser mode selector.
- **Transcript download control** - removed the Download button, keyboard shortcut, and browser-side JSON/TXT export code from the scribe UI.

## [0.2.0] - 2026-03-16

Release covering tool-based role mapping, summaries, replay, transcript grouping, scenario gates, JS extraction, UI polish, developer guidance, multi-mode role inference, local-first defaults, SQLite persistence, manual speaker overrides, and full-stack hardening.

### Added

- **Gruff quality analyzers** - added TypeScript, Python, and PHP dev analyzers: `@blundergoat/gruff-ts`, `gruff-py`, and `blundergoat/gruff-php`.
- **Agent-neutral instruction layer** - added reusable AI guidance in `ai/instructions/` plus routing docs for agents that do not depend on Claude Code or Codex runtime files.
- **CI validation** - added router-table and skills-directory checks, plus a quick-reference commit instruction file.
- **`@tool` role assignment** - added Strands tool support for role state management while keeping the free-text JSON fallback.
- **Session summaries** - added six mode-specific summary prompts, `POST /session/{id}/summary`, Mercure summary publishing, UI display, and transcript export support.
- **Replay demo mode** - added WAV upload replay through NeMo with paced Mercure events, speed control, progress UI, and automatic role inference.
- **Transcript grouping** - merges consecutive same-speaker segments into chat blocks that relabel and download correctly.
- **Scenario assertions** - added duration, content, and fixture-structure validation for the scenario runner.
- **Ollama tool-calling footgun** - documented models that support `assign_roles`, including `qwen3.5:9b`.
- **Frontend extraction** - moved production code to `public/js/scribe.js` and dev-only panel code to `public/js/scribe-dev.js`.
- **Developer instrumentation** - added WebSocket frame/byte counters, Docker hot reload, template rebuild guidance, five multi-mode scenarios, and 37 Python tests.
- **Mode-aware role inference** - added six mode-specific prompts, browser-passed mode, context labels, and per-mode agent caching.
- **Role inference fallback** - falls back from LLM agent to mode-specific heuristics, then to graceful no-role output.
- **Manual speaker override** - lets users cycle roles, publishes overrides to Mercure, locks confirmed speakers, and exposes `POST /session/{id}/roles/override`.
- **SQLite persistence** - added `StorageBackend`, SQLite and memory backends, `SESSION_STORAGE=sqlite|memory`, WAL mode, and Docker data persistence.
- **Reconnect support** - added WebSocket reconnect grace, session resume, and Mercure Last-Event-ID event IDs.
- **Speaker and role UX** - added hallucination filtering, cold-start animation, flip toast, audio-level feedback, clipping warnings, keyboard shortcuts, and transcript accessibility attributes.
- **Runtime cleanup and protocol fields** - added orphan cleanup plus `segment_id`, `revision`, and `supersedes` fields for future reconciliation.
- **Local runtime support** - added optional CPU Ollama service, bundled Tailwind, Python hot reload, and expanded SQLite, role inference, hallucination, and session tests.

### Changed

- **BREAKING: PHP baseline is now 8.3+.** Upgrade local, CI, and deployment PHP from 8.2 to 8.3 before running Composer; this has no deprecation window because the PHP Gruff dev tool requires PHP 8.3.
- **Ollama default model** - changed `llama3.1:8b` to `qwen3.5:9b` to match local pulls, `.env.example`, and Docker Compose.
- **Role inference worker** - detects tool invocation via mapping-history growth and avoids duplicate role mapping application.
- **Role inference prompt and agent setup** - instructs tool calling with JSON fallback and passes `assign_roles` in the agent tool list.
- **Role flip detection** - moved client-side so Mercure reporting reflects visible mapping changes.
- **Developer scripts and labels** - simplified `start-dev.sh` flags and renamed TV/General start labels.
- **Agent guidance** - made Ask First paths, commit areas, evals, and lessons more project-specific.
- **Default role provider** - changed `ROLE_AGENT_MODEL_PROVIDER` from `bedrock` to `ollama` for local-first startup.
- **Confidence scoring** - uses a rolling last-five window instead of lifetime average.
- **Transcript context** - sends the first 500 and last 3000 characters to preserve opening context.
- **Agent parsing and prompt state** - extracts JSON from preamble text and caps mapping history to five entries.
- **Inference queue** - uses `maxsize=50` with non-blocking enqueue and drops overflow batches.
- **Async/runtime internals** - replaced deprecated event-loop access, reused one Mercure `httpx.AsyncClient`, switched `AudioBuffer` to `deque`, optimized relabeling by speaker map, and simplified session destruction.

### Removed

- **`ROLE_INFERENCE_SYSTEM_PROMPT`** - removed the unused backwards-compatibility alias.
- **Legacy live role SSE path** - removed the PHP `/roles/stream` endpoint, `RoleInferenceService::streamRoleInference()`, `RoleInferenceResult`, `fetchAuthoritativeSnapshot()`, and the Python `/session/{id}/roles/stream` endpoint.
- **Unused SSE support** - removed `sse-starlette` imports and SSE consumer tracking.
- **`docker-compose.no-gpu.yml`** - removed the unused no-GPU compose file because the app requires GPU transcription.

### Fixed

- **Instruction drift** - fixed the `blundergoat/strands-php-client` package name, footgun cross-reference, and CI instruction-file triggers.
- **Session cleanup** - clears confidence pulse, dev panel logs, speaker maps, summaries, and replay state.
- **Download fallback** - collects text from grouped segment spans instead of a single segment node.
- **E2E contracts** - uses UUID session IDs, checks the `/summary` endpoint, and asserts the extracted `scribe.js` reference.
- **Python tests** - repaired stale imports, fixtures, UUIDs, and `AudioBuffer` API expectations.
- **File upload security** - replaced user-shaped temp paths with `NamedTemporaryFile`.
- **Session ID validation** - rejects malformed IDs with HTTP 400 on all endpoints.
- **Error privacy** - publishes generic Mercure errors and truncates role inference logs with `error_type`.
- **Frontend/runtime issues** - declared `pcmStreamer`, filtered health-check log spam, and made the ready banner use configured ports.

### Tests

- **230 Python unit tests** cover tool/free-text role mapping, flip detection, agent creation, summaries, replay, heuristics, and scenario fixtures.
- **25 E2E contract tests** cover agent health, sessions, WebSocket, file transcription, PHP proxy, Mercure pub/sub, lifecycle, and cross-service shape matching.

### Security

- Session IDs are UUID-validated on all API endpoints.
- Temp files use secure generated paths.
- Mercure error messages are sanitized.
- Transcript content is stripped from application logs.

## [0.1.0] - 2026-03-15

First release: real-time audio transcription with speaker diarisation, role inference, and a developer scenario runner that works without GPU hardware.

### Added

- **Transcription UI** - added the Twig page with live transcript, recording controls, timer, JSON/text download, and reset.
- **Modes and theme** - added Medical, Meeting, Interview, TV/Media, Lecture, and General modes plus persisted light/dark theme.
- **Streaming clients** - added Mercure `StreamOrchestrator`, browser-side `PcmStreamer`, and WebSocket reconnect logic.
- **Role inference** - added Strands role updates, retroactive relabeling, and confidence badges.
- **Dev panel** - added dev-only scenario, transcript, inspector, pipeline, Mercure, WebSocket, state, and raw-event views.
- **Scenario runner** - added eight fixture-driven scenarios with validation, batch execution, progress, and JSON export.
- **Backend and agent APIs** - added ScribeController routes, FastAPI WebSocket ingest, NeMo diarisation, Mercure publishing, session lifecycle, and role assignment tooling.
- **Infrastructure and tooling** - added Terraform, GPU Docker Compose, Mercure, setup/start/preflight/health/load/e2e/context scripts, quality gates, PHPUnit, pytest, Playwright scaffolding, and project docs.

### Fixed

- `start-dev.sh` no longer crashes on unbound variables or undefined functions.
- Dev panel segment data updates retroactively.
- StreamOrchestrator `_active` flag ordering is correct.
- Python hot-reload uses the correct Docker volume mount path.

[Unreleased]: https://github.com/user/ambient-scribe/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/user/ambient-scribe/releases/tag/v0.2.0
[0.1.0]: https://github.com/user/ambient-scribe/releases/tag/v0.1.0
