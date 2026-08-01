#!/bin/bash
# =============================================================================
# Record a manual test consultation
# =============================================================================
# Usage: ./scripts/record-manual-session.sh [SESSION_ID]
#
# Subscribes to every Mercure topic for a consultation and appends each event
# to a JSONL file. Start this BEFORE clicking "Start Consultation", leave it
# running for the whole visit, then stop it with Ctrl-C after the note renders.
#
# This exists because the browser dev panel is a capped rolling buffer: a 9-minute
# consult emits ~1400 events and the panel keeps the last 200, so copying it out
# silently loses the first two thirds of the session. This file keeps all of them.
#
# Captures all three topics the agent publishes on:
#   raw     - segments, the final quality record, the finalized event
#   roles   - role_update events carrying the speaker->role mapping
#   summary - the clinical note, which has no GET endpoint and is otherwise
#             only ever visible in the browser
#
# Pair with capture-manual-session.sh, which collects the server-side artifacts
# after the visit ends.
# =============================================================================

set -uo pipefail

MERCURE_HOST_PORT="${MERCURE_PORT:-48137}"
MERCURE_URL="${MERCURE_PUBLIC_URL:-http://localhost:${MERCURE_HOST_PORT}/.well-known/mercure}"

SESSION_ID="${1:-}"
OUT_DIR="${OUT_DIR:-.goat-flow/plans/manual-testing}"

BOLD=$'\033[1m'; DIM=$'\033[2m'; RESET=$'\033[0m'
PASS=$'\033[32m✓\033[0m'; FAIL=$'\033[31m✗\033[0m'; ARROW=$'\033[36m→\033[0m'

# A wildcard subscription catches a session whose id is not known until the
# browser creates it, which is the normal case when starting from the UI.
if [[ -n "$SESSION_ID" ]]; then
    TOPICS=(
        "scribe/session/${SESSION_ID}/raw"
        "scribe/session/${SESSION_ID}/roles"
        "scribe/session/${SESSION_ID}/summary"
    )
    LABEL="$SESSION_ID"
else
    TOPICS=(
        "scribe/session/{id}/raw"
        "scribe/session/{id}/roles"
        "scribe/session/{id}/summary"
    )
    LABEL="all sessions (wildcard)"
fi

STAMP="$(date -u +%Y-%m-%d_%H%M)"
mkdir -p "$OUT_DIR"
EVENTS_FILE="${OUT_DIR}/events-${STAMP}.jsonl"

echo ""
echo "  ${BOLD}Ambient Scribe - Session Recorder${RESET}"
echo "  ${DIM}$(printf '─%.0s' {1..56})${RESET}"
echo ""

MERC_CODE=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 5 "$MERCURE_URL" 2>/dev/null) || MERC_CODE="000"
if [[ "$MERC_CODE" =~ ^(200|400|401)$ ]]; then
    echo "  ${PASS}  Mercure reachable (HTTP ${MERC_CODE})"
else
    echo "  ${FAIL}  Mercure not reachable at ${MERCURE_URL} (HTTP ${MERC_CODE})"
    echo "  ${DIM}     Run ./scripts/start-dev.sh first${RESET}"
    exit 1
fi

echo "  ${ARROW}  Recording: ${BOLD}${LABEL}${RESET}"
echo "  ${ARROW}  Writing:   ${BOLD}${EVENTS_FILE}${RESET}"
echo ""
echo "  ${DIM}Start the consultation in the browser now.${RESET}"
echo "  ${DIM}Press Ctrl-C once the clinical note has rendered.${RESET}"
echo ""
echo "  ${DIM}$(printf '─%.0s' {1..56})${RESET}"
echo ""

QUERY=""
for topic in "${TOPICS[@]}"; do
    QUERY+="&topic=$(printf '%s' "$topic" | sed 's/{/%7B/g; s/}/%7D/g; s|/|%2F|g')"
done
QUERY="${QUERY:1}"

SEGMENTS=0
ROLE_UPDATES=0
OTHER=0

# Each SSE frame arrives as `data: {...}`; blank lines separate frames. Stamping
# on arrival gives an ordering key even when payloads carry no timestamp.
record_line() {
    local payload="$1"
    local ts
    ts="$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)"
    printf '{"received_at":"%s","payload":%s}\n' "$ts" "$payload" >> "$EVENTS_FILE"

    case "$payload" in
        *'"type":"segment"'*)      SEGMENTS=$((SEGMENTS + 1)) ;;
        *'"type":"role_update"'*)  ROLE_UPDATES=$((ROLE_UPDATES + 1)) ;;
        *)                         OTHER=$((OTHER + 1)) ;;
    esac

    # A finalized or quality frame means the visit closed; surface it loudly so
    # the operator knows the note is the only thing still outstanding.
    case "$payload" in
        *'"type":"quality"'*)
            echo "  ${PASS}  quality record received - session finalized" ;;
        *'"type":"finalized"'*)
            echo "  ${PASS}  finalized event received" ;;
        *'"type":"summary"'*|*'"sections"'*)
            echo "  ${PASS}  summary received - safe to stop with Ctrl-C" ;;
    esac

    printf '\r  %s  segments=%s  roles=%s  other=%s' "$ARROW" "$SEGMENTS" "$ROLE_UPDATES" "$OTHER"
}

on_exit() {
    echo ""
    echo ""
    echo "  ${DIM}$(printf '─%.0s' {1..56})${RESET}"
    echo "  ${PASS}  Recorded $((SEGMENTS + ROLE_UPDATES + OTHER)) events"
    echo "  ${ARROW}  ${BOLD}${EVENTS_FILE}${RESET}"
    echo ""
    if [[ -s "$EVENTS_FILE" ]]; then
        local sid
        sid="$(grep -o '"session_id":"[^"]*"' "$EVENTS_FILE" 2>/dev/null | head -1 | cut -d'"' -f4)"
        if [[ -n "$sid" ]]; then
            echo "  Next: ${BOLD}./scripts/capture-manual-session.sh ${sid}${RESET}"
            echo "  ${DIM}Run it before stopping the stack - the session store and${RESET}"
            echo "  ${DIM}container logs disappear when the container exits.${RESET}"
        fi
    fi
    echo ""
    exit 0
}
trap on_exit INT TERM

curl -sN -H 'Accept: text/event-stream' "${MERCURE_URL}?${QUERY}" 2>/dev/null | while IFS= read -r line; do
    [[ "$line" == data:* ]] || continue
    payload="${line#data: }"
    [[ "$payload" == \{* ]] || continue
    record_line "$payload"
done

on_exit
