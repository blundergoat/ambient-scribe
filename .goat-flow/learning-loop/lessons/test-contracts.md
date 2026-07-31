---
category: test-contracts
last_reviewed: 2026-07-26
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

**Follow-up (2026-07-26, 0.5.2 allocation diagnostics):** A classifier golden expected one duplicate-allocation insertion while its reference omitted both repeated novel words, so standard S/I/D correctly produced two insertions. Before freezing a class count, derive the expected edit path from the exact reference/hypothesis pair (or make one copy reference-owned), then assert the classifier result.

**Follow-up (2026-07-26, 0.5.2 insertion classification):** Preflight assumed the selected fixture manifest record declared `duration_seconds`; it did not, and the read-only probe failed before any replay. Enumerate the selected record's keys before field assertions, and derive duration from the byte-bound WAV only when the manifest does not declare an authoritative duration.
