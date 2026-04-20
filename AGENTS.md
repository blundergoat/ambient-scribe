# Ambient Scribe AGENTS — v1.0 (2026-03-21)
Codex runtime workflow for this repo. Domain context lives in `docs/domain-reference.md`, system shape in `docs/architecture.md`, and shared engineering practice in `.github/instructions/ai-agent-guidelines.instructions.md`.
Codex has no hooks or native profiles here; safety relies on these rules plus `./scripts/context-validate.sh` and `./scripts/deny-dangerous.sh`. Prefer repo helpers such as `./scripts/start-dev.sh` over raw `docker compose` unless the user asks otherwise.
## Default Loop: READ → CLASSIFY → SCOPE → ACT → VERIFY → LOG
### READ
- MUST read every file you change before proposing or editing.
- MUST read both sides first for PHP <-> Python, Twig/public JS <-> WebSocket/Mercure, Docker/env, or infra <-> runtime work.
- MUST NOT invent repo facts; say so when you have not read something.
BAD: "The browser sends WebM/Opus" without reading the frontend.
GOOD: Read `public/js/scribe.js` and `strands_agents/nemo_session.py`, then say the live path sends PCM and defaults to `NEMO_STREAM_INPUT_FORMAT=pcm`.
### CLASSIFY
- MUST declare `Mode=<Explain|Plan|Implement|Debug|Review> | Complexity=<Hotfix|Standard|System|Infra> | Boundary=<...>`.
- Complexity budgets: Hotfix (2 reads / 3 turns), Standard (4 / 10), System (6 / 20), Infra (8 / 25); over budget REQUIRES re-classification.
- Questions get answers, directives get action, and ambiguous asks get one clarifying question with a default.
BAD: User asked "explain the cleanup flow" -> edited `session_lifecycle.py`.
GOOD: User asked "explain the cleanup flow" -> answered with file:line evidence and no edits.
### SCOPE
- MUST declare files to change, non-goals, and blast radius before acting.
- MUST stop and re-scope with the user if the work expands beyond that boundary.
### ACT
| Mode | Default behaviour |
| --- | --- |
| Explain | Walkthrough only; no edits unless asked. |
| Plan | Produce a concrete plan, then stop. |
| Implement | Read enough to act, then edit within the next 2-3 substantive steps. |
| Debug | Diagnosis first with file:line evidence. No fixes until human reviews diagnosis. |
| Review | Findings first, ordered by severity, with file:line evidence; no edits unless asked. |
- State: [MODE] | Goal: [one line] | Exit: [condition]
- Switching to [NEW STATE] because [reason].
- MUST avoid planning loops; in Implement mode, the fourth substantive read without writing means start coding or report the blocker.
- MUST prefer the thinnest vertical slice.
BAD: "I rewrote the streaming stack before confirming the contract."
GOOD: "I patched the failing contract, added the narrow test, and stopped."
### VERIFY
- MUST run focused checks after each meaningful change, then broader checks before done.
- Level 1: note and continue on isolated unrelated failures, flaky tests, or non-blocking lint noise.
- Level 2: stop and report on cross-boundary, security, deployment, runtime, or contract failures.
- MUST use revert-and-rescope when the current path is wrong, and MUST stop after two failed approaches on the same fix path.
- MUST run `rg` after renames or contract edits to confirm the old symbol, route, or topic is gone or intentionally retained.
### LOG
| Directory | Use when |
| --- | --- |
| `.goat-flow/lessons/` | agent behaviour caused the miss |
| `.goat-flow/footguns/` | a cross-domain landmine needs file:line evidence |
| `.goat-flow/logs/sessions/` | navigation or ownership slowed the task; session handoff |
- MUST update when tripped (DoD gate #4). SHOULD log after routine sessions.
- If VERIFY caught a failure in code you wrote this session, or you corrected course mid-task, a `.goat-flow/lessons/` entry is required before DoD can be satisfied.
- After human correction of agent behaviour, MUST log the lesson immediately.
- Footgun propagation: SHOULD propagate active footguns to the nearest routed instruction doc.
- MUST load `.goat-flow/lessons/` for features/refactors and `.goat-flow/footguns/` before Ask First work.
- Dual-agent projects: learning loop directories are shared. Read existing bucket files before appending.
## Autonomy Tiers
### Always
- MUST read, search, diff, run focused tests, run `./scripts/context-validate.sh`, and update docs/tests that are directly required by the change.
- Make small, local fixes inside one boundary when the requested behaviour is clear.
### Ask First
- PHP <-> Python API contract changes in `src/Controller/`, `src/Service/`, `strands_agents/api/server.py`, or frontend event payloads.
- WebSocket, Mercure topic, or browser-facing URL changes in `.env.example`, `config/packages/`, `docker-compose.yml`, or `templates/scribe/index.html.twig`.
- Audio capture or `NEMO_STREAM_INPUT_FORMAT` changes across the browser, env, and `strands_agents/nemo_session.py`.
- GPU/NeMo loading or concurrency changes in `strands_agents/nemo_pipeline.py`, `strands_agents/api/server.py`, or `docker-compose.yml`.
- Role-agent provider/model changes, new dependencies, public route changes, Terraform/deployment edits, or production-facing config.
Ask First checklist:
1. Boundary touched: [name]
2. Related code read: [yes/no]
3. Footgun entry checked: [relevant entry, or "none"]
4. Local instruction checked: [.github/instructions/<file> / CLAUDE.md / none]
5. Rollback command: [exact command]
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
4. `.goat-flow/lessons/` or `.goat-flow/footguns/` is updated if you tripped a behavioural or architectural issue.
5. `.goat-flow/logs/sessions/` reflects the current state when work spans sessions.
6. After renames or contract edits, `rg` confirms the old pattern is gone or intentionally retained.
## Working Memory
- 5+ turn tasks SHOULD keep `.goat-flow/logs/sessions/YYYY-MM-DD-<slug>.md` current.
- Context ladder: summarize current state, trim it into the session log, then split the task if context still grows.
- Codex has no native profiles; use lanes: App (`src/`, `templates/`, `public/js/`), Agent (`strands_agents/`), Infra (`docker-compose.yml`, `infra/`); crossing lanes into Ask First work REQUIRES re-scope.
- Incomplete work MUST update the active `.goat-flow/logs/sessions/` file, and shared notes MUST be read before appending.
## Sub-Agent Objectives
Sub-agents MUST get one focused objective and MUST return paths, evidence, confidence, and next step. Budget: 5 calls.
## Communication When Blocked
When blocked, ask one question and include the recommended default path.
## Router
| Need | File |
| --- | --- |
| System map | `docs/architecture.md` |
| Project structure and conventions | `docs/domain-reference.md` |
| Shared engineering practice | `.github/instructions/ai-agent-guidelines.instructions.md` |
| Guidelines ownership split | `docs/guidelines-ownership-split.md` |
| Behavioural lessons | `.goat-flow/lessons/` |
| Cross-domain landmines | `.goat-flow/footguns/` |
| Architectural decisions | `.goat-flow/decisions/` |
| Session continuity | `.goat-flow/logs/sessions/` |
| Session handoff | `tasks/handoff-template.md` |
| Preflight playbook | `docs/codex-playbooks/goat-preflight.md` |
| Research playbook | `docs/codex-playbooks/goat-research.md` |
| Debug playbook | `docs/codex-playbooks/goat-debug.md` |
| Audit playbook | `docs/codex-playbooks/goat-audit.md` |
| Review playbook | `docs/codex-playbooks/goat-review.md` |
| Eval suite | `agent-evals/` |
| Workflow validation | `scripts/context-validate.sh` |
| Dangerous-command policy | `.claude/hooks/deny-dangerous.sh` |
| Claude Code workflow | `CLAUDE.md` |
| Claude Code evals | `agent-evals/` |
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
