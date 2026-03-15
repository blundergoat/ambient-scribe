# Codebase Audit Skill

Multi-pass audit with self-verification. Every finding MUST have file:line evidence.
MUST NOT propose fixes — the audit's job is to find issues, not solve them.

## Arguments

The user specifies the audit focus: bugs, security, performance, or architecture.

## Pass 1 — Discovery

Search and read every relevant file across both PHP and Python layers. Log potential issues with exact file:line references. Cast a wide net. Check `docs/footguns.md` for known architectural landmines in the target area.

## Pass 2 — Verification

For EACH finding from Pass 1:
- Re-read the surrounding code (50+ lines of context)
- Trace the code path end-to-end: Twig → PHP controller → Python agent → NeMo/Strands → Mercure
- Cross-check against both PHP and Python layers where applicable
- Confirm the issue is real, not a false positive
- Remove any finding you cannot verify by reading code

## Pass 3 — Prioritisation

Rate verified issues by severity and blast radius:
- **Critical**: Data loss, security vulnerability, broken production functionality
- **High**: Silent failures (Mercure publish, session cleanup), contract mismatches between layers
- **Medium**: Performance issues, missing validation, incomplete edge case handling
- **Low**: Code quality, naming, minor improvements

## Pass 4 — Self-Check (Fabrication Gate)

MUST answer each honestly:
- Did I fabricate any details or assume without reading the actual code?
- Did I verify each finding against the real file contents?
- Would this finding survive scrutiny if someone re-read the code right now?
- Is this a real issue or something that "might" be wrong?

Remove anything uncertain. Remove anything fabricated.

## Constraints

- MUST NOT propose fixes — findings only
- MUST include file:line evidence for every finding
- MUST remove findings that fail the Pass 4 self-check

## Output Format

Present final verified list grouped by severity, with exact file:line citations and evidence.
