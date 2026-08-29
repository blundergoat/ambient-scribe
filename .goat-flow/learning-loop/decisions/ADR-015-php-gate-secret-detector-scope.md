# ADR-015: The PHP Gate Drops the Generic Entropy Heuristic, Not Its Scan Surface

**Status:** Accepted
**Date:** 2026-08-29
**Ticket/Context:** Restoring a preflight PHP gate that failed on 301 findings, none of them from PHP

## Context

Step 5 of `scripts/preflight-checks.sh` runs `gruff-php analyse` and fails on any finding at all. It
was failing on 301: 291 in `.json`, 10 in `.sh`, and **zero in `.php`**. Every one but the
`composer.json` dependency finding was `sensitive-data.high-entropy-string` firing on a committed
SHA-256 content digest. A green step 5 could only ever mean "someone accepted 300 digests", so the
first genuine PHP finding would have arrived invisible.

Three fixes were measured and rejected before this one.

**Raising the entropy threshold does nothing.** A 64-character lowercase hex digest measures about
3.99 bits per character against a 4.0 theoretical maximum, and the configured threshold was already
4.2. Setting it to an unreachable 6.0 still left 297 findings, so the threshold never governed these.

**Excluding non-PHP files blinds the repository.** `paths.ignore` is the analyzer's only path key,
and it removes files at discovery, taking every rule with them. A planted AWS key in a `.sh` file
fired on the current config and fired nothing once excluded, and no other analyzer reads `.sh` at
all. `tests/fixtures/**` is worse: gruff-php's existing ignore entry is `tests/Fixtures/**` with a
capital F, so gruff-php is today the only analyzer scanning the real lowercase directory, while
gruff-py ignores that tree outright. Excluding `.json` would also permanently disable the four
`security.dependency-composer-*` rules, which can fire nowhere else.

**Gating on severity alone is the same hole by another route.** Every `sensitive-data` rule carries
`warning`, the same severity as the digest noise. Under a `failureConditions.severityThresholds`
gate of `error: 0` the run exits 0 with a planted JWT present, so a real credential would ship green.

## Decision

Disable `sensitive-data.high-entropy-string` in `.gruff-php.yaml` and change nothing else. No file is
excluded from any analyzer, no baseline entry is added, and no source file is edited to satisfy a
finding.

gruff-php remains the secret-scan owner for every file it discovers, including shell scripts, the
fixture tree, and data JSON. The eleven specific detectors stay enabled everywhere:
`aws-access-key`, `api-key-pattern`, `database-url-password`, `gcp-service-account-key`,
`hardcoded-env-value`, `jwt-token`, `phi-pattern`, `pii-test-fixture`, `private-key`,
`url-credentials`, plus the `security.*` family.

The baseline shrinks from two accepted entries to one. The `composer.json` unpinned-dependency entry
is genuine debt and survives; the fixture entry accepted an instance of exactly the false positive
this decision removes, so it became dead weight.

Conformance is checkable: `gruff-php analyse --baseline=gruff-php-baseline.json` exits 0 on a clean
tree, and exits 1 with a planted JWT or private key present.

## Failure Mode Comparison

| Option | What fails | Why rejected or accepted |
| --- | --- | --- |
| Raise the entropy threshold | Nothing changes; 297 of 301 findings survive an unreachable 6.0 | Rejected on measurement. The threshold does not govern these findings. |
| Exclude non-PHP files from gruff-php | 52 shell scripts and the whole fixture tree lose every credential detector, and four composer rules die permanently | Rejected. A planted AWS key in a `.sh` file stopped firing, and nothing else scans `.sh`. |
| Fail the gate only on `error` severity | Every credential rule is `warning`, so a planted JWT exits 0 | Rejected. Demonstrated to pass a real credential. |
| Baseline the 300 digests | A future real secret in those same files is masked permanently | Rejected. A false positive is a configuration question, not accepted debt. |
| Disable the generic entropy heuristic only | A high-entropy secret matching no specific pattern is no longer caught by this analyzer | Accepted. It is the only option that keeps every file scanned and every specific detector live, and the planted-secret control still fails the gate. |

## Consequences

- Step 5 green now means "no PHP quality finding", so the first real one is visible.
- A hardcoded secret whose shape matches none of the eleven specific detectors is no longer caught
  here. That is the accepted gap, and it is a real one: generic entropy is what catches an unknown
  token format.
- No coverage moved between analyzers, so nothing depends on a second config staying as it is.

## Reversibility

A two-way door, and a one-line one: set `enabled: true` to restore the heuristic. Doing so returns
the 300 digest findings, so it should be paired with a way to distinguish a committed digest from a
credential.

Revisit when any of these becomes true:

- the analyzer gains a per-rule path scope or a digest allowlist, so the heuristic can run on source
  while ignoring manifest digests;
- a dedicated secret scanner with digest-aware rules is adopted, which would own this scan properly;
  or
- the repository stops committing content digests, which removes the false-positive source entirely.
