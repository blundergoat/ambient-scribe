# Lessons

**Mistakes the agent made.** A lesson exists because the agent did something wrong - not because the code is structured badly. Example: "agent proposed a fix before completing diagnosis" or "agent skipped disambiguation when it should have asked."

If the trap is in the code itself → `.goat-flow/footguns/` instead.

Prefer category bucket files such as `verification.md`, `workflow.md`, or `coordination.md` with file-level frontmatter:

```yaml
---
category: verification
---
```

Inside a bucket, add entries as `## Lesson:` or `## Pattern:` blocks. Legacy one-entry files still work during migration, but category buckets are the preferred format.
