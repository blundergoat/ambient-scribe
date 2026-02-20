# Milestone 0 — Shared Infrastructure Setup

**Timeline:** ~1 evening (before Weekend 1)
**Status:** In Progress (0.1 scaffold complete, 0.2–0.5 code written — awaiting apply)
**Dependencies:** AWS account, Terraform installed, GitHub repos created

---

## Objective

Stand up `blundergoat-infra` so the ambient scribe (and future projects) have a shared VPC, subnets, and ECR repos to deploy into. This decouples infrastructure ownership from individual app repos.

---

## Tasks

### 0.1 Create `blundergoat-infra` Repository

- [x] Create `mattyhansen/blundergoat-infra` (private) on GitHub
- [x] Scaffold Terraform structure:
  ```
  blundergoat-infra/
  ├── terraform/
  │   ├── bootstrap/          # S3 state bucket + DynamoDB locks (local state)
  │   │   ├── main.tf
  │   │   ├── variables.tf
  │   │   ├── outputs.tf
  │   │   └── versions.tf
  │   ├── main.tf             # Provider config, data sources
  │   ├── vpc.tf              # VPC 10.1.0.0/16, public/private subnets, IGW, NAT
  │   ├── security_groups.tf  # Shared SG (allow internal VPC traffic)
  │   ├── ecr.tf              # Shared container registries (for_each)
  │   ├── ssm_outputs.tf      # Write VPC/subnet/SG/ECR IDs to SSM
  │   ├── variables.tf        # Region, CIDR ranges, environment tag
  │   ├── outputs.tf          # Terraform outputs
  │   ├── versions.tf         # Provider constraints
  │   ├── backend.tf          # Partial S3 backend
  │   └── backend.hcl.example # Example backend values
  ├── .gitignore
  └── README.md               # Architecture, deploy instructions, SSM consumption
  ```
- [x] `terraform fmt -check -recursive` — clean
- [x] `terraform validate` — both bootstrap and root modules pass

### 0.2 Set Up Terraform State Backend

- [x] Bootstrap module written: S3 bucket `blundergoat-infra-terraform-state-prod` + DynamoDB `blundergoat-infra-terraform-locks-prod` + KMS key
- [x] S3 backend configured in `terraform/backend.tf` (partial config)
- [ ] `terraform apply` the bootstrap module (creates state infrastructure)

### 0.3 Define Shared VPC

- [x] VPC `10.1.0.0/16` with public/private subnets across 2 AZs in `us-east-1` (non-overlapping with platform's `10.0.0.0/16`)
- [x] Internet Gateway for public subnets
- [x] NAT Gateway (single) for private subnet egress
- [x] Route tables wired correctly
- [ ] `terraform apply` (deploy VPC)

### 0.4 Write SSM Parameter Store Outputs

- [x] `/blundergoat/shared/vpc_id` — defined in `ssm_outputs.tf`
- [x] `/blundergoat/shared/private_subnet_ids` — JSON-encoded list
- [x] `/blundergoat/shared/public_subnet_ids` — JSON-encoded list
- [x] `/blundergoat/shared/internal_sg_id` — security group ID
- [x] `/blundergoat/shared/ecr/ambient-scribe-agent` — ECR repo URL
- [x] `/blundergoat/shared/ecr/ambient-scribe-php` — ECR repo URL
- [ ] Verify parameters are readable after apply: `aws ssm get-parameter --name "/blundergoat/shared/vpc_id"`

### 0.5 Create ECR Repositories

- [x] `blundergoat/ambient-scribe-agent` (Python + NeMo) — defined in `ecr.tf` via `for_each`
- [x] `blundergoat/ambient-scribe-php` (Symfony app) — with scanning + lifecycle policy
- [ ] Verify repos exist after apply: `aws ecr describe-repositories`

### 0.6 Apply and Verify

- [ ] Bootstrap: `cd terraform/bootstrap && terraform init && terraform apply`
- [ ] Main: `cp backend.hcl.example backend.hcl && terraform init -backend-config=backend.hcl`
- [ ] `terraform plan` — review resource creation
- [ ] `terraform apply` — deploy shared infrastructure
- [ ] Verify SSM parameters populated and readable from a separate AWS CLI session
- [ ] Verify ECR repos exist: `aws ecr describe-repositories`

---

## Exit Criteria

- [x] `blundergoat-infra` repo created with working Terraform (validated, fmt clean)
- [ ] VPC + subnets exist in `us-east-1` (after `terraform apply`)
- [ ] SSM parameters populated and readable
- [ ] ECR repos created for ambient scribe containers
- [ ] `terraform plan` on a fresh checkout produces no changes (idempotent)

---

## Deferred: Summit Infrastructure Migration

> Migrating The Summit's VPC into `blundergoat-infra` is a separate task with its own blast radius. Do NOT bundle it into this evening's work.

If The Summit currently has its own VPC in Terraform:
- [ ] File a separate issue / create a dedicated task
- [ ] Plan the migration: update Summit Terraform to read from SSM, import existing VPC into `blundergoat-infra` state, remove from Summit state
- [ ] Execute only after both the ambient scribe and Summit are stable on the new infra
- [ ] This is a "next month" task, not a "this evening" task

---

## Notes

- This infrastructure is deployed once and rarely touched again
- All app repos consume shared state via SSM — no cross-repo Terraform coupling
- Tearing down an app never breaks another app or the shared infra
- Deploy order: `blundergoat-infra` first (once), then any app repo independently
- Repo naming convention: `mattyhansen/blundergoat-infra` (private, personal account) for infrastructure; `blundergoat/ambient-scribe` (public org) for application code
