# Improvement Backlog - Codex Deep Review

> Generated 2026-03-17 from a deeper project review.
> Current review score: 68/100.
> Scope: full project, including browser, Symfony, FastAPI, Terraform, deployment, CI/CD, tests, packaging, and docs.
> Relationship to `TODO_priority-fixes.md`: that file is mostly local-dev and bug-fix oriented; this file is the broader structural improvement backlog.

## Core Themes

- The main weakness is not raw code quality. The main weakness is contract drift across layers.
- The browser, Symfony app, FastAPI agent, Terraform, deployment scripts, and docs each contain overlapping truths, and they are no longer aligned.
- The next wave of work should prioritize correctness of shipped paths before adding new features or polishing UX.
- The highest leverage improvements are the ones that remove duplicated contracts or make drift mechanically detectable.

## Recommended Execution Order

1. Fix browser route ownership and production env/health drift.
2. Repair reconnect/finalization semantics and role taxonomy alignment.
3. Restore trust in verification by fixing Python test teardown and adding CI coverage.
4. Separate dev and prod packaging/docs so operational guidance matches reality.
5. Add machine-checkable contract validation so the same drift does not recur.

## P0 - Make The Shipped Runtime Correct

### 1. Unify Browser HTTP Route Ownership

#### Problem

The browser calls relative `/session/{id}/replay` and `/session/{id}/summary` routes from the Symfony-served page, but those routes only exist in FastAPI. This means replay and summary are broken from the main UI unless an external proxy exists outside the repo.

#### Evidence

- `public/js/scribe.js` uses relative `fetch()` calls for replay and summary.
- `src/Controller/ScribeController.php` exposes `/scribe`, `/scribe/{id}/history`, and `/scribe/{id}/roles`, but not replay or summary proxies.
- `strands_agents/api/server.py` owns `POST /session/{id}/summary` and `POST /session/{id}/replay`.

#### Why This Matters

- Demo mode is a visible product path.
- Summary generation is a user-facing feature, not a background nice-to-have.
- A broken origin contract undermines confidence in every other browser-to-backend interaction.

#### Recommended Direction

Pick one browser contract and apply it consistently:

- Preferred option: make Symfony the single browser-facing HTTP origin for all non-WebSocket session operations, and proxy replay, summary, and role override to FastAPI.
- Alternative option: explicitly inject an API base URL into Twig and have the browser call FastAPI directly for replay, summary, and override operations.

The important part is not which option wins. The important part is that there is exactly one intentional ownership model.

#### Detailed Tasks

- [ ] Decide whether browser-initiated session HTTP actions are owned by Symfony or FastAPI.
- [ ] If Symfony is the browser-facing origin, add controller routes for:
- [ ] `POST /scribe/{sessionId}/summary`
- [ ] `POST /scribe/{sessionId}/replay`
- [ ] `POST /scribe/{sessionId}/roles/override`
- [ ] Update `public/js/scribe.js` to call the chosen routes only.
- [ ] Remove or replace any relative browser calls to `/session/...` that assume FastAPI is same-origin.
- [ ] Add request/response shape tests for replay and summary on the chosen browser origin.
- [ ] Add Playwright coverage that actually clicks the Demo and Summary paths from the UI.
- [ ] Run `rg` after the change to confirm there are no stray browser-side relative `/session/` calls left unless intentionally kept.

#### Validation

- Browser demo replay works end to end from the Symfony page.
- Browser summary generation works end to end from the Symfony page.
- Contract tests prove the chosen origin returns the expected shapes.
- Search confirms only the intended origin strategy remains in the codebase.

#### Expected Impact

High user-facing impact, high confidence gain, moderate implementation effort.

### 2. Align Terraform And ECS Env Vars With The Actual FastAPI Runtime

#### Problem

Terraform provisions an agent environment contract that does not match what FastAPI actually reads at runtime. Critical values used by the agent are absent from Terraform, and at least one injected secret appears unused by the application.

#### Evidence

