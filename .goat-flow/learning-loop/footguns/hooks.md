---
category: hooks
last_reviewed: 2026-07-07
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

## Footgun: `goat-flow hooks sync --check` still writes agent hook config

**Status:** active | **Created:** 2026-07-05 | **Evidence:** OBSERVED

**Symptoms:** A reporting-only quality assessment can accidentally modify tracked hook config even when the command name includes `--check`.

- **Files:** `node_modules/@blundergoat/goat-flow/dist/cli/cli-parser.js` (search: "parseHooksPositionals")
- **Files:** `.goat-flow/config.yaml` (search: "gruff-code-quality:")
- **Files:** `.agents/hooks.json` (search: "deny-dangerous")
- **What breaks:** `goat-flow hooks sync --check` is parsed as a sync invocation, exits 0, and can add missing agent hook registrations such as `gruff-code-quality` to tracked hook config. That violates reporting-only/no-tracked-write assessment contracts.
- **Evidence:** During the 2026-07-05 Codex quality assessment, `goat-flow hooks sync --check` added a `gruff-code-quality` PostToolUse block to `.agents/hooks.json`; the accidental diff was inspected and reverted immediately.
- **Prevention:** Do not use `goat-flow hooks sync --check` as a read-only status probe. Use `goat-flow hooks list` and `goat-flow audit . --harness --agent <agent>` for reporting-only checks; run `goat-flow hooks sync` only when hook-config writes are in scope.
