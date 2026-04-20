# infra/ — Local Context

## Footguns (see `.goat-flow/footguns/`)

- **DynamoDB provisioned but unused:** Terraform creates DynamoDB table; code uses in-memory `SessionStore`. Production will hit memory limits.

## Ask First

All Terraform changes are Ask First. Verify:
- [ ] State file impact (shared state in S3)
- [ ] Cost implications of resource changes
- [ ] Rollback command: `terraform plan` before `terraform apply`
