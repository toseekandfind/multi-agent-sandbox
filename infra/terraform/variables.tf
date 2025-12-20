variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "aws_profile" {
  description = "AWS CLI profile"
  type        = string
  default     = "dan-admin"
}

variable "project_name" {
  description = "Project name for resource naming"
  type        = string
  default     = "agent-runner"
}

variable "environment" {
  description = "Environment name"
  type        = string
  default     = "dev"
}
