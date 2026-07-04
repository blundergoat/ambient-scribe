#!/usr/bin/env bash
#
# health-check-remote.sh - Comprehensive health check for production infrastructure
#
# USAGE:
#   ./scripts/health-check-remote.sh           # Run all checks
#   ./scripts/health-check-remote.sh --quick   # Skip slow checks (logs analysis)
#   ./scripts/health-check-remote.sh --logs    # Only check CloudWatch logs
#   ./scripts/health-check-remote.sh --ecs     # Only check ECS service
#   ./scripts/health-check-remote.sh --api     # Only check production API
#   ./scripts/health-check-remote.sh --secrets # Only check Secrets Manager
#
# WHAT IT CHECKS:
#   1. AWS Credentials - Verify we can authenticate
#   2. Secrets Manager - API key secret exists and is accessible
#   3. ECS Service - Agent service health, task status, deployments
#   4. Production API - Health endpoint, invoke endpoint
#   5. CloudWatch Logs - Recent errors and warnings
#   6. DynamoDB - Table status
#

set -euo pipefail

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AWS_CLI="${SCRIPT_DIR}/aws-cli.sh"

# AWS Resources
ECS_CLUSTER="ambient-scribe-cluster"
ECS_SERVICE="ambient-scribe-agent"
LOG_GROUP_AGENT="/ecs/ambient-scribe-prod-agent"
DYNAMODB_TABLE="ambient-scribe-prod-sessions"
SECRET_PATH="${SECRET_PATH:-REPLACE_WITH_SECRET_PATH}"

# Production URL
PROD_API_URL="${PROD_API_URL:-https://scribe.blundergoat.com}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# Counters
CHECKS_PASSED=0
CHECKS_FAILED=0
CHECKS_WARNED=0

# Helpers
info()      { echo -e "${BLUE}[INFO]${NC} $*"; }
success()   { echo -e "${GREEN}[OK]${NC} $*"; ((CHECKS_PASSED++)); }
warn()      { echo -e "${YELLOW}[WARN]${NC} $*"; ((CHECKS_WARNED++)); }
error()     { echo -e "${RED}[ERROR]${NC} $*"; ((CHECKS_FAILED++)); }
header()    { echo -e "\n${BOLD}${BLUE}--- $* ---${NC}"; }
subheader() { echo -e "\n${CYAN}> $*${NC}"; }

check_prerequisites() {
    if [[ ! -x "${AWS_CLI}" ]]; then
        echo -e "${RED}Error: AWS CLI wrapper not found: ${AWS_CLI}${NC}"
        exit 1
    fi

    if ! command -v jq &> /dev/null; then
        echo -e "${RED}Error: jq is not installed. Install with: sudo apt install jq${NC}"
        exit 1
    fi

    if ! command -v curl &> /dev/null; then
        echo -e "${RED}Error: curl is not installed${NC}"
        exit 1
    fi
}

# =============================================================================
# AWS Credentials
# =============================================================================
check_aws_credentials() {
    header "AWS Credentials"

    if IDENTITY=$("${AWS_CLI}" sts get-caller-identity 2>&1); then
        ACCOUNT=$(echo "${IDENTITY}" | jq -r '.Account')
        ARN=$(echo "${IDENTITY}" | jq -r '.Arn')
        success "Authenticated to AWS account ${ACCOUNT}"
        info "  Identity: ${ARN}"
    else
        error "AWS credentials not valid or expired"
        info "  Run: aws sso login --profile aws_devgoat"
        return 1
    fi
}

# =============================================================================
# Secrets Manager
# =============================================================================
check_secrets() {
    header "Secrets Manager"

    # Missing secret configuration means the operator has not selected which deployed API key the UI should probe.
    if [[ "${SECRET_PATH}" == "REPLACE_WITH_SECRET_PATH" ]]; then
        warn "API key: set SECRET_PATH to the deployed Secrets Manager path before checking secrets"
        return 0
    fi

    if "${AWS_CLI}" secretsmanager describe-secret --secret-id "${SECRET_PATH}" &>/dev/null; then
        if VALUE=$("${AWS_CLI}" secretsmanager get-secret-value \
            --secret-id "${SECRET_PATH}" \
            --query 'SecretString' \
            --output text 2>/dev/null); then
            if [[ -n "${VALUE}" && "${VALUE}" != "null" ]]; then
                success "API key: accessible (${#VALUE} chars)"
            else
                error "API key: empty value"
            fi
        else
            error "API key: cannot read value"
        fi
    else
        error "API key: NOT FOUND at ${SECRET_PATH}"
    fi
}

