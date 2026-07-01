# All variables MUST be declared here without default values.
# Default values live in ./presets/defaults-all.tfvars.

variable "region" {
  description = "AWS region to deploy the cluster into."
  type        = string
}

variable "cluster_name_prefix" {
  description = "Prefix for the EKS cluster name; a random suffix is appended for uniqueness."
  type        = string
}

variable "kubernetes_version" {
  description = "Kubernetes control-plane version for the EKS cluster."
  type        = string
}

variable "karpenter_version" {
  description = "Version of the Karpenter Helm chart to install."
  type        = string
}

variable "custom_tags" {
  description = "Additional tags applied to all resources created by this template."
  type        = map(string)
}
