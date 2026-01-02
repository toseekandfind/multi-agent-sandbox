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

## Local vs VPS Architecture

**Local Claude Code = Orchestrator/Monitor**
- Monitors VPS worker status and job results
- Reviews changes before committing to GitHub
- Makes decisions about what jobs to submit
- Handles user interaction and approvals

**VPS Worker = Execution Engine**
- Executes all heavy workloads (dbt runs, agent spawning, code analysis)
- ALWAYS use VPS for prompts and agent work
- Cannot push to GitHub directly (sandboxed for safety)
- Reports results back to local orchestrator

### Rule: VPS-First for Execution
Always submit work to VPS rather than running locally unless:
1. It's a quick one-line query
2. The VPS is unavailable
3. User explicitly requests local execution

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

### dbt_run Job Payload
```json
{
  "job_type": "dbt_run",
  "payload": {
    "dbt_project_path": "/opt/workspaces/analytics_dbt",
    "command": "run",           // run, build, test, compile, retry
    "target": "databricks",     // target profile name
    "select": "model_name",     // optional: --select argument
    "exclude": "other_model",   // optional: --exclude argument
    "full_refresh": true,       // optional: adds --full-refresh
    "fail_fast": false          // optional: adds --fail-fast
  }
}
```

### Sandbox Security
- VPS agents CANNOT push to GitHub (git remote operations blocked)
- Changes are retrieved via `/workspace/diff` and `/workspace/patch` endpoints
- Orchestrator (local Claude Code) pulls changes and asks user before committing

### Troubleshooting VPS Issues
1. **Check worker health**: `curl http://151.243.109.200:8000/health`
2. **Check logs**: `ssh agent-vps "tail -50 /var/log/worker.log"`
3. **Restart worker**: `ssh agent-vps "pkill -f 'python.*main.py'; cd /opt/multi-agent-sandbox && MODE=vps nohup python3 worker/main.py >> /var/log/worker.log 2>&1 &"`
4. **Check job status**: `curl http://151.243.109.200:8000/jobs/{job_id}`