# =============================================================================
# ECS Service
# =============================================================================
check_ecs() {
    header "ECS Service (Agent)"

    subheader "Service Status"
    SERVICE_INFO=$("${AWS_CLI}" ecs describe-services \
        --cluster "${ECS_CLUSTER}" \
        --services "${ECS_SERVICE}" 2>&1) || {
        error "Failed to describe ECS service"
        return 1
    }

    SERVICE_STATUS=$(echo "${SERVICE_INFO}" | jq -r '.services[0].status // "NOT_FOUND"')
    DESIRED_COUNT=$(echo "${SERVICE_INFO}" | jq -r '.services[0].desiredCount // 0')
    RUNNING_COUNT=$(echo "${SERVICE_INFO}" | jq -r '.services[0].runningCount // 0')
    PENDING_COUNT=$(echo "${SERVICE_INFO}" | jq -r '.services[0].pendingCount // 0')

    if [[ "${SERVICE_STATUS}" == "ACTIVE" ]]; then
        success "Service status: ACTIVE"
    else
        error "Service status: ${SERVICE_STATUS}"
    fi

    if [[ "${RUNNING_COUNT}" -eq "${DESIRED_COUNT}" && "${DESIRED_COUNT}" -gt 0 ]]; then
        success "Tasks: ${RUNNING_COUNT}/${DESIRED_COUNT} running"
    elif [[ "${RUNNING_COUNT}" -lt "${DESIRED_COUNT}" ]]; then
        warn "Tasks: ${RUNNING_COUNT}/${DESIRED_COUNT} running (${PENDING_COUNT} pending)"
    else
        error "Tasks: ${RUNNING_COUNT}/${DESIRED_COUNT} running"
    fi

    subheader "Deployments"
    DEPLOYMENTS=$(echo "${SERVICE_INFO}" | jq -r '.services[0].deployments')
    DEPLOY_COUNT=$(echo "${DEPLOYMENTS}" | jq 'length')

    if [[ "${DEPLOY_COUNT}" -eq 1 ]]; then
        DEPLOY_STATUS=$(echo "${DEPLOYMENTS}" | jq -r '.[0].status')
        DEPLOY_RUNNING=$(echo "${DEPLOYMENTS}" | jq -r '.[0].runningCount')
        success "Single deployment: ${DEPLOY_STATUS} (${DEPLOY_RUNNING} tasks)"
    else
        warn "Multiple deployments in progress (${DEPLOY_COUNT})"
        echo "${DEPLOYMENTS}" | jq -r '.[] | "    \(.status): \(.runningCount)/\(.desiredCount) tasks"'
    fi

    subheader "Task Health"
    TASKS=$("${AWS_CLI}" ecs list-tasks \
        --cluster "${ECS_CLUSTER}" \
        --service-name "${ECS_SERVICE}" 2>&1) || {
        warn "Failed to list tasks"
        return 0
    }

    TASK_ARNS=$(echo "${TASKS}" | jq -r '.taskArns[]' 2>/dev/null || echo "")

    if [[ -n "${TASK_ARNS}" ]]; then
        TASK_DETAILS=$("${AWS_CLI}" ecs describe-tasks \
            --cluster "${ECS_CLUSTER}" \
            --tasks ${TASK_ARNS} 2>&1) || {
            warn "Failed to describe tasks"
            return 0
        }

        echo "${TASK_DETAILS}" | jq -r '.tasks[] |
            "    Task \(.taskArn | split("/") | .[-1]): \(.lastStatus) (health: \(.healthStatus // "N/A"))"'

        UNHEALTHY=$(echo "${TASK_DETAILS}" | jq '[.tasks[] | select(.healthStatus == "UNHEALTHY")] | length')
        if [[ "${UNHEALTHY}" -gt 0 ]]; then
            error "${UNHEALTHY} unhealthy task(s)"
        else
            success "All tasks healthy"
        fi
    else
        warn "No running tasks found"
    fi
}

