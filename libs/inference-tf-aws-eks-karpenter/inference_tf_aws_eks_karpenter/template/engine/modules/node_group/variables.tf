variable "cluster_name" {
  type = string
}

variable "node_group_name" {
  type = string
}

variable "node_role_arn" {
  type = string
}

variable "subnet_ids" {
  type = list(string)
}

variable "instance_types" {
  type        = list(string)
  description = "Candidate instance types for the managed node group."
}

variable "ami_type" {
  type        = string
  description = "EKS AMI type, resolved concretely at the root (e.g. AL2023_x86_64_STANDARD). Must NOT be 'default' — capability auto-detection lives at the root so this value stays plan-time-stable and never forces a node group replacement."
}

variable "labels" {
  type        = map(string)
  description = "Kubernetes labels applied to nodes in this group."
}

variable "taints" {
  type = list(object({
    key    = string
    value  = string
    effect = string # NO_SCHEDULE | PREFER_NO_SCHEDULE | NO_EXECUTE
  }))
  description = "Kubernetes taints applied to nodes in this group."
}

variable "disk_size_gb" {
  type = number
}

variable "min_size" {
  type = number
}

variable "max_size" {
  type = number
}

variable "desired_size" {
  type = number
}

variable "ecr_registry" {
  type        = string
  description = "Private ECR registry base URI (<acct>.dkr.ecr.<region>.amazonaws.com) for the containerd pull-through mirror."
}

variable "mirror_map" {
  type        = map(string)
  description = "Map of upstream registry host => ECR pull-through repo prefix, for the containerd hosts.toml backup mirror."
}

variable "combined_tags" {
  type = map(string)
}
