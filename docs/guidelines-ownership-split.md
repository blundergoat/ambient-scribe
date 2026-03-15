# Guidelines Ownership Split

Audit date: 2026-03-15

## Scope

- Runtime workflow now lives in `AGENTS.md`.
- Shared engineering practice stays in `.github/instructions/ai-agent-guidelines.instructions.md`.
- Project structure and domain detail live in `docs/domain-reference.md` and `docs/architecture.md`.

## Existing AGENTS Migration

Moved out of the old root `AGENTS.md`:
- Project structure/module map -> `docs/domain-reference.md`
- Architecture/runtime notes -> `docs/architecture.md` and `docs/domain-reference.md`

Kept in the new root `AGENTS.md`:
- Execution loop
- Autonomy tiers
- Definition of Done
- Router table
- Essential commands
- Repo-specific safety rules such as no direct `.env` edits and no raw `docker compose` by default

## Guidelines Audit

Audited file: `.github/instructions/ai-agent-guidelines.instructions.md`

Sections kept in guidelines because they are cross-project engineering practice:
- Core Principles
- Project-Specific Constraints
- Engineering Practices
- Testing Conventions
- Git Hygiene
- Commit Messages

Sections removed or rewritten before the ownership split:
1. Intro sentence pointing workflow ownership at `CLAUDE.md`
   - Before: "Workflow rules (execution loop, DoD, autonomy tiers, stop-the-line) live in CLAUDE.md."
   - After: "Runtime workflow rules ... live in AGENTS.md. This file owns shared engineering practice only."
   - Why: workflow ownership moved to Codex-native `AGENTS.md`, and the guidance file should not own runtime behaviour.

No broader section removal was needed because the existing guidelines file was already mostly engineering-only.
