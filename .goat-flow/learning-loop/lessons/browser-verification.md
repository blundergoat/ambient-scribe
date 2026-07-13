---
category: browser-verification
last_reviewed: 2026-07-14
---

# Browser verification lessons

## Lesson: Transcript card grouping needs arrival-order reproduction

**Created:** 2026-07-14
**What happened:** A manual consult showed adjacent Patient cards, making role relabeling or two
raw speaker IDs plausible causes. Saved history proved the fragments shared one speaker ID, and a
three-event browser repro with stable roles isolated the real branch: a delayed continuation at a
card boundary was always inserted as a new card.
**Evidence:** `var/quality/transcript-card-grouping-20260713T192500Z/diagnosis.md` and
`reproduce-card-split.js` retain the same-speaker rows, arrival order, and failing DOM shape.
**Prevention:** Reproduce transcript grouping with raw speaker IDs, spoken timestamps, and event
arrival order before changing role policy. Assert both visible card count and row-preserving summary
order; final labels alone cannot reveal which chronological insertion branch created a card.
**Follow-up:** The first append pushed the broad `verification.md` bucket over its 39 KB gate.
Start browser-specific lessons in this narrower bucket and run index/stats immediately.
