# =============================================================================
# PROD ENVIRONMENT VARIABLES - All Configurable Settings
# =============================================================================

# =============================================================================
# Core Settings
# =============================================================================

variable "aws_region" {
  description = "AWS region for all resources"
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  type    = string
  default = "prod"
}

variable "project_name" {
  type    = string
  default = "the-summit"
}

# =============================================================================
# Shared VPC (from blundergoat-platform)
# =============================================================================

variable "vpc_id" {
  description = "VPC ID from blundergoat-platform (shared VPC)"
  type        = string
}

variable "public_subnet_ids" {
  description = "Public subnet IDs from blundergoat-platform (for ALB)"
  type        = list(string)
}

variable "private_subnet_ids" {
  description = "Private subnet IDs from blundergoat-platform (for ECS tasks)"
  type        = list(string)
}

# =============================================================================
# DNS / Domain Settings
# =============================================================================

variable "domain_name" {
  description = "Root domain name"
  type        = string
  default     = "blundergoat.com"
}

variable "subdomain" {
  description = "Subdomain for the agent endpoint"
  type        = string
  default     = "summit"
}

variable "create_hosted_zone" {
  description = "Set to true to create a new Route53 hosted zone"
  type        = bool
  default     = false
}

variable "hosted_zone_id" {
  description = "Existing Route53 hosted zone ID (e.g., from blundergoat-platform)"
  type        = string
  default     = ""
}

# =============================================================================
# Networking Settings
# =============================================================================

variable "alb_ingress_cidrs" {
  description = "CIDRs allowed to reach the ALB"
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "ecs_egress_cidrs" {
  description = "Egress CIDRs for ECS tasks (needs internet for Bedrock API)"
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "alb_idle_timeout_seconds" {
  description = "ALB idle timeout (120s for streaming responses)"
  type        = number
  default     = 120
}

# =============================================================================
# Agent Settings
# =============================================================================

variable "model_id" {
  description = "Bedrock model ID for the agent"
  type        = string
  default     = "us.anthropic.claude-sonnet-4-20250514-v1:0"
}

variable "agent_image_tag" {
  description = "Docker image tag for the agent container"
  type        = string
  default     = "latest"
}

variable "ecr_repository_name" {
  description = "ECR repository name for the agent image"
  type        = string
  default     = "the-summit-agent"
}

# =============================================================================
# App Settings (PHP Symfony web UI)
# =============================================================================

variable "app_image_tag" {
  description = "Docker image tag for the app container"
  type        = string
  default     = "latest"
}

variable "ecr_app_repository_name" {
  description = "ECR repository name for the app image"
  type        = string
  default     = "the-summit-app"
}

variable "app_log_retention_days" {
  description = "Retention for app task logs (in days)"
  type        = number
  default     = 14
}

# =============================================================================
# Mercure Settings (SSE streaming hub)
# =============================================================================

variable "mercure_image" {
  description = "Docker image for the Mercure SSE hub (e.g., dunglas/mercure)"
  type        = string
  default     = "dunglas/mercure"
}

variable "mercure_log_retention_days" {
  description = "Retention for Mercure container logs (in days)"
  type        = number
  default     = 14
}

# =============================================================================
# DynamoDB Settings
# =============================================================================

variable "dynamodb_table_name" {
  description = "DynamoDB table name for session persistence"
  type        = string
  default     = "the-summit-prod-sessions"
}

# =============================================================================
# Security Settings
# =============================================================================

variable "api_key" {
  description = "API key for authenticating agent requests (auto-generated if empty)"
  type        = string
  default     = ""
  sensitive   = true
}

variable "secrets_recovery_window_days" {
  description = "Secrets Manager deletion window (0 for immediate)"
  type        = number
  default     = 0
}

# =============================================================================
# Observability Settings
# =============================================================================

variable "enable_container_insights" {
  description = "Enable ECS Container Insights for detailed metrics"
  type        = bool
  default     = false
}

variable "agent_log_retention_days" {
  description = "Retention for agent task logs (in days)"
  type        = number
  default     = 14
}

# =============================================================================
# Alerting Settings
# =============================================================================

variable "alarm_email" {
  description = "Email address for CloudWatch alarm notifications"
  type        = string
  default     = ""
}

# =============================================================================
# WAF Settings
# =============================================================================

variable "waf_rate_limit" {
  description = "WAF rate limit - max requests per 5 minutes per IP"
  type        = number
  default     = 2000
}

# =============================================================================
# CI/CD Settings (GitHub Actions OIDC)
# =============================================================================

variable "github_repository" {
  description = "GitHub repository for OIDC (format: owner/repo)"
  type        = string
  default     = ""
}