- `infra/terraform/environments/prod/main.tf` injects `PORT`, `MODEL_ID`, `MODEL_PROVIDER`, `AWS_DEFAULT_REGION`, `ALLOW_SYSTEM_PROMPT_OVERRIDE`, and `DYNAMODB_TABLE`.
- `strands_agents/api/server.py` actually reads `MERCURE_HUB_URL`, `MERCURE_JWT_SECRET`, `MERCURE_JWT`, `NEMO_STREAM_INPUT_FORMAT`, `NEMO_BUFFER_MAX_DURATION`, `SESSION_STORAGE`, and `SESSION_RECONNECT_GRACE_SECONDS`.
- `publish_to_mercure()` skips publishing when no JWT is configured.
- Terraform provisions an `API_KEY` secret, but the current runtime does not appear to consume it.

#### Why This Matters

- Real-time publishing can silently fail in production if Mercure credentials are not wired correctly.
- Infra-level drift is harder to notice because local dev can still work.
- Unused secrets and variables create false confidence and operational confusion.

#### Recommended Direction

Make the runtime contract explicit and source Terraform, scripts, and docs from that contract instead of hand-maintaining multiple divergent lists.

#### Detailed Tasks

- [ ] Inventory the actual env vars read by:
- [ ] `strands_agents/api/server.py`
- [ ] `strands_agents/session.py`
- [ ] `strands_agents/nemo_session.py`
- [ ] any startup scripts that set compatibility aliases
- [ ] Define a canonical runtime env contract in one place.
- [ ] Update Terraform `agent_env` to include all production-relevant runtime vars.
- [ ] Decide whether the agent should receive `MERCURE_JWT` or `MERCURE_JWT_SECRET`; document the choice and implement it consistently.
- [ ] Ensure the production agent receives a valid Mercure publisher credential.
- [ ] Remove the unused `API_KEY` secret path, or wire it into real auth if an authenticated public API is actually intended.
- [ ] Add a startup validation step that logs a clear error when required env vars are missing or self-contradictory.
- [ ] Update docs so the production env table matches the actual runtime code.

#### Validation

- A deploy artifact can publish to Mercure without relying on hidden out-of-band config.
- The agent logs a clear startup configuration summary and clear missing-var failures.
- Terraform env tables, docs, and runtime reads match exactly.
- Search for `API_KEY` either shows a real implementation path or no longer shows dead infrastructure wiring.

#### Expected Impact

Very high production reliability impact, moderate implementation effort.

### 3. Unify Health Checks, Service Names, And Deployment Verification

#### Problem

The production health model is inconsistent across Symfony, ALB, ECS, deploy workflow, and helper scripts. Different layers appear to expect different service names and different health endpoints.

#### Evidence

- ALB health check path is `/` and matcher is `200`.
- Symfony root route redirects `/` to `/scribe`.
- Deploy workflow checks `https://scribe.blundergoat.com/health`.
- Terraform ECS service name is `${project_name}-app`, while workflow and remote scripts refer to `ambient-scribe-agent`.

#### Why This Matters

- Health checks are the control plane for deploy stability.
- If health paths and service names drift, rollouts become unreliable and rollback signals become noisy.
- A deployment process that "looks green" but checks the wrong target is worse than no automation.

#### Recommended Direction

Define one public health endpoint per externally routed container and make every layer use it. Stop hardcoding service names in scripts when Terraform already knows them.

#### Detailed Tasks

- [ ] Add a real Symfony health endpoint that returns HTTP 200 without redirecting.
- [ ] Decide whether ALB should health-check the app container only or a more explicit app health route.
- [ ] Update ALB target group health path to the chosen route.
- [ ] Update ECS container health checks so container-level and ALB-level health use compatible semantics.
- [ ] Update the deploy workflow to verify the actual app service name and the real public health path.
- [ ] Update `scripts/health-check-remote.sh` and `scripts/aws-cli.sh` examples so they point at the correct ECS service.
- [ ] Replace hardcoded service names in scripts with values derived from Terraform outputs where possible.
- [ ] Add a small verification script that asserts:
- [ ] workflow service name matches Terraform service name
- [ ] workflow health URL matches the actual public route
- [ ] ALB health check path exists in the app