# =============================================================================
# Production API
# =============================================================================
check_production_api() {
    header "Production API"

    subheader "Health Endpoint"
    HEALTH_STATUS=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "${PROD_API_URL}/health" 2>/dev/null || echo "000")

    if [[ "${HEALTH_STATUS}" == "200" ]]; then
        success "${PROD_API_URL}/health -> 200 OK"

        HEALTH_BODY=$(curl -s --max-time 10 "${PROD_API_URL}/health" 2>/dev/null || echo "{}")
        if echo "${HEALTH_BODY}" | jq -e '.' &>/dev/null; then
            info "  Response: ${HEALTH_BODY}"
        fi
    elif [[ "${HEALTH_STATUS}" == "000" ]]; then
        error "${PROD_API_URL}/health -> UNREACHABLE"
    else
        error "${PROD_API_URL}/health -> ${HEALTH_STATUS}"
    fi

    subheader "Invoke Endpoint (OPTIONS)"
    INVOKE_STATUS=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 -X OPTIONS "${PROD_API_URL}/invoke" 2>/dev/null || echo "000")

    if [[ "${INVOKE_STATUS}" != "000" ]]; then
        success "${PROD_API_URL}/invoke -> reachable (HTTP ${INVOKE_STATUS})"
    else
        error "${PROD_API_URL}/invoke -> UNREACHABLE"
    fi
}

# =============================================================================
# DynamoDB
# =============================================================================
check_dynamodb() {
    header "DynamoDB"

    TABLE_INFO=$("${AWS_CLI}" dynamodb describe-table \
        --table-name "${DYNAMODB_TABLE}" 2>&1) || {
        error "Table ${DYNAMODB_TABLE} not found"
        return 1
    }

    TABLE_STATUS=$(echo "${TABLE_INFO}" | jq -r '.Table.TableStatus // "UNKNOWN"')
    ITEM_COUNT=$(echo "${TABLE_INFO}" | jq -r '.Table.ItemCount // 0')

    if [[ "${TABLE_STATUS}" == "ACTIVE" ]]; then
        success "Table ${DYNAMODB_TABLE}: ACTIVE (~${ITEM_COUNT} items)"
    else
        error "Table ${DYNAMODB_TABLE}: ${TABLE_STATUS}"
    fi
}

# =============================================================================
# CloudWatch Logs
# =============================================================================
check_cloudwatch_logs() {
    header "CloudWatch Logs"

    subheader "Agent Logs (${LOG_GROUP_AGENT})"

    if ! "${AWS_CLI}" logs describe-log-groups --log-group-name-prefix "${LOG_GROUP_AGENT}" --query 'logGroups[0].logGroupName' --output text &>/dev/null; then
        warn "Log group ${LOG_GROUP_AGENT} not found"
        return 0
    fi

    # Check for errors in last 15 minutes
    START_TIME=$(($(date +%s) - 900))000

    ERRORS=$("${AWS_CLI}" logs filter-log-events \
        --log-group-name "${LOG_GROUP_AGENT}" \
        --start-time "${START_TIME}" \
        --filter-pattern "?ERROR ?error ?Error ?FATAL ?fatal ?panic ?Traceback" \
        --limit 10 \
        --query 'events[*].message' \
        --output json 2>/dev/null || echo "[]")

    ERROR_COUNT=$(echo "${ERRORS}" | jq 'length')

    if [[ "${ERROR_COUNT}" -eq 0 ]]; then
        success "No errors in last 15 minutes"
    else
        warn "${ERROR_COUNT} error(s) in last 15 minutes"
        echo -e "${YELLOW}Recent errors:${NC}"
        echo "${ERRORS}" | jq -r '.[:5][] | "    " + (. | gsub("\n"; " ") | .[0:120])'
    fi

    # Check for recent activity
    RECENT_LOGS=$("${AWS_CLI}" logs filter-log-events \
        --log-group-name "${LOG_GROUP_AGENT}" \
        --start-time "${START_TIME}" \
        --limit 1 \
        --query 'events | length(@)' \
        --output text 2>/dev/null || echo "0")

    if [[ "${RECENT_LOGS}" -gt 0 ]]; then
        success "Log activity detected in last 15 minutes"
    else
        warn "No log activity in last 15 minutes"
    fi
}

