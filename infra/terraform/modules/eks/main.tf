# A small EKS cluster: one managed node group, core addons, and its own IRSA OIDC
# provider (separate from the GitHub Actions OIDC provider in ../iam — this one is for
# pod-level IAM roles inside the cluster, a later concern, not CI).

data "aws_iam_policy_document" "cluster_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["eks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "cluster" {
  name               = "${var.name}-eks-cluster"
  assume_role_policy = data.aws_iam_policy_document.cluster_assume.json
  tags               = var.tags
}

resource "aws_iam_role_policy_attachment" "cluster_policy" {
  role       = aws_iam_role.cluster.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"
}

# EKS needs this to use aws_kms_key.eks_secrets for Secret envelope encryption -
# AmazonEKSClusterPolicy above does not include any KMS permissions.
data "aws_iam_policy_document" "cluster_kms" {
  statement {
    effect    = "Allow"
    actions   = ["kms:DescribeKey", "kms:CreateGrant", "kms:Encrypt", "kms:Decrypt", "kms:ReEncrypt*", "kms:GenerateDataKey*"]
    resources = [aws_kms_key.eks_secrets.arn]
  }
}

resource "aws_iam_role_policy" "cluster_kms" {
  name   = "${var.name}-eks-cluster-kms"
  role   = aws_iam_role.cluster.id
  policy = data.aws_iam_policy_document.cluster_kms.json
}

resource "aws_security_group" "cluster" {
  name_prefix = "${var.name}-eks-cluster-"
  description = "EKS control plane ENIs"
  vpc_id      = var.vpc_id
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = var.tags
}

resource "aws_kms_key" "eks_secrets" {
  description         = "Envelope encryption for ${var.name} EKS cluster Kubernetes Secrets"
  enable_key_rotation = true
  tags                = var.tags
}

resource "aws_kms_alias" "eks_secrets" {
  name          = "alias/${var.name}-eks-secrets"
  target_key_id = aws_kms_key.eks_secrets.key_id
}

resource "aws_eks_cluster" "this" {
  name     = "${var.name}-eks"
  role_arn = aws_iam_role.cluster.arn
  version  = var.kubernetes_version

  vpc_config {
    subnet_ids              = var.private_subnet_ids
    security_group_ids      = [aws_security_group.cluster.id]
    endpoint_private_access = true
    # Public endpoint stays enabled so `kubectl`/CI can reach it without a VPN/bastion;
    # this is a dev/reference environment, not a hardened production cluster. Waived
    # as AWS-0040/AWS-0041 in .trivyignore with the same reasoning.
    endpoint_public_access = true
  }

  access_config {
    authentication_mode = "API_AND_CONFIG_MAP"
  }

  # Envelope-encrypts Kubernetes Secret objects at rest in etcd with this CMK, on top
  # of etcd's own EBS-level encryption - closes AWS-0039 ("EKS should have the
  # encryption of secrets enabled").
  encryption_config {
    provider {
      key_arn = aws_kms_key.eks_secrets.arn
    }
    resources = ["secrets"]
  }

  depends_on = [aws_iam_role_policy_attachment.cluster_policy]
  tags       = var.tags
}

resource "aws_iam_openid_connect_provider" "irsa" {
  url             = aws_eks_cluster.this.identity[0].oidc[0].issuer
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = [data.tls_certificate.irsa.certificates[0].sha1_fingerprint]
  tags            = var.tags
}

data "tls_certificate" "irsa" {
  url = aws_eks_cluster.this.identity[0].oidc[0].issuer
}

data "aws_iam_policy_document" "node_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "node" {
  name               = "${var.name}-eks-node"
  assume_role_policy = data.aws_iam_policy_document.node_assume.json
  tags               = var.tags
}

resource "aws_iam_role_policy_attachment" "node_worker" {
  role       = aws_iam_role.node.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy"
}

resource "aws_iam_role_policy_attachment" "node_cni" {
  role       = aws_iam_role.node.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy"
}

resource "aws_iam_role_policy_attachment" "node_ecr" {
  role       = aws_iam_role.node.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
}

resource "aws_eks_node_group" "default" {
  cluster_name    = aws_eks_cluster.this.name
  node_group_name = "${var.name}-default"
  node_role_arn   = aws_iam_role.node.arn
  subnet_ids      = var.private_subnet_ids

  capacity_type  = var.capacity_type
  instance_types = var.instance_types

  scaling_config {
    desired_size = var.desired_size
    min_size     = var.min_size
    max_size     = var.max_size
  }

  update_config {
    max_unavailable = 1
  }

  depends_on = [
    aws_iam_role_policy_attachment.node_worker,
    aws_iam_role_policy_attachment.node_cni,
    aws_iam_role_policy_attachment.node_ecr,
  ]
  tags = var.tags
}

# The node group's auto-created security group ID isn't exposed as a resource
# attribute, so we look it up by its EKS-assigned tag instead of hardcoding one.
data "aws_security_groups" "node" {
  filter {
    name   = "tag:kubernetes.io/cluster/${aws_eks_cluster.this.name}"
    values = ["owned"]
  }
  depends_on = [aws_eks_node_group.default]
}

resource "aws_eks_addon" "vpc_cni" {
  cluster_name                = aws_eks_cluster.this.name
  addon_name                  = "vpc-cni"
  resolve_conflicts_on_update = "OVERWRITE"
  depends_on                  = [aws_eks_node_group.default]
}

resource "aws_eks_addon" "coredns" {
  cluster_name                = aws_eks_cluster.this.name
  addon_name                  = "coredns"
  resolve_conflicts_on_update = "OVERWRITE"
  depends_on                  = [aws_eks_node_group.default]
}

resource "aws_eks_addon" "kube_proxy" {
  cluster_name                = aws_eks_cluster.this.name
  addon_name                  = "kube-proxy"
  resolve_conflicts_on_update = "OVERWRITE"
  depends_on                  = [aws_eks_node_group.default]
}
