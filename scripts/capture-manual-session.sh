#!/bin/bash
# =============================================================================
# Collect the extra artifacts a manual test consultation cannot write itself
# =============================================================================
# Usage: ./scripts/capture-manual-session.sh SESSION_ID [OUTPUT_DIR]
#
# OPTIONAL. The agent already writes the important material by itself: finalizing
# a visit saves the live rows, correction saves the corrected rows, and the
# summary saves the clinical note and its fidelity trace, all under
# SESSION_EVIDENCE_DIR (default strands_agents/var/session-evidence/<session-id>/).
# Nothing needs to run alongside the browser and there is no window to miss.
#
# This script adds the few things that live outside the application:
#   nemo-agent-session.log   docker logs, filtered to this session
#   runtime-identity.json    docker inspect + /health + /agent/model-health
#   artifact-manifest.md     byte size and SHA-256 of everything written
#
# It also re-fetches the transcripts from the session store, which is useful as a
# cross-check that what was served matches what was written to evidence. Those
# GETs only answer while the agent is up and inside SESSION_TTL_SECONDS; the
# evidence bundle has no such limit.
# =============================================================================

set -uo pipefail

SESSION_ID="${1:-}"
if [[ -z "$SESSION_ID" ]]; then
    echo "usage: $0 SESSION_ID [OUTPUT_DIR]" >&2
    exit 2
fi

AGENT_HOST_PORT="${AGENT_PORT:-48101}"
NEMO_URL="${AGENT_ENDPOINT:-http://localhost:${AGENT_HOST_PORT}}"
NEMO_CONTAINER="${NEMO_CONTAINER:-ambient-scribe-nemo-agent-1}"

STAMP="$(date -u +%Y-%m-%d_%H%M)"
OUT_DIR="${2:-.goat-flow/plans/manual-testing/${STAMP}}"

BOLD=$'\033[1m'; DIM=$'\033[2m'; RESET=$'\033[0m'
PASS=$'\033[32m✓\033[0m'; FAIL=$'\033[31m✗\033[0m'; WARN=$'\033[33m!\033[0m'; ARROW=$'\033[36m→\033[0m'

mkdir -p "$OUT_DIR"

echo ""
echo "  ${BOLD}Ambient Scribe - Session Capture${RESET}"
echo "  ${DIM}$(printf '─%.0s' {1..56})${RESET}"
echo ""
echo "  ${ARROW}  Session: ${BOLD}${SESSION_ID}${RESET}"
echo "  ${ARROW}  Output:  ${BOLD}${OUT_DIR}${RESET}"
echo ""

AGENT_UP=$(curl -sf -o /dev/null -w "%{http_code}" --connect-timeout 5 "$NEMO_URL/health" 2>/dev/null) || AGENT_UP="000"
if [[ "$AGENT_UP" == "200" ]]; then
    echo "  ${PASS}  agent reachable"
else
    echo "  ${WARN}  agent NOT reachable (HTTP ${AGENT_UP}) - session-store artifacts will be missing"
    echo "  ${DIM}     Everything below that needs a GET will be skipped.${RESET}"
fi
echo ""

# Fetch one endpoint into a file, reporting whether it produced usable content.
fetch() {
    local label="$1" url="$2" dest="$3"
    local code
    code=$(curl -s -o "${OUT_DIR}/${dest}" -w "%{http_code}" --connect-timeout 10 --max-time 120 "$url" 2>/dev/null) || code="000"
    if [[ "$code" == "200" && -s "${OUT_DIR}/${dest}" ]]; then
        local n
        n=$(wc -c < "${OUT_DIR}/${dest}")
        echo "  ${PASS}  ${label} (${n} bytes)"
    else
        rm -f "${OUT_DIR}/${dest}"
        echo "  ${FAIL}  ${label} - HTTP ${code}, not written"
    fi
}

