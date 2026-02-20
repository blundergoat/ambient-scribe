# =============================================================================
# DNS MODULE - Domain Name System & SSL Certificates
# =============================================================================
#
# Manages DNS hosted zone and ACM certificate with DNS validation.
# Supports both creating a new hosted zone or using an existing one.
#
# =============================================================================

locals {
  zone_id = var.create_hosted_zone ? aws_route53_zone.this[0].zone_id : var.hosted_zone_id

  # When subdomain is set, cert covers only that subdomain (tenant on shared domain).
  # When empty, cert covers root + www + any additional subdomains.
  cert_domain = var.subdomain != "" ? "${var.subdomain}.${var.domain_name}" : var.domain_name
  subject_alternative_names = var.subdomain != "" ? [] : distinct(concat(
    ["www.${var.domain_name}"],
    [for sub in var.additional_subdomains : "${sub}.${var.domain_name}"]
  ))
}

# Optional public hosted zone for the root domain.
resource "aws_route53_zone" "this" {
  count = var.create_hosted_zone ? 1 : 0
  name  = var.domain_name

  tags = var.tags
}

# ACM certificate for HTTPS on the ALB (validated via DNS).
resource "aws_acm_certificate" "this" {
  domain_name               = local.cert_domain
  subject_alternative_names = local.subject_alternative_names
  validation_method         = "DNS"

  tags = var.tags

  lifecycle {
    create_before_destroy = true
  }
}

# Route53 records required by ACM for DNS validation.
resource "aws_route53_record" "cert_validation" {
  for_each = {
    for dvo in aws_acm_certificate.this.domain_validation_options : dvo.domain_name => {
      name   = dvo.resource_record_name
      record = dvo.resource_record_value
      type   = dvo.resource_record_type
    }
  }

  zone_id = local.zone_id
  name    = each.value.name
  type    = each.value.type
  ttl     = 60
  records = [each.value.record]
}

# Wait for ACM to confirm DNS validation before proceeding.
resource "aws_acm_certificate_validation" "this" {
  certificate_arn         = aws_acm_certificate.this.arn
  validation_record_fqdns = [for record in aws_route53_record.cert_validation : record.fqdn]
}
