# =============================================================================
# ECS SERVICE MODULE - Long-Running Container Deployment
# =============================================================================
#
# Maintains desired number of agent tasks running behind the ALB.
# Rolling deployments with automatic rollback via circuit breaker.
#
# =============================================================================

locals {
  load_balancers = concat(
    [
      {
        target_group_arn = var.target_group_arn
        container_name   = var.container_name
        container_port   = var.container_port
      }
    ],
    var.mercure_target_group_arn != "" ? [
      {
        target_group_arn = var.mercure_target_group_arn
        container_name   = var.mercure_container_name
        container_port   = var.mercure_container_port
      }
    ] : []
  )
}

resource "aws_ecs_service" "this" {
  name            = var.service_name
  cluster         = var.cluster_arn
  task_definition = var.task_definition_arn
  desired_count   = var.desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = var.subnet_ids
    security_groups  = var.security_group_ids
    assign_public_ip = false
  }

  dynamic "load_balancer" {
    for_each = local.load_balancers
    content {
      target_group_arn = load_balancer.value.target_group_arn
      container_name   = load_balancer.value.container_name
      container_port   = load_balancer.value.container_port
    }
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  enable_ecs_managed_tags = true
  propagate_tags          = "SERVICE"

  tags = var.tags
}
