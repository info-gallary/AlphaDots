output "endpoint" {
  value = aws_db_instance.this.address
}

output "port" {
  value = aws_db_instance.this.port
}

output "database_name" {
  value = aws_db_instance.this.db_name
}

output "security_group_id" {
  value = aws_security_group.db.id
}

output "credentials_secret_arn" {
  description = "Secrets Manager ARN holding the master username/password/host/port/dbname. Fetch at deploy time; never read into Terraform state as plain output."
  value       = aws_secretsmanager_secret.db_credentials.arn
}