#### Validation

- ALB health checks succeed against an intentional 200 endpoint.
- Deploy workflow updates the intended ECS service.
- Remote health-check scripts inspect the same service the workflow deploys.
- A failed deployment is attributable to a real runtime issue, not naming drift.

#### Expected Impact

Very high operations impact, moderate effort.

### 4. Add Startup Guardrails For Critical Runtime Drift

#### Problem

Some high-value runtime failures remain too easy to miss until a full user flow fails. The system logs many things, but it does not yet fail loudly enough when its basic contract is misconfigured.

#### Examples

- Mercure publish can be skipped when JWT is missing.
- Browser origin assumptions can drift from backend ownership.
- Health route drift can survive until deployment time.

#### Recommended Direction

Add lightweight startup and pre-deploy assertions for "must be true" invariants rather than relying on human review and memory.

#### Detailed Tasks

- [ ] Add an application startup self-check for the agent that verifies Mercure config, session storage config, and known mode configuration.
- [ ] Add an app startup self-check for Symfony that verifies required parameters such as agent endpoint and Mercure URL are set.
- [ ] Add a pre-deploy validation script that checks:
- [ ] FastAPI required env vars are represented in Terraform
- [ ] browser-facing session routes are intentionally owned
- [ ] configured public health route exists
- [ ] known service name and task family are consistent
- [ ] Fail CI or deploy preparation if any critical invariant is violated.

#### Validation

- Critical misconfiguration becomes a startup error or a pre-deploy error, not a late runtime surprise.

#### Expected Impact

High leverage, lower effort than broad refactors.

## P1 - Fix State And Contract Semantics

### 5. Replace Implicit Session Behavior With An Explicit State Machine

#### Problem

The current reconnect lifecycle is described as graceful, but the implementation finalizes immediately and schedules destruction without actually waiting. The UI interprets finalization as terminal.

#### Evidence

- `SessionLifecycle.schedule_destroy()` logs grace expiry and destroys immediately without sleeping.
- WebSocket disconnect finalizes the session before any grace-period decision.
- The UI treats `finalized` as the end of the session.

#### Why This Matters

- A transient disconnect should not be semantically equivalent to a user stop.
- Finalization is expensive and user-visible.
- Reconnect support is only real if both state retention and event semantics are consistent.

#### Recommended Direction

Move from implied lifecycle semantics to an explicit state machine with clear transitions and event types.

#### Suggested States

- `active`
- `disconnect_grace`
- `finalizing`
- `finalized`
- `destroyed`

#### Detailed Tasks

- [ ] Add a real grace delay in `schedule_destroy()`.
- [ ] Separate "socket disconnected" from "session finalized".
- [ ] Emit a distinct event for transient disconnect if the browser needs to know.
- [ ] Finalize only on:
- [ ] explicit user stop
- [ ] replay completion
- [ ] grace-period expiry without reconnect
- [ ] Ensure reconnect cancels pending destroy and resumes the same session state.
- [ ] Update browser messaging so it distinguishes temporary disconnect from terminal completion.
- [ ] Add tests for:
- [ ] reconnect within grace preserves state and avoids `finalized`
- [ ] reconnect after grace expires creates a fresh session
- [ ] explicit stop produces `finalized`
- [ ] replay completion produces `finalized`

#### Validation

- A dropped connection during recording no longer prematurely finalizes the transcript.
- A reconnect within grace keeps the same session and transcript buffer.
- Event sequences are stable and documented.

#### Expected Impact

High correctness impact, moderate effort.

### 6. Normalize Role Vocabulary End To End

#### Problem

The frontend still models multiple modes with legacy `DOCTOR` and `PATIENT` keys while the backend emits mode-specific roles such as `ORGANISER`, `INTERVIEWER`, `HOST`, and `LECTURER`.

