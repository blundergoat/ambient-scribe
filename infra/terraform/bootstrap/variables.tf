# =============================================================================
# BOOTSTRAP VARIABLES - Configuration for Remote State Infrastructure
# =============================================================================

variable "aws_region" {
  description = "AWS region for the state bucket and lock table"
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Name of the project, used in resource tags"
  type        = string
  default     = "the-summit"
}

variable "environment" {
  description = "Environment name (dev, staging, prod)"
  type        = string
  default     = "prod"
}

variable "state_bucket_name" {
  description = "Name of the S3 bucket for Terraform state (must be globally unique)"
  type        = string
  default     = "the-summit-terraform-state-prod"
}

variable "lock_table_name" {
  description = "Name of the DynamoDB table for state locking"
  type        = string
  default     = "the-summit-terraform-locks-prod"
}
