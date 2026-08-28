---
category: shell-validation
last_reviewed: 2026-08-29
---

# Shell validation lessons

## Lesson: Fail-closed shell validators must return explicitly at each gate

**Created:** 2026-07-30
**Decision changed:** Every reusable shell validator now guards each required command with an
explicit failure return instead of depending on the caller's `errexit` state.
**Trigger phase:** VERIFY

The first receipt-hash smoke unexpectedly let the `hash_drift` case pass. The validator
ran `sha256sum --check` as a bare command and relied on the script's top-level `set -e`, but
the negative-case harness correctly used `set +e` while capturing the validator status.
Checksum failure therefore continued into later successful checks, and the function returned
zero. Adding `|| return 1` to every required receipt, checksum, canonicalization, and identity
gate made the same proof fail before the attempt sentinel.

When a shell function is itself a security or one-shot boundary, treat `set -e` as ambient
caller policy rather than part of the function contract. Return explicitly after every
required command, and test the function from a caller that temporarily disables `errexit`;
otherwise expected-failure harnesses can hide exactly the fail-open path they are meant to
exercise.

## Lesson: A sed delimiter that appears in the pattern silently rewrites the edit

**Created:** 2026-08-29
**Decision changed:** Bulk in-place `sed` edits now pick a delimiter absent from both halves of
every expression, and prove one file before the set.
**Trigger phase:** ACT

A version rename across seven Markdown files bundled six `-e` expressions into one `sed -i` run.
One expression used `#` as the `s` delimiter while matching a Markdown heading, so the pattern's
own `#` closed the substitution early: the pattern collapsed to `^`, the replacement became the
literal heading text, and GNU sed read the surplus `#` as the start of a comment. It exited zero
with no diagnostic and prefixed that literal to every line of all seven files, not just the one
the expression named.

Nothing in the command's output revealed the damage. A follow-up `grep` for the old version
string exposed it, and recovery was provable only because the injected text was a fixed literal
and the intended substitution was length-preserving: stripping the prefix and comparing each file
against its pre-edit byte size demonstrated the repair instead of assuming it.

Choose an `s` delimiter that appears in neither the pattern nor the replacement; `#` is never safe
against Markdown headings, and `/` is never safe against paths. Run a multi-expression in-place
edit against a single file and read the result before widening to a set, and capture sizes or
hashes first so a bad bulk edit can be shown undone rather than argued undone.