#### Why This Matters

- Manual overrides can assign invalid roles for the selected mode.
- CSS classes, labels, avatars, and backend mappings are operating on different assumptions.
- Non-medical modes feel less finished because they are partially emulated rather than truly modeled.

#### Recommended Direction

Choose a canonical role schema and keep presentation labels separate from backend identity.

There are two viable approaches:

- Canonical backend roles per mode, with UI label maps and UI styling keyed off real backend values.
- Canonical generic roles like `PRIMARY`, `SECONDARY`, `TERTIARY`, with mode-specific labels layered above.

The first approach likely fits the current backend better. The second reduces drift but requires deeper semantic simplification.

#### Detailed Tasks

- [ ] Decide on the canonical role enum strategy.
- [ ] Update `public/js/scribe.js` mode config so role keys reflect the canonical backend identity rather than legacy placeholders.
- [ ] Update manual override cycling so it only presents valid roles for the current mode.
- [ ] Update avatar and CSS class logic to handle all supported role values.
- [ ] Update backend validation for `/roles/override` so impossible roles are rejected with a clear error.
- [ ] Update tests to cover each mode's valid role set.
- [ ] Add at least one browser test per non-medical mode to confirm labels and overrides work correctly.

#### Validation

- Meeting mode uses meeting roles, interview mode uses interview roles, and so on.
- A user cannot assign a medical role while in lecture mode unless that is an explicitly supported fallback.
- UI labels, styles, and backend mappings all agree.

#### Expected Impact

High product consistency impact, moderate effort.

### 7. Clarify Replay And Summary Lifecycle Semantics

#### Problem

Replay, summary generation, transcript finalization, and Mercure subscription timing are tightly coupled but only partly documented. The current flow works by a combination of assumptions rather than a clearly owned session lifecycle.

#### Why This Matters

- Replay is both a demo feature and an internal test path.
- Summary currently depends on the transcript being available and the right endpoint being reachable.
- Event timing bugs in replay or summary can be subtle and user-visible.

#### Recommended Direction

Treat replay and summary as first-class session modes with explicit lifecycle behavior, not ad hoc extensions of the live recording path.

#### Detailed Tasks

- [ ] Document the replay lifecycle from file upload to Mercure replay to completion to summary request.
- [ ] Decide whether summary should be:
- [ ] auto-triggered after replay completion
- [ ] user-triggered
- [ ] both
- [ ] Ensure replay and live recording share only intentional behavior.
- [ ] Add a browser-visible error state for summary unavailability versus summary generation failure.
- [ ] Ensure replay task cleanup is deterministic and testable.
- [ ] Add tests for:
- [ ] replay success path
- [ ] replay with empty transcript
- [ ] replay completion event ordering
- [ ] summary request after replay completion
- [ ] summary error rendering in the browser

#### Validation

- Replay behavior is deterministic and documented.
- Summary behavior is explicit, not inferred from side effects.

#### Expected Impact

Medium-to-high UX and maintainability impact.

### 8. Create One Machine-Readable Cross-Layer Contract

#### Problem

Routes, service names, health paths, role enums, and environment expectations are currently duplicated in code, Terraform, docs, and scripts.

#### Why This Matters

- Manual synchronization does not scale.
- Drift keeps reappearing in slightly different forms.
- Review effort stays high because every cross-layer change requires human diffing.

#### Recommended Direction

Introduce a machine-readable contract source, even if it starts small.

#### Candidate Contents

- browser-owned or app-owned session routes
- public health endpoints
- ECS service names and task families
- supported modes
- allowed roles per mode
- required env vars per runtime

#### Detailed Tasks

- [ ] Create a small contract file in a neutral format such as YAML or JSON.
- [ ] Use it to drive or validate:
- [ ] supported mode names
- [ ] allowed roles per mode
- [ ] public route ownership
- [ ] deployment service names
- [ ] required env vars
- [ ] Add a validation script that compares the contract file against code and Terraform usage.
- [ ] Keep the first version narrow and useful rather than trying to model the entire system.