echo "  ${BOLD}Session store${RESET}"
fetch "live-history.json"         "${NEMO_URL}/session/${SESSION_ID}/history"              "live-history.json"
fetch "corrected-transcript.json" "${NEMO_URL}/session/${SESSION_ID}/corrected-transcript" "corrected-transcript.json"
fetch "roles.json"                "${NEMO_URL}/session/${SESSION_ID}/roles"                "roles.json"
echo ""

# ── the persisted quality record ────────────────────────────────
# The agent appends one JSON object per finalized session. Its working directory
# is /app, which compose binds to ./strands_agents, so the file lands beside the
# code rather than in the repository's var/. Both paths are checked so this keeps
# working if that mount is ever corrected.
echo "  ${BOLD}Quality record${RESET}"
QUALITY_SRC=""
for candidate in "strands_agents/var/quality/sessions.jsonl" "var/quality/sessions.jsonl"; do
    if [[ -f "$candidate" ]] && grep -q "$SESSION_ID" "$candidate" 2>/dev/null; then
        QUALITY_SRC="$candidate"
        break
    fi
done

if [[ -n "$QUALITY_SRC" ]]; then
    python3 - "$QUALITY_SRC" "$SESSION_ID" "${OUT_DIR}/streaming-quality.json" <<'PY'
import json, sys
src, session_id, dest = sys.argv[1], sys.argv[2], sys.argv[3]
match = None
with open(src) as handle:
    for line in handle:
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("session_id") == session_id:
            match = record
with open(dest, "w") as handle:
    json.dump(match, handle, indent=2, sort_keys=True)
    handle.write("\n")
PY
    echo "  ${PASS}  streaming-quality.json (from ${QUALITY_SRC})"
else
    echo "  ${FAIL}  no persisted quality record found for this session"
    echo "  ${DIM}     The record is written when the session finalizes. If the visit${RESET}"
    echo "  ${DIM}     was never stopped in the browser, there is nothing to find.${RESET}"
fi
echo ""

# ── runtime identity ────────────────────────────────────────────
echo "  ${BOLD}Runtime identity${RESET}"
INSPECT_JSON="null"
if docker inspect "$NEMO_CONTAINER" >/dev/null 2>&1; then
    INSPECT_JSON=$(docker inspect "$NEMO_CONTAINER" 2>/dev/null)
    echo "  ${PASS}  container inspected"
else
    echo "  ${WARN}  container ${NEMO_CONTAINER} not found - identity will be partial"
fi

HEALTH_JSON=$(curl -s --connect-timeout 5 "${NEMO_URL}/health" 2>/dev/null || echo 'null')
MODEL_JSON=$(curl -s --connect-timeout 5 "${NEMO_URL}/agent/model-health" 2>/dev/null || echo 'null')

python3 - "$SESSION_ID" "${OUT_DIR}/runtime-identity.json" <<PY
import json, subprocess, sys
session_id, dest = sys.argv[1], sys.argv[2]

def parse(raw, default):
    try:
        return json.loads(raw)
    except Exception:
        return default

inspect = parse(r'''${INSPECT_JSON}''', None)
health = parse(r'''${HEALTH_JSON}''', None)
model = parse(r'''${MODEL_JSON}''', None)

container = {}
if isinstance(inspect, list) and inspect:
    c = inspect[0]
    state = c.get("State", {})
    config = c.get("Config", {})
    env = {}
    # Runtime env is the truth; the compose default can differ from what is running.
    for item in config.get("Env", []) or []:
        if "=" in item:
            key, value = item.split("=", 1)
            env[key] = value
    redacted = {
        k: ("<redacted>" if any(s in k.upper() for s in ("SECRET", "JWT", "TOKEN", "KEY", "PASSWORD")) else v)
        for k, v in env.items()
    }
    container = {
        "container_id": c.get("Id"),
        "container_name": c.get("Name"),
        "image_id": c.get("Image"),
        "image_name": config.get("Image"),
        "created_at": c.get("Created"),
        "started_at": state.get("StartedAt"),
        "restart_count": c.get("RestartCount"),
        "status": state.get("Status"),
        "health": (state.get("Health") or {}).get("Status"),
        "env": redacted,
    }

