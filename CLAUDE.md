# Claude Code Project Rules

## Project Architecture

This repo is an **org-agnostic agent framework**. Org-specific configurations (dbt, credentials, etc.) should NOT live in this repo.

### Multi-Tenant Design
- Agent framework code lives here (universal)
- Org-specific configs live in `~/.config/{service}/{org-name}/` or on dedicated VPS workers
- Workers execute org-specific jobs and report back

## dbt Cloud MCP Server Rules

### NEVER Trigger Cloud Jobs
Do NOT use the dbt MCP server to trigger any cloud jobs. This includes:
- `trigger_job_run`
- `cancel_job_run`
- `retry_job_run`

### ALLOWED: Query/Explore Only
The dbt MCP server should ONLY be used for:
- Listing and exploring metrics (`list_metrics`, `query_metrics`, `get_dimensions`, etc.)
- Getting model information (`get_all_models`, `get_model_details`, `get_model_parents`, etc.)
- Exploring lineage and dependencies
- Viewing sources, exposures, and documentation

### dbt Execution Strategy
Use the VPS worker to execute dbt commands. See VPS section below.

## VPS Worker (CONFIGURED)

The VPS is set up and ready to use for job execution.

### Connection Details
- **Host**: 151.243.109.200
- **API URL**: http://151.243.109.200:8000
- **SSH Alias**: `agent-vps` (configured in ~/.ssh/config)
- **Config File**: `config/vps.json`

### How to Use the VPS

**Submit a job:**
```bash
curl -X POST http://151.243.109.200:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{"job_type": "echo", "payload": {"message": "test"}}'
```

**Check job status:**
```bash
curl http://151.243.109.200:8000/jobs/{job_id}
```

**List all jobs:**
```bash
curl http://151.243.109.200:8000/jobs
```

### Available Job Types
- `echo` - Simple test job
- `agent_farm` - Spawn N Claude Code agents in parallel
- `dbt_run` - Run dbt commands in a workspace

### Sandbox Security
- VPS agents CANNOT push to GitHub (git remote operations blocked)
- Changes are retrieved via `/workspace/diff` and `/workspace/patch` endpoints
- Orchestrator (local Claude Code) pulls changes and asks user before committing