#### Validation

- Known route names, role enums, and service names are validated automatically.
- A cross-layer rename breaks a checker instead of silently drifting.

#### Expected Impact

Very high long-term leverage, moderate upfront design effort.

## P2 - Restore Trust In Verification

### 9. Fix Python Test Teardown And Async Resource Cleanup

#### Problem

Important Python endpoint tests appear to hang because clients and async resources are not always shut down cleanly.

#### Evidence

- Tests create `TestClient(app)` without consistently using context managers.
- Some tests replace `app.state.http_client` with `httpx.AsyncClient()` and do not close it.
- Replay and summary tests initialize app state but do not consistently tear it down.

#### Why This Matters

- A hanging test suite trains people not to trust or run it.
- The most important API tests are the ones least likely to be exercised in day-to-day local work.

#### Recommended Direction

Treat test resource cleanup as part of the product's async correctness, not as test-only hygiene.

#### Detailed Tasks

- [ ] Convert `TestClient(app)` usage to context-managed fixtures where appropriate.
- [ ] Ensure every replacement `httpx.AsyncClient()` is closed in fixture teardown.
- [ ] Add teardown for pending replay tasks and any other module-level async state.
- [ ] Review `app.state` mutations in tests and reset them consistently.
- [ ] Add a focused CI target that runs the Python API/replay/summary tests with a timeout budget so hangs are caught early.
- [ ] Document the cleanup pattern in test fixtures so future tests follow it.

#### Validation

- `tests/python/test_api.py` completes cleanly.
- `tests/python/test_summary.py` completes cleanly.
- `tests/python/test_replay.py` completes cleanly.
- Repeated test runs do not leak state or hang.

#### Expected Impact

High confidence impact, moderate effort.

### 10. Put Python, PHP, And Browser Checks Into Real CI

#### Problem

Current CI is very light on product verification. The main workflow validates context docs, while deploy logic exists separately and is not backed by a broad test gate.

#### Why This Matters

- Broken runtime contracts can merge if they do not affect the narrow checks that currently run.
- Deploy automation without strong pre-deploy verification increases operational risk.

#### Recommended Direction

Add a real CI workflow that runs the core fast checks on every PR and blocks deployment on those checks.

#### Suggested CI Layers

- PHP: `phpunit`, `phpstan`
- Python: `ruff`, focused `pytest`
- Browser: lightweight Playwright smoke on the mock/no-GPU path
- Contract checks: route ownership, service-name drift, env-contract drift

#### Detailed Tasks

- [ ] Create a PR workflow for core validation.
- [ ] Run PHP checks in that workflow.
- [ ] Run focused Python tests in that workflow.
- [ ] Run browser smoke tests against the mock model path if practical.
- [ ] Add a contract-validation step for the cross-layer invariants described above.
- [ ] Make deploy depend on successful validation or an equivalent protected branch policy.

#### Validation

- A PR that breaks replay, summary, or role contracts fails before merge.
- Deployment no longer acts as the first integrated test.

#### Expected Impact

Very high quality impact, moderate effort.

### 11. Expand E2E Coverage To Match Real Failure Modes

#### Problem

Current E2E coverage proves some useful UI behaviors but misses several high-risk real flows, including the replay and summary path that is currently broken.

#### Why This Matters

- A test suite should be strongest around known cross-layer seams.
- Browser-origin routing bugs are invisible if tests only call the agent directly.

#### Recommended Direction

Add integration coverage for the exact cross-boundary flows that previously drifted.

#### Detailed Tasks

- [ ] Add Playwright coverage for Demo replay from the real page.
- [ ] Add Playwright coverage for summary generation from the real page.
- [ ] Add a contract test that asserts the browser-origin path for replay and summary is valid.
- [ ] Add reconnect-within-grace tests once lifecycle semantics are fixed.
- [ ] Add mode coverage for meeting, interview, TV/media, lecture, and general.
- [ ] Add a test for invalid role override submission and browser handling of the rejection.

