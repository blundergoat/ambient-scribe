# Milestone 5 — Cloud Deployment + Production

**Timeline:** ~4-5 hours
**Status:** Not Started (Terraform code complete, apply pending)
**Dependencies:** Milestone 4 complete (product is polished locally)

---

## Objective

Deploy Ambient Scribe to AWS with HTTPS/WSS, persistent storage, authentication, and production-grade infrastructure. This milestone only matters after the local product is solid.

---

## Tasks

### 5.1 Terraform Apply

- [x] 12 Terraform modules written and validated: network, ECR, ECS, ALB, IAM, secrets, observability, alarms, DynamoDB, DNS, security, WAF
- [ ] Bootstrap: `cd infra/terraform/bootstrap && terraform init && terraform apply`
- [ ] Main: `terraform init -backend-config=backend.hcl && terraform apply`
- [ ] Verify: VPC, subnets, ECR repos, ECS cluster, ALB, DynamoDB table
- [ ] `terraform plan` on fresh checkout produces no changes (idempotent)

### 5.2 HTTPS/WSS

- [ ] ACM certificate for `scribe.blundergoat.com`
- [ ] ALB HTTPS listener with TLS termination
- [ ] WebSocket upgrade support at ALB (target group stickiness + WebSocket protocol)
- [ ] Browser uses `wss://` not `ws://`
- [ ] Route53 A record pointing to ALB

### 5.3 DynamoDB Storage Backend

- [ ] Implement `DynamoDbBackend` (behind `StorageBackend` interface from M3.5)
- [ ] Configure via `SESSION_STORAGE=dynamodb`
- [ ] DynamoDB table already provisioned by Terraform
- [ ] Session recovery works across ECS task restarts

### 5.4 Bedrock as Cloud Role Inference

- [ ] Bedrock model access configured (Claude Sonnet in ap-southeast-2)
- [ ] AWS credentials passed through ECS task role (not hardcoded)
- [ ] Fallback to Ollama if Bedrock is unreachable

### 5.5 Production Hardening

- [ ] WebSocket authentication (token-based, passed as query param)
- [ ] Rate limiting on WebSocket connections (WAF or application-level)
- [ ] Container health checks verified in ECS
- [ ] Structured log aggregation (CloudWatch)
- [ ] Alarm on GPU OOM, NeMo load failure, Mercure publish failure rate

### 5.6 Deployment Pipeline

- [ ] ECR push script: `scripts/deploy-ecr.sh`
- [ ] ECS redeploy script: `scripts/deploy-ecs.sh`
- [ ] GitHub Actions workflow for CI/CD
- [ ] Zero-downtime deployment with ECS rolling update

---

## Exit Criteria

- [ ] `https://scribe.blundergoat.com` serves the UI with valid TLS
- [ ] Live transcription works over WSS in production
- [ ] Session data persists in DynamoDB across deployments
- [ ] Role inference via Bedrock with Ollama fallback
- [ ] Monitoring and alerting in place
