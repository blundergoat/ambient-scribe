#!/usr/bin/env bash
# =============================================================================
# API Load Testing Script - ambient-scribe
# =============================================================================
#
# Performance testing for the ambient-scribe agent API using 'hey', 'ab', or 'wrk'.
#
# USAGE:
#   ./scripts/api-load-test.sh                         # Default: test /health
#   ./scripts/api-load-test.sh --endpoint /invoke      # Test invoke endpoint
#   ./scripts/api-load-test.sh --requests 500 -c 20    # Custom load
#   ./scripts/api-load-test.sh --url https://scribe.blundergoat.com
#
# =============================================================================

set -euo pipefail

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

log_info()  { echo -e "${BLUE}[INFO]${NC} $1"; }
log_ok()    { echo -e "${GREEN}[OK]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Default configuration
API_URL="${API_URL:-http://localhost:8000}"
REQUESTS=200
CONCURRENCY=10
ENDPOINT="/health"
DURATION=""

show_help() {
    echo ""
    echo -e "${BLUE}API Load Testing Script - ambient-scribe${NC}"
    echo ""
    echo "Usage:"
    echo "  $0 [options]"
    echo ""
    echo "Options:"
    echo "  -e, --endpoint PATH    API endpoint to test (default: /health)"
    echo "  -n, --requests NUM     Total number of requests (default: 200)"
    echo "  -c, --concurrency NUM  Concurrent requests (default: 10)"
    echo "  -d, --duration SEC     Test duration in seconds (overrides -n)"
    echo "  -u, --url URL          API base URL (default: http://localhost:8000)"
    echo "  --suite                Run all endpoints"
    echo "  -h, --help             Show this help"
    echo ""
    echo "Endpoints:"
    echo "  /health                Health check (fast, no model call)"
    echo "  /invoke                Synchronous agent invocation (slow, calls Bedrock)"
    echo "  /stream                Streaming agent invocation (SSE)"
    echo ""
    echo "Examples:"
    echo "  $0                                       # Default: 200 req to /health"
    echo "  $0 -e /health -n 500 -c 50               # Hammer health endpoint"
    echo "  $0 -e /invoke -n 10 -c 2                  # Light invoke test"
    echo "  $0 -d 30 -c 5                             # 30 seconds sustained"
    echo "  $0 --url https://scribe.blundergoat.com  # Test production"
    echo "  $0 --suite                                # All endpoints"
    echo ""
}

RUN_SUITE=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -e|--endpoint)   ENDPOINT="$2"; shift 2 ;;
        -n|--requests)   REQUESTS="$2"; shift 2 ;;
        -c|--concurrency) CONCURRENCY="$2"; shift 2 ;;
        -d|--duration)   DURATION="$2"; shift 2 ;;
        -u|--url)        API_URL="$2"; shift 2 ;;
        --suite)         RUN_SUITE=true; shift ;;
        -h|--help)       show_help; exit 0 ;;
        *)               log_error "Unknown option: $1"; show_help; exit 1 ;;
    esac
done

# Check for load testing tool
check_tool() {
    if command -v hey &> /dev/null; then
        echo "hey"
    elif command -v ab &> /dev/null; then
        echo "ab"
    elif command -v wrk &> /dev/null; then
        echo "wrk"
    else
        log_error "No load testing tool found"
        echo ""
        echo "Install one of the following:"
        echo "  hey: go install github.com/rakyll/hey@latest"
        echo "  ab:  apt-get install apache2-utils"
        echo "  wrk: apt-get install wrk"
        exit 1
    fi
}

# Check if API is running
check_api() {
    local health_url="${API_URL}/health"
    log_info "Checking API health at ${health_url}..."

    if curl -sf "${health_url}" > /dev/null 2>&1; then
        log_ok "API is responding"
    else
        log_error "API is not responding at ${health_url}"
        log_info "Make sure the agent is running:"
        echo "  docker compose up agent"
        echo "  # or for production:"
        echo "  export API_URL=https://scribe.blundergoat.com"
        exit 1
    fi
}

run_hey() {
    local url="${API_URL}${ENDPOINT}"

    log_info "Running load test with hey..."
    echo ""

    local args=("-c" "$CONCURRENCY")

    if [ -n "$DURATION" ]; then
        args+=("-z" "${DURATION}s")
    else
        args+=("-n" "$REQUESTS")
    fi

    args+=("-H" "Accept: application/json")

    echo -e "${CYAN}Target:      ${url}${NC}"
    echo -e "${CYAN}Concurrency: ${CONCURRENCY}${NC}"
    if [ -n "$DURATION" ]; then
        echo -e "${CYAN}Duration:    ${DURATION}s${NC}"
    else
        echo -e "${CYAN}Requests:    ${REQUESTS}${NC}"
    fi
    echo ""

    hey "${args[@]}" "$url"
}

run_ab() {
    local url="${API_URL}${ENDPOINT}"

    log_info "Running load test with Apache Bench..."
    echo ""

    echo -e "${CYAN}Target:      ${url}${NC}"
    echo -e "${CYAN}Concurrency: ${CONCURRENCY}${NC}"
    echo -e "${CYAN}Requests:    ${REQUESTS}${NC}"
    echo ""

    ab -c "$CONCURRENCY" -n "$REQUESTS" -H "Accept: application/json" "$url"
}

run_wrk() {
    local url="${API_URL}${ENDPOINT}"
    local duration="${DURATION:-30}"

    log_info "Running load test with wrk..."
    echo ""

    echo -e "${CYAN}Target:      ${url}${NC}"
    echo -e "${CYAN}Concurrency: ${CONCURRENCY}${NC}"
    echo -e "${CYAN}Duration:    ${duration}s${NC}"
    echo ""

    wrk -t4 -c"$CONCURRENCY" -d"${duration}s" "$url"
}

run_suite() {
    echo ""
    echo -e "${BLUE}=============================================="
    echo "  Agent Endpoint Test Suite"
    echo "==============================================${NC}"
    echo ""

    local endpoints=("/health")

    for endpoint in "${endpoints[@]}"; do
        echo ""
        echo -e "${CYAN}Testing: ${endpoint}${NC}"
        echo "----------------------------------------"

        ENDPOINT="$endpoint"
        local saved_requests=$REQUESTS
        REQUESTS=100
        run_hey
        REQUESTS=$saved_requests

        echo ""
    done

    echo -e "${YELLOW}Note: /invoke and /stream are not included in the suite"
    echo -e "because they call Bedrock and are expensive. Test manually:${NC}"
    echo "  $0 -e /invoke -n 5 -c 1"
}

run_test() {
    local tool="$1"

    case $tool in
        hey) run_hey ;;
        ab)  run_ab ;;
        wrk) run_wrk ;;
    esac
}

# Main
main() {
    echo ""
    echo -e "${BLUE}=============================================="
    echo "  ambient-scribe - API Load Testing"
    echo "==============================================${NC}"
    echo ""

    local tool
    tool=$(check_tool)
    log_ok "Using load testing tool: ${tool}"

    check_api

    if [[ "${RUN_SUITE}" == "true" ]]; then
        run_suite
    else
        echo ""
        run_test "$tool"
    fi

    echo ""
    log_ok "Load test completed!"
    echo ""
    log_info "Note: WAF rate limiting is configured at 2000 requests per 5 minutes per IP"
    log_info "High concurrency tests may trigger rate limiting (429 responses)"
    echo ""
}

main "$@"
