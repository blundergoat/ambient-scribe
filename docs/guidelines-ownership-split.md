# Guidelines Ownership Split

Audit date: 2026-03-21

## Scope

- `CLAUDE.md` owns the Claude Code runtime workflow.
- `AGENTS.md` owns the Codex runtime workflow.
- Shared engineering practice stays in `.github/instructions/ai-agent-guidelines.instructions.md`.
- Project structure and domain detail stay in `docs/domain-reference.md` and `docs/architecture.md`.

## Runtime Ownership

- Shared between `CLAUDE.md` and `AGENTS.md`: execution loop, autonomy tiers, definition of done, working-memory/handoff rules, learning-loop references, router tables, and essential commands.
- `CLAUDE.md` keeps Claude-specific mechanics such as local `CLAUDE.md` propagation and Claude eval routing.
- `AGENTS.md` keeps Codex-specific mechanics such as playbook files in `docs/codex-playbooks/`, `apply_patch`, and explicit acknowledgement that `scripts/deny-dangerous.sh` is policy verification rather than a runtime hook.

## Guidelines Audit

Audited file: `.github/instructions/ai-agent-guidelines.instructions.md`

Sections kept in guidelines:
- Core Rules
- Project-Specific Constraints
- Architecture
- Cross-Layer Impact
- Git Hygiene
- Testing Conventions
- Commit Messages

Sections trimmed or rewritten to avoid workflow overlap:
- Intro now points to both `CLAUDE.md` and `AGENTS.md` for runtime rules.
- Removed the `Working Discipline` section because stop-the-line, scope control, and delivery sequencing belong to the instruction files.
- Removed `Before Marking Done` because verification/DoD rules belong to the instruction files and preflight playbooks.
