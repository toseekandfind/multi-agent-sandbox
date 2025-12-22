# Multi-Agent Sandbox Guide

This guide explains how the orchestrator + on-demand agents system works and how to extend it with Claude-powered AI agents.

## Table of Contents

1. [How It Works](#how-it-works)
2. [Current State](#current-state)
3. [Adding Claude Integration](#adding-claude-integration)
4. [Job Types & Patterns](#job-types--patterns)
5. [Running Agents](#running-agents)
6. [Cost Considerations](#cost-considerations)
7. [Next Steps](#next-steps)

---

## How It Works

### Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                           AWS Cloud                                  │
│                                                                      │
│  ┌──────────────┐     ┌─────────┐     ┌──────────────────────────┐ │
│  │ Orchestrator │────▶│   SQS   │     │      ECS Fargate         │ │
│  │   (FastAPI)  │     │  Queue  │     │                          │ │
│  │              │     └────┬────┘     │  ┌────────┐ ┌────────┐   │ │
│  │  - POST /jobs│          │          │  │Worker 1│ │Worker 2│   │ │
│  │  - GET /jobs │     ┌────▼────┐     │  │ (Job A)│ │ (Job B)│   │ │
│  │              │────▶│DynamoDB │     │  └────────┘ └────────┘   │ │
│  └──────────────┘     │ (State) │     │       ...more workers    │ │
│         │             └─────────┘     └──────────────────────────┘ │
│         │                                        │                  │
│         │             ┌─────────┐                │                  │
│         └────────────▶│   S3    │◀───────────────┘                  │
│                       │(Results)│                                   │
│                       └─────────┘                                   │
└─────────────────────────────────────────────────────────────────────┘
```

### Request Flow

1. **Submit Job**: Client sends `POST /jobs` with job type and payload
2. **Store State**: Orchestrator writes job to DynamoDB (status: `QUEUED`)
3. **Queue Message**: Orchestrator sends job_id to SQS
4. **Start Worker**: Orchestrator launches a Fargate task with `JOB_ID` env var
5. **Process Job**: Worker reads job from DynamoDB, executes handler, updates status to `RUNNING`
6. **Complete Job**: Worker uploads result to S3, updates DynamoDB to `SUCCEEDED` or `FAILED`
7. **Worker Exits**: Fargate task terminates (no idle cost)

### Why This Architecture?

| Benefit | Description |
|---------|-------------|
| **Pay-per-use** | Workers only run when there's work; no idle costs |
| **Scalable** | Each job gets its own container; run hundreds in parallel |
| **Isolated** | Jobs can't interfere with each other |
| **Durable** | State in DynamoDB survives container crashes |
| **Observable** | All logs in CloudWatch, results in S3 |

---

## Current State

Right now, the system has one job type: `echo`

```python
# worker/main.py
def handle_echo_job(job_id: str, payload: dict) -> dict:
    message = payload.get("message", "")
    time.sleep(0.5)  # Simulate work
    return {
        "echoed_message": message,
        "processed_by": WORKER_ID,
        "processed_at": datetime.utcnow().isoformat(),
    }
```

This is a **placeholder** to prove the infrastructure works. It doesn't use Claude or any AI.

---

## Adding Claude Integration

To make workers into Claude-powered agents, you need to:

### Step 1: Add Anthropic SDK to Worker

Update `worker/requirements.txt`:

```
anthropic>=0.39.0
boto3>=1.26.0
```

### Step 2: Store API Key in Secrets Manager

```bash
# Create secret (do this once)
aws secretsmanager create-secret \
  --name agent-runner/anthropic-api-key \
  --secret-string "sk-ant-your-api-key-here" \
  --profile dan-admin --region us-east-1
```

### Step 3: Update IAM Policy

Add to `infra/terraform/iam.tf` in `worker_task` policy:

```hcl
{
  Effect = "Allow"
  Action = [
    "secretsmanager:GetSecretValue"
  ]
  Resource = "arn:aws:secretsmanager:${local.region}:${data.aws_caller_identity.current.account_id}:secret:agent-runner/*"
}
```

### Step 4: Create Claude Job Handler

Add to `worker/main.py`:

```python
import anthropic

def get_anthropic_client():
    """Get Anthropic client with API key from Secrets Manager."""
    if MODE == "aws":
        import boto3
        secrets = boto3.client("secretsmanager", region_name=AWS_REGION)
        response = secrets.get_secret_value(SecretId="agent-runner/anthropic-api-key")
        api_key = response["SecretString"]
    else:
        api_key = os.getenv("ANTHROPIC_API_KEY")

    return anthropic.Anthropic(api_key=api_key)


def handle_claude_chat_job(job_id: str, payload: dict) -> dict:
    """
    Claude chat job handler.

    Payload:
        prompt: str - The user prompt
        system: str (optional) - System prompt
        max_tokens: int (optional) - Max response tokens (default 1024)
        model: str (optional) - Model to use (default claude-sonnet-4-20250514)
    """
    client = get_anthropic_client()

    prompt = payload.get("prompt", "")
    system = payload.get("system", "You are a helpful assistant.")
    max_tokens = payload.get("max_tokens", 1024)
    model = payload.get("model", "claude-sonnet-4-20250514")

    message = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": prompt}]
    )

    return {
        "response": message.content[0].text,
        "model": model,
        "usage": {
            "input_tokens": message.usage.input_tokens,
            "output_tokens": message.usage.output_tokens,
        },
        "processed_by": WORKER_ID,
        "processed_at": datetime.utcnow().isoformat(),
    }
```

### Step 5: Register the Handler

Update the job dispatch in `process_job()`:

```python
if job_type == "echo":
    result = handle_echo_job(job_id, payload)
elif job_type == "claude_chat":
    result = handle_claude_chat_job(job_id, payload)
else:
    raise ValueError(f"Unknown job type: {job_type}")
```

### Step 6: Rebuild and Deploy

```bash
# Build with linux/amd64 platform
docker build --platform linux/amd64 -t agent-runner-worker worker/

# Tag and push
docker tag agent-runner-worker:latest 515705785593.dkr.ecr.us-east-1.amazonaws.com/agent-runner-worker:latest
docker push 515705785593.dkr.ecr.us-east-1.amazonaws.com/agent-runner-worker:latest
```

---

## Job Types & Patterns

### Pattern 1: Simple Chat

```json
{
  "job_type": "claude_chat",
  "payload": {
    "prompt": "Explain quantum computing in simple terms",
    "max_tokens": 500
  }
}
```

### Pattern 2: Code Generation

```python
def handle_claude_code_job(job_id: str, payload: dict) -> dict:
    """Generate code based on specifications."""
    client = get_anthropic_client()

    spec = payload.get("specification", "")
    language = payload.get("language", "python")

    system = f"""You are an expert {language} developer.
    Generate clean, well-documented code based on specifications.
    Only output code, no explanations."""

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        system=system,
        messages=[{"role": "user", "content": spec}]
    )

    return {
        "code": message.content[0].text,
        "language": language,
        "usage": {...},
    }
```

### Pattern 3: Multi-Step Agent

```python
def handle_research_agent_job(job_id: str, payload: dict) -> dict:
    """
    Multi-step research agent that:
    1. Breaks down the research question
    2. Gathers information (could call external APIs)
    3. Synthesizes findings
    """
    client = get_anthropic_client()
    topic = payload.get("topic", "")

    # Step 1: Plan research
    plan = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1024,
        messages=[{
            "role": "user",
            "content": f"Break down this research topic into 3-5 key questions: {topic}"
        }]
    )

    # Step 2: Research each question (simplified - could call web APIs)
    questions = plan.content[0].text

    # Step 3: Synthesize
    synthesis = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2048,
        messages=[{
            "role": "user",
            "content": f"Research topic: {topic}\n\nKey questions:\n{questions}\n\nProvide a comprehensive summary."
        }]
    )

    return {
        "topic": topic,
        "research_plan": questions,
        "synthesis": synthesis.content[0].text,
    }
```

### Pattern 4: Tool-Using Agent

```python
def handle_tool_agent_job(job_id: str, payload: dict) -> dict:
    """Agent that can use tools (Claude's tool use feature)."""
    client = get_anthropic_client()

    tools = [
        {
            "name": "get_weather",
            "description": "Get current weather for a location",
            "input_schema": {
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "City name"}
                },
                "required": ["location"]
            }
        }
    ]

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1024,
        tools=tools,
        messages=[{"role": "user", "content": payload.get("prompt", "")}]
    )

    # Handle tool calls...
    return {"response": message.content, "tool_calls": [...]}
