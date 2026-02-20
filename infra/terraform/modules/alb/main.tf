# =============================================================================
# ALB MODULE - Application Load Balancer
# =============================================================================
#
# Creates ALB with HTTPS termination for the agent service.
# Health check path: /health (not /healthz like blundergoat).
# Default idle timeout: 120s (for streaming responses).
#
# =============================================================================

locals {
  name_prefix = "${var.project_name}-${var.environment}"
}

resource "aws_lb" "this" {
  name               = "${local.name_prefix}-alb"
  load_balancer_type = "application"
  internal           = var.internal
  subnets            = var.public_subnet_ids
  security_groups    = [var.alb_security_group_id]

  enable_deletion_protection = var.enable_deletion_protection
  idle_timeout               = var.idle_timeout
  drop_invalid_header_fields = true

  tags = merge(var.tags, {
    Name = "${local.name_prefix}-alb"
  })
}

resource "aws_lb_target_group" "agent" {
  name_prefix = "sumtg-"
  port        = var.target_port
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = var.vpc_id

  health_check {
    path                = var.health_check_path
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
    matcher             = "200"
  }

  tags = merge(var.tags, {
    Name = "${local.name_prefix}-tg"
  })

  lifecycle {
    create_before_destroy = true
  }
}

# HTTP listener redirects to HTTPS.
resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.this.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "redirect"

    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}

# HTTPS listener forwards to the target group.
resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.this.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.agent.arn
  }
}

# =============================================================================
# Mercure SSE Hub (optional)
# =============================================================================

resource "aws_lb_target_group" "mercure" {
  count       = var.enable_mercure ? 1 : 0
  name_prefix = "merc-"
  port        = var.mercure_target_port
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = var.vpc_id

  health_check {
    path                = var.mercure_health_check_path
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
    matcher             = "200"
  }

  tags = merge(var.tags, {
    Name = "${local.name_prefix}-mercure-tg"
  })

  lifecycle {
    create_before_destroy = true
  }
}

# Route /.well-known/mercure* to the Mercure target group.
resource "aws_lb_listener_rule" "mercure" {
  count        = var.enable_mercure ? 1 : 0
  listener_arn = aws_lb_listener.https.arn
  priority     = 10

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.mercure[0].arn
  }

  condition {
    path_pattern {
      values = ["/.well-known/mercure*"]
    }
  }
}
