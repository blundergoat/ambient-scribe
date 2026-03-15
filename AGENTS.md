# Ambient Scribe AGENTS

Codex runtime workflow for this repo. Project structure and conventions live in `docs/domain-reference.md`, system shape lives in `docs/architecture.md`, and shared engineering practice stays in `.github/instructions/ai-agent-guidelines.instructions.md`.

Use repo scripts for local stack work. Prefer `./scripts/start-dev.sh` and related helpers over raw `docker compose` unless the user explicitly asks otherwise.

## Default Loop

### READ
- Read the files you will change before proposing or editing.
- For cross-boundary work, read both sides first: PHP <-> Python contracts, Twig <-> WebSocket/Mercure wiring, Docker/env config, or infra <-> runtime.
- Never invent repo facts. If you have not read it, say so.

Bad: "The browser records WebM/Opus" without reading the current template.
Good: Read `templates/scribe/index.html.twig` first, then say "the live path uses `PcmStreamer` and the repo default is `NEMO_STREAM_INPUT_FORMAT=pcm`."

### CLASSIFY
- Start each task with a clear state declaration in your reasoning: `Mode=<Answer|Plan|Implement|Debug|Review> | Complexity=<Low|Medium|High>`.
- Questions get answers. Directives get action. Do not edit files when the user asked for explanation or review only.
- If the task crosses a named boundary, call it out before acting.

Example state: `Mode=Implement | Complexity=High | Boundary=PHP<->Python API`

### ACT

| Mode | Default behaviour |
| --- | --- |
| Answer | Explain clearly. No edits unless asked. |
| Plan | Produce a concrete plan, then stop. |
| Implement | Read enough to act, then start editing within the next 2-3 substantive steps. |
| Debug | Diagnose first with file:line evidence. No fixes until the diagnosis is clear. |
| Review | Findings first, ordered by severity, with file:line references. No code changes unless asked. |

- Anti-planning-loop: in Implement mode, stop reading once you have enough to change the file or explain the blocker.
- Anti-BDUF: prefer the thinnest vertical slice that proves the path.

Bad: "I rewrote the streaming stack before confirming the broken contract."
Good: "I patched the failing contract, added the narrow test, and stopped."

### VERIFY
- Run relevant tests after meaningful changes. Prefer focused checks first, then broader checks.
- After renames or contract edits, use `rg` to confirm the old symbol, route, or topic is gone or intentionally retained.
- Isolated failure: document it and continue if the task boundary is still clear.
- Cross-boundary, security, or runtime failure: stop, diagnose, and report before proceeding.
- Two failed approaches on the same fix path means stop and report the dead end.

### RECORD
- `docs/lessons.md`: behavioural mistakes or wasted loops.
- `docs/footguns.md`: real cross-domain landmines with evidence.
- Load `docs/lessons.md` when starting features/refactors or after a failed attempt.
- Load `docs/footguns.md` before touching Ask First boundaries.
- Keep `tasks/todo.md` current during multi-step work. If you pause unfinished work, update `tasks/handoff.md`.

## Autonomy Tiers

### Always
- Read, search, diff, run focused tests, run `./scripts/context-validate.sh`, and update docs/tests that are directly required by the change.
- Make small, local fixes inside one boundary when the requested behaviour is clear.

### Ask First
- PHP <-> Python API contract changes in `src/Controller/`, `src/Service/`, `strands_agents/api/server.py`, or frontend event payloads.
- WebSocket, Mercure topic, or browser-facing URL changes in `.env.example`, `config/packages/`, `docker-compose.yml`, or `templates/scribe/index.html.twig`.
- Audio capture or `NEMO_STREAM_INPUT_FORMAT` changes across the browser, env, and `strands_agents/nemo_session.py`.
- GPU/NeMo loading or concurrency changes in `strands_agents/nemo_pipeline.py`, `strands_agents/api/server.py`, or `docker-compose.yml`.
- Role-agent provider/model changes, new dependencies, public route changes, Terraform/deployment edits, or production-facing config.

Ask First checklist:
- Which boundary is changing?
- Which files on both sides did you read?
- What is the rollback path?
- What tests or scripts will prove it worked?

### Never
- Delete or weaken tests to make a failure disappear.
- Edit `.env`, secrets, or credentials files directly.
- Commit, amend, push, or run destructive git commands unless the user asked.
- Run unscoped `rm -rf`.
- Manually edit generated outputs such as `coverage.xml`, `coverage-html/`, or vendor-installed code.

## Definition of Done
1. Relevant tests/checks pass, or any unresolved failure is explicitly explained.
2. `./scripts/context-validate.sh` passes after workflow-file changes.
3. No Ask First boundary was changed without approval or a clear user instruction.
4. `docs/lessons.md` or `docs/footguns.md` is updated if you tripped a behavioural or architectural issue.
5. `tasks/todo.md` and `tasks/handoff.md` reflect the current state when work spans sessions.
6. After renames or contract edits, `rg` confirms the old pattern is gone or intentionally retained.

## Router

| Need | File |
| --- | --- |
| System map | `docs/architecture.md` |
| Project structure, environment, conventions | `docs/domain-reference.md` |
| Cross-domain landmines | `docs/footguns.md` |
| Behavioural lessons | `docs/lessons.md` |
| Guidelines ownership split | `docs/guidelines-ownership-split.md` |
| Preflight procedure | `docs/codex-playbooks/preflight.md` |
| Deep read before planning | `docs/codex-playbooks/research.md` |
| Diagnosis-first debugging | `docs/codex-playbooks/debug-investigate.md` |
| Audit workflow | `docs/codex-playbooks/audit.md` |
| Review workflow | `docs/codex-playbooks/code-review.md` |
| Eval suite | `codex-evals/README.md` |
| Workflow validation | `scripts/context-validate.sh` |
| Dangerous-command policy | `scripts/deny-dangerous.sh` |

## Essential Commands

```bash
cp .env.example .env
./scripts/start-dev.sh
./scripts/health-check-localdev.sh
./scripts/gpu-check.sh
composer test
vendor/bin/phpunit tests/Unit
python3 -m pytest tests/python -q
./scripts/preflight-checks.sh
./scripts/context-validate.sh
./scripts/api-load-test.sh -n 20 -c 5
```