```

---

## Running Agents

### Local Development (with Claude)

```bash
# Set API key in .env
echo "ANTHROPIC_API_KEY=sk-ant-..." >> .env

# Start services
docker-compose -f compose/docker-compose.yml up --build

# Submit a Claude job
curl -X POST http://localhost:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "job_type": "claude_chat",
    "payload": {
      "prompt": "Write a haiku about containers",
      "max_tokens": 100
    }
  }'
```

### AWS (via ECS Exec)

Since there's no public inbound access, use ECS Exec:

```bash
# Install Session Manager plugin first:
# https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html

# Get task ID
TASK_ID=$(aws ecs list-tasks --cluster agent-runner --service-name orchestrator \
  --profile dan-admin --query 'taskArns[0]' --output text | rev | cut -d'/' -f1 | rev)

# Execute command in container
aws ecs execute-command \
  --cluster agent-runner \
  --task $TASK_ID \
  --container orchestrator \
  --interactive \
  --command "curl -X POST http://localhost:8000/jobs -H 'Content-Type: application/json' -d '{\"job_type\":\"echo\",\"payload\":{\"message\":\"test\"}}'"
```

---

## Cost Considerations

### Per-Job Costs

| Component | Cost Estimate |
|-----------|---------------|
| ECS Fargate (0.25 vCPU, 0.5GB, 10 sec) | ~$0.00003 |
| Claude Sonnet (1K input, 500 output tokens) | ~$0.0045 |
| DynamoDB (1 write, 1 read) | ~$0.000001 |
| S3 (1KB result) | ~$0.00001 |
| **Total per job** | **~$0.005** |

### Fixed Costs (Orchestrator Running)

| Component | Cost/Month |
|-----------|------------|
| ECS Fargate (0.25 vCPU, 0.5GB, 24/7) | ~$9 |
| CloudWatch Logs | ~$1-5 |
| **Total** | **~$10-15/month** |

### Cost Control

```bash
# Stop all costs (except S3/DynamoDB storage)
aws ecs update-service --cluster agent-runner --service orchestrator \
  --desired-count 0 --profile dan-admin --region us-east-1

