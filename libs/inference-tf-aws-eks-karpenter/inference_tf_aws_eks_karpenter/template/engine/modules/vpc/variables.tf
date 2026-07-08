variable "resource_name_prefix" {
  type = string
}

variable "combined_tags" {
  type = map(string)
}

variable "cluster_name" {
  type        = string
  description = "Cluster name used for the karpenter.sh/discovery subnet/SG tag."
}

variable "enable_nat_gateway" {
  type        = bool
  description = <<-EOT
    When false (default posture), the VPC is endpoints-only: no IGW, no NAT, no
    public subnets — nodes reach AWS services solely via VPC endpoints. When true,
    an IGW + per-AZ NAT gateways + public subnets are created and private subnets
    get a 0.0.0.0/0 route to NAT (break-glass for arbitrary public egress).
  EOT
}
