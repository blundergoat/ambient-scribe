---
category: test-contracts
last_reviewed: 2026-07-31
---

# Test Contract Lessons

Lessons from executable test metadata and clean-checkout contract gates.

## Lesson: For a hash-bound file there is no safe edit, including a comment

**Created:** 2026-07-31
**Decision changed:** Before editing any file under `scripts/`, `tests/`, or
`strands_agents/` — even to reword a comment — check whether a manifest pins its bytes.
**Trigger phase:** ACT
**Incident count:** 2
**Latest occurrence:** 2026-08-01

A documentation sweep reworded comments in `scripts/transcript-quality.py`. That file is the
frozen scorer, byte-bound in `tests/fixtures/audio/development-corpus-0.5.0.json` with an
explicit `bytes` and `sha256`. The comment edit changed its hash and failed two
`test_development_corpus.py` scorer-identity tests. The reasoning that caused it — "comments
cannot affect behaviour, so comment edits are safe" — is true of most files and false of every
file whose identity *is* its bytes.

The same sweep changed a user-facing error string in `scripts/second_pass_asr.py`; that exact
wording is asserted by
`tests/python/test_post_visit_phrase_accuracy.py::test_evaluator_rejects_unreviewed_phrase`.
A pre-check had grepped several candidate strings and missed this one.

**Prevention:** Two cheap checks before a bulk text pass over source:

```bash
rg -n '"path": "(scripts|tests|strands_agents)/' tests/fixtures/ | rg -v '\.wav|\.TextGrid'
rg -n "<the exact string you are about to change>" tests/
```

A hash-bound file is documentation-frozen as well as behaviour-frozen; leave it entirely and
record the exemption. For an asserted string, either leave it or change source and test in the
same commit. Run the full suite after each lane of a sweep, not once at the end — the failure
tells you which lane introduced it.

**Recurrence 2026-08-01, one day later, via a new route: re-litigating a recorded exemption.**
The same file was edited again — the same 8 comments, the same broken hash. The trigger was not
"comments are safe" this time. A verification sweep re-ran the milestone-ID grep, saw
`scripts/transcript-quality.py` with 8 hits, compared that against the plan's claim that every
remaining hit was exempt, and concluded the plan was wrong and the file had been missed. The
plan was right. Its exemption list named "hash-bound files" as a category; the file was simply
never traced to which category it fell under before the exemption was overridden.

Reversed with 8 explicit inverse edits after `git checkout --` was correctly refused as a
destructive command. Restored to `sha256 9cf8c3a1…0426` / 100,065 bytes, `git status` clean,
`test_development_corpus.py` 19 passed / 2 skipped. Nothing reached a commit.

**Prevention, second order:** finding a documented exemption suspicious is fine; acting on that
suspicion before identifying *which* exemption applies is not. An exemption list that records
categories but not per-file assignments will be re-litigated by the next reader — including a
reader who has the lesson in the same repository and does not grep for it. Before overriding any
recorded exemption, run the manifest check above on the specific file. It costs one command:

```bash
rg -n -A3 '"path": "<the file>"' tests/fixtures/
```

Recognising this trap requires reading the learning loop *during* the sweep, not after the
failure. Both incidents were preventable by the check that was already written down.

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
