---
category: evidence-artifacts
last_reviewed: 2026-07-19
---

# Evidence Artifact Lessons

This bucket records mistakes that can corrupt an evidence bundle without changing its visible meaning.
Use it before duplicating hash-bound fixtures or sealing a write-once artifact manifest.

## Lesson: Reference byte-bound fixtures instead of text-patch copying them

**Created:** 2026-07-19
**What happened:** A manual-test evidence bundle copied two official TextGrid files through
`apply_patch`. The visible text survived, but newline normalization changed both byte sizes
and SHA-256 values. Verification rejected and removed the copies before the manifest was sealed.
**Evidence:** `var/quality/0.5.0-manual-consult53-20260718T231930Z/verification/truth-copy-normalization-failure.md`.
**Prevention:** When fixture identity is defined by bytes, keep the verified original path and
hash as the evidence reference. If a duplicate is required, use a byte-preserving approved
mechanism and verify size plus SHA-256 before any scorer or manifest consumes it.
