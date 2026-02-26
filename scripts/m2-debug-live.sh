#!/bin/bash
# =============================================================================
# M2 Debug — Live Transcription Pipeline Debugger
# =============================================================================
# Usage: ./scripts/m2-debug-live.sh [SESSION_ID]
#
# Subscribes to Mercure SSE and tails nemo-agent logs while you test in
# the browser. Run this in one terminal, then click "Start Consultation".
#
# If no SESSION_ID is given, subscribes to ALL scribe sessions (wildcard).
#
# What to look for:
#   1. "websocket.connected" in logs     → WebSocket accepted
#   2. "chunk_processed" in logs          → NeMo produced output
#   3. "MERCURE EVENT" lines              → Segments reached Mercure
#   4. Events in browser                  → Full pipeline works
#
# Prerequisites:
#   ./scripts/start-dev.sh -b    (services running and healthy)
# =============================================================================

set -uo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/env-detect.sh"

SESSION_ID="${1:-}"
NEMO_URL="http://localhost:8001"
MERCURE_URL="${MERCURE_PUBLIC_URL:-http://localhost:3701/.well-known/mercure}"

# ── Header ──────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}  Ambient Scribe — Live Debug${RESET}"
echo -e "  ${DIM}$(printf '─%.0s' {1..50})${RESET}"
echo ""

# ── Pre-checks ──────────────────────────────────────────────────
HTTP_CODE=$(curl -sf -o /dev/null -w "%{http_code}" --connect-timeout 5 "$NEMO_URL/health" 2>/dev/null) || HTTP_CODE="000"
if [[ "$HTTP_CODE" != "200" ]]; then
    echo -e "  ${FAIL}  nemo-agent not reachable (HTTP ${HTTP_CODE})"
    echo -e "  ${DIM}  Run ./scripts/start-dev.sh -b first${RESET}"
    exit 1
fi
echo -e "  ${PASS}  nemo-agent reachable"

# Check Mercure is reachable
MERC_CODE=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 5 "$MERCURE_URL" 2>/dev/null) || MERC_CODE="000"
if [[ "$MERC_CODE" =~ ^(200|400|401)$ ]]; then
    echo -e "  ${PASS}  Mercure reachable (HTTP ${MERC_CODE})"
else
    echo -e "  ${FAIL}  Mercure not reachable (HTTP ${MERC_CODE})"
    exit 1
fi

# Check MERCURE_JWT inside nemo-agent container
JWT_LEN=$(docker exec "$NEMO_CONTAINER" sh -c 'echo -n "${MERCURE_JWT:-}" | wc -c' 2>/dev/null || echo "0")
JWT_LEN="${JWT_LEN// /}"
if [[ "$JWT_LEN" -gt 0 ]]; then
    echo -e "  ${PASS}  MERCURE_JWT set in container (${JWT_LEN} chars)"
else
    echo -e "  ${FAIL}  MERCURE_JWT empty in container — Mercure publishes will be skipped!"
fi

echo ""

# ── Determine Mercure topic ─────────────────────────────────────
if [[ -n "$SESSION_ID" ]]; then
    TOPIC="scribe/session/${SESSION_ID}/raw"
    echo -e "  ${ARROW}  Watching session: ${BOLD}${SESSION_ID}${RESET}"
else
    # Wildcard: subscribe to all scribe session topics
    TOPIC="scribe/session/{id}/raw"
    echo -e "  ${ARROW}  Watching: ${BOLD}all sessions${RESET} (wildcard)"
    echo -e "  ${DIM}     Tip: pass a session ID to filter: ./scripts/m2-debug-live.sh <uuid>${RESET}"
fi

echo ""
echo -e "  ${DIM}$(printf '─%.0s' {1..50})${RESET}"
echo ""

# ── Quick Mercure publish test ──────────────────────────────────
echo -e "  ${BOLD}Test: Mercure publish round-trip${RESET}"
echo ""

TEST_TOPIC="scribe/debug/test-$(date +%s)"
PUBLISH_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
    -X POST "$MERCURE_URL" \
    -H "Authorization: Bearer ${MERCURE_PUBLISHER_JWT:-}" \
    -d "topic=${TEST_TOPIC}" \
    -d 'data={"type":"debug","msg":"hello"}' \
    2>/dev/null) || PUBLISH_CODE="000"

if [[ "$PUBLISH_CODE" == "200" ]]; then
    echo -e "  ${PASS}  Host → Mercure publish works (HTTP ${PUBLISH_CODE})"
