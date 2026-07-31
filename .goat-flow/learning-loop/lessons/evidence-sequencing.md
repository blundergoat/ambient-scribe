---
category: evidence-sequencing
last_reviewed: 2026-07-31
---

# Evidence Sequencing Lessons

Use this bucket when individually correct build, formatting, verification, and
sealing steps were performed in an order that invalidated their evidence.

## Lesson: Capture container logs before the cleanup that recreates the container

**Created:** 2026-07-31
**Decision changed:** In any run wrapper that restores service state afterwards, dump
volatile evidence into the run folder before the restore step, not after.
**Trigger phase:** ACT

A baseline run wrapper recreated `nemo-agent` unconditionally as its final step to restore
`LOG_FORMAT=console`. The corpus run had failed on fixture three, and `docker logs` starts
empty on a recreated container, so the only record of why the session never finalized was
destroyed by the wrapper's own cleanup. Diagnosis then cost a second full container
recreation plus an isolated replay purely to regenerate logs that had already existed once.

The reproduction proved the session had been healthy - still streaming at chunk 116 of 133
with no error - and that the client's fixed 90-iteration `session.quality` poll had simply
expired while a `EVAL_PACE=fast` backlog drained. That answer was in the discarded logs.

**Prevention:** Order run wrappers as work, then capture, then restore - and capture
unconditionally, including on the failure paths, since a failed run is exactly when the
evidence matters. `docker logs <svc> > "$RUN_DIR/<svc>.log" 2>&1` before any
`docker compose up --force-recreate`. Cleanup that runs on failure must not be allowed to
consume the failure's own explanation. Related: [[audit-runners]] on restoring service state
after a bounded campaign.

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
a local-only verification artifact
(search: `"SUPERSEDED_FORMAT_ONLY"`).
**Prevention:** For self-hash-bound evidence tooling, use this order: format source ->
format check -> behavior tests -> build/hash packet -> seal -> post-seal verification.
