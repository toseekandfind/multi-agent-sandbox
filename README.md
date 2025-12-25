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

## What You Need

| Item | For `agent_farm` | For `claude_chat`/`analytics` |
|------|------------------|-------------------------------|
| **VPS** | ✅ Required | ✅ Required |
| **Claude Code CLI** | ✅ Required (run `claude login`) | ❌ Not used |
| **Anthropic API Key** | ❌ Not needed | ✅ Required |
| **GitHub Token** | Only for private repos | Only for private repos |

**Most users only need:** VPS + Claude Code CLI (Max subscription)

---

## Quick Start (End-to-End)

### Step 1: Provision & Setup VPS

```bash
# SSH to your VPS
ssh root@your-vps-ip

# Clone and run setup (use your repo URL)
git clone https://github.com/OWNER/multi-agent-sandbox.git /opt/multi-agent-sandbox
cd /opt/multi-agent-sandbox && bash infra/vps/setup.sh

# Authenticate Claude Code CLI
claude login

# (Optional) Add API key only if using claude_chat/analytics jobs
echo "ANTHROPIC_API_KEY=sk-ant-..." | sudo tee -a /opt/multi-agent-sandbox/.env

# Start services
sudo systemctl start agent-orchestrator
sudo systemctl enable agent-orchestrator
```

### Step 2: Submit an Agent Farm Job

```bash
# Option A: Clone from GitHub and run agents
curl -X POST http://your-vps:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "job_type": "agent_farm",
    "payload": {
      "repo_url": "https://github.com/you/your-repo",
      "branch": "main",
      "agent_count": 3
    }
  }'

# Option B: Use a repo already on the VPS
curl -X POST http://your-vps:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "job_type": "agent_farm",
    "payload": {
      "path": "/workspace/my-project",
      "agent_count": 3
    }
  }'
```

### Step 3: Monitor Agents

```bash
# Watch agents work in real-time
sudo -u agent tmux attach -t agents

# Check job status via API
curl http://your-vps:8000/jobs/{job_id}
```

### Step 4: Learning Over Time (ELF)

The ELF memory layer tracks patterns across sessions:

```
Job 1: Agent fixes bug in auth.py
       → ELF records: "auth.py often has token expiry issues"

Job 2: Agent works on auth.py again
       → ELF injects context: "Check token expiry (learned from Job 1)"
       → Agent solves faster

Job N: Pattern becomes a "golden rule"
       → All future agents automatically receive this knowledge
```

View learned patterns at: `http://your-vps:8000/dashboard`

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

| Type | What It Does | Requires | Modes |
|------|--------------|----------|-------|
| `echo` | Test job - returns payload | Nothing | All |
| `claude_chat` | Single prompt → response | **Anthropic API key** | All |
| `analytics` | Read files, call Claude, write output | **Anthropic API key** | All |
| `agent_farm` | Multi-agent orchestration in tmux | **Claude Code CLI** (Max subscription) | VPS only |

### Job Type Details

#### `agent_farm` (Primary)
Spawns multiple Claude Code CLI agents in tmux panes to work on a codebase together. This is the main feature.

- **Uses:** Claude Code CLI with your Max subscription
- **No API key needed** - authenticates via `claude login`
- **Requires:** VPS with tmux installed

```bash
curl -X POST http://localhost:8000/jobs \
  -d '{"job_type": "agent_farm", "payload": {"path": "/workspace/project", "agent_count": 3}}'
```

#### `claude_chat` (Simple)
Single API call to Claude. Prompt in, response out. No file editing, no agentic behavior.

- **Uses:** Anthropic API directly
- **Requires:** `ANTHROPIC_API_KEY` in `.env`

```bash
curl -X POST http://localhost:8000/jobs \
  -d '{"job_type": "claude_chat", "payload": {"prompt": "Explain Docker in one paragraph"}}'
```

#### `analytics` (dbt/SQL)
Reads files from a repo, builds context, calls Claude for analytics work, writes output files.

- **Uses:** Anthropic API directly
- **Requires:** `ANTHROPIC_API_KEY` in `.env`

