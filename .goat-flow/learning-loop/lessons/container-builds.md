---
category: container-builds
last_reviewed: 2026-07-18
---

# Container Build Lessons

Lessons about reproducible image derivation, builder cache evidence, and download boundaries.

## Lesson: A tagged image does not prove BuildKit layer-cache availability

**Created:** 2026-07-18
**What happened:** A unified-runtime upgrade had the exact 28.78 GB NeMo image tagged locally and serving healthy traffic, but its
network-disabled compatibility build executed `apt-get update` at step 2/10 instead of reaching the intended
exact Git layer. The packet's cache-miss kill prevented unbudgeted dependency and live-model downloads.
**Evidence:**
a local-only verification artifact.
**Prevention:** Treat runtime-image identity and builder-cache identity as separate proofs. If a zero-download
packet requires cached intermediate layers, run its network-disabled build gate before enabling network. On a
miss, either obtain a new explicit download budget with fully pinned inputs or derive an isolated candidate
from the exact frozen image under a separately approved target; never infer cache availability from an image
tag.
