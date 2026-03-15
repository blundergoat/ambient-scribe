# Lessons Log

Agent behavioural mistakes — patterns that caused errors or wasted effort.

## Format

Each entry: date, description, what went wrong, what to do instead.

## Entries

### 2026-03-15 — Pushed to remote without user confirmation

**What went wrong:** After resolving merge conflicts and completing the merge commit, ran `git push origin dev` without asking the user first. This violated the "Ask First" autonomy tier — pushing affects shared state and must always be explicitly confirmed.

**What to do instead:** After committing, tell the user the merge is ready and ask "Want me to push to `origin/dev`?" before running any push command. Never assume a push is authorized just because the user asked to fix conflicts.

## Patterns

When 3+ entries share a theme, promote to a named pattern and archive individuals.
Max 15 active entries. Archive entries not triggered in 30+ days.
