#!/usr/bin/env bash
# Exercise the live eval runner's development-corpus selection without
# streaming audio or loading NeMo.
#
# The smoke sources the runner (kept main-guarded so sourcing never starts a
# replay), proves the fail-closed --development-corpus resolver selects exactly
# the ten manifest-ordered WAVs, and pins rejection of mixed selection and of a
# rejected corpus helper. Direct-path resolution is pinned unchanged.

set -Eeuo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SMOKE_DIR="$(mktemp -d)"
trap 'rm -rf "$SMOKE_DIR"' EXIT

# Sourcing exposes resolver helpers without starting the developer-facing eval.
source "$REPO_ROOT/scripts/eval-fixtures.sh"

# --- 1. The selector resolves exactly the ten manifest fixtures in order ---
FIXTURE_QUERIES=()
FIXTURE_PATHS=()
resolve_development_corpus_fixtures
expected_paths=(
    "tests/fixtures/audio/primock57-day1-consultation02-i-have-sore-red-skin.wav"
    "tests/fixtures/audio/primock57-day1-consultation03-i-have-terrible-headache.wav"
    "tests/fixtures/audio/primock57-day1-consultation06-hard-to-breathe.wav"
    "tests/fixtures/audio/primock57-day1-consultation07-i-have-a-cough-and-cold.wav"
    "tests/fixtures/audio/primock57-day1-consultation08-i-have-dry-itchy-skin.wav"
    "tests/fixtures/audio/primock57-day2-consultation03-i-cant-hear-very-well-and-my-face-is-a-bit-numb.wav"
    "tests/fixtures/audio/primock57-day2-consultation09-i-cant-move-my-left-arm.wav"
    "tests/fixtures/audio/primock57-day3-consultation01-lips-swelling-after-eating-a-sandwich.wav"
    "tests/fixtures/audio/primock57-day5-consultation03-im-feeling-very-anxious.wav"
    "tests/fixtures/audio/primock57-day5-consultation09-tired-all-the-time.wav"
)
if [[ "${FIXTURE_PATHS[*]}" != "${expected_paths[*]}" ]]; then
    echo "smoke failure: development corpus selection differs from manifest order" >&2
    printf 'got:  %s\n' "${FIXTURE_PATHS[@]}" >&2
    exit 1
fi

# --- 2. Mixing the frozen corpus with any other selection fails closed ---
FIXTURE_QUERIES=("__all__")
FIXTURE_PATHS=()
conflict_exit=0
(resolve_development_corpus_fixtures) >/dev/null 2>&1 || conflict_exit=$?
if [[ "$conflict_exit" != "2" ]]; then
    echo "smoke failure: mixed corpus selection did not fail closed (exit $conflict_exit)" >&2
    exit 1
fi

# --- 3. A rejected corpus helper stops selection before any fixture path ---
cat > "$SMOKE_DIR/rejecting-python" <<'STUB'
#!/usr/bin/env bash
echo "development corpus rejected: smoke stub" >&2
exit 2
STUB
chmod +x "$SMOKE_DIR/rejecting-python"
FIXTURE_QUERIES=()
FIXTURE_PATHS=()
real_python_bin="$PYTHON_BIN"
PYTHON_BIN="$SMOKE_DIR/rejecting-python"
reject_exit=0
(resolve_development_corpus_fixtures) >/dev/null 2>&1 || reject_exit=$?
PYTHON_BIN="$real_python_bin"
if [[ "$reject_exit" != "2" ]]; then
    echo "smoke failure: rejected corpus helper did not fail closed (exit $reject_exit)" >&2
    exit 1
fi

# --- 4. Direct-path resolution outside the corpus flag stays unchanged ---
touch "$SMOKE_DIR/direct-fixture.wav"
FIXTURE_QUERIES=("$SMOKE_DIR/direct-fixture.wav")
FIXTURE_PATHS=()
resolve_fixtures
if [[ "${FIXTURE_PATHS[*]}" != "$SMOKE_DIR/direct-fixture.wav" ]]; then
    echo "smoke failure: direct-path fixture resolution changed" >&2
    exit 1
fi

printf 'eval-fixtures smoke passed: corpus order, mixed-selection rejection, helper rejection, direct path\n'
