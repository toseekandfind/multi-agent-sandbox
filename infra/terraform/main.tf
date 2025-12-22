data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# Get default VPC
data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.name
  name       = var.project_name
}

#------------------------------------------------------------------------------
# S3 Bucket for Artifacts
#------------------------------------------------------------------------------
resource "aws_s3_bucket" "artifacts" {
  bucket = "${local.name}-artifacts-${local.account_id}"

  tags = {
    Name        = "${local.name}-artifacts"
    Environment = var.environment
  }
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

#------------------------------------------------------------------------------
# ECR Repositories
#------------------------------------------------------------------------------
resource "aws_ecr_repository" "control_api" {
  name                 = "${local.name}-control-api"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = {
    Name        = "${local.name}-control-api"
    Environment = var.environment
  }
}

resource "aws_ecr_repository" "worker" {
  name                 = "${local.name}-worker"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = {
    Name        = "${local.name}-worker"
    Environment = var.environment
  }
}

#------------------------------------------------------------------------------
# SQS Queue
#------------------------------------------------------------------------------
resource "aws_sqs_queue" "jobs" {
  name                       = "${local.name}-jobs"
  visibility_timeout_seconds = 300
  message_retention_seconds  = 86400
  receive_wait_time_seconds  = 20

  tags = {
    Name        = "${local.name}-jobs"
    Environment = var.environment
  }
}

#------------------------------------------------------------------------------
# DynamoDB Table
#------------------------------------------------------------------------------
resource "aws_dynamodb_table" "jobs" {
  name         = "${local.name}-jobs"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "job_id"

  attribute {
    name = "job_id"
    type = "S"
  }

  tags = {
    Name        = "${local.name}-jobs"
    Environment = var.environment
  }
}

#------------------------------------------------------------------------------
# CloudWatch Log Groups
#------------------------------------------------------------------------------
resource "aws_cloudwatch_log_group" "orchestrator" {
  name              = "/ecs/${local.name}/orchestrator"
  retention_in_days = 7

  tags = {
    Name        = "${local.name}-orchestrator-logs"
    Environment = var.environment
  }
}

resource "aws_cloudwatch_log_group" "worker" {
  name              = "/ecs/${local.name}/worker"
  retention_in_days = 7

  tags = {
    Name        = "${local.name}-worker-logs"
    Environment = var.environment
  }
}

#------------------------------------------------------------------------------
# ECS Cluster
#------------------------------------------------------------------------------
resource "aws_ecs_cluster" "main" {
  name = local.name

  setting {
    name  = "containerInsights"
    value = "disabled"
  }

  tags = {
    Name        = local.name
    Environment = var.environment
  }
}

resource "aws_ecs_cluster_capacity_providers" "main" {
  cluster_name = aws_ecs_cluster.main.name

  capacity_providers = ["FARGATE", "FARGATE_SPOT"]

  default_capacity_provider_strategy {
    capacity_provider = "FARGATE_SPOT"
    weight            = 1
  }
}
