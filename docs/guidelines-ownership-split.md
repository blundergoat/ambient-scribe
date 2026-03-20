# Guidelines Ownership Split

Audit date: 2026-03-20 (updated from 2026-03-15)

## Scope

- Runtime workflow (execution loop, autonomy tiers, DoD) lives in `CLAUDE.md`.
- Shared engineering practice stays in `.github/instructions/ai-agent-guidelines.instructions.md`.
- Project structure and domain detail live in `docs/domain-reference.md` and `docs/architecture.md`.

## CLAUDE.md Migration

Moved out of the original CLAUDE.md:
- Project structure/module map -> `docs/domain-reference.md`
- Architecture/runtime notes -> `docs/architecture.md` and `docs/domain-reference.md`
- Domain-specific hard rules (GPU, ThreadPoolExecutor, session coupling) remain as Hard Constraints in CLAUDE.md

Kept in CLAUDE.md (GOAT Flow v1.0):
- Execution loop (READ → CLASSIFY → SCOPE → ACT → VERIFY → LOG)
- Autonomy tiers (Always / Ask First / Never)
- Definition of Done (6 gates)
- Working Memory + Sub-Agents + When Blocked
- Hard Constraints (project-specific safety rules)
- Essential commands
- Router table

## Guidelines Audit

Audited file: `.github/instructions/ai-agent-guidelines.instructions.md`

Sections kept in guidelines (cross-project engineering practice):
- Core Principles
- Project-Specific Constraints
- Engineering Practices
- Testing Conventions
- Git Hygiene
- Commit Messages

The guidelines intro references CLAUDE.md for workflow ownership. No content overlap with CLAUDE.md execution loop, DoD, or autonomy tiers.
