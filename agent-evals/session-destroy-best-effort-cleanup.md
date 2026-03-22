# Session Destroy Best Effort Cleanup

**Origin:** real-incident (commit `4a3046e`)
**Agents:** all

## Bug Description

A timeout while acquiring the session destroy lock could leave role state or background cleanup incomplete unless the timeout path still performed best-effort teardown.

**Related:** commit `4a3046e`

## Replay Prompt

```
Review strands_agents/session_lifecycle.py. If destroy hits a lock timeout, role state and worker cleanup must still happen best-effort. Make the smallest safe fix and describe the verification.
```

## Expected Outcome

1. Agent reads `strands_agents/session_lifecycle.py` and its tests
2. Agent preserves best-effort cleanup in the timeout path
3. Agent avoids rewriting session lifecycle ownership (bounded scope)
4. Agent mentions the focused pytest that exercises the timeout case

## Failure Mode Tested

- **Bounded scope**: Cross-component cleanup fix without over-engineering
- **VERIFY**: Agent describes targeted verification for the timeout path