# =============================================================================
# Summary
# =============================================================================
print_summary() {
    header "Summary"

    echo ""
    echo -e "  ${GREEN}Passed:${NC}   ${CHECKS_PASSED}"
    echo -e "  ${YELLOW}Warnings:${NC} ${CHECKS_WARNED}"
    echo -e "  ${RED}Failed:${NC}   ${CHECKS_FAILED}"
    echo ""

    if [[ ${CHECKS_FAILED} -gt 0 ]]; then
        echo -e "${RED}${BOLD}Some checks failed!${NC}"
        echo ""
        echo "Troubleshooting:"
        echo "  - View logs:     ./scripts/aws-cli.sh logs tail ${LOG_GROUP_AGENT} --follow"
        echo "  - Check secret:  ./scripts/secrets-manager-get.sh"
        echo "  - ECS service:   ./scripts/aws-cli.sh ecs describe-services --cluster ${ECS_CLUSTER} --services ${ECS_SERVICE}"
        echo ""
        return 1
    elif [[ ${CHECKS_WARNED} -gt 0 ]]; then
        echo -e "${YELLOW}${BOLD}All critical checks passed with warnings${NC}"
        return 0
    else
        echo -e "${GREEN}${BOLD}All checks passed!${NC}"
        return 0
    fi
}

# =============================================================================
# Help
# =============================================================================
show_help() {
    echo "Usage: $0 [options]"
    echo ""
    echo "Options:"
    echo "  --quick       Skip slow checks (CloudWatch log analysis)"
    echo "  --logs        Only check CloudWatch logs"
    echo "  --ecs         Only check ECS service"
    echo "  --api         Only check production API"
    echo "  --secrets     Only check Secrets Manager"
    echo "  --dynamodb    Only check DynamoDB table"
    echo "  --help, -h    Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0                    # Run all checks"
    echo "  $0 --quick            # Skip log analysis for faster check"
    echo "  $0 --api --ecs        # Only check API and ECS"
    echo ""
    echo "Environment Variables:"
    echo "  PROD_API_URL    Production API URL (default: https://scribe.blundergoat.com)"
}

# =============================================================================
# Main
# =============================================================================
main() {
    local check_all=true
    local check_logs_only=false
    local check_ecs_only=false
    local check_api_only=false
    local check_secrets_only=false
    local check_dynamodb_only=false
    local skip_logs=false

    while [[ $# -gt 0 ]]; do
        case $1 in
            --quick)     skip_logs=true; shift ;;
            --logs)      check_logs_only=true; check_all=false; shift ;;
            --ecs)       check_ecs_only=true; check_all=false; shift ;;
            --api)       check_api_only=true; check_all=false; shift ;;
            --secrets)   check_secrets_only=true; check_all=false; shift ;;
            --dynamodb)  check_dynamodb_only=true; check_all=false; shift ;;
            --help|-h)   show_help; exit 0 ;;
            *)           echo -e "${RED}Unknown option: $1${NC}"; show_help; exit 1 ;;
        esac
    done

    echo -e "${BLUE}"
    echo "  +===============================================================+"
    echo "  |     ambient-scribe - Remote Health Check                    |"
    echo "  +===============================================================+"
    echo -e "${NC}"

    check_prerequisites

    if [[ "${check_all}" == "true" ]]; then
        check_aws_credentials || exit 1
        check_secrets
        check_ecs
        check_production_api
        check_dynamodb
        if [[ "${skip_logs}" != "true" ]]; then
            check_cloudwatch_logs
        else
            info "Skipping CloudWatch log analysis (--quick mode)"
        fi
    else
        check_aws_credentials || exit 1
        [[ "${check_secrets_only}" == "true" ]] && check_secrets
        [[ "${check_ecs_only}" == "true" ]] && check_ecs
        [[ "${check_api_only}" == "true" ]] && check_production_api
        [[ "${check_dynamodb_only}" == "true" ]] && check_dynamodb
        [[ "${check_logs_only}" == "true" ]] && check_cloudwatch_logs
    fi

    print_summary
}

main "$@"
