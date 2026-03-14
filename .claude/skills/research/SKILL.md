# Research Skill

Deep codebase research before planning any non-trivial feature. Hard gate: do NOT proceed to planning until human reviews research output.

## When to Use

Before implementing any feature that:
- Crosses PHP ↔ Python ↔ Twig boundaries
- Touches NeMo pipeline, Mercure publishing, or session state
- Adds new API endpoints or WebSocket message types
- Modifies Terraform infrastructure or Docker services

## Steps

1. Read all files involved in the target area — both sides of every boundary
2. Check `docs/footguns.md` for known landmines in the affected area
3. Check `docs/architecture.md` and `docs/domain-reference.md` for design constraints
4. Produce `research.md` (or present inline) with the template below

## Output Template

```
## Research: [Feature/Area Name]

### Files Involved
- [file:line range] — [what this file does in the context of this feature]

### Request/Data Flow
[Trace the flow from trigger to result, naming every file touched]

### Boundaries Touched
- [ ] PHP ↔ Python API contract
- [ ] Browser ↔ WebSocket
- [ ] NeMo GPU pipeline
- [ ] Mercure publishing
- [ ] Docker/env config
- [ ] Terraform infrastructure

### Risks / Gotchas (minimum 3, with file:line evidence)
1. [risk] — [file:line] — [why this matters]
2. [risk] — [file:line] — [why this matters]
3. [risk] — [file:line] — [why this matters]

### Open Questions
- [anything that needs human input before planning]
```

## Constraints

- MUST produce research output before any planning or implementation
- MUST include minimum 3 risks with file:line evidence
- MUST NOT proceed to planning until human reviews research
- Every claim must be backed by a file read — do not fabricate
