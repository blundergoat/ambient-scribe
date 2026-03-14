# CLAUDE.md — v1.1 (2026-03-14)

Ambient Scribe — real-time medical transcription (PHP/Symfony + Python/FastAPI + NeMo GPU + Mercure SSE).

## Essential Commands

```bash
composer preflight           # All quality gates (tests + lint + analysis + coverage)
composer test                # PHPUnit | composer analyse  # PHPStan L10
composer cs:fix              # PHP-CS-Fixer PSR-12
pytest tests/python/         # Python tests (run from strands_agents/)
docker compose up --build    # Full stack (requires NVIDIA GPU)
```

## Execution Loop: READ → CLASSIFY → ACT → VERIFY → LOG

### READ

MUST read relevant files before acting. For cross-layer changes, MUST read BOTH sides.

```
❌ "The Python agent uses Flask" (guessed without reading api/server.py)
✅ Read api/server.py → "FastAPI with WebSocket + ThreadPoolExecutor"
```

### CLASSIFY

MUST declare complexity (Hotfix / Standard / System / Infra) and mode before acting.

| Mode | Behaviour |
|------|-----------|
| Plan | Produce artefact, no app code. Exit on LGTM |
| Implement | Code in 2-3 turns. 4th file read without writing = stop exploring |
| Explain | Walkthrough only. No code changes unless asked |
| Debug | Diagnosis with file:line first. Fixes only after human reviews |
| Review | Investigate independently. Never blindly apply suggestions |

MUST distinguish questions from directives: questions get answers, NOT implementations.
MUST declare: `State: [MODE] | Goal: [one line] | Exit: [condition]`

```
❌ Created INotificationProvider interface (only one implementation)
✅ Concrete class handles it. Extract interface when second provider needed.
```

### VERIFY

MUST run tests after each meaningful change. MUST run `composer preflight` before reporting done.

- **Level 1** (isolated failure): Note, continue with caution
- **Level 2** (cross-boundary: PHP↔Python, Mercure, auth, Terraform): MUST full stop → diagnosis with file:line → wait for human

Two corrections on same approach = MUST cut losses (rewind / git revert).

### LOG

| File | When |
|------|------|
| `docs/lessons.md` | Agent behavioural mistake |
| `docs/footguns.md` | Cross-domain architectural landmine |
| `docs/confusion-log.md` | Structural navigation difficulty |

SHOULD load contextually (lessons before features, footguns before Ask First boundaries).
SHOULD propagate directory-specific footguns to local CLAUDE.md files.

## Autonomy Tiers

**Always** (no confirmation): Run tests/lint/format. Read any file. Write within scope. Append to log files.

**Ask First** (MUST pause + micro-checklist: boundary, related code read, footgun checked, rollback command):
- PHP↔Python API contracts (Pydantic models, SSE events, WebSocket messages)
- NeMo pipeline (GPU singleton, ThreadPoolExecutor, model config)
- Mercure topics/JWT, Docker Compose services, env vars
- Terraform/deployment, Strands agent model provider, new dependencies

**Never**: Delete tests to pass builds. Modify .env/secrets. Push main. Commit unless asked.

## Definition of Done

MUST confirm ALL before reporting done:
1. Tests green (PHP + Python if cross-layer)
2. `composer preflight` passes
3. No unapproved cross-boundary changes
4. If tripped: logs updated
5. Working Notes current
6. After renames: grep old pattern → ZERO remaining

## Working Memory

SHOULD maintain Working Notes in `tasks/todo.md` for 5+ turn tasks.
SHOULD write handoff to `tasks/handoff.md` before ending incomplete work (template: `tasks/handoff-template.md`).
Context: `/compact` early → split if two compactions → `/clear` between unrelated tasks.

## Sub-Agent Objectives

One objective, structured return (paths + evidence + confidence + next step), 5-call budget.

## Communication When Blocked

MAY ask ONE question with recommended default and what changes per answer.

## Router Table

| Resource | Path |
|----------|------|
| Domain reference | `docs/domain-reference.md` |
| Architecture | `docs/architecture.md` |
| Lessons log | `docs/lessons.md` |
| Footguns index | `docs/footguns.md` |
| Confusion log | `docs/confusion-log.md` |
| ADRs | `docs/decisions/` |
| Handoff template | `tasks/handoff-template.md` |
| Skills | `.claude/skills/{preflight,debug-investigate,audit,research,code-review}/` |
| Agent evals | `agent-evals/` |
| Milestones | `milestones/` |
| NeMo API notes | `docs/nemo-api-notes.md` |
| Guidelines | `.github/instructions/ai-agent-guidelines.instructions.md` |
| Profiles | `.claude/profiles/` |
