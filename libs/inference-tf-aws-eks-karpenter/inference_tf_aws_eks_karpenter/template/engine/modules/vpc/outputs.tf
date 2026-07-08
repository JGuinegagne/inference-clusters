output "vpc_id" {
  value = aws_vpc.this.id
}

output "vpc_cidr" {
  value = aws_vpc.this.cidr_block
}

# The subnet IDs are the only networking outputs a consumer (EKS cluster, node
# group) references — so by default a consumer depends on the subnets but NOT on
# the route tables, associations, NAT gateways, IGW, or VPC endpoints that make
# those subnets actually route/reach traffic. Those are siblings with no external
# reference, so on destroy Terraform is free to remove them in the first wave —
# tearing down routing/endpoints out from under still-running nodes and pods.
#
# depends_on on the subnet outputs pulls the whole networking set into every
# consumer's dependency closure:
#   - on create: routing + endpoints exist before nodes/ELBs come up;
#   - on destroy: nodes/cluster/ELBs are torn down before routing/endpoints go.
# Count-gated NAT/IGW resolve to empty lists via splat when endpoints-only.
output "private_subnet_ids" {
  value = aws_subnet.private[*].id
  depends_on = [
    aws_route_table_association.private,
    aws_route_table.private,
    aws_route.private_nat,
    aws_nat_gateway.this,
    aws_internet_gateway.this,
    aws_vpc_endpoint.s3,
    aws_vpc_endpoint.interface,
  ]
}

# Empty list in the endpoints-only posture (no public subnets). The EKS cluster
# concats this onto private_subnet_ids, so an empty list is correct there.
output "public_subnet_ids" {
  value = aws_subnet.public[*].id
  depends_on = [
    aws_route_table_association.public,
    aws_route_table.public,
    aws_nat_gateway.this,
    aws_internet_gateway.this,
  ]
}

# Security group Karpenter EC2NodeClass selects via karpenter.sh/discovery is the
# cluster SG (added in main.tf); this is the endpoint SG, exposed for reference.
output "endpoint_security_group_id" {
  value = aws_security_group.endpoints.id
}
