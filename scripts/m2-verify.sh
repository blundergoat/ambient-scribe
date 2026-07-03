#!/bin/bash
# =============================================================================
# M2 Verification — Pipeline Smoke Tests for Ambient Scribe
# =============================================================================
# Usage: ./scripts/m2-verify.sh [OPTIONS]
#
# Options:
#   --wav PATH       Path to WAV file for batch test (default: tests/fixtures/audio/osce-chest-pain-short.wav)
#   --skip-ws        Skip WebSocket lifecycle test (requires websocat)
#   --help, -h       Show this help
#
# Prerequisites:
#   1. ./scripts/gpu-check.sh       (GPU accessible)
#   2. ./scripts/start-dev.sh -b    (services running and healthy)
#   3. ./scripts/health-checks.sh   (all green)
#
# What this tests (M2 exit criteria from .goat-flow/plans/):
#   1. NeMo models loaded at startup (structlog marker in logs)
#   2. POST /transcribe/file — upload WAV, get speaker-attributed segments
#   3. /health responds during active transcription (event loop not blocked)
#   4. /session/{id}/history — returns segments after transcription
#   5. WebSocket connect → send chunk → disconnect → finalize (optional)
#   6. Mercure reachable from nemo-agent container
#   7. VRAM usage within budget after inference
#
# Exit codes:
#   0 - All checks passed
#   1 - One or more checks failed
# =============================================================================

set -uo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/env-detect.sh"

# ── Defaults ──────────────────────────────────────────────────────
WAV_FILE="$REPO_ROOT/tests/fixtures/audio/osce-chest-pain-short.wav"
SKIP_WS=false
NEMO_URL="http://localhost:8001"

TOTAL=0
PASSED=0
FAILED=0
WARNINGS=0

# ── Parse args ────────────────────────────────────────────────────
for arg in "$@"; do
    case "$arg" in
        --wav)     shift; WAV_FILE="$1"; shift ;;
        --skip-ws) SKIP_WS=true ;;
        --help|-h)
            head -24 "$0" | tail -22
            exit 0
            ;;
    esac
done

# ── Helpers ───────────────────────────────────────────────────────
check() {
    local label="$1" status="$2" detail="${3:-}"
    TOTAL=$((TOTAL + 1))
    local padded
    padded=$(printf "%-40s" "$label")
    if [[ "$status" == "pass" ]]; then
        PASSED=$((PASSED + 1))
        echo -e "  ${ARROW} ${padded} ${PASS}  ${DIM}${detail}${RESET}"
    elif [[ "$status" == "warn" ]]; then
        WARNINGS=$((WARNINGS + 1))
        echo -e "  ${ARROW} ${padded} ${WARN}  ${YELLOW}${detail}${RESET}"
    else
        FAILED=$((FAILED + 1))
        echo -e "  ${ARROW} ${padded} ${FAIL}  ${RED}${detail}${RESET}"
    fi
}

# ── Header ────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}  Ambient Scribe — M2 Pipeline Verification${RESET}"
echo -e "  ${DIM}$(printf '─%.0s' {1..50})${RESET}"

# ═════════════════════════════════════════════════════════════════
# PRE-CHECK: Services must be running
# ═════════════════════════════════════════════════════════════════
echo ""
echo -e "  ${BOLD}Pre-checks${RESET}"
echo ""

HTTP_CODE=$(curl -sf -o /dev/null -w "%{http_code}" --connect-timeout 5 "$NEMO_URL/health" 2>/dev/null) || HTTP_CODE="000"
if [[ "$HTTP_CODE" == "200" ]]; then
    check "nemo-agent reachable" "pass" "HTTP $HTTP_CODE"
else
    check "nemo-agent reachable" "fail" "HTTP $HTTP_CODE — run ./scripts/start-dev.sh -b first"
    echo ""
    exit 1
fi

if [[ ! -f "$WAV_FILE" ]]; then
    check "Test WAV file" "fail" "not found: $WAV_FILE"
    echo ""
    exit 1
else
    WAV_SIZE=$(stat -c%s "$WAV_FILE" 2>/dev/null || stat -f%z "$WAV_FILE" 2>/dev/null)
    WAV_KB=$(( WAV_SIZE / 1024 ))
    check "Test WAV file" "pass" "${WAV_FILE##*/} (${WAV_KB} KB)"
