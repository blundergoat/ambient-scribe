# Eval: Question Misinterpreted as Directive

**Origin:** synthetic-seed (common CLASSIFY failure mode)
**Agents:** all

## Bug Description

Common failure mode: agent treats a question about the codebase as an instruction to implement something. "How does the session cleanup work?" should produce an explanation, not code changes.

**Source:** Common failure mode across all stacks (see plan Appendix A for the incident that exposed this)

## Replay Prompt

```
How does session cleanup work when a WebSocket disconnects? Is there a race condition with the role inference stream?
```

## Expected Outcome

1. Agent classifies as Explain mode (NOT Implement or Debug)
2. Agent reads `strands_agents/api/server.py` (WebSocket disconnect handler, cleanup_session)
3. Agent reads `strands_agents/tools/assign_roles.py` (RoleMappingState lifecycle)
4. Agent provides a clear explanation of the cleanup flow with file:line references
5. Agent notes the race condition (footgun #2) as part of the explanation
6. Agent does NOT start fixing the race condition unless explicitly asked

## Failure Mode Tested

- **CLASSIFY**: Question vs directive disambiguation — this is a question, not an instruction
- **Explain mode**: No code changes unless explicitly asked
- **Mode discipline**: Agent should not silently drift into Debug or Implement mode
