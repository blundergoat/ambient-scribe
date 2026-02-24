---
applyTo: '**'
---

# Git Commit Instructions

## Commit Message Format

All commit messages MUST start with the corresponding GitHub issue number.

**Format:** `#<issue_number> <Area> - <Action>`

### Subject Line Rules

- **Max 72 characters** — the `#<issue> <Area> - ` prefix takes ~15-20 chars, so keep the action portion under ~50 chars
- **Use imperative mood** — write as if completing the sentence "This commit will ..." (e.g., "Add", "Fix", "Remove", not "Added", "Fixes", "Removing")
- **Describe what AND why** — if the "why" doesn't fit in the subject, put it in the body
- **No trailing period**

### Commit Scope

Each commit should represent **one logical change**. This makes reviews easier and enables clean `git bisect`.

- **Do:** One feature, one bug fix, or one refactor per commit
- **Don't:** Mix unrelated changes (e.g., a bug fix + a formatting cleanup + a new feature)
- **Splitting rule:** If you need the word "and" in the subject line to describe what the commit does, consider splitting it into two commits

## Areas

Use these standard area prefixes for consistency. Choose the area where the **most significant logic change** occurs:

| Area | Purpose |
|------|---------|
| Forge | Forge provisioning engine (WSL setup) |
| Docker Runtime | Container orchestration features |
| App Operations | Makefile-based task runner |
| Secrets | Secrets management (.env, infra files) |
| Workspace Config | Workspace YAML configuration |
| Workspaces | Multi-workspace switching |
| WSL | WSL instance management |
| Frontend | General React/TypeScript changes |
| Backend | General Rust/Tauri changes |
| Scripts | Shell scripts (setup, preflight, etc.) |
| CI | GitHub Actions, workflows |
| Docs | Documentation updates |
| Tests | Test additions/modifications |
| Deps | Dependency updates |
| Infra | Infrastructure (Docker compose, etc.) |

### Multi-Area Commits

When a commit spans multiple areas (e.g., a Rust command + React UI + config schema), use the area containing the **primary logic change** — typically where the new behavior originates. Mention the other affected areas in the body.

```
#55 Backend - Add workspace-specific path handling to all commands

Updates read_env_file, write_env_file, export_secrets_bundle, and
import_secrets_bundle to accept optional path parameters.

Frontend: Updated invoke calls in useSecrets hook to pass workspace path.
Config: Added path field to workspace-config schema.
```

## Subject Line Examples

**Good:**

```
#12 Forge - Add system check for installed packages
#45 Secrets - Support workspace-specific env paths
#23 Scripts - Rename setup scripts for clarity
#67 Workspaces - Add multi-workspace store with persistence
#31 Backend - Fix path traversal validation on nested directories
```

**Bad:**

```
#12 fixed bug                     →  Vague, past tense, no area
#45 updated stuff                 →  No area, unclear action
changes                           →  Missing issue number entirely
#33 Frontend - Add new button and also refactor the sidebar and update tests
                                  →  Too many unrelated changes, too long
```

## Commit Body

A body is **expected** for any non-trivial change. Only truly simple commits (typo fixes, single-line config changes) should be subject-only.

### Body Structure

Separate the body from the subject with a **blank line**. Wrap lines at **72 characters**.

```
#<issue> <Area> - <Subject>

<What changed and why — 1-3 short paragraphs>

<Optional: references, co-authors, trailers>
```

### What to Include in the Body

| Question | When to include |
|----------|----------------|
| **What** changed? | Always — summarize the key changes |
| **Why** this approach? | When the reasoning isn't obvious from the diff |
| **What alternatives** were considered? | When you chose between meaningful options |
| **What's the impact?** | When behavior changes for users or other code |
| **What's NOT included?** | When you deliberately deferred related work |

### Body Example

```
#42 Forge - Add mode-aware cancel behavior

Cancel now only terminates the WSL instance when running in Reset mode.
Previously, cancelling any Forge operation would kill the WSL instance,
which destroyed the user's active development environment during
UpdateRepos or UpdateStacks operations.

Considered adding a confirmation dialog instead, but opted for
mode-aware logic since UpdateRepos/UpdateStacks should never need
instance termination.

Closes #42
Refs #38
```

## Issue and PR References

Use Git trailers and GitHub keywords in the commit body to link related work:

| Syntax | Effect |
|--------|--------|
| `Closes #42` | Automatically closes issue #42 when merged |
| `Fixes #42` | Same as Closes — auto-closes the issue |
| `Refs #38` | Links to issue #38 without closing it |
| `See also: #50` | Informal reference to related issue/PR |

Place these at the **end** of the body, after a blank line.

## Branch-to-Issue Mapping

Branches using the pattern `feat/2_initial-setup` (or similar) map to issue `#2`, so commits on that branch must start with `#2`.

## Breaking Changes

Mark breaking changes with `BREAKING:` after the area prefix. Always include a body listing what broke and how to migrate:

```
#78 Scripts - BREAKING: Rename setup scripts

Renamed for consistency with the `<noun>-<verb>` convention:
- setup.sh → setup-initial.sh
- verify-setup.sh → setup-verify.sh
- install-dependencies.sh → dependencies-install.sh

Migration: Update any CI workflows or documentation that reference
the old script names.
```

## Co-Authorship

When a commit is co-authored (pair programming, AI-assisted, etc.), add `Co-authored-by` trailers:

```
#19 Frontend - Add workspace selector dropdown

Co-authored-by: Jane Doe <jane@example.com>
Co-authored-by: Claude Opus 4.6 <noreply@anthropic.com>
```

## WIP and Fixup Commits

During development on a feature branch, work-in-progress and fixup commits are acceptable. Clean them up **before merging** to main.

| Prefix | Purpose | Before merge |
|--------|---------|--------------|
| `WIP: #12 Forge - ...` | Work in progress, not ready for review | Squash or rewrite into a proper commit |
| `fixup! #12 Forge - ...` | Fixes a previous commit on the branch | Squash into the target commit with `git rebase --autosquash` |

**Main branch must only contain clean, atomic commits** — no WIP or fixup commits should survive the merge.

## Merge Strategy

When merging PRs to main:

- **Prefer squash-and-merge** for feature branches with messy history (multiple WIP/fixup commits)
- **Prefer rebase-and-merge** for branches with clean, atomic commits that each tell a meaningful story
- **Avoid merge commits** — they add noise to `git log` and make bisecting harder
