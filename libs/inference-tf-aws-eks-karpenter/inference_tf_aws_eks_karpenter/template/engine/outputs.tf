output "deployment_id" {
  description = "Unique identifier for this deployment, suffixed onto resource names."
  value       = random_id.postfix.hex
}

output "region" {
  description = "AWS region the cluster is deployed into."
  value       = var.region
}

# NOTE: seed scaffold. cluster_name, cluster_endpoint, and kubeconfig_path outputs
# (referenced in manifest.yaml) are added once the EKS cluster module lands.
