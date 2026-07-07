# Terraform Infrastructure - Self-Contained, Create-or-BYO

ambient-scribe owns all its infrastructure. No external repos required.

## How It Works

```
infra/terraform/
├── bootstrap/              # Run once: S3 state bucket + DynamoDB locks
├── environments/prod/      # Root module: wires all modules together
│   ├── main.tf             # Conditional network + all infra modules
│   ├── variables.tf        # Every knob (VPC, DNS, ECS, WAF, etc.)
│   ├── outputs.tf          # Deployed resource IDs and URLs
│   ├── terraform.tfvars.example
│   └── backend.hcl.example
└── modules/                # Reusable, composable modules
    ├── network/            # VPC, subnets, IGW, NAT, route tables
    ├── security/           # Security groups (ALB + ECS)
    ├── iam/                # Task roles, execution roles, GitHub OIDC
    ├── ecs/                # Cluster + task definition (3 containers)
    ├── ecs-service/        # Fargate service + target group registration
    ├── alb/                # ALB + listeners + target groups
    ├── dns/                # Route53 zone + ACM certificate
    ├── ecr/                # Container registry (used twice: agent + app)
    ├── dynamodb/           # Sessions table
    ├── secrets/            # Secrets Manager (API key)
    ├── observability/      # CloudWatch log groups
    ├── alarms/             # CloudWatch alarms + SNS
    └── waf/                # WAF rate limiting
```

## VPC: Create or Bring Your Own

The `network` module is **conditionally created** based on whether you provide a `vpc_id`.

### Default: Create new VPC (zero config)

Leave `vpc_id` empty (the default). Terraform creates everything:

```
VPC 10.0.0.0/16
├── Public subnets:  10.0.1.0/24, 10.0.2.0/24  (2 AZs, for ALB)
├── Private subnets: 10.0.10.0/24, 10.0.11.0/24 (2 AZs, for ECS)
├── Internet Gateway
├── NAT Gateway (single, ~$32/month)
└── Route tables wired correctly
```

Nothing to set in `terraform.tfvars`. Just apply.

### BYO: Use an existing VPC

Set three variables in `terraform.tfvars` and the network module is skipped entirely:

```hcl
vpc_id             = "vpc-0123456789abcdef0"
public_subnet_ids  = ["subnet-aaa", "subnet-bbb"]
private_subnet_ids = ["subnet-ccc", "subnet-ddd"]
```

The conditional logic in `main.tf`:

```hcl
locals {
  create_vpc         = var.vpc_id == ""
  vpc_id             = local.create_vpc ? module.network[0].vpc_id : var.vpc_id
  public_subnet_ids  = local.create_vpc ? module.network[0].public_subnet_ids : var.public_subnet_ids
  private_subnet_ids = local.create_vpc ? module.network[0].private_subnet_ids : var.private_subnet_ids
}

module "network" {
  count  = local.create_vpc ? 1 : 0   # 1 = create, 0 = skip
  source = "../../modules/network"
  ...
}
```

Every downstream module (`security`, `alb`, `ecs_service`) reads from `local.vpc_id` / `local.public_subnet_ids` / `local.private_subnet_ids` - they don't know or care where the VPC came from.

## Module Dependency Chain

```
Phase 0:  network (conditional)
              │
Phase 1:  dynamodb, ecr ×2, observability, secrets  (independent)
              │
Phase 2:  security ← vpc_id     iam ← phase 1 ARNs
              │                    │
Phase 3:  ecs ← iam, ecr        dns ← domain config
              │                    │
Phase 4:  alb ← security, dns cert, vpc/subnets
              │
Phase 5:  ecs_service ← ecs, alb, subnets    waf ← alb
              │
Phase 6:  alarms ← alb, ecs
```

## Quick Start

```bash
# 1. Bootstrap (once - creates S3 + DynamoDB for state)
cd infra/terraform/bootstrap
terraform init && terraform apply

# 2. Configure
cd ../environments/prod
cp terraform.tfvars.example terraform.tfvars
cp backend.hcl.example backend.hcl
# Edit both files - at minimum set hosted_zone_id

# 3. Deploy
terraform init -backend-config=backend.hcl
terraform plan    # Review ~30+ resources
terraform apply
```

## What About blundergoat-infra?

Shelved. ambient-scribe originally consumed a shared VPC from `blundergoat-infra` via SSM parameters. That cross-repo dependency made first-time setup harder than it needed to be. Now everything lives here. If you have an existing VPC from another repo, use BYO mode - but you don't need one.
