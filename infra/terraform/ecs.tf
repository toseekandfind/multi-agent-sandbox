#------------------------------------------------------------------------------
# Security Group for ECS Tasks
#------------------------------------------------------------------------------
resource "aws_security_group" "ecs_tasks" {
  name        = "${local.name}-ecs-tasks"
  description = "Security group for ECS tasks"
  vpc_id      = data.aws_vpc.default.id

  # Outbound: Allow all (needed for ECR, CloudWatch, SQS, DynamoDB, S3)
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name        = "${local.name}-ecs-tasks"
    Environment = var.environment
  }
}

#------------------------------------------------------------------------------
# Orchestrator Task Definition
#------------------------------------------------------------------------------
resource "aws_ecs_task_definition" "orchestrator" {
  family                   = "${local.name}-orchestrator"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.ecs_task_execution.arn
  task_role_arn            = aws_iam_role.orchestrator_task.arn

  container_definitions = jsonencode([
    {
      name      = "orchestrator"
      image     = "${aws_ecr_repository.control_api.repository_url}:latest"
      essential = true

      portMappings = [
        {
          containerPort = 8000
          hostPort      = 8000
          protocol      = "tcp"
        }
      ]

      environment = [
        {
          name  = "AWS_REGION"
          value = local.region
        },
        {
          name  = "SQS_QUEUE_URL"
          value = aws_sqs_queue.jobs.url
        },
        {
          name  = "DYNAMODB_TABLE"
          value = aws_dynamodb_table.jobs.name
        },
        {
          name  = "S3_BUCKET"
          value = aws_s3_bucket.artifacts.id
        },
        {
          name  = "ECS_CLUSTER"
          value = aws_ecs_cluster.main.arn
        },
        {
          name  = "WORKER_TASK_DEFINITION"
          value = "${local.name}-worker"
        },
        {
          name  = "WORKER_SUBNETS"
          value = join(",", data.aws_subnets.default.ids)
        },
        {
          name  = "WORKER_SECURITY_GROUP"
          value = aws_security_group.ecs_tasks.id
        },
        {
          name  = "MODE"
          value = "aws"
        }
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.orchestrator.name
          "awslogs-region"        = local.region
          "awslogs-stream-prefix" = "ecs"
        }
      }

      healthCheck = {
        command     = ["CMD-SHELL", "curl -f http://localhost:8000/health || exit 1"]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 60
      }
    }
  ])

  tags = {
    Name        = "${local.name}-orchestrator"
    Environment = var.environment
  }
}

#------------------------------------------------------------------------------
# Worker Task Definition
#------------------------------------------------------------------------------
resource "aws_ecs_task_definition" "worker" {
  family                   = "${local.name}-worker"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.ecs_task_execution.arn
  task_role_arn            = aws_iam_role.worker_task.arn

  container_definitions = jsonencode([
    {
      name      = "worker"
      image     = "${aws_ecr_repository.worker.repository_url}:latest"
      essential = true

      environment = [
        {
          name  = "AWS_REGION"
          value = local.region
        },
        {
          name  = "SQS_QUEUE_URL"
          value = aws_sqs_queue.jobs.url
        },
        {
          name  = "DYNAMODB_TABLE"
          value = aws_dynamodb_table.jobs.name
        },
        {
          name  = "S3_BUCKET"
          value = aws_s3_bucket.artifacts.id
        },
        {
          name  = "MODE"
          value = "aws"
        }
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.worker.name
          "awslogs-region"        = local.region
          "awslogs-stream-prefix" = "ecs"
        }
      }
    }
  ])

  tags = {
    Name        = "${local.name}-worker"
    Environment = var.environment
  }
}

#------------------------------------------------------------------------------
# Orchestrator ECS Service
#------------------------------------------------------------------------------
resource "aws_ecs_service" "orchestrator" {
  name            = "orchestrator"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.orchestrator.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  enable_execute_command = true

  network_configuration {
    subnets          = data.aws_subnets.default.ids
    security_groups  = [aws_security_group.ecs_tasks.id]
    assign_public_ip = true
  }

  deployment_maximum_percent         = 200
  deployment_minimum_healthy_percent = 100

  tags = {
    Name        = "${local.name}-orchestrator"
    Environment = var.environment
  }
}
