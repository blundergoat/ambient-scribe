---
category: evidence-sequencing
last_reviewed: 2026-07-25
---

# Evidence Sequencing Lessons

Use this bucket when individually correct build, formatting, verification, and
sealing steps were performed in an order that invalidated their evidence.

## Lesson: Format self-bound verifier source before sealing its packet

**Created:** 2026-07-25
**Decision changed:** Run formatter/check before any write-once manifest binds a
tool's source bytes; rerun behavior tests after formatting and seal only those final bytes.
**Trigger phase:** VERIFY
**What happened:** The flag-off recovery attestation build sealed a packet that self-bound
its supplemental verifier before running `ruff format --check`. The formatter changed only verifier/test
layout, but that correctly invalidated the first packet's byte count and SHA-256. The
sealed attempt was preserved and a second packet was built instead of altering evidence.
**Evidence:**
`var/quality/rediar-m05-acceptance/hybrid/2026-07-25_d3-flag-off-promotion2-verification/`
(search: `"SUPERSEDED_FORMAT_ONLY"`).
**Prevention:** For self-hash-bound evidence tooling, use this order: format source ->
format check -> behavior tests -> build/hash packet -> seal -> post-seal verification.