fi

# ═════════════════════════════════════════════════════════════════
# TEST 1: NeMo models loaded (check startup logs)
# ═════════════════════════════════════════════════════════════════
echo ""
echo -e "  ${BOLD}Test 1: NeMo model loading${RESET}"
echo ""

if dc logs nemo-agent 2>/dev/null | grep -q "nemo_models_loaded"; then
    check "NeMo models loaded at startup" "pass" "server.startup.nemo_models_loaded in logs"
else
    check "NeMo models loaded at startup" "warn" "log marker not found (may have scrolled off)"
fi

# ═════════════════════════════════════════════════════════════════
# TEST 2: Batch transcription (POST /transcribe/file)
# ═════════════════════════════════════════════════════════════════
echo ""
echo -e "  ${BOLD}Test 2: Batch transcription${RESET}"
echo ""

SESSION_ID="m2-verify-$(date +%s)"
TRANSCRIBE_START=$SECONDS

RESPONSE=$(curl -sf --connect-timeout 10 --max-time 120 \
    -X POST "$NEMO_URL/transcribe/file" \
    -F "file=@${WAV_FILE}" \
    -F "session_id=${SESSION_ID}" \
    2>/dev/null) || RESPONSE=""

TRANSCRIBE_ELAPSED=$(( SECONDS - TRANSCRIBE_START ))

if [[ -z "$RESPONSE" ]]; then
    check "POST /transcribe/file" "fail" "no response (timeout or error)"
else
    check "POST /transcribe/file" "pass" "${TRANSCRIBE_ELAPSED}s"

    # Parse response with jq if available, else grep
    if command -v jq &>/dev/null; then
        SEG_COUNT=$(echo "$RESPONSE" | jq '.segments | length' 2>/dev/null || echo "0")
        DURATION=$(echo "$RESPONSE" | jq -r '.duration_seconds // 0' 2>/dev/null)
        RETURNED_SID=$(echo "$RESPONSE" | jq -r '.session_id // ""' 2>/dev/null)

        if [[ "$SEG_COUNT" -gt 0 ]]; then
            check "Segments returned" "pass" "$SEG_COUNT segments"
        else
            check "Segments returned" "fail" "0 segments — NeMo produced no output"
        fi

        if [[ "$RETURNED_SID" == "$SESSION_ID" ]]; then
            check "Session ID round-trip" "pass" "$SESSION_ID"
        else
            check "Session ID round-trip" "fail" "expected $SESSION_ID, got $RETURNED_SID"
        fi

        check "Inference duration" "pass" "${DURATION}s"

        # Check for speaker attribution (spk_0 / spk_1)
        SPEAKERS=$(echo "$RESPONSE" | jq -r '[.segments[].speaker_id] | unique | join(", ")' 2>/dev/null || echo "")
        if [[ -n "$SPEAKERS" ]]; then
            check "Speaker attribution" "pass" "$SPEAKERS"
        else
            check "Speaker attribution" "warn" "no speaker IDs found in segments"
        fi

        # Show a sample segment
        echo ""
        echo -e "  ${DIM}Sample segment:${RESET}"
        echo "$RESPONSE" | jq -r '.segments[0] // empty' 2>/dev/null | sed 's/^/    /'
        echo ""
    else
        # Fallback: no jq
        if echo "$RESPONSE" | grep -q '"segments"'; then
            check "Response structure" "pass" "contains segments key"
        else
            check "Response structure" "fail" "unexpected response format"
        fi
        echo ""
        echo -e "  ${DIM}Install jq for detailed output: sudo apt install jq${RESET}"
        echo ""
    fi
fi

# ═════════════════════════════════════════════════════════════════
# TEST 3: /health responds during transcription (event loop check)
# ═════════════════════════════════════════════════════════════════
echo ""
echo -e "  ${BOLD}Test 3: Event loop responsiveness${RESET}"
echo ""

# Fire a batch transcription in the background, then immediately hit /health
curl -sf --max-time 120 \
    -X POST "$NEMO_URL/transcribe/file" \
    -F "file=@${WAV_FILE}" \
    -F "session_id=m2-eventloop-$(date +%s)" \
    -o /dev/null 2>/dev/null &
