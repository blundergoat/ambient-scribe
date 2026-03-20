# CLAUDE.md — v1.0 (2026-03-20)

Ambient scribe: audio → WebSocket → NeMo GPU → Mercure SSE. Symfony 6.4 (PHP) + FastAPI (Python) + NeMo + Mercure.

## Execution Loop: READ → CLASSIFY → SCOPE → ACT → VERIFY → LOG

**READ** — MUST read relevant files first. Cross-boundary changes MUST read both sides (PHP + Python + Twig).

```
BAD:  "WebSocket publishes to topic 'transcribe'" (fabricated)
GOOD: Read server.py:236 → publishes to 'scribe/session/{id}/raw'
```

**CLASSIFY** — Determine complexity and mode. Questions ≠ directives — if asked to explain, explain; don't implement.

Complexity: Hotfix (2 reads / 3 turns), Standard (4 / 10), System (6 / 20), Infra (8 / 25).

| Mode | Behaviour |
|---|---|
| Plan | Artefact only, no code. Exit on "LGTM" |
| Implement | Code in 2–3 turns. 4th read without writing = start coding |
| Explain | Walkthrough only, no changes |
| Debug | Diagnosis + file:line. No fixes until human reviews |
| Review | Investigate independently. Never blindly apply suggestions |

Anti-BDUF: `BAD: Created IProvider (one impl). GOOD: Notifier handles it. Extract when second needed.`

**SCOPE** — MUST declare before acting: files to change, non-goals, blast radius. Expanding scope = stop + re-scope.

**ACT** — `State: [MODE] | Goal: [one line] | Exit: [condition]`
No actions outside declared state. Mode switch: "Switching to [MODE] because [reason]."

**VERIFY** — MUST run tests after each meaningful change.
- Level 1 (note, continue): flaky test, unrelated failure, non-blocking lint warning
- Level 2 (full stop, escalate): auth, API contracts, session state, Mercure, NeMo, cross-boundary
- Revert-and-rescope: (1) Esc+restate (2) git revert+rescope (3) /clear+handoff. Two corrections = cut losses

**LOG** — MUST update when tripped (DoD gate #4). SHOULD propagate footguns to local CLAUDE.md.

| File | When |
|---|---|
| `docs/lessons.md` | Agent behavioural mistake |
| `docs/footguns.md` | Cross-domain landmine (MUST include file:line) |
| `docs/confusion-log.md` | Structural navigation difficulty |

## Autonomy Tiers

**Always:** run tests/lint/format, read any file, write within assigned scope, append to learning loop files.

**Ask First** (MUST complete micro-checklist before proceeding):
Auth, session lifecycle, API contracts (PHP↔Python), Mercure topics, NeMo pipeline, Docker/Terraform, CI/CD, new dirs.
- [ ] Boundary: [name] | Code read: [y/n] | Footgun: [entry/none] | Local CLAUDE.md: [warnings/none] | Rollback: [cmd]

**Never:** delete tests, modify .env/secrets, push main, chmod, commit unless asked, edit outside repo, modify lockfiles/generated code.

## Definition of Done

1. Relevant tests green
2. MUST-level preflight items pass
3. No cross-boundary change without Ask First
4. If tripped: lessons/footguns updated
5. Working Notes in `tasks/todo.md` current
6. After renames: grep old pattern, confirm zero remaining refs

## Working Memory

5+ turn tasks → Working Notes in `tasks/todo.md`. Escalation: /compact after 15 turns → 2 compactions = split sub-tasks → /clear between unrelated tasks. Incomplete work → write `tasks/handoff.md`.

## Sub-Agents

One focused objective per sub-agent. MUST return: paths, evidence, confidence, next step. Budget: 5 tool calls.

## When Blocked

One question with recommended default: "Stuck on X. I suggest Y — should I proceed, or Z?"

## Hard Constraints

- GPU exclusivity: NeMo owns GPU. Role inference MUST use Bedrock or CPU Ollama — never local GPU
- ThreadPoolExecutor: NeMo inference MUST use `run_in_executor`
- Session ID coupling: UUID flows PHP → Twig → JS → WS → Mercure. All layers MUST match

## Commands

```bash
./scripts/preflight-checks.sh   # All quality gates (MUST before done)
composer test                   # PHPUnit
composer analyse                # PHPStan Level 10
pytest tests/python/            # Python tests (from project root)
ruff check strands_agents/      # Python lint
docker compose up --build       # Full stack (requires NVIDIA GPU)
```

## Router

| Resource | Read when... |
|---|---|
| `.claude/skills/goat-preflight/` | Running quality checks |
| `.claude/skills/goat-debug/` | Debugging issues |
| `.claude/skills/goat-audit/` | Auditing codebase |
| `.claude/skills/goat-research/` | Pre-implementation research |
| `.claude/skills/goat-review/` | Reviewing code changes |
| `docs/architecture.md` | System design, data flows |
| `docs/footguns.md` | Cross-domain issues, CUDA, Mercure, sessions |
| `docs/lessons.md` | Past agent mistakes |
| `docs/confusion-log.md` | Navigation difficulty |
| `docs/domain-reference.md` | Domain knowledge |
| `docs/domain-php-symfony.md` | PHP: `src/`, `config/`, `templates/` |
| `docs/domain-python-nemo.md` | Python: `strands_agents/`, `tests/python/` |
| `docs/domain-infrastructure.md` | Docker, Terraform, deployment |
| `docs/nemo-api-notes.md` | NeMo API, VRAM measurements |
| `docs/code-map.md` | Entry points, file roles |
| `tasks/handoff-template.md` | Session handoff |
| `agent-evals/` | Regression tests |
| `.github/instructions/` | Per-language coding standards |
| `milestones/` | Task breakdowns M0–M4 |
