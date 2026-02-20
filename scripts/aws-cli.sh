#!/usr/bin/env bash
# =============================================================================
# AWS CLI Wrapper Script
# =============================================================================
#
# Ensures the correct AWS profile is used for all AWS commands.
# Sets AWS_PROFILE and passes all arguments to the specified command.
#
# USAGE:
#   ./scripts/aws-cli.sh <command> [arguments...]
#
# EXAMPLES:
#   ./scripts/aws-cli.sh sts get-caller-identity
#   ./scripts/aws-cli.sh s3 ls
#   ./scripts/aws-cli.sh ecs describe-services --cluster ambient-scribe-cluster --services ambient-scribe-agent
#   ./scripts/aws-cli.sh logs tail /ecs/ambient-scribe-prod-agent --follow
#
# =============================================================================

set -euo pipefail

# Configuration
AWS_PROFILE_NAME="aws_devgoat"
AWS_REGION="us-east-1"

# Load .env file if it exists
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
ENV_FILE="$PROJECT_ROOT/.env"

if [[ -f "$ENV_FILE" ]]; then
    set -a
    source <(grep -v '^\s*#' "$ENV_FILE" | grep -v '^\s*$')
    set +a
fi

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Export AWS profile and region
export AWS_PROFILE="$AWS_PROFILE_NAME"
export AWS_DEFAULT_REGION="$AWS_REGION"

# Print usage if no arguments
if [[ $# -eq 0 ]]; then
    echo -e "${BLUE}AWS CLI Wrapper Script - ambient-scribe${NC}"
    echo ""
    echo "Usage: $0 <command> [arguments...]"
    echo ""
    echo "Examples:"
    echo "  $0 sts get-caller-identity                        # Check AWS credentials"
    echo "  $0 s3 ls                                          # List S3 buckets"
    echo "  $0 ecr describe-repositories                      # List ECR repositories"
    echo "  $0 ecs describe-services --cluster ambient-scribe-cluster --services ambient-scribe-agent"
    echo "  $0 logs tail /ecs/ambient-scribe-prod-agent --follow"
    echo "  $0 secretsmanager get-secret-value --secret-id /ambient-scribe/prod/api-key"
    echo ""
    echo "Environment:"
    echo "  AWS_PROFILE: $AWS_PROFILE_NAME"
    echo "  AWS_REGION:  $AWS_REGION"
    echo ""
    echo "If you see credential errors, try:"
    echo "  aws sso login --profile $AWS_PROFILE_NAME"
    exit 0
fi

# Get the command to run
COMMAND="$1"
shift

# Check for required tools
if [[ "$COMMAND" == "terraform" ]]; then
    if ! command -v terraform &> /dev/null; then
        echo -e "${RED}Error: Terraform is not installed${NC}"
        echo "Install it from: https://developer.hashicorp.com/terraform/tutorials/aws-get-started/install-cli"
        exit 1
    fi
elif ! command -v aws &> /dev/null; then
    echo -e "${RED}Error: AWS CLI is not installed${NC}"
    echo "Install it from: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html"
    exit 1
fi

# Verify credentials
if ! aws sts get-caller-identity &> /dev/null; then
    echo -e "${YELLOW}AWS credentials not found or expired for profile '$AWS_PROFILE_NAME'${NC}"
    echo ""
    echo "To fix this, run:"
    echo "  aws sso login --profile $AWS_PROFILE_NAME"
    echo ""
    exit 1
fi

# Run the command
if [[ "$COMMAND" == "aws" ]]; then
    aws "$@"
elif [[ "$COMMAND" == "terraform" ]]; then
    echo -e "${BLUE}Running: terraform $*${NC}"
    echo -e "${BLUE}Profile: $AWS_PROFILE_NAME | Region: $AWS_REGION${NC}"
    echo ""
    terraform "$@"
else
    # Assume it's an AWS CLI subcommand (s3, ecs, ecr, logs, etc.)
    aws "$COMMAND" "$@"
fi
