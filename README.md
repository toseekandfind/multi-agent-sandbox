# Multi-Agent Sandbox

A multi-agent orchestration system supporting both **VPS (primary)** and **AWS (secondary)** deployments. Includes Agent Farm coordination patterns and ELF memory layer for cross-session learning.

> **New here?** See the [Setup Guide](SETUP.md) for first-time setup, or the [Full Guide](docs/GUIDE.md) for Claude integration.

## Quick Summary

| What | Description |
|------|-------------|
| **Modes** | VPS (tmux-based), AWS (ECS Fargate), Local (Redis) |
| **Multi-Agent** | Integrated Agent Farm coordination patterns |
| **Memory** | ELF-based cross-session learning |
| **Cost (VPS)** | ~$20-50/month fixed |
| **Cost (AWS)** | ~$15/month + pay-per-job |

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│  VPS (Primary) or AWS (Secondary)                                   │
│                                                                      │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │  Orchestrator (FastAPI)                                        │ │
│  │  POST /jobs → spawn agents → monitor → return results          │ │
│  └────────────────────────────────────────────────────────────────┘ │
│                              │                                       │
│                              ▼                                       │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │  Agent Farm (multi-agent coordination)                         │ │
│  │  ┌─────────┐ ┌─────────┐ ┌─────────┐                          │ │
│  │  │ Agent 1 │ │ Agent 2 │ │ Agent 3 │  ← Claude Code instances │ │
│  │  └─────────┘ └─────────┘ └─────────┘                          │ │
│  │  Coordination: file locks, work registry, health monitoring   │ │
│  └────────────────────────────────────────────────────────────────┘ │
│                              │                                       │
│                              ▼                                       │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │  ELF Memory Layer                                              │ │
│  │  Heuristics • Pheromone Trails • Golden Rules • Learning      │ │
│  └────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
```

### Mode Comparison

| Mode | Orchestration | Storage | Best For |
|------|---------------|---------|----------|
| **VPS** | tmux panes | SQLite | Development, consulting |
| **AWS** | ECS Fargate | DynamoDB/S3 | Production, scale |
| **Local** | Docker containers | Redis | Quick testing |

---

## Project Structure

```
.
├── control_api/          # FastAPI orchestrator
│   └── main.py           # VPS, AWS, and local modes
├── worker/               # Job worker
│   └── main.py           # Job processing with Claude
├── agent_farm/           # Multi-agent coordination (from Agent Farm)
│   ├── claude_code_agent_farm.py  # Main orchestrator
│   ├── prompts/          # Specialized prompts
│   └── configs/          # Technology stack configs
├── elf/                  # Persistent memory layer (from ELF)
│   ├── query/            # Core memory and query system
│   ├── conductor/        # Workflow orchestration
│   └── watcher/          # Background monitoring
├── infra/
│   ├── terraform/        # AWS infrastructure
│   └── vps/              # VPS setup scripts
├── compose/              # Docker Compose for local dev
└── docs/                 # Documentation
```

---

## VPS Setup (Recommended)

### 1. Provision VPS

Any Ubuntu 22.04+ VPS works. Recommended specs:
- 4+ CPU cores
- 8+ GB RAM
- 50+ GB SSD

### 2. Run Setup Script

```bash
# SSH to your VPS
ssh root@your-vps-ip

# Run the setup script
curl -fsSL https://raw.githubusercontent.com/YOUR_USERNAME/multi-agent-sandbox/main/infra/vps/setup.sh | bash
```

This installs:
- Python 3.11 + dependencies
- Node.js + Claude Code
- tmux with custom config
- SQLite for job storage
- Systemd services

### 3. Configure Environment

```bash
# Edit environment file
sudo nano /opt/multi-agent-sandbox/.env

# Add your keys:
ANTHROPIC_API_KEY=sk-ant-...
GITHUB_TOKEN=ghp_...
```

### 4. Start Services

```bash
# Start the orchestrator
sudo systemctl start agent-orchestrator
sudo systemctl enable agent-orchestrator

