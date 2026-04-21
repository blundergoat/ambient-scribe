# Ambient Scribe GEMINI — v1.0 (2026-03-21)
Gemini CLI runtime workflow for this repo. Domain context lives in `docs/domain-reference.md`, system shape in `docs/architecture.md`, and shared engineering practice in `.github/instructions/ai-agent-guidelines.instructions.md`.
Gemini CLI has no hooks or native profiles here; safety relies on these rules plus `./scripts/preflight-checks.sh`.
## Default Loop: READ → CLASSIFY → SCOPE → ACT → VERIFY → LOG
### READ
- MUST read every file you change before proposing or editing.
- MUST read both sides first for PHP <-> Python, Twig/public JS <-> WebSocket/Mercure, or Docker/env work.
- MUST NOT invent repo facts; say so when you have not read something.
### CLASSIFY
- MUST declare `Mode=<Explain|Plan|Implement|Debug|Review> | Complexity=<Hotfix|Standard|System|Infra>`.
- Budgets: Hotfix (2 reads / 3 turns), Standard (4 / 10), System (6 / 20), Infra (8 / 25). Over budget REQUIRES re-classification.
### SCOPE
- MUST declare files to change, non-goals, and blast radius before acting.
- MUST stop and re-scope if the work expands beyond that boundary.
### ACT
| Mode | Default behaviour |
| --- | --- |
| Explain | Walkthrough only; no edits unless asked. |
| Plan | Produce a concrete plan, then stop. |
| Implement | Read enough to act, then edit within 2-3 substantive steps. |
| Debug | Diagnosis first with file:line evidence. No fixes until human reviews diagnosis. |
| Review | Findings first, ordered by severity, with file:line evidence. |
- State: [MODE] | Goal: [one line] | Exit: [condition]
- MUST avoid planning loops; in Implement mode, 4th substantive read without writing means start coding or report blocker.
### VERIFY
- MUST run focused checks after each change, then broader checks before done.
- MUST use revert-and-rescope when the current path is wrong. Stop after two failed approaches on same fix.
- MUST run `rg` after renames or contract edits to confirm old pattern is gone.
### LOG
- Update `.goat-flow/lessons/`, `.goat-flow/footguns/`, or `.goat-flow/decisions/` when tripped (DoD gate #4).
- Multi-agent project: Learning loop directories are shared with `AGENTS.md` (Codex) and `CLAUDE.md` (Claude). Read existing bucket files before appending.
## Autonomy Tiers
### Always
- Read, search, diff, run focused tests, and update docs/tests required by the change.
### Ask First
- PHP <-> Python API contract changes, WebSocket/Mercure topic changes.
- GPU/NeMo loading or concurrency changes, audio format changes.
- New dependencies, public route changes, Terraform/deployment edits.
### Never
- Delete or weaken tests. Edit `.env` or secrets directly.
- Commit or push without explicit instruction. Run unscoped `rm -rf`.
## Definition of Done
1. Relevant tests pass, or failure is explained.
2. `scripts/preflight-checks.sh` passes after meaningful changes.
3. No Ask First boundary was changed without approval.
4. `.goat-flow/lessons/` or `.goat-flow/footguns/` updated if you tripped a behavioral or architectural issue.
5. `.goat-flow/logs/sessions/` reflects current state when work spans sessions.
## Working Memory
- 5+ turn tasks SHOULD keep `.goat-flow/logs/sessions/YYYY-MM-DD-<slug>.md` current.
- Incomplete work MUST update the active session file (see `tasks/handoff-template.md` for the shape).
## Sub-Agent Objectives
Sub-agents MUST get one focused objective and return paths, evidence, and next step. Budget: 5 calls.
## Communication When Blocked
Ask one question and include the recommended default path.
## Router
| Need | File |
| --- | --- |
| System map | `docs/architecture.md` |
| Project structure / Technical ref | `docs/domain-reference.md` |
| Shared engineering practice | `.github/instructions/ai-agent-guidelines.instructions.md` |
| Ownership split report | `docs/guidelines-ownership-split.md` |
| Learning Loop | `.goat-flow/lessons/`, `.goat-flow/footguns/`, `.goat-flow/decisions/` |
| GOAT Skills | `.gemini/skills/` |
| Codex Agent (Codex) | `AGENTS.md` |
| Claude Agent (Claude Code) | `CLAUDE.md` |
## Essential Commands
```bash
cp .env.example .env && ./scripts/start-dev.sh
composer test && strands_agents/.venv/bin/pytest tests/python/ -q
./scripts/preflight-checks.sh
```