else
    echo -e "  ${WARN}  Host → Mercure publish returned HTTP ${PUBLISH_CODE}"
    echo -e "  ${DIM}     This tests from the host. Container publish may differ.${RESET}"
fi

# Test from inside the nemo-agent container (same path as real publishes)
CONTAINER_CODE=$(docker exec "$NEMO_CONTAINER" \
    sh -c 'curl -s -o /dev/null -w "%{http_code}" \
    -X POST "http://mercure:3701/.well-known/mercure" \
    -H "Authorization: Bearer ${MERCURE_JWT}" \
    -d "topic='"${TEST_TOPIC}"'" \
    -d '"'"'data={"type":"debug","msg":"container-test"}'"'"'' \
    2>/dev/null) || CONTAINER_CODE="000"

if [[ "$CONTAINER_CODE" == "200" ]]; then
    echo -e "  ${PASS}  Container → Mercure publish works (HTTP ${CONTAINER_CODE})"
else
    echo -e "  ${FAIL}  Container → Mercure publish failed (HTTP ${CONTAINER_CODE})"
    echo -e "  ${RED}     This is the publish path used by the live pipeline!${RESET}"
fi

echo ""
echo -e "  ${DIM}$(printf '─%.0s' {1..50})${RESET}"
echo ""

# ── Start Mercure SSE listener ──────────────────────────────────
echo -e "  ${BOLD}Listening for Mercure events + nemo-agent logs${RESET}"
echo -e "  ${DIM}  Open http://localhost:8082/scribe and click Start Consultation${RESET}"
echo -e "  ${DIM}  Press Ctrl+C to stop${RESET}"
echo ""

# Encode topic for URL
ENCODED_TOPIC=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$TOPIC'))" 2>/dev/null || echo "$TOPIC")
SSE_URL="${MERCURE_URL}?topic=${ENCODED_TOPIC}"

# Background: subscribe to Mercure SSE
(
    while true; do
        curl -sN "$SSE_URL" 2>/dev/null | while IFS= read -r line; do
            if [[ "$line" == data:* ]]; then
                TIMESTAMP=$(date '+%H:%M:%S')
                DATA="${line#data:}"
                # Pretty-print with jq, fall back to raw
                if command -v jq &>/dev/null; then
                    TYPE=$(echo "$DATA" | jq -r '.type // "unknown"' 2>/dev/null)
                    SPEAKER=$(echo "$DATA" | jq -r '.speaker_id // ""' 2>/dev/null)
                    TEXT=$(echo "$DATA" | jq -r '.text // ""' 2>/dev/null | head -c 80)
                    TEXT_LEN=${#TEXT}
                    echo -e "  ${GREEN}[${TIMESTAMP}] MERCURE EVENT${RESET}  type=${TYPE}  speaker=${SPEAKER}  text[${TEXT_LEN}]=\"${TEXT}\""
                    # Also dump full JSON for debugging
                    echo "$DATA" | jq -C '.' 2>/dev/null | sed 's/^/                           /'
                else
                    echo -e "  ${GREEN}[${TIMESTAMP}] MERCURE EVENT${RESET}  ${DATA}"
                fi
            fi
        done
        # Reconnect on disconnect
        sleep 1
    done
) &
SSE_PID=$!

# Background: tail nemo-agent logs, filter for relevant events
(
    dc logs -f --since=1s nemo-agent 2>/dev/null | while IFS= read -r line; do
        # Show structlog events and key NeMo messages
        if echo "$line" | grep -qE 'websocket\.|mercure\.|transcription_session\.|chunk_processed|ffmpeg|WebSocket.*accepted|connection open|connection closed'; then
            TIMESTAMP=$(date '+%H:%M:%S')
            # Strip container prefix for readability
            CLEAN=$(echo "$line" | sed 's/^nemo-agent-1  | //')
            echo -e "  ${CYAN}[${TIMESTAMP}] NEMO LOG${RESET}     ${DIM}${CLEAN}${RESET}"
        fi
    done
) &
LOG_PID=$!

# Cleanup on exit
cleanup() {
    kill "$SSE_PID" "$LOG_PID" 2>/dev/null
    wait "$SSE_PID" "$LOG_PID" 2>/dev/null
    echo ""
    echo -e "  ${DIM}Debug session ended${RESET}"
    echo ""
}
trap cleanup EXIT INT TERM

# Wait for Ctrl+C
wait
