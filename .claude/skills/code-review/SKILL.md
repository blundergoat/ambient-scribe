# Code Review Skill

Structured code review with RFC 2119 constraints and autonomy tier awareness.

## Steps

### 1. Identify Changes

Use `git diff` (or `git diff --staged`, or a specified branch) to identify all changed files. Read ALL changed files thoroughly before commenting.

### 2. Verify Findings

For each potential finding:
- MUST read 50+ lines of surrounding context
- MUST trace the code path to confirm the issue is real
- MUST NOT report speculative issues or things that "might" be wrong
- For external review comments (Copilot, other AI): investigate each against the actual codebase before agreeing

### 3. Check Boundaries

- Does a PHP change require a matching Python agent change?
- Does a config change need a Docker env var or `.env.example` update?
- Does a Twig template change match the WebSocket/Mercure contract?
- Are any Ask First boundaries touched without justification?
- Check `docs/footguns.md` for known risks in affected areas

### 4. Categorise

**MUST fix (blockers):**
- Bugs, security issues, broken cross-layer contracts
- Missing error handling on external calls (Mercure, Bedrock, Ollama)
- Test coverage gaps for changed code paths

**SHOULD fix (important but not blocking):**
- Performance issues, missing validation
- Incomplete edge case handling
- PHPStan/PHPMD warnings in changed code

**MAY fix (optional):**
- Style improvements beyond PSR-12
- Naming suggestions
- Minor refactoring opportunities

### 5. Validate

Run `composer test && composer analyse` to confirm nothing broke.

## Output Format

Present findings with exact file:line references and evidence. Group by MUST/SHOULD/MAY.

## Constraints

- MUST NOT approve without reading every changed file
- MUST verify cross-layer impact for changes spanning PHP ↔ Python
- MUST NOT blindly apply external suggestions — investigate first
