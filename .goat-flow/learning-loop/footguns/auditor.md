---
category: auditor
last_reviewed: 2026-07-07
---

# Auditor Footguns

## Footgun: Harness provenance can lag behind consolidated learning-loop buckets

**Status:** active | **Created:** 2026-07-05 | **Evidence:** OBSERVED

**Symptoms:** A harness or dashboard quality finding can report stale footgun/lesson references even when the structural `feedback-loop-active` check passes.

- **Files:** `node_modules/@blundergoat/goat-flow/dist/cli/audit/harness/check-verification.js` (search: ".goat-flow/learning-loop/lessons/review-feedback.md")
- **Files:** `node_modules/@blundergoat/goat-flow/dist/cli/audit/check-agent-deny-mechanism.js` (search: ".goat-flow/learning-loop/footguns/auditor.md")
- **What breaks:** The audit runtime can emit provenance paths from older generic bucket names while the project has consolidated entries into different bucket files. A downstream harness or quality check that validates provenance paths can then flag stale local-path markup even though the live learning-loop entry counts are healthy.
- **Evidence:** The installed audit runtime still references legacy bucket paths for verification and guardrail provenance. Keep those buckets real with evidence-backed entries, or update the audit runtime provenance upstream.
