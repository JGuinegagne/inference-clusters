data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_region" "current" {}

locals {
  azs      = slice(data.aws_availability_zones.available.names, 0, 2)
  vpc_cidr = "10.0.0.0/16"
  # Large private subnets: GPU fleets + multi-node inference need many pod IPs
  # (VPC CNI, no prefix delegation assumed). /19 = 8k addresses per AZ.
  private_cidrs = ["10.0.0.0/19", "10.0.32.0/19"]
  # Public subnets exist only in the break-glass NAT posture.
  public_cidrs = ["10.0.64.0/24", "10.0.65.0/24"]
  nat_count    = var.enable_nat_gateway ? 2 : 0

  # Interface endpoints that replace NAT for the endpoints-only posture.
  # S3 is a separate gateway endpoint (below). One interface endpoint per service,
  # spread across both private subnets, private DNS on so SDK/containerd resolve
  # the service name to the endpoint transparently.
  interface_endpoints = [
    "ecr.api",              # image pull auth + manifests
    "ecr.dkr",              # image pull (layers stream from S3 gateway)
    "sts",                  # Pod Identity token exchange
    "logs",                 # CloudWatch / Container Insights
    "sqs",                  # Karpenter interruption queue
    "ec2",                  # Karpenter provisioning
    "eks",                  # Karpenter eks:DescribeCluster (cluster CIDR detection)
    "eks-auth",             # Pod Identity
    "elasticloadbalancing", # internal NLB provisioning
    # On no-NAT, a missing endpoint = a silent hang. The set below is required by
    # OTHER decisions (cross-checked against fully-private-cluster blueprint):
    "autoscaling", # Cluster Autoscaler SetDesiredCapacity/Describe*
    "ssm",         # node role has AmazonSSMManagedInstanceCore + Karpenter ssm:GetParameter
    "ssmmessages", # SSM Session Manager control channel
    "ec2messages", # SSM Session Manager messages
    "kms",         # EBS gp3 default encryption + EKS secrets envelope
    "monitoring",  # Container Insights pushes CloudWatch METRICS here (logs uses "logs")
  ]
}

resource "aws_vpc" "this" {
  cidr_block           = local.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = merge(var.combined_tags, {
    Name = "${var.resource_name_prefix}-vpc"
  })
}

# --- Private subnets ---
#
# Karpenter discovers these via the karpenter.sh/discovery tag (EC2NodeClass
# subnetSelectorTerms). internal-elb tag lets the cloud controller place internal
# NLBs here (the inference invoke path is internal).
resource "aws_subnet" "private" {
  count             = 2
  vpc_id            = aws_vpc.this.id
  cidr_block        = local.private_cidrs[count.index]
  availability_zone = local.azs[count.index]

  tags = merge(var.combined_tags, {
    Name                              = "${var.resource_name_prefix}-private-${local.azs[count.index]}"
    "kubernetes.io/role/internal-elb" = "1"
    "karpenter.sh/discovery"          = var.cluster_name
  })
}

resource "aws_route_table" "private" {
  count  = 2
  vpc_id = aws_vpc.this.id

  tags = merge(var.combined_tags, {
    Name = "${var.resource_name_prefix}-private-rt-${local.azs[count.index]}"
  })
}

resource "aws_route_table_association" "private" {
  count          = 2
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}

# --- Break-glass public egress (enable_nat_gateway = true only) ---
#
# Default posture is endpoints-only: none of the IGW/EIP/NAT/public-subnet
# resources below exist. Flipping enable_nat_gateway restores arbitrary public
# egress from private subnets.

resource "aws_internet_gateway" "this" {
  count  = var.enable_nat_gateway ? 1 : 0
  vpc_id = aws_vpc.this.id

  tags = merge(var.combined_tags, {
    Name = "${var.resource_name_prefix}-igw"
  })
}

resource "aws_subnet" "public" {
  count                   = local.nat_count
  vpc_id                  = aws_vpc.this.id
  cidr_block              = local.public_cidrs[count.index]
  availability_zone       = local.azs[count.index]
  map_public_ip_on_launch = true

  tags = merge(var.combined_tags, {
    Name                     = "${var.resource_name_prefix}-public-${local.azs[count.index]}"
    "kubernetes.io/role/elb" = "1"
  })
}

resource "aws_eip" "nat" {
  count  = local.nat_count
  domain = "vpc"

  tags = merge(var.combined_tags, {
    Name = "${var.resource_name_prefix}-nat-eip-${local.azs[count.index]}"
  })
}

resource "aws_nat_gateway" "this" {
  count         = local.nat_count
  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public[count.index].id

  tags = merge(var.combined_tags, {
    Name = "${var.resource_name_prefix}-nat-${local.azs[count.index]}"
  })

  depends_on = [aws_internet_gateway.this]
}

resource "aws_route_table" "public" {
  count  = var.enable_nat_gateway ? 1 : 0
  vpc_id = aws_vpc.this.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this[0].id
  }

  tags = merge(var.combined_tags, {
    Name = "${var.resource_name_prefix}-public-rt"
  })
}

resource "aws_route_table_association" "public" {
  count          = local.nat_count
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public[0].id
}

# Per-AZ 0.0.0.0/0 → NAT route added to the private route tables only in the
# break-glass posture. In endpoints-only mode the private route tables carry just
# the local route + the S3 gateway prefix-list route.
resource "aws_route" "private_nat" {
  count                  = local.nat_count
  route_table_id         = aws_route_table.private[count.index].id
  destination_cidr_block = "0.0.0.0/0"
  nat_gateway_id         = aws_nat_gateway.this[count.index].id
}

# --- VPC endpoints (what replaces NAT) ---

# S3 gateway endpoint (free). Load-bearing for ALL image pulls, not just weights:
# ECR stores image layers in S3, so without this even vpc-cni's pull hangs.
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.this.id
  service_name      = "com.amazonaws.${data.aws_region.current.name}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = aws_route_table.private[*].id

  tags = merge(var.combined_tags, {
    Name = "${var.resource_name_prefix}-s3-gateway"
  })
}

# Security group for interface endpoints: allow 443 from within the VPC (nodes/pods).
resource "aws_security_group" "endpoints" {
  name_prefix = "${var.resource_name_prefix}-vpce-"
  description = "Allow HTTPS from the VPC to interface endpoints"
  vpc_id      = aws_vpc.this.id

  ingress {
    description = "HTTPS from within the VPC"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [local.vpc_cidr]
  }

  # Open egress is safe here: this SG is attached only to the interface-endpoint ENIs,
  # not to nodes/pods. A PrivateLink endpoint ENI never initiates outbound connections
  # (it only terminates inbound 443 from the VPC), and in the endpoints-only posture there
  # is no IGW/NAT — so the ENI has no route off the PrivateLink fabric regardless. The SG
  # is stateful too, so responses to the allowed inbound flow out anyway. The real boundary
  # is the ingress rule (443 from the VPC CIDR only); this egress cannot widen it.
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(var.combined_tags, {
    Name = "${var.resource_name_prefix}-vpce-sg"
  })

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_endpoint" "interface" {
  for_each = toset(local.interface_endpoints)

  vpc_id              = aws_vpc.this.id
  service_name        = "com.amazonaws.${data.aws_region.current.name}.${each.value}"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_security_group.endpoints.id]
  private_dns_enabled = true

  tags = merge(var.combined_tags, {
    Name = "${var.resource_name_prefix}-vpce-${each.value}"
  })
}
