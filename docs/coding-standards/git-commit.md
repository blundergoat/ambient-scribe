# Git Commit Instructions — Ambient Scribe

## Format

```
#<issue_number> <Area> - <Action>

<What changed and why — 1-3 short paragraphs>

<Optional: Closes #N, Refs #N, Co-authored-by>
```

## Rules

- Subject line max 72 characters, imperative mood, no trailing period
- Each commit = one logical change (if you need "and" in the subject, split it)
- Body expected for non-trivial changes; wrap at 72 characters
- Always start with the GitHub issue number: `#12 Backend - Add session timeout`

## Areas

| Area | Scope |
|------|-------|
| Backend | PHP/Symfony (`src/`, `config/`) |
| Frontend | Twig templates, external JS (`public/js/scribe.js`) |
| Agent | Python agent layer (`strands_agents/`) |
| NeMo | NeMo pipeline, GPU inference |
| Infra | Docker, Terraform, deployment |
| Scripts | Shell scripts (`scripts/`) |
| CI | GitHub Actions workflows |
| Docs | Documentation updates |
| Tests | Test additions/modifications |
| Deps | Dependency updates |

## Examples

```
#12 Backend - Add session timeout to ScribeController
#45 Agent - Accept session_id from multipart form data
#23 Infra - Pin NeMo container to CUDA 12.2
```

## Breaking Changes

Prefix with `BREAKING:` after the area: `#78 Agent - BREAKING: Rename session endpoint`. Always include migration steps in the body.

## Co-Authorship

```
Co-authored-by: Claude Opus 4.6 <noreply@anthropic.com>
```

## Branch Strategy

- Squash-and-merge for messy branches, rebase-and-merge for clean atomic commits
- No WIP or fixup commits on main
