# Setup Guide

This guide helps set up the multi-agent sandbox on a new machine. It's designed for both humans and AI agents to follow.

## Prerequisites Checklist

Before starting, ensure these tools are installed:

- [ ] **Docker** - `docker --version`
- [ ] **AWS CLI v2** - `aws --version`
- [ ] **Terraform 1.0+** - `terraform --version`
- [ ] **Session Manager Plugin** (optional, for ECS Exec) - [Install guide](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html)

---

## Required Information

**AI Agent Note:** Ask the user for these values before proceeding with setup.

| Variable | Description | Example |
|----------|-------------|---------|
| `AWS_PROFILE` | AWS CLI profile name to use | `default`, `dev`, `prod` |
| `AWS_REGION` | AWS region for deployment | `us-east-1`, `us-west-2` |
| `ANTHROPIC_API_KEY` | API key from console.anthropic.com | `sk-ant-...` |

### Questions to Ask

1. **"What AWS profile should I use?"**
   - Check available profiles: `aws configure list-profiles`
   - If none exist, help them configure one: `aws configure --profile <name>`

2. **"What AWS region should I deploy to?"**
   - Default: `us-east-1`
   - Consider: proximity to users, service availability

3. **"Do you have an Anthropic API key?"**
   - Get one at: https://console.anthropic.com/settings/keys
   - Required for Claude-powered agents (can skip for echo-only testing)

---

## Setup Steps

### Step 1: Configure AWS Profile

```bash
# Verify AWS credentials work
aws sts get-caller-identity --profile <AWS_PROFILE>

# Expected output includes: Account, Arn, UserId
```

If this fails, the user needs to configure AWS credentials first.

### Step 2: Set Terraform Variables

Create `infra/terraform/terraform.tfvars`:

```hcl
aws_profile = "<AWS_PROFILE>"
aws_region  = "<AWS_REGION>"
```

### Step 3: Deploy Infrastructure

```bash
cd infra/terraform

# Initialize Terraform
terraform init

# Preview changes
terraform plan

# Deploy (requires user confirmation)
terraform apply
```

**Save the outputs** - they contain resource IDs needed for later steps.

### Step 4: Store Anthropic API Key (if using Claude)

```bash
aws secretsmanager create-secret \
  --name agent-runner/anthropic-api-key \
  --secret-string "<ANTHROPIC_API_KEY>" \
  --profile <AWS_PROFILE> \
  --region <AWS_REGION>
```

### Step 5: Build and Push Docker Images

```bash
# Get values from terraform output
ACCOUNT_ID=$(aws sts get-caller-identity --profile <AWS_PROFILE> --query Account --output text)
REGION=<AWS_REGION>

# Login to ECR
aws ecr get-login-password --region $REGION --profile <AWS_PROFILE> | \
  docker login --username AWS --password-stdin $ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com

# Build and push control-api
docker build --platform linux/amd64 -t $ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/agent-runner-control-api:latest control_api/
docker push $ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/agent-runner-control-api:latest

# Build and push worker
docker build --platform linux/amd64 -t $ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/agent-runner-worker:latest worker/
docker push $ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/agent-runner-worker:latest
```

### Step 6: Deploy ECS Service

```bash
# Force new deployment to pull latest images
aws ecs update-service \
  --cluster agent-runner \
  --service orchestrator \
  --force-new-deployment \
  --profile <AWS_PROFILE> \
  --region <AWS_REGION>

# Wait for service to stabilize (takes ~2-3 minutes)
aws ecs wait services-stable \
  --cluster agent-runner \
  --services orchestrator \
  --profile <AWS_PROFILE> \
  --region <AWS_REGION>
```

### Step 7: Verify Deployment

```bash
# Check service status
aws ecs describe-services \
  --cluster agent-runner \
  --services orchestrator \
  --profile <AWS_PROFILE> \
  --region <AWS_REGION> \
  --query 'services[0].{status:status,running:runningCount,desired:desiredCount}'
```

Expected: `status: ACTIVE`, `running: 1`, `desired: 1`

---

## Local Development Setup

For local testing without AWS:

```bash
# Copy environment template
cp .env.example .env

# Start services (Redis + API + Workers)
docker-compose -f compose/docker-compose.yml up --build

# Test health endpoint
curl http://localhost:8000/health

# Submit a test job
curl -X POST http://localhost:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{"job_type": "echo", "payload": {"message": "Hello!"}}'
```

---

## Quick Reference Commands

### Cost Control

```bash
# Stop all ECS tasks (stop spending)
aws ecs update-service --cluster agent-runner --service orchestrator \
  --desired-count 0 --profile <AWS_PROFILE> --region <AWS_REGION>

# Resume
aws ecs update-service --cluster agent-runner --service orchestrator \
  --desired-count 1 --profile <AWS_PROFILE> --region <AWS_REGION>
```

### Full Cleanup

```bash
cd infra/terraform
terraform destroy
```

### Submit a Claude Job (after setup)

```bash
# Store job in DynamoDB
JOB_ID="test-$(date +%s)"
aws dynamodb put-item \
  --table-name agent-runner-jobs \
  --item "{
    \"job_id\": {\"S\": \"$JOB_ID\"},
    \"job_type\": {\"S\": \"claude_chat\"},
    \"status\": {\"S\": \"QUEUED\"},
    \"payload\": {\"M\": {\"prompt\": {\"S\": \"Say hello\"}, \"max_tokens\": {\"N\": \"50\"}}},
    \"created_at\": {\"S\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"},
    \"updated_at\": {\"S\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}
  }" \
  --profile <AWS_PROFILE> --region <AWS_REGION>

# Send to SQS
aws sqs send-message \
  --queue-url https://sqs.<AWS_REGION>.amazonaws.com/<ACCOUNT_ID>/agent-runner-jobs \
  --message-body "{\"job_id\": \"$JOB_ID\"}" \
  --profile <AWS_PROFILE> --region <AWS_REGION>

# Start worker
aws ecs run-task \
  --cluster agent-runner \
  --task-definition agent-runner-worker \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[<SUBNET_ID>],securityGroups=[<SG_ID>],assignPublicIp=ENABLED}" \
  --overrides "{\"containerOverrides\":[{\"name\":\"worker\",\"environment\":[{\"name\":\"JOB_ID\",\"value\":\"$JOB_ID\"}]}]}" \
  --profile <AWS_PROFILE> --region <AWS_REGION>
```

---

## Troubleshooting

### "No credentials" error
```bash
aws configure --profile <AWS_PROFILE>
# Enter: Access Key ID, Secret Access Key, Region, Output format
```

### ECS tasks failing health checks
- Ensure Docker images were built with `--platform linux/amd64`
- Check CloudWatch logs: `/ecs/agent-runner/orchestrator`

### Claude jobs failing with "Connection error"
- Verify API key in Secrets Manager (no trailing whitespace)
- Check worker IAM role has `secretsmanager:GetSecretValue` permission

### Terraform state issues
```bash
# If state is corrupted, import existing resources or start fresh
terraform init -reconfigure
```
