---
category: hooks
last_reviewed: 2026-07-05
---

# Hook Footguns

## Footgun: Hook version drift blocks audit independently of harness checks

**Status:** active | **Created:** 2026-07-05 | **Evidence:** OBSERVED

**Symptoms:** The Claude harness check can pass while the overall audit fails on `hook-version` or `agent-guardrails`.

- **Files:** `.goat-flow/hooks/deny-dangerous.sh` (search: "goat-flow-hook-version")
- **Files:** `node_modules/@blundergoat/goat-flow/workflow/hooks/deny-dangerous.sh` (search: "goat-flow-hook-version")
- **What breaks:** Hook files are compared against the installed goat-flow release. If local hooks are from an older release, the audit reports setup or guardrail failure even when the requested harness concern is already green.
- **Evidence:** `goat-flow audit . --agent claude --harness` reported `hook-version` and `agent-guardrails` failures while `feedback-loop-active` stayed `pass`.
- **Prevention:** Treat hook sync as a separate scope from feedback-loop reference repair. Run the exact audit-provided hook sync/install command only after approving the broader hook-file change.