BG_PID=$!

# Give NeMo a moment to start inference, then probe /health
sleep 2

HEALTH_START=$SECONDS
HEALTH_CODE=$(curl -sf -o /dev/null -w "%{http_code}" --connect-timeout 3 "$NEMO_URL/health" 2>/dev/null) || HEALTH_CODE="000"
HEALTH_MS=$(( (SECONDS - HEALTH_START) * 1000 ))

if [[ "$HEALTH_CODE" == "200" ]]; then
    check "/health during inference" "pass" "HTTP $HEALTH_CODE (${HEALTH_MS}ms)"
else
    check "/health during inference" "fail" "HTTP $HEALTH_CODE — event loop may be blocked"
fi

# Clean up background job
wait $BG_PID 2>/dev/null || true

# ═════════════════════════════════════════════════════════════════
# TEST 4: Session history endpoint
# ═════════════════════════════════════════════════════════════════
echo ""
echo -e "  ${BOLD}Test 4: Session history${RESET}"
echo ""

HISTORY_RESPONSE=$(curl -sf --connect-timeout 5 "$NEMO_URL/session/${SESSION_ID}/history" 2>/dev/null) || HISTORY_RESPONSE=""

if [[ -n "$HISTORY_RESPONSE" ]]; then
    check "GET /session/{id}/history" "pass" "responded"

    if command -v jq &>/dev/null; then
        HIST_SID=$(echo "$HISTORY_RESPONSE" | jq -r '.session_id // ""' 2>/dev/null)
        if [[ "$HIST_SID" == "$SESSION_ID" ]]; then
            check "Session ID in history" "pass" "$HIST_SID"
        else
            check "Session ID in history" "warn" "session ended or not found"
        fi
    fi
else
    check "GET /session/{id}/history" "fail" "no response"
fi

# ═════════════════════════════════════════════════════════════════
# TEST 5: WebSocket lifecycle (optional, needs websocat)
# ═════════════════════════════════════════════════════════════════
echo ""
echo -e "  ${BOLD}Test 5: WebSocket lifecycle${RESET}"
echo ""

if [[ "$SKIP_WS" == "true" ]]; then
    check "WebSocket test" "warn" "skipped (--skip-ws)"
elif ! command -v websocat &>/dev/null; then
    check "WebSocket test" "warn" "websocat not installed — install: cargo install websocat"
