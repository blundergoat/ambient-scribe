# Codebase Audit Skill

Multi-pass audit with self-verification. Every finding must have evidence.

## Arguments

The user will specify the audit focus: bugs, security, performance, or architecture.

## Steps

### Pass 1 - Discovery

Search and read every relevant file across both PHP and Python layers. Log potential issues with exact file:line references. Cast a wide net.

### Pass 2 - Verification

For EACH finding from Pass 1:
- Re-read the surrounding code (50+ lines of context)
- Trace the code path end-to-end (PHP controller → orchestrator → Python agent → LLM)
- Confirm the issue is real, not a false positive
- Remove any finding you cannot verify by reading code

### Pass 3 - Prioritization

Rate verified issues by severity:
- **Critical**: Data loss, security vulnerability, broken production functionality
- **High**: Silent failures, missing error handling, contract mismatches between layers
- **Medium**: Performance issues, missing validation, incomplete edge case handling
- **Low**: Code quality, naming, minor improvements

### Pass 4 - Self-check

Ask yourself:
- Did I fabricate any details or assume without reading the actual code?
- Did I verify each finding against both PHP and Python layers where applicable?
- Would this finding survive scrutiny if someone re-read the code?

Remove anything uncertain. Present final verified list with exact file:line citations and evidence.
