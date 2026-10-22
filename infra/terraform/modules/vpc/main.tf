# Minimal VPC for the AlphaDots dev reference environment: two public subnets (NAT,
# load balancers) and two private subnets (EKS nodes, RDS) across two AZs. A single NAT
# gateway is used, not one per AZ, to keep the always-on cost of this environment low;
# that makes outbound traffic from private subnets single-AZ-dependent, which is an
# accepted tradeoff for a non-production reference stack (see ../../environments/dev/README.md).

data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  azs = slice(data.aws_availability_zones.available.names, 0, 2)
}

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags = merge(var.tags, {
    Name = "${var.name}-vpc"
  })
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id
  tags = merge(var.tags, {
    Name = "${var.name}-igw"
  })
}

resource "aws_subnet" "public" {
  count             = length(local.azs)
  vpc_id            = aws_vpc.this.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 4, count.index)
  availability_zone = local.azs[count.index]
  # Off, not on: the NAT gateway's public IP comes from its explicitly associated EIP
  # (aws_nat_gateway.allocation_id below), not from this subnet setting. Auto-assigning
  # a public IP to every ENI launched here would give one to anything else placed in
  # this subnet too, whether it needs one or not (Trivy AWS-0164) - a future resource
  # that genuinely needs a public IP should request one explicitly instead.
  map_public_ip_on_launch = false
  tags = merge(var.tags, {
    Name                     = "${var.name}-public-${local.azs[count.index]}"
    "kubernetes.io/role/elb" = "1"
  })
}

resource "aws_subnet" "private" {
  count             = length(local.azs)
  vpc_id            = aws_vpc.this.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 4, count.index + length(local.azs))
  availability_zone = local.azs[count.index]
  tags = merge(var.tags, {
    Name                              = "${var.name}-private-${local.azs[count.index]}"
    "kubernetes.io/role/internal-elb" = "1"
  })
}

resource "aws_eip" "nat" {
  domain = "vpc"
  tags = merge(var.tags, {
    Name = "${var.name}-nat-eip"
  })
}

# Single NAT gateway (cost tradeoff noted above), placed in the first public subnet.
resource "aws_nat_gateway" "this" {
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.public[0].id
  tags = merge(var.tags, {
    Name = "${var.name}-nat"
  })
  depends_on = [aws_internet_gateway.this]
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }
  tags = merge(var.tags, { Name = "${var.name}-public-rt" })
}

resource "aws_route_table_association" "public" {
  count          = length(aws_subnet.public)
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.this.id
  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.this.id
  }
  tags = merge(var.tags, { Name = "${var.name}-private-rt" })
}

resource "aws_route_table_association" "private" {
  count          = length(aws_subnet.private)
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}
