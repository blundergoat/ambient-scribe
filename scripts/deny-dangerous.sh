#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

usage() {
    cat <<'EOF'
Codifies dangerous-command policy for review, CI, and preflight.

Usage:
  ./scripts/deny-dangerous.sh --check-repo
  ./scripts/deny-dangerous.sh --command "git push --force"
  ./scripts/deny-dangerous.sh --self-test
  ./scripts/deny-dangerous.sh git commit --no-verify

This script is policy verification only. It does not intercept Codex at runtime.
EOF
}

fail() {
    echo "DENY: $1" >&2
    exit 1
}

check_command() {
    local command_text="$1"

    [[ -n "$command_text" ]] || fail "empty command"

    if [[ "$command_text" =~ (^|[[:space:]])git[[:space:]]+push([^[:alnum:]]|[[:space:]]) && "$command_text" =~ (--force|-f)([[:space:]]|$) ]]; then
        fail "force push is blocked"
    fi

    if [[ "$command_text" =~ (^|[[:space:]])git[[:space:]]+commit([^[:alnum:]]|[[:space:]]) && "$command_text" =~ --no-verify([[:space:]]|$) ]]; then
        fail "no-verify commits are blocked"
    fi

    if [[ "$command_text" =~ (^|[[:space:]])terraform[[:space:]]+apply([[:space:]]|$) ]]; then
        fail "terraform apply is Ask First"
    fi

    if [[ "$command_text" =~ (^|[[:space:]])rm[[:space:]]+-rf([[:space:]]|$) ]]; then
        if [[ "$command_text" =~ (^|[[:space:]])rm[[:space:]]+-rf[[:space:]]+(/|~|\.\.?([[:space:]]|$)|\*|--no-preserve-root) ]]; then
            fail "unscoped rm -rf is blocked"
        fi
    fi

    if [[ "$command_text" =~ (^|[[:space:]])([^[:space:]]*/)?\.env([[:space:]]|$) ]]; then
        fail "direct .env edits are blocked"
    fi

    echo "deny-dangerous: command allowed"
}

check_repo() {
    local changed
    changed="$(
        {
            git diff --name-only
            git diff --cached --name-only
        } | sort -u
    )"

    while IFS= read -r path; do
        [[ -n "$path" ]] || continue
        case "$path" in
            .env)
                fail "worktree includes .env edits; use .env.example or explicit user approval"
                ;;
            coverage.xml|coverage-html/*|vendor/*)
                fail "tool-managed output edited directly: $path"
                ;;
        esac
    done <<< "$changed"

    echo "deny-dangerous: repo diff allowed"
}

self_test() {
    local script_path
    script_path="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"

    expect_deny() {
        local command_text="$1"
        if "$script_path" --command "$command_text" >/dev/null 2>&1; then
            echo "SELF-TEST FAIL: expected denial for '$command_text'" >&2
            exit 1
        fi
    }

    expect_allow() {
        local command_text="$1"
        if ! "$script_path" --command "$command_text" >/dev/null 2>&1; then
            echo "SELF-TEST FAIL: expected allow for '$command_text'" >&2
            exit 1
        fi
    }

    expect_deny "git push --force origin main"
    expect_deny "git commit --no-verify -m test"
    expect_deny "terraform apply"
    expect_deny "rm -rf /"
    expect_deny "sed -i .env"
    expect_allow "strands_agents/.venv/bin/pytest tests/python/ -q"
    expect_allow "git status"

    echo "deny-dangerous: self-test passed"
}

if [[ $# -eq 0 ]]; then
    usage
    exit 0
fi

case "${1:-}" in
    --help|-h)
        usage
        exit 0
        ;;
    --check-repo)
        check_repo
        exit 0
        ;;
    --self-test)
        self_test
        exit 0
        ;;
    --command)
        shift
        [[ -n "${1:-}" ]] || fail "--command requires an argument"
        check_command "$1"
        exit 0
        ;;
esac

check_command "$*"
