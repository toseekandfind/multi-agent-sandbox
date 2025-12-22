output "aws_account_id" {
  description = "AWS Account ID"
  value       = local.account_id
}

output "aws_region" {
  description = "AWS Region"
  value       = local.region
}

output "s3_bucket" {
  description = "S3 bucket for artifacts"
  value       = aws_s3_bucket.artifacts.id
}

output "ecr_control_api_url" {
  description = "ECR repository URL for control API"
  value       = aws_ecr_repository.control_api.repository_url
}

output "ecr_worker_url" {
  description = "ECR repository URL for worker"
  value       = aws_ecr_repository.worker.repository_url
}

output "sqs_queue_url" {
  description = "SQS queue URL"
  value       = aws_sqs_queue.jobs.url
}

output "dynamodb_table" {
  description = "DynamoDB table name"
  value       = aws_dynamodb_table.jobs.name
}

output "ecs_cluster" {
  description = "ECS cluster name"
  value       = aws_ecs_cluster.main.name
}

output "ecs_service" {
  description = "ECS service name"
  value       = aws_ecs_service.orchestrator.name
}

output "security_group_id" {
  description = "Security group ID for ECS tasks"
  value       = aws_security_group.ecs_tasks.id
}

output "subnets" {
  description = "Subnet IDs"
  value       = join(",", data.aws_subnets.default.ids)
}

output "ecr_login_command" {
  description = "Command to login to ECR"
  value       = "aws ecr get-login-password --region ${local.region} --profile ${var.aws_profile} | docker login --username AWS --password-stdin ${local.account_id}.dkr.ecr.${local.region}.amazonaws.com"
}

output "scale_down_command" {
  description = "Command to scale down orchestrator"
  value       = "aws ecs update-service --cluster ${aws_ecs_cluster.main.name} --service orchestrator --desired-count 0 --profile ${var.aws_profile}"
}

output "scale_up_command" {
  description = "Command to scale up orchestrator"
  value       = "aws ecs update-service --cluster ${aws_ecs_cluster.main.name} --service orchestrator --desired-count 1 --profile ${var.aws_profile}"
}