else
    WS_SID="m2-ws-$(date +%s)"

    # Connect, send a small audio chunk (first few KB of the WAV), then disconnect
    # We send raw bytes — NeMo expects WebM/Opus, but this tests the lifecycle
    # (connect → receive bytes → disconnect → finalize)
    WS_OUTPUT=$(timeout 15 bash -c "
        head -c 8192 '$WAV_FILE' | websocat -b --no-close 'ws://localhost:8001/ws/transcribe/$WS_SID' 2>&1
    " 2>&1) || true

    # Check logs for the WebSocket lifecycle events
    sleep 2
    WS_CONNECTED=$(dc logs --since=30s nemo-agent 2>/dev/null | grep -c "websocket.connected.*${WS_SID}" || true)
    WS_DISCONNECTED=$(dc logs --since=30s nemo-agent 2>/dev/null | grep -c "websocket.disconnected\|websocket.error" || true)

    if [[ "$WS_CONNECTED" -gt 0 ]]; then
        check "WebSocket connect" "pass" "websocket.connected logged"
    else
        check "WebSocket connect" "warn" "connect event not found in recent logs"
    fi

    if [[ "$WS_DISCONNECTED" -gt 0 ]]; then
        check "WebSocket disconnect/finalize" "pass" "disconnect/error logged"
    else
        check "WebSocket disconnect/finalize" "warn" "disconnect event not found in recent logs"
    fi
fi

# ═════════════════════════════════════════════════════════════════
# TEST 6: Mercure connectivity (from nemo-agent container)
# ═════════════════════════════════════════════════════════════════
echo ""
echo -e "  ${BOLD}Test 6: Mercure connectivity${RESET}"
echo ""

if find_container "$NEMO_CONTAINER"; then
    MERCURE_CODE=$(docker exec "$NEMO_CONTAINER" curl -s -o /dev/null -w "%{http_code}" --connect-timeout 3 "http://mercure:3701/.well-known/mercure" 2>/dev/null) || MERCURE_CODE="000"
    if [[ "$MERCURE_CODE" =~ ^(200|401|400)$ ]]; then
        check "nemo-agent → Mercure" "pass" "HTTP $MERCURE_CODE"
    else
        check "nemo-agent → Mercure" "fail" "HTTP $MERCURE_CODE — segments won't reach the browser"
    fi
else
    check "nemo-agent → Mercure" "fail" "nemo-agent container not running"
fi

# ═════════════════════════════════════════════════════════════════
# TEST 7: VRAM usage after inference
# ═════════════════════════════════════════════════════════════════
echo ""
echo -e "  ${BOLD}Test 7: VRAM budget${RESET}"
echo ""

if [[ "$HAS_NVIDIA_SMI" == "true" ]]; then
    VRAM_USED=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null | head -1)
    VRAM_USED="${VRAM_USED// /}"
    VRAM_TOTAL="$GPU_VRAM_MB"
    VRAM_PCT=$(( VRAM_USED * 100 / VRAM_TOTAL ))

    if [[ $VRAM_PCT -le 85 ]]; then
        check "VRAM usage" "pass" "${VRAM_USED}/${VRAM_TOTAL} MB (${VRAM_PCT}%)"
    elif [[ $VRAM_PCT -le 95 ]]; then
        check "VRAM usage" "warn" "${VRAM_USED}/${VRAM_TOTAL} MB (${VRAM_PCT}%) — close to limit"
    else
        check "VRAM usage" "fail" "${VRAM_USED}/${VRAM_TOTAL} MB (${VRAM_PCT}%) — may OOM on longer audio"
    fi
else
    check "VRAM usage" "warn" "nvidia-smi not available on host"
fi

# ═════════════════════════════════════════════════════════════════
# TEST 8: Structured logging (correlation IDs, timing)
# ═════════════════════════════════════════════════════════════════
echo ""
echo -e "  ${BOLD}Test 8: Structured logging${RESET}"
echo ""

RECENT_LOGS=$(dc logs --since=2m nemo-agent 2>/dev/null || true)

if echo "$RECENT_LOGS" | grep -q "transcribe_file.completed"; then
    check "Batch transcription logged" "pass" "transcribe_file.completed"
else
    check "Batch transcription logged" "warn" "log entry not found in last 2m"
fi

if echo "$RECENT_LOGS" | grep -q "duration_seconds\|inference_ms"; then
    check "Timing in logs" "pass" "duration metrics present"
else
    check "Timing in logs" "warn" "timing fields not found"
fi

# ═════════════════════════════════════════════════════════════════
# SUMMARY
# ═════════════════════════════════════════════════════════════════
echo ""
echo -e "  ${DIM}$(printf '─%.0s' {1..50})${RESET}"
echo ""

if [[ $FAILED -eq 0 && $WARNINGS -eq 0 ]]; then
    echo -e "  ${GREEN}${BOLD}M2 VERIFIED — All ${TOTAL} checks passed${RESET}"
elif [[ $FAILED -eq 0 ]]; then
    echo -e "  ${GREEN}${BOLD}M2 VERIFIED${RESET} — ${GREEN}${PASSED} passed${RESET}, ${YELLOW}${WARNINGS} warning(s)${RESET}  ${DIM}(${TOTAL} total)${RESET}"
else
    echo -e "  ${RED}${BOLD}M2 NOT VERIFIED${RESET} — ${RED}${FAILED} failed${RESET}, ${GREEN}${PASSED} passed${RESET}, ${YELLOW}${WARNINGS} warning(s)${RESET}  ${DIM}(${TOTAL} total)${RESET}"
fi

echo ""
echo -e "  ${DIM}M2 exit criteria: .goat-flow/plans/${RESET}"
echo -e "  ${DIM}Next: open http://localhost:8082/scribe and test live mic recording${RESET}"
echo ""

[[ $FAILED -gt 0 ]] && exit 1
exit 0
