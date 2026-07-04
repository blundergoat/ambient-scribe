---
category: verification
last_reviewed: 2026-07-04
---

# READ / SCOPE / VERIFY Lessons

## Lesson: Audio format mismatch — read both pipeline ends (2026-03-21)

AudioBuffer in `strands_agents/nemo_session.py` assumed 16 kHz 16-bit PCM while the browser MediaRecorder sent WebM/Opus. NeMo received garbage audio and produced nonsensical transcriptions with no errors in logs. Root cause was found only after reading both `templates/scribe/index.html.twig` (producer) and `strands_agents/nemo_session.py` (consumer).

**Lesson:** Always read both ends of a data pipeline before diagnosing silent failures. Related footgun: `.goat-flow/learning-loop/footguns/audio.md`.

## Lesson: Question misclassified as directive (2026-03-21)

"How does session cleanup work?" was treated as a directive to implement changes to session cleanup. The SCOPE step should have identified this as a question and kept the agent in Explain mode.

**Lesson:** Questions get explanations, not edits — do NOT migrate to Implement mode unless a Directive is issued.

## Lesson: Stale references after rename (2026-03-21)

After renaming `mercure_topic_raw` to `mercure_topic_segments`, stale references remained in config and docs.

**Lesson:** Always run `rg <old_symbol>` after renames and confirm zero remaining refs (DoD gate #6).

## Lesson: Harness line-count failures after instruction edits (2026-07-04)

Adding required hot-path headings to `CLAUDE.md` fixed structural checks but pushed the file over the harness hard limit reported by `instruction-line-count`.

**Lesson:** When editing audited instruction files, include `wc -l <file>` in the verification gate before the first audit rerun and keep required section additions under the harness hard limit.

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
**Evidence:** `composer.json` (search: "\"analyse:complexity\": \"vendor/bin/gruff-php analyse\""), `.goat-flow/plans/0.3.0/M07-fix-gruff-php-findings.md` (search: "rewired").

During M07, a complexity-only gruff-php command was initially considered for the retired cyclomatic alias. The command still failed while unrelated advisory findings existed, because gruff-php's report selection changes displayed findings but the configured `minimumSeverity.analyse` threshold still controls the process exit.

**Lesson:** When replacing a legacy quality gate with gruff-php, use the full `gruff-php analyse` command unless the tool documentation explicitly says a selector changes exit semantics; prove the alias with a failing and then clean run before marking the plan checkbox complete.

## Lesson: SDK observability plans must match installed vendor contracts (2026-07-04)

**Created:** 2026-07-04
**Evidence:** `.goat-flow/plans/0.3.0/M08-observability-and-eval.md` (search: "ResponseObserver"), `vendor/blundergoat/strands-php-client/src/Http/RequestMiddleware.php` (search: "interface RequestMiddleware"), `src/Observability/StrandsClientTelemetry.php` (search: "implements RequestMiddleware").

During M08, the plan described PHP client 1.5.x `ResponseObserver` hooks, but the installed 1.4.0 client only exposes `RequestMiddleware` with `beforeRequest()` and `afterResponse()`. Implementing from the plan text alone would have created a class against an absent interface.

**Lesson:** Before implementing SDK instrumentation from a plan, verify the installed vendor interface and lockfile version, then update the plan with the actual contract used.

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
**Evidence:** `templates/scribe/index.html.twig` (search: "app-layout--with-hints"), `public/js/scribe-output.js` (search: "setClinicalHintsLayoutVisible").

During M12, the first clinical-hints UI pass hid the sidebar element but left a dedicated desktop grid column in the base layout. Static analyzers and API tests stayed green, but the clinician page would have opened with blank right-side space until hints arrived.

**Lesson:** When adding a hidden/dismissible panel that changes page columns, verify both empty and populated layout states with a DOM or browser smoke test. The hidden state must remove reserved layout space, not only hide panel contents.

## Lesson: Optional JSON knowledge files need malformed-file coverage (2026-07-04)

**Created:** 2026-07-04
**Evidence:** `strands_agents/clinical_hints.py` (search: "except json.JSONDecodeError"), `tests/python/test_clinical_hints.py` (search: "test_load_clinical_knowledge_ignores_invalid_json").

During M12, the clinical KB loader handled missing files and invalid shapes but did not handle malformed JSON. A bad PoC knowledge file would have turned an assistive hint/grounding feature into a summary-generation failure for the user.

**Lesson:** For optional local JSON/fixture inputs used by a user-facing path, cover malformed JSON as well as missing files and empty data. Optional assistive data should degrade to no context, not block the primary workflow.

## Lesson: Model stack docs need env, Compose, and code defaults checked together (2026-07-04)

**Created:** 2026-07-04
**Evidence:** `README_STACK.md` (search: "Compose has an older no-`.env` fallback"), `.env.example` (search: "ROLE_AGENT_MODEL_PROVIDER=ollama"), `strands_agents/agents/transcription_agent.py` (search: "ROLE_AGENT_MODEL_PROVIDER").

While creating the stack inventory, the root README still described Bedrock as the role-inference default, `.env.example` described Ollama as the local default, Compose passed Ollama by default, and the Python agent retained Bedrock defaults for missing env vars. Reading only one source would have produced another stale model summary.

**Lesson:** For docs that name model providers or IDs, verify `.env.example`, `docker-compose.yml`, agent factory code, and any existing README before writing the final wording. Call out intentional fallback differences instead of flattening them into one default.

## Lesson: Browser replay smokes need a trustworthy origin (2026-07-04)

**Created:** 2026-07-04
**Evidence:** `public/js/scribe-recording.js` (search: "crypto.randomUUID"), `public/js/scribe-fixtures.js` (search: "startReplay(replayFile, { audioUrl").

While testing the Demo Audio picker, a Playwright smoke loaded the page on `http://app.test/`. The replay flow called `resetSession()`, which uses `crypto.randomUUID()`, and Chromium denied that API on the non-trustworthy fake origin. The same smoke passed when routed through `http://localhost/`, matching local app behavior.

**Lesson:** Browser smokes that exercise recording or replay session reset should run on `localhost` or HTTPS, not arbitrary fake HTTP hosts. Otherwise secure-context browser APIs can fail before the app flow is actually tested.
