#!/usr/bin/env bash
# =============================================================================
# Deploy Script - Build and Push Docker Images to ECR
# =============================================================================
#
# Builds both Docker images (agent + app) and pushes them to ECR,
# then forces an ECS service redeployment.
#
# USAGE:
#   ./scripts/deploy.sh              # Build and deploy both images
#   ./scripts/deploy.sh agent        # Build and deploy agent only
#   ./scripts/deploy.sh app          # Build and deploy app only
#
# PREREQUISITES:
#   - AWS CLI configured with correct profile
#   - Docker running
#   - Terraform applied (ECR repos must exist)
#   - strands-php-client sibling directory (for app image)
#
# =============================================================================

set -euo pipefail

# Configuration
AWS_PROFILE_NAME="aws_devgoat"
AWS_REGION="us-east-1"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
TF_DIR="$PROJECT_ROOT/infra/terraform/environments/prod"
IMAGE_TAG="${IMAGE_TAG:-latest}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

export AWS_PROFILE="$AWS_PROFILE_NAME"
export AWS_DEFAULT_REGION="$AWS_REGION"

# What to deploy
TARGET="${1:-all}"

log() { echo -e "${BLUE}[deploy]${NC} $*"; }
success() { echo -e "${GREEN}[deploy]${NC} $*"; }
warn() { echo -e "${YELLOW}[deploy]${NC} $*"; }
error() { echo -e "${RED}[deploy]${NC} $*"; exit 1; }

# Get ECR repository URLs from Terraform outputs
get_tf_output() {
    terraform -chdir="$TF_DIR" output -raw "$1" 2>/dev/null
}

# Verify prerequisites
log "Checking prerequisites..."

if ! command -v docker &> /dev/null; then
    error "Docker is not installed"
fi

if ! command -v aws &> /dev/null; then
    error "AWS CLI is not installed"
fi

if ! aws sts get-caller-identity &> /dev/null; then
    error "AWS credentials not found or expired. Run: aws sso login --profile $AWS_PROFILE_NAME"
fi

# Get ECR URLs from Terraform
log "Reading ECR repository URLs from Terraform..."
AGENT_REPO=$(get_tf_output "ecr_agent_repository_url") || error "Could not read ecr_agent_repository_url. Run terraform apply first."
APP_REPO=$(get_tf_output "ecr_app_repository_url") || error "Could not read ecr_app_repository_url. Run terraform apply first."
ECS_CLUSTER=$(get_tf_output "ecs_cluster_name") || error "Could not read ecs_cluster_name."

# Extract AWS account ID and region from repo URL
AWS_ACCOUNT_ID=$(echo "$AGENT_REPO" | cut -d. -f1)
ECR_REGISTRY="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

# Login to ECR
log "Logging in to ECR..."
aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "$ECR_REGISTRY"

# Build and push agent image
if [[ "$TARGET" == "all" || "$TARGET" == "agent" ]]; then
    log ""
    log "${BOLD}Building agent image...${NC}"
    docker build \
        -t "${AGENT_REPO}:${IMAGE_TAG}" \
        -f "$PROJECT_ROOT/strands_agents/Dockerfile" \
        "$PROJECT_ROOT/strands_agents"

    log "Pushing agent image..."
    docker push "${AGENT_REPO}:${IMAGE_TAG}"
    success "Agent image pushed: ${AGENT_REPO}:${IMAGE_TAG}"
fi

# Build and push app image
if [[ "$TARGET" == "all" || "$TARGET" == "app" ]]; then
    STRANDS_CLIENT="$PROJECT_ROOT/../strands-php-client"
    if [[ ! -d "$STRANDS_CLIENT" ]]; then
        error "strands-php-client not found at $STRANDS_CLIENT. It must be a sibling directory."
    fi

    log ""
    log "${BOLD}Building app image...${NC}"
    docker build \
        --build-context strands-php-client="$STRANDS_CLIENT" \
        -t "${APP_REPO}:${IMAGE_TAG}" \
        -f "$PROJECT_ROOT/Dockerfile" \
        "$PROJECT_ROOT"

    log "Pushing app image..."
    docker push "${APP_REPO}:${IMAGE_TAG}"
    success "App image pushed: ${APP_REPO}:${IMAGE_TAG}"
fi

# Force ECS redeployment
SERVICE_NAME="ambient-scribe-app"
log ""
log "Forcing ECS service redeployment..."
aws ecs update-service \
    --cluster "$ECS_CLUSTER" \
    --service "$SERVICE_NAME" \
    --force-new-deployment \
    --region "$AWS_REGION" \
    --no-cli-pager > /dev/null

success ""
success "============================================="
success " Deployment initiated!"
success "============================================="
success ""
success " Images pushed:"
if [[ "$TARGET" == "all" || "$TARGET" == "agent" ]]; then
    success "   Agent: ${AGENT_REPO}:${IMAGE_TAG}"
fi
if [[ "$TARGET" == "all" || "$TARGET" == "app" ]]; then
    success "   App:   ${APP_REPO}:${IMAGE_TAG}"
fi
success ""
success " ECS service redeployment triggered."
success " Monitor progress:"
success "   aws ecs describe-services --cluster $ECS_CLUSTER --services $SERVICE_NAME --query 'services[0].deployments' --no-cli-pager"
success ""
