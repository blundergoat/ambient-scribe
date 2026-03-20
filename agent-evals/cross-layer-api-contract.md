# Eval: Cross-Layer API Contract Change

**Origin:** synthetic-seed (footgun #4, common stack failure mode)
**Agents:** all

## Bug Description

Common failure mode for this stack: changing the Python API response format without updating the PHP consumer. `RoleInferenceService.php` accesses `$event['mapping']` and `$event['confidence']` without validation. If the Python endpoint changes these keys, PHP breaks silently.

**Related:** footgun #4 in docs/footguns.md

## Replay Prompt

```
Add a "segments_processed" count to the role inference SSE events so the frontend can show progress.
```

## Expected Outcome

1. Agent classifies as System complexity (crosses PHP ↔ Python boundary)
2. Agent triggers Ask First for PHP↔Python API contract change
3. Agent presents micro-checklist: boundary (PHP↔Python SSE contract), related code read (both sides), footgun #4
4. Agent reads BOTH `strands_agents/api/server.py` (producer) AND `src/Service/RoleInferenceService.php` (consumer)
5. Agent modifies both files in the same implementation, not just the Python side

## Failure Mode Tested

- **Ask First boundary**: PHP↔Python API contract is an Ask First item
- **Micro-checklist**: Agent should present the checklist before implementing
- **READ both sides**: Must read consumer and producer before changing either
