# Terraform Infrastructure

This project uses Terraform to deploy to AWS ECS Fargate behind an ALB with WAF, Route 53 DNS, and
Mercure streaming. For the architecture those modules produce, see `docs/infrastructure.md`.

## Prerequisites

- [Terraform](https://developer.hashicorp.com/terraform/tutorials/aws-get-started/install-cli) >= 1.5.0
- [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) v2
- AWS SSO profile `aws_devgoat` configured and logged in

```bash
# Log in to AWS SSO (required before any terraform command)
aws sso login --profile aws_devgoat
```

## Quick Reference

All commands go through the helper script:

```bash
scripts/terraform.sh init          # Initialize / configure backend
scripts/terraform.sh plan          # Preview changes
scripts/terraform.sh apply         # Apply changes (interactive)
scripts/terraform.sh apply -y      # Apply changes (auto-approve)
scripts/terraform.sh output        # Show outputs (ALB URL, ECR repos, etc.)
scripts/terraform.sh destroy       # Tear down everything
scripts/terraform.sh fmt           # Format all .tf files
scripts/terraform.sh state list    # List resources in state
```

The script automatically sets `AWS_PROFILE=aws_devgoat` and `AWS_DEFAULT_REGION=us-east-1`. That
region covers the state backend only: the prod AWS provider takes its region from `var.aws_region`,
which defaults to `ap-southeast-2`.

## First-Time Setup

There are two Terraform modules that need to be initialized in order. The **bootstrap** module creates the S3 bucket and DynamoDB table that store Terraform state. The **prod** module creates the actual infrastructure.

### Step 1: Bootstrap (remote state backend)

This creates the S3 bucket and DynamoDB lock table. Only needs to run once, ever.

```bash
scripts/terraform.sh --bootstrap init
scripts/terraform.sh --bootstrap apply
```

This creates:
- **S3 bucket**: `ambient-scribe-terraform-state-prod` (versioned, KMS-encrypted, public access blocked)
- **DynamoDB table**: `ambient-scribe-terraform-locks-prod` (prevents concurrent applies)

The bootstrap module uses **local state** (stored in `infra/terraform/bootstrap/terraform.tfstate`). Keep this file safe - it tracks the backend infrastructure itself.

### Step 2: Create backend.hcl

Copy the example and fill in the bootstrap outputs:

```bash
cp infra/terraform/environments/prod/backend.hcl.example \
   infra/terraform/environments/prod/backend.hcl
```

The defaults already match the bootstrap outputs:

```hcl
bucket         = "ambient-scribe-terraform-state-prod"
key            = "prod/terraform.tfstate"
region         = "us-east-1"
dynamodb_table = "ambient-scribe-terraform-locks-prod"
encrypt        = true
```

If you changed the bootstrap variable defaults, update these values to match `scripts/terraform.sh --bootstrap output`.

### Step 3: Initialize prod environment

```bash
scripts/terraform.sh init
```

This downloads providers, configures the S3 backend, and prepares the working directory. The init command automatically passes `-backend-config=backend.hcl`.

### Step 4: Deploy

```bash
scripts/terraform.sh plan     # Review what will be created
scripts/terraform.sh apply    # Create the infrastructure
```

## Directory Structure

```
infra/terraform/
├── bootstrap/                      # Remote state backend (run first)
│   ├── main.tf                     # S3 bucket + DynamoDB table + KMS key
│   ├── outputs.tf                  # Bucket name and lock table name
│   ├── variables.tf                # Defaults for resource names
│   └── versions.tf                 # Provider version constraints
├── environments/prod/              # Production environment
│   ├── backend.tf                  # Empty S3 backend block (configured via backend.hcl)
│   ├── backend.hcl                 # Actual backend config (not committed - create from .example)
│   ├── backend.hcl.example         # Template for backend.hcl
│   ├── main.tf                     # Module composition and wiring
│   ├── outputs.tf                  # ALB URL, ECR repos, ECS cluster, etc.
│   ├── terraform.tfvars            # Your values (not committed - create from .example)
│   ├── terraform.tfvars.example    # Template for terraform.tfvars
│   ├── variables.tf                # Variable declarations with defaults
│   └── versions.tf                 # Provider version constraints
└── modules/                        # 13 reusable infrastructure modules
    ├── alarms/                     # CloudWatch alarms
    ├── alb/                        # Application Load Balancer + HTTPS listener
    ├── dns/                        # Route53 records + ACM certificate
    ├── dynamodb/                   # DynamoDB table for session state
    ├── ecr/                        # ECR repositories (agent + app images)
    ├── ecs/                        # ECS cluster + task definition (3 containers)
    ├── ecs-service/                # ECS service + auto-scaling
    ├── iam/                        # Task execution role, task role, OIDC for GitHub Actions
    ├── network/                    # VPC + subnets, created only when vpc_id is empty
    ├── observability/              # CloudWatch log groups
    ├── secrets/                    # Secrets Manager (API keys)
    ├── security/                   # Security groups (ALB, ECS, Mercure)
    └── waf/                        # WAF rate limiting
```

## Configuration

Production settings live in `infra/terraform/environments/prod/terraform.tfvars`, copied from
`terraform.tfvars.example`. Key values and their defaults:

| Setting | Default | Notes |
|---------|---------|-------|
| `aws_region` | `ap-southeast-2` | Where the workload runs; matches the AU Bedrock model IDs |
| `project_name` | `ambient-scribe` | Prefixes the cluster (`ambient-scribe-cluster`) and service (`ambient-scribe-app`) |
| `vpc_id` | *(empty)* | Empty creates a VPC via the `network` module; set it plus the subnet ID lists to bring your own |
| `domain_name` | `blundergoat.com` | |
| `subdomain` | `scribe` | Deploys to scribe.blundergoat.com |
| `hosted_zone_id` | *(empty)* | Required unless `create_hosted_zone = true` |
| `model_id` | `au.anthropic.claude-haiku-4-5-20251001-v1:0` | Bedrock model for role inference |
| `summary_model_id` | `au.anthropic.claude-haiku-4-5-20251001-v1:0` | Bedrock model for the post-visit note |
| `alb_idle_timeout_seconds` | `120` | Long enough for streaming responses |
| `waf_rate_limit` | `2000` | Requests per 5-minute window per IP |

## Architecture

The ECS task runs 3 containers:

| Container | Port | Role |
|-----------|------|------|
| **app** | 8080 | PHP/Symfony web UI; the only ALB target for `/*` |
| **agent** | 8000 | Python/FastAPI AI agent layer; internal sidecar reached at `localhost:8000` |
| **mercure** | 3701 | SSE hub for real-time streaming |

Task size is 1024 CPU / 2048 MB.

Traffic flow: Route 53 -> ALB (HTTPS :443) -> ECS (app :8080, mercure :3701 via path-based routing)

## Troubleshooting

**"Backend initialization required"**
```bash
scripts/terraform.sh init
```
You need to run init before plan/apply. If `backend.hcl` doesn't exist, create it from the example (see Step 2 above).

**"AWS credentials not found or expired"**
```bash
aws sso login --profile aws_devgoat
```

**State lock stuck** (e.g. after a crash during apply)
```bash
# Get the lock ID from the error message, then:
scripts/terraform.sh unlock <LOCK_ID>
```
