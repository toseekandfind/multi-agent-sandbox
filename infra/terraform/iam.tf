#------------------------------------------------------------------------------
# ECS Task Execution Role (for pulling images, writing logs)
#------------------------------------------------------------------------------
resource "aws_iam_role" "ecs_task_execution" {
  name = "${local.name}-ecs-task-execution"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
      }
    ]
  })

  tags = {
    Name        = "${local.name}-ecs-task-execution"
    Environment = var.environment
  }
}

resource "aws_iam_role_policy_attachment" "ecs_task_execution" {
  role       = aws_iam_role.ecs_task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

#------------------------------------------------------------------------------
# Orchestrator Task Role
#------------------------------------------------------------------------------
resource "aws_iam_role" "orchestrator_task" {
  name = "${local.name}-orchestrator-task"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
      }
    ]
  })

  tags = {
    Name        = "${local.name}-orchestrator-task"
    Environment = var.environment
  }
}

resource "aws_iam_role_policy" "orchestrator_task" {
  name = "${local.name}-orchestrator-task-policy"
  role = aws_iam_role.orchestrator_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "SQSAccess"
        Effect = "Allow"
        Action = [
          "sqs:SendMessage",
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes",
          "sqs:GetQueueUrl"
        ]
        Resource = aws_sqs_queue.jobs.arn
      },
      {
        Sid    = "ECSRunTask"
        Effect = "Allow"
        Action = [
          "ecs:RunTask",
          "ecs:StopTask",
          "ecs:DescribeTasks"
        ]
        Resource = [
          "arn:aws:ecs:${local.region}:${local.account_id}:task/${local.name}/*",
          aws_ecs_task_definition.worker.arn
        ]
      },
      {
        Sid    = "PassWorkerRole"
        Effect = "Allow"
        Action = "iam:PassRole"
        Resource = [
          aws_iam_role.worker_task.arn,
          aws_iam_role.ecs_task_execution.arn
        ]
      },
      {
        Sid    = "DynamoDBAccess"
        Effect = "Allow"
        Action = [
          "dynamodb:PutItem",
          "dynamodb:UpdateItem",
          "dynamodb:GetItem",
          "dynamodb:Query"
        ]
        Resource = aws_dynamodb_table.jobs.arn
      },
      {
        Sid    = "S3ArtifactsWrite"
        Effect = "Allow"
        Action = [
          "s3:PutObject",
          "s3:GetObject"
        ]
        Resource = "${aws_s3_bucket.artifacts.arn}/jobs/*"
      }
    ]
  })
}

# SSM access for ECS Exec
resource "aws_iam_role_policy" "orchestrator_ssm" {
  name = "${local.name}-orchestrator-ssm-policy"
  role = aws_iam_role.orchestrator_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ssmmessages:CreateControlChannel",
          "ssmmessages:CreateDataChannel",
          "ssmmessages:OpenControlChannel",
          "ssmmessages:OpenDataChannel"
        ]
        Resource = "*"
      }
    ]
  })
}

#------------------------------------------------------------------------------
# Worker Task Role
#------------------------------------------------------------------------------
resource "aws_iam_role" "worker_task" {
  name = "${local.name}-worker-task"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
      }
    ]
  })

  tags = {
    Name        = "${local.name}-worker-task"
    Environment = var.environment
  }
}

resource "aws_iam_role_policy" "worker_task" {
  name = "${local.name}-worker-task-policy"
  role = aws_iam_role.worker_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "SQSReceive"
        Effect = "Allow"
        Action = [
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes"
        ]
        Resource = aws_sqs_queue.jobs.arn
      },
      {
        Sid    = "DynamoDBJobUpdate"
        Effect = "Allow"
        Action = [
          "dynamodb:UpdateItem",
          "dynamodb:GetItem"
        ]
        Resource = aws_dynamodb_table.jobs.arn
      },
      {
        Sid      = "S3ArtifactsWrite"
        Effect   = "Allow"
        Action   = "s3:PutObject"
        Resource = "${aws_s3_bucket.artifacts.arn}/jobs/*"
      }
    ]
  })
}
