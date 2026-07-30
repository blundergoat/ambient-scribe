---
category: shell-validation
last_reviewed: 2026-07-30
---

# Shell validation lessons

## Lesson: Fail-closed shell validators must return explicitly at each gate

**Created:** 2026-07-30
**Decision changed:** Every reusable shell validator now guards each required command with an
explicit failure return instead of depending on the caller's `errexit` state.
**Trigger phase:** VERIFY

The first M05A receipt-hash smoke unexpectedly let the `hash_drift` case pass. The validator
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
