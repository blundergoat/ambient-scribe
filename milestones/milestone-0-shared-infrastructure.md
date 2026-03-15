# Milestone 0 — Infrastructure Setup

**Timeline:** ~1 evening (before Weekend 1)
**Status:** Code Complete (Terraform written + validated; `terraform apply` pending)
**Dependencies:** AWS account, Terraform installed, GitHub repos created

---

## Objective

Make ambient-scribe fully self-contained for infrastructure. By default, `terraform apply` creates its own VPC, subnets, NAT gateway, and all supporting resources. Users with existing infrastructure can bring their own VPC by passing `vpc_id` and subnet IDs in `terraform.tfvars`.

> **Note:** The `blundergoat-infra` shared-VPC approach has been shelved. Ambient-scribe manages its own networking to eliminate cross-repo dependencies and simplify first-time setup.

---

## Tasks

### 0.1 Create Repository and Scaffold

- [x] Scaffold Terraform structure under `infra/terraform/`
- [x] Bootstrap module for S3 state bucket + DynamoDB locks
- [x] Environment module (`environments/prod/`) with all infrastructure modules
- [x] `terraform fmt -check -recursive` — clean
- [x] `terraform validate` — passes

### 0.2 Set Up Terraform State Backend

- [x] Bootstrap module written: S3 bucket + DynamoDB lock table
- [ ] `terraform apply` the bootstrap module (creates state infrastructure)

### 0.3 Self-Contained VPC (Create-or-BYO)

- [x] Network module (`modules/network/`): VPC, public/private subnets, IGW, NAT, route tables
- [x] Conditional creation: `vpc_id == ""` creates new VPC, otherwise uses provided IDs
- [x] Default CIDR: `10.0.0.0/16` with `10.0.1.0/24`, `10.0.2.0/24` (public) and `10.0.10.0/24`, `10.0.11.0/24` (private)
- [x] NAT gateway enabled by default (single, ~$32/month) for private subnet egress
- [x] VPC outputs exposed: `vpc_id`, `public_subnet_ids`, `private_subnet_ids`
- [ ] `terraform apply` (deploy VPC)

### 0.4 ECR Repositories

- [x] `ambient-scribe-agent` (Python + NeMo) — defined in `modules/ecr`
- [x] `ambient-scribe-app` (Symfony app) — with scanning + lifecycle policy
- [ ] Verify repos exist after apply: `aws ecr describe-repositories`

### 0.5 Apply and Verify

- [ ] Bootstrap: `cd infra/terraform/bootstrap && terraform init && terraform apply`
- [ ] Main: `cp backend.hcl.example backend.hcl && terraform init -backend-config=backend.hcl`
- [ ] `terraform plan` — review resource creation (~30+ resources including network)
- [ ] `terraform apply` — deploy all infrastructure
- [ ] Verify VPC, subnets, NAT gateway created
- [ ] Verify ECR repos exist: `aws ecr describe-repositories`

---

## Exit Criteria

- [x] Self-contained Terraform validated and fmt-clean
- [ ] VPC + subnets exist in `us-east-1` (after `terraform apply`)
- [ ] ECR repos created for ambient scribe containers
- [ ] `terraform plan` on a fresh checkout produces no changes (idempotent)
- [ ] BYO mode works: passing `vpc_id` + subnet IDs skips network module entirely

---

## Notes

- The `blundergoat-infra` repo still exists but is no longer a prerequisite for ambient-scribe
- ambient-scribe's Terraform is fully standalone — clone, configure `terraform.tfvars`, apply
- For users with existing VPCs, set `vpc_id`, `public_subnet_ids`, and `private_subnet_ids` to skip VPC creation
- Single NAT gateway keeps costs low (~$32/month) while still allowing private subnet egress
