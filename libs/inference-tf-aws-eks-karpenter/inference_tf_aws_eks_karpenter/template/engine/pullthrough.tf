# === ECR pull-through: account-regional SHARED infrastructure ===
#
# The pull-through cache RULE and the repository-creation TEMPLATE are private-registry
# singletons: exactly ONE per repository-prefix per (account, region) — NOT per-deployment
# resources. Two deployments in the same account+region must therefore share them.
#
# Managing them as Terraform resources (fixed prefix, in state) collides: the second
# `apply` fails *AlreadyExists*, and the first `jd down` deletes them out from under the
# surviving deployment. So they are provisioned imperatively here with a create-time
# provisioner that, per prefix:
#   - CREATE it if absent (take ownership), else
#   - ADOPT it if present and the attributes that matter already agree, else
#   - FAIL the run if a pre-existing one DIVERGES (never silently run on a foreign config).
# There is deliberately NO destroy provisioner: these are shared account infra that must
# outlive any single deployment, so `jd down` leaves them in place.
#
# Because the repos these create are the SHARED cache (e.g. quay/prometheus/... pulled by
# every deployment), they are deployment-independent: no DeploymentId tag. Dropping
# resource_tags is what lets us omit custom_role_arn entirely — tags/KMS is the ONLY thing
# that forces the ECR repository-creation role; lifecycle_policy alone does not (AWS docs).
# The per-deployment node import grant (aws_iam_role_policy.node_pullthrough, images.tf) is
# a node-identity policy and stays per-deployment — it never collides.
#
# "Attributes that matter" (a divergence here breaks pulls, so we fail):
#   - rule.upstreamRegistryUrl — a different upstream means images resolve to the WRONG source.
#   - template.imageTagMutability — IMMUTABLE blocks pull-through's tag re-import (24h re-validate).
#   - template.appliedFor — must include PULL_THROUGH_CACHE or the template never applies to our repos.
# lifecycle_policy is SET on create but NOT part of the strict compare: it only tunes GC
# cadence, never correctness, so a pre-existing template with a different expiry is adopted.

locals {
  # 90-day expiry applied to every auto-created pull-through repo (see the images.tf note on
  # why these repos are never removed by terraform destroy). Set on create; not strict-compared.
  pullthrough_lifecycle_policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Expire cached images 90d after last push (unused pull-through repos self-trim)"
      selection = {
        tagStatus   = "any"
        countType   = "sinceImagePushed"
        countUnit   = "days"
        countNumber = 90
      }
      action = { type = "expire" }
    }]
  })
}

resource "null_resource" "pullthrough_infra" {
  for_each = local.trusted_upstreams

  # Re-run only when the shared config for THIS prefix changes.
  triggers = {
    prefix           = each.value.prefix
    upstream_url     = each.value.url
    region           = var.region
    mutability       = "MUTABLE"
    lifecycle_policy = local.pullthrough_lifecycle_policy
  }

  provisioner "local-exec" {
    interpreter = ["/bin/bash", "-c"]
    command     = <<-EOT
      set -euo pipefail

      PREFIX="${self.triggers.prefix}"
      UPSTREAM="${self.triggers.upstream_url}"
      REGION="${self.triggers.region}"
      MUTABILITY="${self.triggers.mutability}"

      echo "[pullthrough] ensuring shared infra for prefix '$PREFIX' ($REGION)"

      # --- 1. Pull-through cache rule (matters: upstreamRegistryUrl) ---
      describe_rule() {
        aws ecr describe-pull-through-cache-rules --region "$REGION" \
          --ecr-repository-prefixes "$PREFIX" \
          --query 'pullThroughCacheRules[0].upstreamRegistryUrl' --output text 2>/dev/null || true
      }

      got="$(describe_rule)"
      if [ -z "$got" ] || [ "$got" = "None" ]; then
        echo "[pullthrough] creating cache rule: $PREFIX -> $UPSTREAM"
        # `|| true`: a concurrent deployment may win the create race; we re-verify below.
        aws ecr create-pull-through-cache-rule --region "$REGION" \
          --ecr-repository-prefix "$PREFIX" --upstream-registry-url "$UPSTREAM" >/dev/null 2>&1 || true
        got="$(describe_rule)"
      fi
      if [ "$got" != "$UPSTREAM" ]; then
        echo "[pullthrough] ERROR: cache rule '$PREFIX' has upstream '$got', expected '$UPSTREAM' — refusing to adopt a divergent shared rule." >&2
        exit 1
      fi
      echo "[pullthrough] cache rule '$PREFIX' -> '$UPSTREAM' OK."

      # --- 2. Repository-creation template (matters: imageTagMutability + appliedFor) ---
      describe_tmpl_field() {
        aws ecr describe-repository-creation-templates --region "$REGION" \
          --prefixes "$PREFIX" --query "repositoryCreationTemplates[0].$1" --output text 2>/dev/null || true
      }

      mut="$(describe_tmpl_field imageTagMutability)"
      if [ -z "$mut" ] || [ "$mut" = "None" ]; then
        echo "[pullthrough] creating repo-creation template: $PREFIX"
        aws ecr create-repository-creation-template --region "$REGION" \
          --prefix "$PREFIX" --applied-for PULL_THROUGH_CACHE \
          --image-tag-mutability "$MUTABILITY" \
          --lifecycle-policy '${self.triggers.lifecycle_policy}' >/dev/null 2>&1 || true
        mut="$(describe_tmpl_field imageTagMutability)"
      fi
      if [ "$mut" != "$MUTABILITY" ]; then
        echo "[pullthrough] ERROR: template '$PREFIX' imageTagMutability is '$mut', expected '$MUTABILITY' (IMMUTABLE breaks pull-through re-import)." >&2
        exit 1
      fi
      af="$(describe_tmpl_field appliedFor)"
      if ! echo "$af" | grep -q 'PULL_THROUGH_CACHE'; then
        echo "[pullthrough] ERROR: template '$PREFIX' appliedFor is '$af', expected to include PULL_THROUGH_CACHE." >&2
        exit 1
      fi
      echo "[pullthrough] repo-creation template '$PREFIX' OK."
    EOT
  }
}