def head(raw):
    return subprocess.run(raw, shell=True, capture_output=True, text=True).stdout.strip()

payload = {
    "captured_at_utc": head("date -u +%Y-%m-%dT%H:%M:%SZ"),
    "manual_run_session_id": session_id,
    "workspace": {
        "head": head("git rev-parse HEAD"),
        "tracked_state": "clean" if not head("git status --short") else "dirty at capture",
        "dirty_paths": head("git status --short").splitlines(),
    },
    "container": container or None,
    "health": health,
    "model_health": model,
}
with open(dest, "w") as handle:
    json.dump(payload, handle, indent=2)
    handle.write("\n")
PY
echo "  ${PASS}  runtime-identity.json"
echo ""

# ── container logs ──────────────────────────────────────────────
echo "  ${BOLD}Container logs${RESET}"
if docker inspect "$NEMO_CONTAINER" >/dev/null 2>&1; then
    docker logs "$NEMO_CONTAINER" >"${OUT_DIR}/nemo-agent-full.log" 2>&1
    grep -F "$SESSION_ID" "${OUT_DIR}/nemo-agent-full.log" >"${OUT_DIR}/nemo-agent-session.log" 2>/dev/null
    FULL=$(wc -l < "${OUT_DIR}/nemo-agent-full.log")
    SESS=$(wc -l < "${OUT_DIR}/nemo-agent-session.log" 2>/dev/null || echo 0)
    echo "  ${PASS}  nemo-agent-session.log (${SESS} lines, from ${FULL} total)"
    # The full log is large and mostly unrelated; keep it only when the filter
    # found nothing, so the failure is still diagnosable.
    if [[ "$SESS" -gt 0 ]]; then
        rm -f "${OUT_DIR}/nemo-agent-full.log"
    else
        echo "  ${WARN}  no lines matched this session id - keeping the full log"
    fi
else
    echo "  ${FAIL}  container gone - logs unrecoverable"
fi
echo ""

# ── manifest ────────────────────────────────────────────────────
{
    echo "# Manual-test artifact manifest"
    echo ""
    echo "Session \`${SESSION_ID}\`, captured $(date -u +%Y-%m-%dT%H:%M:%SZ) by"
    echo "\`scripts/capture-manual-session.sh\`."
    echo ""
    echo "| Artifact | Bytes | SHA-256 |"
    echo "| --- | ---: | --- |"
    for path in "$OUT_DIR"/*; do
        [[ -f "$path" ]] || continue
        f="$(basename "$path")"
        [[ "$f" == "artifact-manifest.md" ]] && continue
        printf '| `%s` | %s | `%s` |\n' \
            "$f" "$(wc -c < "$path")" "$(sha256sum "$path" | cut -d' ' -f1)"
    done
    echo ""
    echo "## The primary evidence is elsewhere"
    echo ""
    echo "The agent writes the live rows, corrected rows, and the clinical note plus"
    echo "its fidelity trace by itself, under \`SESSION_EVIDENCE_DIR\` (default"
    echo "\`strands_agents/var/session-evidence/${SESSION_ID}/\`). This directory holds"
    echo "only the container-side extras and a cross-check copy of the transcripts."
} >"${OUT_DIR}/artifact-manifest.md"

echo "  ${PASS}  artifact-manifest.md"
echo ""
echo "  ${DIM}$(printf '─%.0s' {1..56})${RESET}"
echo "  ${PASS}  Captured $(cd "$OUT_DIR" && ls -1 | wc -l) artifacts"
echo "  ${ARROW}  ${BOLD}${OUT_DIR}${RESET}"
echo ""