```bash
curl -X POST http://localhost:8000/jobs \
  -d '{"job_type": "analytics", "payload": {"task": "Create a revenue model", "repo": "client_dbt"}}'
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

#### How ELF Works

ELF is **pure SQLite storage** - it doesn't call any API.

| Stage | What Happens |
|-------|--------------|
| **After jobs** | ELF records: outcome (success/failure), files touched, duration, errors |
| **Before jobs** | ELF injects learned patterns into agent prompts |
| **Over time** | Patterns validated repeatedly become "golden rules" (90%+ confidence) |

#### ELF Integration by Job Type

| Job Type | Records Outcomes | Gets ELF Context |
|----------|------------------|------------------|
| `agent_farm` | ✅ Yes | ✅ Yes (via prompt injection) |
| `claude_chat` | ✅ Yes | ✅ Yes |
| `analytics` | ✅ Yes | ✅ Yes |

For `agent_farm`, ELF context is automatically injected into the agent prompt file. This means agents start each session with knowledge of:
- **Golden Rules**: High-confidence patterns (90%+) that should always be followed
- **Learned Patterns**: Heuristics from previous sessions with confidence scores
- **Recent Issues**: Failed jobs to avoid repeating mistakes

To disable ELF context injection for a specific job, set `"inject_elf_context": false` in the payload.

#### ELF MCP Server (Dynamic Access)

For dynamic access during agent work, use the ELF MCP server:

```bash
# Add to Claude Code (one-time setup)
claude mcp add elf -- env CLIENT_ID=default python3 /opt/multi-agent-sandbox/elf/mcp/server.py

# For multi-tenant setup, use client-specific ID
claude mcp add elf -- env CLIENT_ID=client-a python3 /opt/multi-agent-sandbox/elf/mcp/server.py
```

**Available MCP Tools:**

| Tool | Purpose |
|------|---------|
| `elf_query` | Get context (golden rules, heuristics) |
| `elf_search` | Search knowledge base |
| `elf_record_heuristic` | Add new rule with confidence |
| `elf_record_outcome` | Log job success/failure |
| `elf_validate_heuristic` | Increase confidence of a rule |
| `elf_violate_heuristic` | Decrease confidence of a rule |
| `elf_record_plan` | Record task plan |
| `elf_record_postmortem` | Compare expected vs actual outcome |
| `elf_stats` | Get memory statistics |

This allows agents to:
- Query ELF mid-task: "What patterns do you know about auth.py?"
- Record learnings in real-time as they discover them
- Run plan/postmortem cycles for structured learning

#### Viewing Learned Patterns

```bash
# Dashboard (visual)
open http://your-vps:8000/dashboard

# API endpoints
curl http://your-vps:8000/elf/stats
curl http://your-vps:8000/elf/heuristics
curl http://your-vps:8000/elf/golden-rules
```

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
| GET | `/dashboard` | All | ELF Dashboard (HTML) |
| GET | `/elf/stats` | All | ELF memory statistics |
| GET | `/elf/heuristics` | All | Get learned heuristics |
| GET | `/elf/golden-rules` | All | Get high-confidence rules |

### Job Status Flow

```
QUEUED → RUNNING → SUCCEEDED | FAILED
```

---

## Multi-Tenant Setup

Run multiple clients on a single VPS with complete isolation.

### How It Works

```
VPS
├── /workspace/
│   ├── client-a/           ← Client A's workspaces
│   │   └── job-abc123/
│   └── client-b/           ← Client B's workspaces
│       └── job-def456/
├── ~/.claude/elf/
│   ├── client-a/memory.db  ← Client A's learnings
│   └── client-b/memory.db  ← Client B's learnings
└── /etc/agent-sandbox/clients.json  ← API key → client mapping
```

### Enable Authentication

1. Create a clients config file:

```bash
sudo mkdir -p /etc/agent-sandbox
sudo tee /etc/agent-sandbox/clients.json << 'EOF'
{
    "sk-client-a-secret-key-here": "client-a",
    "sk-client-b-secret-key-here": "client-b"
}
EOF
sudo chmod 600 /etc/agent-sandbox/clients.json
```

2. Enable authentication:

```bash
# Add to .env
AUTH_ENABLED=true
```

3. Restart the orchestrator:

```bash
sudo systemctl restart agent-orchestrator
```

### Using API Keys

```bash
# Submit job as client-a
curl -X POST http://your-vps:8000/jobs \
  -H "X-API-Key: sk-client-a-secret-key-here" \
  -H "Content-Type: application/json" \
  -d '{"job_type": "agent_farm", "payload": {"path": "/workspace/project"}}'

# View client-a's jobs only
curl http://your-vps:8000/jobs \
  -H "X-API-Key: sk-client-a-secret-key-here"

# View client-a's ELF dashboard
curl http://your-vps:8000/dashboard \
  -H "X-API-Key: sk-client-a-secret-key-here"
```

### Isolation Guarantees

| Resource | Isolation |
|----------|-----------|
| **Workspaces** | `/workspace/{client_id}/` |
| **ELF Database** | `~/.claude/elf/{client_id}/memory.db` |
| **Jobs** | Only visible to owning client |
| **Agents** | Only visible to owning client |
| **Golden Rules** | Per-client, not shared |

### Without Authentication

If `AUTH_ENABLED=false` (default), all requests use `client_id: "default"`:

```bash
# No API key needed
curl http://your-vps:8000/jobs
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
