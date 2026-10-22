output "cluster_name" {
  value = aws_eks_cluster.this.name
}

output "cluster_arn" {
  value = aws_eks_cluster.this.arn
}

output "cluster_endpoint" {
  value = aws_eks_cluster.this.endpoint
}

output "cluster_certificate_authority_data" {
  value     = aws_eks_cluster.this.certificate_authority[0].data
  sensitive = true
}

output "cluster_oidc_issuer_url" {
  value = aws_eks_cluster.this.identity[0].oidc[0].issuer
}

output "node_security_group_id" {
  description = "The node group's EKS-managed security group, looked up by its cluster-owned tag."
  value       = data.aws_security_groups.node.ids[0]
}
