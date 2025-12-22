# Multi-Agent Sandbox

An orchestrator + on-demand agents system on AWS. The orchestrator stays up; agents (workers) run only when there is work.

> **New here?** See the [Full Guide](docs/GUIDE.md) for how to add Claude integration and build AI agents.

## Quick Summary

| What | Description |
|------|-------------|
| **Current State** | Job processing framework with `echo` placeholder |
| **Next Step** | Add Claude integration to create AI-powered agents |
| **Cost When Idle** | ~$10-15/month (orchestrator only) |
| **Cost Per Job** | ~$0.005 (mostly Claude API) |

## Architecture

- **Orchestrator**: ECS Service (Fargate), desired_count=1
- **Workers**: ECS Fargate tasks started per job
- **Queue**: SQS (Redis for local dev)
- **Job Status**: DynamoDB (Redis for local dev)
- **Artifacts**: S3
- **Images**: ECR (two repos)
- **Logs**: CloudWatch Logs

## Job Contract

- `job_type`: "echo" for V1
- `payload`: JSON
- Worker writes: `result.json` + logs
- Artifacts uploaded to: `s3://<bucket>/jobs/<job_id>/`
- Status flow: `QUEUED -> RUNNING -> SUCCEEDED | FAILED`

---

## Local Development

### Prerequisites

- Docker & Docker Compose
- Python 3.11+

### Run Locally

```bash
# Copy environment file
cp .env.example .env

# Start all services
docker-compose -f compose/docker-compose.yml up --build

# Scale workers
docker-compose -f compose/docker-compose.yml up --scale worker=5
```

### API Endpoints

| Method | Endpoint         | Description          |
|--------|------------------|----------------------|
| POST   | /jobs            | Submit a new job     |
| GET    | /jobs/{job_id}   | Get job status       |
| GET    | /health          | Health check         |

### curl Examples

```bash
# Health check
curl http://localhost:8000/health

# Submit an echo job
curl -X POST http://localhost:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{"job_type": "echo", "payload": {"message": "Hello, World!"}}'

# Get job status
curl http://localhost:8000/jobs/{job_id}

# Submit 20 jobs (bash loop)
for i in {1..20}; do
  curl -X POST http://localhost:8000/jobs \
    -H "Content-Type: application/json" \
    -d "{\"job_type\": \"echo\", \"payload\": {\"message\": \"Job $i\"}}"
  echo ""
done
```

---

## AWS Deployment

### Prerequisites

- AWS CLI configured with `dan-admin` profile
- Terraform 1.0+

### Deploy Infrastructure

```bash
cd infra/terraform

# Initialize
terraform init

# Format and validate
terraform fmt
terraform validate

# Plan
terraform plan

# Apply (only when ready)
terraform apply
```

### Build & Push Images

```bash
# Login to ECR
aws ecr get-login-password --region us-east-1 --profile dan-admin | \
  docker login --username AWS --password-stdin <account_id>.dkr.ecr.us-east-1.amazonaws.com

# Build and push control-api
docker build -t agent-runner-control-api control_api/
docker tag agent-runner-control-api:latest <ecr_uri>/agent-runner-control-api:latest
docker push <ecr_uri>/agent-runner-control-api:latest

# Build and push worker
docker build -t agent-runner-worker worker/
docker tag agent-runner-worker:latest <ecr_uri>/agent-runner-worker:latest
docker push <ecr_uri>/agent-runner-worker:latest
```

### Access Orchestrator (SSM Port Forward)

```bash
# Find the orchestrator task
TASK_ID=$(aws ecs list-tasks --cluster agent-runner --service-name orchestrator \
  --profile dan-admin --query 'taskArns[0]' --output text | cut -d'/' -f3)

# Port forward via SSM
aws ssm start-session \
  --target ecs:agent-runner_${TASK_ID}_<container_runtime_id> \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["8000"],"localPortNumber":["8000"]}' \
  --profile dan-admin
```

---

## Cost Control

### Scale Down (Stop Spend)

```bash
# Scale orchestrator to 0
aws ecs update-service --cluster agent-runner --service orchestrator \
  --desired-count 0 --profile dan-admin

# Scale back up
aws ecs update-service --cluster agent-runner --service orchestrator \
  --desired-count 1 --profile dan-admin
```

### Destroy All Resources

```bash
cd infra/terraform
terraform destroy
```

---

## Project Structure

```
.
├── control_api/          # FastAPI orchestrator
├── worker/               # Job worker
├── infra/terraform/      # AWS infrastructure
├── compose/              # Docker Compose for local dev
├── docs/                 # Documentation
│   └── GUIDE.md          # Full usage guide
├── .env.example          # Environment template
└── README.md

---

## What's Next?

The current `echo` job type is a placeholder. To build actual AI agents:

### 1. Add Claude Integration (5 min)

```bash
# Add to worker/requirements.txt
echo "anthropic>=0.39.0" >> worker/requirements.txt
```

### 2. Store API Key

```bash
aws secretsmanager create-secret \
  --name agent-runner/anthropic-api-key \
  --secret-string "sk-ant-your-key" \
  --profile dan-admin --region us-east-1
```

### 3. Add Claude Handler

See [docs/GUIDE.md](docs/GUIDE.md#adding-claude-integration) for the full code.

### 4. Rebuild & Deploy

```bash
docker build --platform linux/amd64 -t 515705785593.dkr.ecr.us-east-1.amazonaws.com/agent-runner-worker:latest worker/
docker push 515705785593.dkr.ecr.us-east-1.amazonaws.com/agent-runner-worker:latest
```

### Example Claude Job

```bash
curl -X POST http://localhost:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "job_type": "claude_chat",
    "payload": {
      "prompt": "Explain Docker in one paragraph",
      "max_tokens": 200
    }
  }'
```

---

## Does Each Agent Use Claude?

**Yes, each worker makes its own Claude API calls.** The architecture:

```
Job A submitted ──▶ Worker A starts ──▶ Calls Claude API ──▶ Returns result ──▶ Worker exits
Job B submitted ──▶ Worker B starts ──▶ Calls Claude API ──▶ Returns result ──▶ Worker exits
```

- Workers are isolated containers (no shared state)
- Each worker loads the API key from Secrets Manager
- You pay per Claude API call, not per worker
- Workers exit after completing their job (no idle time)
