# Session Destroy Best Effort Cleanup

**Origin:** real-incident (commit `4a3046e`)
**Agents:** all

- Bug description: A timeout while acquiring the session destroy lock could leave role state or background cleanup incomplete unless the timeout path still performed best-effort teardown.
- Replay prompt: Review `strands_agents/session_lifecycle.py`. If destroy hits a lock timeout, role state and worker cleanup must still happen best-effort. Make the smallest safe fix and describe the verification.
- Expected outcome: Preserve best-effort cleanup in the timeout path, avoid rewriting session lifecycle ownership, and mention the focused pytest that exercises the timeout case.
- Failure mode tested: Cross-component cleanup fix with bounded scope.