# View agent tmux session
sudo -u agent tmux attach -t agents
```

### 5. Submit Jobs

```bash
# Health check
curl http://localhost:8000/health

# Submit a job
curl -X POST http://localhost:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{"job_type": "claude_chat", "payload": {"prompt": "Hello!"}}'

# List agents (VPS only)
curl http://localhost:8000/agents
```

---

## Local Development

```bash
# Copy environment file
cp .env.example .env
# Add your ANTHROPIC_API_KEY

# Start all services (Redis mode)
docker-compose -f compose/docker-compose.yml up --build

# Test
curl http://localhost:8000/health
```

---

## AWS Deployment

See [SETUP.md](SETUP.md) for full AWS setup. Quick overview:

```bash
cd infra/terraform
terraform init
terraform apply

# Build and push images
docker build -t control-api control_api/
docker build -t worker worker/
# Push to ECR...
```

---

## Job Types

| Type | Description |
|------|-------------|
| `echo` | Test job - returns payload |
| `claude_chat` | Single Claude API call |
| `analytics` | Analytics/dbt workflow |
| `agent_farm` | Multi-agent coordination via Claude Code instances |

### Example Jobs

```bash
# Echo test
curl -X POST http://localhost:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{"job_type": "echo", "payload": {"message": "Hello"}}'

# Claude chat
curl -X POST http://localhost:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "job_type": "claude_chat",
    "payload": {
      "prompt": "Explain Docker in one paragraph",
      "max_tokens": 200
    }
  }'

# Agent Farm - multi-agent coordination
curl -X POST http://localhost:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "job_type": "agent_farm",
    "payload": {
      "path": "/path/to/your/project",
      "agent_count": 3,
      "skip_commit": true
    }
  }'
```

---

## Integrated Components

### Agent Farm

Absorbed from [claude_code_agent_farm](https://github.com/Dicklesworthstone/claude_code_agent_farm):

- **Multi-agent orchestration** via tmux panes
- **Work coordination** with file locks and registries
- **Health monitoring** and auto-restart
- **37 specialized prompts** for different workflows
- **34 technology stack configs**

### ELF (Emergent Learning Framework)

Absorbed from [Emergent-Learning-Framework_ELF](https://github.com/Spacehunterz/Emergent-Learning-Framework_ELF):

- **Persistent memory** across sessions
- **Heuristics** with confidence scoring
- **Pheromone trails** for file activity tracking
- **Golden rules** for high-confidence patterns
- **Dashboard** for visualization
- **Workflow orchestration** with the conductor

---

## API Reference

### Endpoints

| Method | Endpoint | Mode | Description |
|--------|----------|------|-------------|
| GET | `/health` | All | Health check |
| POST | `/jobs` | All | Submit a new job |
| GET | `/jobs/{job_id}` | All | Get job status |
| GET | `/agents` | VPS | List all agents |
| GET | `/agents/{agent_id}` | VPS | Get agent details |

### Job Status Flow

```
QUEUED → RUNNING → SUCCEEDED | FAILED
```

---

## Cost Control

### VPS Mode

Fixed monthly cost based on VPS tier. No per-job infrastructure costs.

### AWS Mode

```bash
# Scale to zero (stop all costs except storage)
aws ecs update-service --cluster agent-runner --service orchestrator --desired-count 0

# Scale back up
aws ecs update-service --cluster agent-runner --service orchestrator --desired-count 1

# Destroy everything
cd infra/terraform && terraform destroy
```

---

## Contributing

See [AGENTS.md](AGENTS.md) for AI agent instructions when working on this codebase.

---

## Credits

This project incorporates code from:
- [claude_code_agent_farm](https://github.com/Dicklesworthstone/claude_code_agent_farm) by Jeff Emanuel
- [Emergent-Learning-Framework_ELF](https://github.com/Spacehunterz/Emergent-Learning-Framework_ELF) by Spacehunterz
