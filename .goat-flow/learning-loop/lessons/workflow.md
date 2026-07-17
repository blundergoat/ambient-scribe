---
category: workflow
last_reviewed: 2026-07-14
---

## Lesson: Re-read the active plan pointer immediately before changing it

**Created:** 2026-07-14
**What happened:** A fresh-plan patch bundled new milestone files with an expected
`.goat-flow/plans/.active` value. Another workflow changed the pointer after intake, so
`apply_patch` rejected the whole batch before writing any plan content.
**Prevention:** Treat `.active` as an optimistic-concurrency write. Re-read it immediately before
the patch; if it changed, preserve the concurrent pointer unless the user explicitly asks to
activate the new plan, and write the user-requested plan artifacts in a separate patch.

