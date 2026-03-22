# Git Commit Instructions

Format: `#<issue_number> <Area> - <Action>`

- Max 72 characters, imperative mood, no trailing period
- One logical change per commit
- Body expected for non-trivial changes (wrap at 72 chars)
- Areas: Backend, Frontend, Agent, NeMo, Infra, Scripts, CI, Docs, Tests, Deps
- Breaking changes: `#N Area - BREAKING: Subject` with migration steps in body
- Co-authorship: add `Co-authored-by:` trailers

See `ai/instructions/git-commit.md` for full format and examples.