#### Validation

- Known broken paths become test failures instead of review findings.
- Mode-specific regressions are caught before merge.

#### Expected Impact

High correctness impact, moderate effort.

### 12. Extend Preflight Checks Beyond Lint And PHP Unit Coverage

#### Problem

The existing preflight script is useful, but it still leaves major Python and cross-layer checks outside the default developer loop.

#### Recommended Direction

Keep preflight fast, but include a meaningful Python test slice and at least one contract checker.

#### Detailed Tasks

- [ ] Add a focused Python test slice to `scripts/preflight-checks.sh`.
- [ ] Add a contract drift checker to `scripts/preflight-checks.sh`.
- [ ] Keep the test slice intentionally small enough to stay runnable during normal work.
- [ ] Document which checks are "fast required" versus "slower optional."

#### Validation

- Developers can catch cross-layer regressions locally without running the full suite.

#### Expected Impact

Medium-to-high leverage with relatively low effort.

## P3 - Separate Dev And Prod Paths Cleanly

### 13. Split Development And Production Packaging For The PHP App

#### Problem

The root `Dockerfile` is explicitly described as a development image using PHP's built-in server, but deployment scripts still build and push it for the app.

#### Why This Matters

- Dev and prod concerns are mixed in a way that creates operational ambiguity.
- A production image should not depend on assumptions that are acceptable only in local development.

#### Recommended Direction

Create a dedicated production packaging path for the app and stop presenting the dev image as deployment-grade.

#### Detailed Tasks

- [ ] Introduce a production Dockerfile for the app.
- [ ] Choose a production-grade app server model and document it clearly.
- [ ] Keep the current developer-friendly path for local iteration if it is still useful.
- [ ] Update deploy scripts so they build the production image, not the dev one.
- [ ] Update docs to distinguish local dev image behavior from production image behavior.

#### Validation

- Deployment scripts and Dockerfiles clearly distinguish dev and prod.
- Production packaging no longer depends on a dev-only comment being ignored.

#### Expected Impact

High operational clarity impact, moderate effort.

### 14. Clean Up Docs And Script Drift

#### Problem

Multiple docs and scripts still contain inherited or stale assumptions, including Summit references, wrong ports, old route comments, outdated service names, and architecture statements that no longer match the code.

#### Examples

- README quick start points to `http://localhost:8080` while compose exposes `48082`.
- infra docs still describe inherited Summit concepts and outdated architecture.
- `config/packages/strands.yaml` comments mention `/roles/stream`.
- helper scripts and workflow examples still reference `ambient-scribe-agent` as the service name in places where the app service is the public target.

#### Why This Matters

- Drifted docs make maintenance harder and inflate onboarding time.
- Drifted scripts are operational landmines because they look authoritative.

#### Recommended Direction

Do a targeted accuracy pass focused on "things someone would actually copy-paste or trust."

#### Detailed Tasks

- [ ] Fix README quick-start ports and commands.
- [ ] Rewrite the top sections of deployment and infrastructure docs so they describe the current system, not inherited history.
- [ ] Update local development docs to remove outdated "sync mode" or optional-Mercure language if no longer true.
- [ ] Audit comments in config files for stale endpoint references.
- [ ] Audit helper scripts for stale service names and URLs.
- [ ] Add a "last verified" note to key operational docs if that helps ownership.

#### Validation

- The first page of docs and the common scripts match the running system.
- A new engineer following README and local-dev docs lands on the correct ports and services.

#### Expected Impact

Medium-to-high onboarding and maintenance impact.

### 15. Reduce Hidden Compatibility Aliases And Legacy Names

#### Problem

There are still signs of compatibility aliasing and inherited naming that make the system harder to reason about.

#### Examples

- legacy provider aliases in startup scripts
- inherited comments and variable defaults from earlier projects
- helper scripts with mismatched service assumptions

#### Recommended Direction

