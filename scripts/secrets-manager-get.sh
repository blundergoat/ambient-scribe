#!/usr/bin/env bash
#
# secrets-manager-get.sh - View secrets from AWS Secrets Manager
#
# USAGE:
#   ./scripts/secrets-manager-get.sh                 # Get the API key
#   ./scripts/secrets-manager-get.sh --list           # List all project secrets
#   ./scripts/secrets-manager-get.sh <secret-path>    # Get a specific secret
#
# EXAMPLES:
#   ./scripts/secrets-manager-get.sh
#   ./scripts/secrets-manager-get.sh /ambient-scribe/prod/api-key
#   ./scripts/secrets-manager-get.sh --list
#   ./scripts/secrets-manager-get.sh --json
#
# SECURITY WARNING:
#   This script outputs secret values to the terminal.
#   Be careful about shell history and screen sharing.
#

set -euo pipefail

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AWS_CLI="${SCRIPT_DIR}/aws-cli.sh"
SECRET_PREFIX="/ambient-scribe"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

info()    { echo -e "${BLUE}[INFO]${NC} $*"; }
success() { echo -e "${GREEN}[OK]${NC} $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $*" >&2; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }

show_help() {
    cat << EOF
Usage: $(basename "$0") [OPTIONS] [secret-path]

View secrets from AWS Secrets Manager for ambient-scribe.

Options:
  --list        List all secrets (names only, no values)
  --json        Output as JSON (for scripting)
  --help        Show this help

Arguments:
  secret-path   Full secret path (default: /ambient-scribe/prod/api-key)

Examples:
  $(basename "$0")                                    # Get the API key
  $(basename "$0") /ambient-scribe/prod/api-key          # Same, explicit path
  $(basename "$0") --list                              # List all secrets
  $(basename "$0") --json                              # JSON output

SECURITY WARNING:
  Secret values are output to the terminal. Be careful with:
  - Shell history (consider: HISTCONTROL=ignorespace)
  - Screen sharing
EOF
}

# Check prerequisites
if [[ ! -x "${AWS_CLI}" ]]; then
    error "AWS CLI wrapper not found: ${AWS_CLI}"
    exit 1
fi

# Parse arguments
LIST_MODE=false
JSON_OUTPUT=false
SECRET_NAME=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --list)   LIST_MODE=true; shift ;;
        --json)   JSON_OUTPUT=true; shift ;;
        --help|-h) show_help; exit 0 ;;
        -*)       error "Unknown option: $1"; exit 1 ;;
        *)        SECRET_NAME="$1"; shift ;;
    esac
done

# List mode
if [[ "${LIST_MODE}" == "true" ]]; then
    LIST_RESULT=$("${AWS_CLI}" secretsmanager list-secrets \
        --filter "Key=name,Values=${SECRET_PREFIX}" \
        --query 'SecretList[*].{Name:Name,Description:Description,Modified:LastChangedDate}' \
        --output json 2>&1) || {
        error "Failed to list secrets"
        exit 1
    }

    if [[ "${JSON_OUTPUT}" == "true" ]]; then
        echo "${LIST_RESULT}" | jq .
    else
        echo -e "${BLUE}Secrets under ${SECRET_PREFIX}:${NC}"
        echo ""

        SECRET_COUNT=$(echo "${LIST_RESULT}" | jq 'length')
        if [[ "${SECRET_COUNT}" -eq 0 ]]; then
            warn "No secrets found"
            exit 0
        fi

        echo "${LIST_RESULT}" | jq -r '.[] | "\(.Name)\t\(.Description // "")"' | while IFS=$'\t' read -r name desc; do
            SHORT_NAME="${name#${SECRET_PREFIX}/}"
            if [[ -n "${desc}" ]]; then
                echo -e "  ${CYAN}${SHORT_NAME}${NC} - ${desc}"
            else
                echo -e "  ${CYAN}${SHORT_NAME}${NC}"
            fi
        done

        echo ""
        info "Total: ${SECRET_COUNT} secrets"
    fi
    exit 0
fi

# Default to the API key secret
if [[ -z "${SECRET_NAME}" ]]; then
    SECRET_NAME="${SECRET_PREFIX}/prod/api-key"
fi

# Get the secret
GET_RESULT=$("${AWS_CLI}" secretsmanager get-secret-value \
    --secret-id "${SECRET_NAME}" \
    --output json 2>&1) || {
    error "Failed to get secret: ${SECRET_NAME}"
    echo "${GET_RESULT}" >&2
    exit 1
}

if [[ "${JSON_OUTPUT}" == "true" ]]; then
    echo "${GET_RESULT}" | jq .
else
    VALUE=$(echo "${GET_RESULT}" | jq -r '.SecretString')
    VERSION=$(echo "${GET_RESULT}" | jq -r '.VersionId // "N/A"')

    SHORT_NAME="${SECRET_NAME#${SECRET_PREFIX}/}"

    echo -e "${BLUE}Secret: ${CYAN}${SHORT_NAME}${NC}"
    echo -e "${BLUE}Path:   ${NC}${SECRET_NAME}"
    echo -e "${BLUE}Version:${NC} ${VERSION}"
    echo ""
    echo -e "${YELLOW}Value:${NC}"
    echo "${VALUE}"
fi
