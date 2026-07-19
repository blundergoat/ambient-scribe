---
category: test-contracts
last_reviewed: 2026-07-20
---

# Test Contract Lessons

Lessons from executable test metadata and clean-checkout contract gates.

## Lesson: Inventory manifest categories before narrowing a metadata assertion

**Created:** 2026-07-20
**What happened:** A clean-checkout test replacement assumed every retained artifact in the two
registered consultations used only the `live` or `corrected` lane. The first focused run failed because
the same manifest deliberately also registers `selected_source` and `saved_note` evidence.
**Evidence:** `tests/fixtures/audio/development-corpus-0.5.0.json` (search: `persisted_artifacts`) and
`tests/python/test_development_corpus.py` (search:
`test_registered_persisted_artifacts_have_reproducible_identities`).
**Prevention:** Query the complete category vector from tracked metadata before writing a narrowed
assertion. Pin the exact declared order when order is contractual; do not infer allowed categories from
the test name or the first records inspected.