Keep compatibility shims only where they materially reduce migration pain, and document their retirement path.

#### Detailed Tasks

- [ ] Inventory compatibility aliases and legacy names still used by scripts and env handling.
- [ ] Decide which ones remain intentional and which ones should be removed.
- [ ] Add deprecation warnings for aliases that should eventually disappear.
- [ ] Remove dead names once the remaining scripts and docs are aligned.

#### Validation

- Runtime configuration becomes easier to reason about because there are fewer hidden alternate names.

#### Expected Impact

Medium maintainability gain.

## P4 - Longer-Term Structural Improvements

### 16. Add Better Operational Observability Around Cross-Layer Failures

#### Problem

The project logs a fair amount already, but it could do more to surface cross-layer failures in a way that accelerates debugging in staging or production.

#### Recommended Direction

Make the highest-risk contracts observable by default.

#### Detailed Tasks

- [ ] Log the resolved public health route and Mercure configuration mode at startup.
- [ ] Log whether replay and summary are enabled on the browser-facing origin strategy.
- [ ] Add structured logs when role override requests are rejected due to invalid roles.
- [ ] Add metrics or counters for:
- [ ] Mercure publish failures
- [ ] reconnect-success versus reconnect-expired
- [ ] summary success versus summary failure
- [ ] replay success versus replay failure

#### Validation

- Common runtime failures are visible in logs or dashboards without code spelunking.

#### Expected Impact

Medium operational benefit.

### 17. Consider A Narrow Architecture Boundary Pass

#### Problem

The repo has a workable split between PHP, Python, and browser responsibilities, but the exact boundary is not consistently enforced.

#### Recommended Direction

After the high-priority correctness fixes land, do a narrow architecture cleanup focused on boundary clarity rather than broad rewrites.

#### Candidate Questions

- Should Symfony proxy every browser HTTP action except audio WebSocket and Mercure subscription?
- Should FastAPI remain fully internal except for WebSocket audio and Mercure publish?
- Should the browser know one API base or two?
- Which layer owns validation for mode names and role enums?

#### Detailed Tasks

- [ ] Write down the intended boundary for browser, Symfony, FastAPI, Mercure, and Terraform.
- [ ] Remove any accidental exceptions to that boundary.
- [ ] Keep the resulting design simple enough that new contributors can predict route ownership without hunting through code.

#### Validation

- Layer responsibilities are easy to explain in one page and easy to verify in code.

#### Expected Impact

Medium-to-high long-term maintainability benefit.

## Suggested Milestone Packaging

### Milestone A - Correctness First

- [ ] Item 1: unify browser HTTP route ownership
- [ ] Item 2: align Terraform and runtime env contract
- [ ] Item 3: unify health checks and deploy verification
- [ ] Item 9: fix Python test teardown for API/replay/summary

### Milestone B - State And Semantics

- [ ] Item 5: explicit session state machine
- [ ] Item 6: normalize role vocabulary
- [ ] Item 7: clarify replay and summary lifecycle

### Milestone C - Verification And Drift Prevention

- [ ] Item 8: machine-readable contract
- [ ] Item 10: real CI workflow
- [ ] Item 11: expanded E2E coverage
- [ ] Item 12: stronger preflight checks

### Milestone D - Operational Cleanup

- [ ] Item 13: separate dev and prod packaging
- [ ] Item 14: clean up docs and scripts
- [ ] Item 15: reduce hidden aliases and legacy names
- [ ] Item 16: better observability

## Definition Of Done For This Backlog

- The main browser features work from the intended origin without hidden proxies.
- Production deploy checks the right service and the right health path.
- Runtime env vars in Terraform and code match.
- Reconnect behavior is genuinely graceful and test-proven.
- Role behavior is consistent across all supported modes.
- Python endpoint tests terminate cleanly and run in CI.
- Dev docs, prod docs, deploy scripts, and helper scripts describe the same system.
- At least one machine-checkable contract validator exists to prevent recurrence of the current drift pattern.