# Resume
aws ecs update-service --cluster agent-runner --service orchestrator \
  --desired-count 1 --profile dan-admin --region us-east-1

# Full cleanup
cd infra/terraform && terraform destroy
```

---

## Next Steps

### Recommended Order

1. **Add Claude integration** - Follow steps above to add the `claude_chat` job type
2. **Test locally** - Verify Claude jobs work with docker-compose
3. **Deploy to AWS** - Push updated worker image
4. **Add more job types** - Code generation, research, tool use
5. **Add monitoring** - CloudWatch alarms for failures
6. **Add API Gateway** (optional) - For external access with auth

### Advanced Features to Consider

| Feature | Description |
|---------|-------------|
| **Job Priority** | Add priority field, use SQS FIFO |
| **Retries** | Auto-retry failed jobs with backoff |
| **Timeouts** | Kill long-running workers |
| **Callbacks** | Webhook on job completion |
| **Streaming** | Stream Claude responses via WebSocket |
| **Job Chains** | One job triggers another |
| **Cost Tracking** | Track Claude API costs per job |

### Example: Adding Job Priority

```python
# In orchestrator, when creating job:
priority = request.payload.get("priority", "normal")
sqs.send_message(
    QueueUrl=SQS_QUEUE_URL,
    MessageBody=json.dumps({"job_id": job_id}),
    MessageGroupId=priority,  # Requires FIFO queue
)
```

---

## FAQ

**Q: Does each job/agent use its own Claude API call?**
A: Yes. Each worker makes its own API calls. They're isolated containers.

**Q: Can I use different Claude models?**
A: Yes. Pass `model` in the payload (e.g., `claude-sonnet-4-20250514`, `claude-3-5-haiku-20241022`).

**Q: How do I handle long-running agents?**
A: Increase the ECS task timeout. For very long tasks (>15 min), consider breaking into sub-jobs.

**Q: Can agents communicate with each other?**
A: Not directly. Use DynamoDB or S3 as shared state, or create job chains.

**Q: How do I debug failed jobs?**
A: Check CloudWatch Logs at `/ecs/agent-runner/worker`. Each job has its own log stream.
