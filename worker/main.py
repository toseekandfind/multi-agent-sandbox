import os
import json
import time
import signal
import sys
import sqlite3
import subprocess
from datetime import datetime
from functools import lru_cache
from pathlib import Path

# Mode: "local" (Redis), "aws" (SQS + DynamoDB + S3), or "vps" (SQLite + local files)
MODE = os.getenv("MODE", "local")

# AWS Configuration
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
SQS_QUEUE_URL = os.getenv("SQS_QUEUE_URL", "")
DYNAMODB_TABLE = os.getenv("DYNAMODB_TABLE", "agent-jobs")
S3_BUCKET = os.getenv("S3_BUCKET", "")
JOB_ID_OVERRIDE = os.getenv("JOB_ID", "")  # For ECS/VPS task with specific job

# VPS Configuration
WORKSPACE_DIR = os.getenv("WORKSPACE_DIR", "/workspace")
VPS_DB_PATH = os.getenv("VPS_DB_PATH", "/var/lib/agent-sandbox/jobs.db")
RESULTS_DIR = os.getenv("RESULTS_DIR", "/var/lib/agent-sandbox/results")

# Initialize clients based on mode
if MODE == "aws":
    import boto3
    sqs = boto3.client("sqs", region_name=AWS_REGION)
    dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
    s3 = boto3.client("s3", region_name=AWS_REGION)
    table = dynamodb.Table(DYNAMODB_TABLE)
elif MODE == "vps":
    def get_db_connection():
        conn = sqlite3.connect(VPS_DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn

    # Ensure results directory exists
    Path(RESULTS_DIR).mkdir(parents=True, exist_ok=True)
else:
    import redis
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    QUEUE_NAME = "job_queue"
    JOB_PREFIX = "job:"

WORKER_ID = os.getenv("HOSTNAME", f"worker-{os.getpid()}")

# ELF Memory configuration
ELF_ENABLED = os.getenv("ELF_ENABLED", "true").lower() == "true"
ELF_DB_PATH = os.getenv("ELF_DB_PATH", None)  # Uses default if not set

# Anthropic client (lazy loaded)
_anthropic_client = None

# ELF Memory (lazy loaded)
_elf_memory = None


def get_elf_memory():
    """Get ELF memory interface (lazy loaded)."""
    global _elf_memory
    if not ELF_ENABLED:
        return None
    if _elf_memory is None:
        try:
            sys.path.insert(0, str(Path(__file__).parent.parent / "elf"))
            from memory import ELFMemory
            _elf_memory = ELFMemory(db_path=ELF_DB_PATH)
            print(f"[{WORKER_ID}] ELF memory initialized")
        except Exception as e:
            print(f"[{WORKER_ID}] Warning: Could not initialize ELF memory: {e}")
            return None
    return _elf_memory


def get_anthropic_client():
    """Get Anthropic client with API key from Secrets Manager or env."""
    global _anthropic_client
    if _anthropic_client is not None:
        return _anthropic_client

    import anthropic

    if MODE == "aws":
        secrets = boto3.client("secretsmanager", region_name=AWS_REGION)
        response = secrets.get_secret_value(SecretId="agent-runner/anthropic-api-key")
        api_key = response["SecretString"].strip()  # Strip whitespace
    else:
        api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()

    _anthropic_client = anthropic.Anthropic(api_key=api_key)
    return _anthropic_client

# Graceful shutdown
shutdown_requested = False


def signal_handler(signum, frame):
    global shutdown_requested
    print(f"[{WORKER_ID}] Shutdown signal received, finishing current job...")
    shutdown_requested = True


signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)


def update_job_status_aws(job_id: str, status: str, result: dict = None):
    now = datetime.utcnow().isoformat()
    update_expr = "SET #status = :status, updated_at = :updated_at"
    expr_values = {":status": status, ":updated_at": now}
    expr_names = {"#status": "status"}

    if result is not None:
        update_expr += ", #result = :result"
        expr_values[":result"] = result
        expr_names["#result"] = "result"

    table.update_item(
        Key={"job_id": job_id},
        UpdateExpression=update_expr,
        ExpressionAttributeValues=expr_values,
        ExpressionAttributeNames=expr_names,
    )
    print(f"[{WORKER_ID}] Job {job_id} -> {status}")


def update_job_status_redis(job_id: str, status: str, result: dict = None):
    now = datetime.utcnow().isoformat()
    updates = {"status": status, "updated_at": now}
    if result is not None:
        updates["result"] = json.dumps(result)
    redis_client.hset(f"{JOB_PREFIX}{job_id}", mapping=updates)
    print(f"[{WORKER_ID}] Job {job_id} -> {status}")


def update_job_status_vps(job_id: str, status: str, result: dict = None):
    """Update job status in SQLite (VPS mode)."""
    now = datetime.utcnow().isoformat()
    with get_db_connection() as conn:
        if result is not None:
            conn.execute("""
                UPDATE jobs SET status = ?, result = ?, updated_at = ?
                WHERE job_id = ?
            """, (status, json.dumps(result), now, job_id))
        else:
            conn.execute("""
                UPDATE jobs SET status = ?, updated_at = ?
                WHERE job_id = ?
            """, (status, now, job_id))
        conn.commit()
    print(f"[{WORKER_ID}] Job {job_id} -> {status}")


def update_agent_status_vps(agent_id: str, status: str):
    """Update agent status in SQLite (VPS mode)."""
    now = datetime.utcnow().isoformat()
    with get_db_connection() as conn:
        conn.execute("""
            UPDATE agents SET status = ?, last_heartbeat = ?
            WHERE agent_id = ?
        """, (status, now, agent_id))
        conn.commit()


def save_result_to_file(job_id: str, result: dict):
    """Save job result to local file (VPS mode)."""
    result_dir = Path(RESULTS_DIR) / job_id
    result_dir.mkdir(parents=True, exist_ok=True)
    result_path = result_dir / "result.json"
    with open(result_path, "w") as f:
        json.dump(result, f, indent=2)
    print(f"[{WORKER_ID}] Saved result to {result_path}")


def upload_result_to_s3(job_id: str, result: dict):
    result_json = json.dumps(result, indent=2)
    s3.put_object(
        Bucket=S3_BUCKET,
        Key=f"jobs/{job_id}/result.json",
        Body=result_json,
        ContentType="application/json",
    )
    print(f"[{WORKER_ID}] Uploaded result to s3://{S3_BUCKET}/jobs/{job_id}/result.json")


def get_elf_context(payload: dict) -> str:
    """
    Get ELF context for a job based on payload.

    Returns formatted context string to inject into prompts.
    """
    memory = get_elf_memory()
    if not memory:
        return ""

    try:
        project_path = payload.get("path") or payload.get("repo")
        domain = payload.get("domain")
        files = payload.get("files_to_read", [])

        context = memory.get_context(
            project_path=project_path,
            domain=domain,
            files=files
        )

        if context.get("prompt_context"):
            print(f"[{WORKER_ID}] ELF context loaded: {len(context['golden_rules'])} golden rules, {len(context['heuristics'])} heuristics")
            return context["prompt_context"]
    except Exception as e:
        print(f"[{WORKER_ID}] Warning: Could not load ELF context: {e}")

    return ""


def record_elf_outcome(
    job_id: str,
    job_type: str,
    payload: dict,
    result: dict,
    success: bool,
    duration_seconds: float = None,
    error_message: str = None
):
    """Record job outcome to ELF memory."""
    memory = get_elf_memory()
    if not memory:
        return

    try:
        project_path = payload.get("path") or payload.get("repo")

        # Extract files touched from result if available
        files_touched = result.get("files_written", []) or result.get("files_modified", [])

        # Extract learnings if any
        learnings = result.get("learnings", [])

        memory.record_outcome(
            job_id=job_id,
            job_type=job_type,
            outcome="success" if success else "failure",
            project_path=project_path,
            duration_seconds=duration_seconds,
            agent_count=payload.get("agent_count"),
            files_touched=files_touched if files_touched else None,
            learnings=learnings if learnings else None,
            error_message=error_message
        )
        print(f"[{WORKER_ID}] ELF outcome recorded: {job_type} -> {'success' if success else 'failure'}")
    except Exception as e:
        print(f"[{WORKER_ID}] Warning: Could not record ELF outcome: {e}")


def handle_echo_job(job_id: str, payload: dict) -> dict:
    """Echo handler - returns the payload with metadata."""
    message = payload.get("message", "")

    # Simulate some work
    time.sleep(0.5)

    result = {
        "echoed_message": message,
        "processed_by": WORKER_ID,
        "processed_at": datetime.utcnow().isoformat(),
    }

    return result


def handle_claude_chat_job(job_id: str, payload: dict) -> dict:
    """
    Claude chat job handler.

    Payload:
        prompt: str - The user prompt
        system: str (optional) - System prompt
        max_tokens: int (optional) - Max response tokens (default 1024)
        model: str (optional) - Model to use (default claude-sonnet-4-20250514)
        domain: str (optional) - Domain for ELF context lookup
    """
    client = get_anthropic_client()

    prompt = payload.get("prompt", "")
    system = payload.get("system", "You are a helpful assistant.")
    max_tokens = int(payload.get("max_tokens", 1024))  # Convert Decimal to int
    model = payload.get("model", "claude-sonnet-4-20250514")

    # Inject ELF context if available
    elf_context = get_elf_context(payload)
    if elf_context:
        system = f"{system}\n\n## Learned Context\n{elf_context}"

    print(f"[{WORKER_ID}] Calling Claude API with model {model}...")

    message = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )

    result = {
        "response": message.content[0].text,
        "model": model,
        "usage": {
            "input_tokens": message.usage.input_tokens,
            "output_tokens": message.usage.output_tokens,
        },
        "processed_by": WORKER_ID,
        "processed_at": datetime.utcnow().isoformat(),
    }

    print(f"[{WORKER_ID}] Claude API call complete. Tokens: {message.usage.input_tokens} in, {message.usage.output_tokens} out")

    return result


def handle_analytics_job(job_id: str, payload: dict) -> dict:
    """
    Analytics job handler - reads files, calls Claude, writes output.

    Payload:
        task: str - Description of the analytics task
        repo: str - Subdirectory under /workspace (e.g., "client_a_dbt")
        files_to_read: list[str] (optional) - Files to read as context
        output_files: dict (optional) - {filename: description} for files to create
    """
    import os
    from pathlib import Path

    client = get_anthropic_client()

    task = payload.get("task", "")
    repo = payload.get("repo", "")
    files_to_read = payload.get("files_to_read", [])

    workspace = Path("/workspace") / repo
    output_dir = workspace / "_agent_output" / job_id

    print(f"[{WORKER_ID}] Analytics job: {task[:100]}...")
    print(f"[{WORKER_ID}] Working in: {workspace}")

    # Read requested files
    file_contents = {}
    for file_path in files_to_read:
        full_path = workspace / file_path
        if full_path.exists():
            try:
                file_contents[file_path] = full_path.read_text()
                print(f"[{WORKER_ID}] Read: {file_path}")
            except Exception as e:
                file_contents[file_path] = f"[Error reading file: {e}]"
        else:
            file_contents[file_path] = f"[File not found: {file_path}]"

    # Build context for Claude
    context_parts = []
    if file_contents:
        context_parts.append("## Files provided as context:\n")
        for path, content in file_contents.items():
            context_parts.append(f"### {path}\n```\n{content}\n```\n")

    # List available files for reference
    if workspace.exists():
        available_files = []
        for p in workspace.rglob("*"):
            if p.is_file() and not any(part.startswith(".") for part in p.parts):
                rel_path = p.relative_to(workspace)
                if len(available_files) < 50:  # Limit to 50 files
                    available_files.append(str(rel_path))
        if available_files:
            context_parts.append(f"\n## Available files in repo:\n{chr(10).join(available_files[:50])}")
            if len(available_files) == 50:
                context_parts.append("\n(truncated, more files exist)")

    context = "\n".join(context_parts)

    system_prompt = """You are an expert Analytics Engineer. You help create dbt models, SQL transformations, and data tests.

When creating files, format your response with clear file markers like this:
--- FILE: path/to/file.sql ---
<file contents>
--- END FILE ---

Always explain your reasoning before showing the files.
After showing files, add an analysis section explaining what you built and how to verify it works."""

    # Inject ELF context if available
    elf_context = get_elf_context(payload)
    if elf_context:
        system_prompt = f"{system_prompt}\n\n## Learned Context\n{elf_context}"

    user_message = f"""Task: {task}

{context}

Please complete this task. If creating new files, use the FILE markers shown in the system prompt."""

    print(f"[{WORKER_ID}] Calling Claude for analytics task...")

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )

    response_text = message.content[0].text
    print(f"[{WORKER_ID}] Claude responded with {message.usage.output_tokens} tokens")

    # Parse and write output files
    output_dir.mkdir(parents=True, exist_ok=True)
    written_files = []

    # Extract files from response using markers
    import re
    file_pattern = r"--- FILE: (.+?) ---\n(.*?)--- END FILE ---"
    matches = re.findall(file_pattern, response_text, re.DOTALL)

    for file_path, content in matches:
        file_path = file_path.strip()
        content = content.strip()

        # Write to output directory
        out_path = output_dir / file_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(content)
        written_files.append(str(file_path))
        print(f"[{WORKER_ID}] Wrote: {out_path}")

    # Write full response as analysis.md
    analysis_path = output_dir / "analysis.md"
    analysis_path.write_text(f"# Analytics Task: {task}\n\n{response_text}")
    written_files.append("analysis.md")

    result = {
        "task": task,
        "repo": repo,
        "files_read": list(file_contents.keys()),
        "files_written": written_files,
        "output_dir": str(output_dir),
        "response_preview": response_text[:500] + "..." if len(response_text) > 500 else response_text,
        "usage": {
            "input_tokens": message.usage.input_tokens,
            "output_tokens": message.usage.output_tokens,
        },
        "processed_by": WORKER_ID,
        "processed_at": datetime.utcnow().isoformat(),
    }

    return result


def get_job_data_aws(job_id: str) -> dict:
    response = table.get_item(Key={"job_id": job_id})
    return response.get("Item", {})


def get_job_data_redis(job_id: str) -> dict:
    return redis_client.hgetall(f"{JOB_PREFIX}{job_id}")


def get_job_data_vps(job_id: str) -> dict:
    """Get job data from SQLite (VPS mode)."""
    with get_db_connection() as conn:
        cursor = conn.execute(
            "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
        )
        row = cursor.fetchone()
    if row:
        return dict(row)
    return {}


def setup_workspace(job_id: str, repo_url: str, branch: str = "main") -> str:
    """Clone a repo to the workspace directory for this job."""
    workspace = Path(WORKSPACE_DIR) / job_id
    workspace.mkdir(parents=True, exist_ok=True)

    # Clone the repo
    clone_cmd = ["git", "clone", "-b", branch, repo_url, str(workspace)]
    result = subprocess.run(clone_cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"[{WORKER_ID}] Git clone failed: {result.stderr}")
        raise RuntimeError(f"Failed to clone repo: {result.stderr}")

    print(f"[{WORKER_ID}] Cloned {repo_url} to {workspace}")
    return str(workspace)


def handle_agent_farm_job(job_id: str, payload: dict) -> dict:
    """
    Agent Farm job handler - orchestrates multiple Claude Code agents.

    Payload:
        task: str - Natural language description of the task (optional)
        repo_url: str - GitHub repo to clone (optional if path provided)
        branch: str - Branch to work on (default: main)
        path: str - Local path to project (overrides repo cloning)
        agent_count: int - Number of parallel agents (default: 3)
        session: str - tmux session name (default: job-{job_id[:8]})
        prompt_file: str - Path to custom prompt file (optional)
        config: str - Path to config file (optional)
        auto_restart: bool - Auto-restart agents on errors (default: False)
        skip_commit: bool - Skip git commit/push (default: True for safety)
        stagger: float - Seconds between starting agents (default: 10.0)
        context_threshold: int - Restart when context <= this % (default: 20)
    """
    # Import Agent Farm here to avoid circular imports
    sys.path.insert(0, str(Path(__file__).parent.parent / "agent_farm"))
    from claude_code_agent_farm import ClaudeAgentFarm

    # Determine project path
    if "path" in payload:
        project_path = payload["path"]
    elif "repo_url" in payload:
        project_path = setup_workspace(
            job_id,
            payload["repo_url"],
            payload.get("branch", "main")
        )
    else:
        raise ValueError("Either 'path' or 'repo_url' must be provided")

    # Extract configuration from payload
    agent_count = int(payload.get("agent_count", 3))
    session_name = payload.get("session", f"job-{job_id[:8]}")

    print(f"[{WORKER_ID}] Starting Agent Farm with {agent_count} agents")
    print(f"[{WORKER_ID}] Project path: {project_path}")
    print(f"[{WORKER_ID}] Session: {session_name}")

    # Create the orchestrator
    farm = ClaudeAgentFarm(
        path=project_path,
        agents=agent_count,
        session=session_name,
        stagger=float(payload.get("stagger", 10.0)),
        wait_after_cc=float(payload.get("wait_after_cc", 15.0)),
        check_interval=int(payload.get("check_interval", 10)),
        skip_regenerate=payload.get("skip_regenerate", False),
        skip_commit=payload.get("skip_commit", True),  # Safe default
        auto_restart=payload.get("auto_restart", False),
        no_monitor=payload.get("no_monitor", False),
        attach=False,  # Never attach in job mode
        prompt_file=payload.get("prompt_file"),
        config=payload.get("config"),
        context_threshold=int(payload.get("context_threshold", 20)),
        idle_timeout=int(payload.get("idle_timeout", 60)),
        max_errors=int(payload.get("max_errors", 3)),
        tmux_kill_on_exit=payload.get("tmux_kill_on_exit", True),
        tmux_mouse=True,
        fast_start=payload.get("fast_start", False),
        full_backup=payload.get("full_backup", False),
    )

    start_time = datetime.utcnow()

    try:
        # Run the orchestration
        farm.run()

        end_time = datetime.utcnow()
        duration = (end_time - start_time).total_seconds()

        result = {
            "status": "completed",
            "project_path": project_path,
            "agent_count": agent_count,
            "session": session_name,
            "duration_seconds": duration,
            "problems_fixed": farm.total_problems_fixed,
            "commits_made": farm.total_commits_made,
            "agent_restarts": farm.agent_restart_count,
            "processed_by": WORKER_ID,
            "processed_at": datetime.utcnow().isoformat(),
        }

    except KeyboardInterrupt:
        result = {
            "status": "interrupted",
            "project_path": project_path,
            "agent_count": agent_count,
            "session": session_name,
            "processed_by": WORKER_ID,
            "processed_at": datetime.utcnow().isoformat(),
        }

    except Exception as e:
        import traceback
        result = {
            "status": "error",
            "error": str(e),
            "traceback": traceback.format_exc(),
            "project_path": project_path,
            "agent_count": agent_count,
            "session": session_name,
            "processed_by": WORKER_ID,
            "processed_at": datetime.utcnow().isoformat(),
        }

    finally:
        # Always shut down the farm cleanly
        try:
            farm.shutdown()
        except Exception:
            pass

    print(f"[{WORKER_ID}] Agent Farm job complete: {result.get('status')}")
    return result


def process_job(job_id: str):
    """Process a single job."""
    if MODE == "aws":
        job_data = get_job_data_aws(job_id)
        if not job_data:
            print(f"[{WORKER_ID}] Job {job_id} not found, skipping")
            return

        job_type = job_data.get("job_type", "")
        payload = job_data.get("payload", {})

        # Mark as running
        update_job_status_aws(job_id, "RUNNING")

        try:
            if job_type == "echo":
                result = handle_echo_job(job_id, payload)
            elif job_type == "claude_chat":
                result = handle_claude_chat_job(job_id, payload)
            elif job_type == "analytics":
                result = handle_analytics_job(job_id, payload)
            elif job_type == "agent_farm":
                result = handle_agent_farm_job(job_id, payload)
            else:
                raise ValueError(f"Unknown job type: {job_type}")

            # Upload result to S3
            upload_result_to_s3(job_id, result)

            # Update status
            update_job_status_aws(job_id, "SUCCEEDED", result)

            # Record ELF outcome
            record_elf_outcome(job_id, job_type, payload, result, success=True)

        except Exception as e:
            import traceback
            error_result = {
                "error": str(e),
                "error_type": type(e).__name__,
                "traceback": traceback.format_exc(),
                "worker_id": WORKER_ID,
                "failed_at": datetime.utcnow().isoformat(),
            }
            update_job_status_aws(job_id, "FAILED", error_result)
            print(f"[{WORKER_ID}] Job {job_id} failed: {e}")
            print(f"[{WORKER_ID}] Traceback: {traceback.format_exc()}")

            # Record ELF outcome
            record_elf_outcome(job_id, job_type, payload, error_result, success=False, error_message=str(e))

    elif MODE == "vps":
        # VPS mode: SQLite + local file storage
        job_data = get_job_data_vps(job_id)
        if not job_data:
            print(f"[{WORKER_ID}] Job {job_id} not found, skipping")
            return

        job_type = job_data.get("job_type", "")
        payload = json.loads(job_data.get("payload", "{}"))

        # Update agent status
        update_agent_status_vps(job_id, "working")

        # Mark job as running
        update_job_status_vps(job_id, "RUNNING")

        try:
            if job_type == "echo":
                result = handle_echo_job(job_id, payload)
            elif job_type == "claude_chat":
                result = handle_claude_chat_job(job_id, payload)
            elif job_type == "analytics":
                result = handle_analytics_job(job_id, payload)
            elif job_type == "agent_farm":
                result = handle_agent_farm_job(job_id, payload)
            else:
                raise ValueError(f"Unknown job type: {job_type}")

            # Save result to local file
            save_result_to_file(job_id, result)

            # Update status
            update_job_status_vps(job_id, "SUCCEEDED", result)
            update_agent_status_vps(job_id, "complete")

            # Record ELF outcome
            record_elf_outcome(job_id, job_type, payload, result, success=True)

        except Exception as e:
            import traceback
            error_result = {
                "error": str(e),
                "error_type": type(e).__name__,
                "traceback": traceback.format_exc(),
                "worker_id": WORKER_ID,
                "failed_at": datetime.utcnow().isoformat(),
            }
            update_job_status_vps(job_id, "FAILED", error_result)
            update_agent_status_vps(job_id, "error")
            print(f"[{WORKER_ID}] Job {job_id} failed: {e}")
            print(f"[{WORKER_ID}] Traceback: {traceback.format_exc()}")

            # Record ELF outcome
            record_elf_outcome(job_id, job_type, payload, error_result, success=False, error_message=str(e))

    else:
        # Local mode: Redis
        job_data = get_job_data_redis(job_id)
        if not job_data:
            print(f"[{WORKER_ID}] Job {job_id} not found, skipping")
            return

        job_type = job_data.get("job_type", "")
        payload = json.loads(job_data.get("payload", "{}"))

        # Mark as running
        update_job_status_redis(job_id, "RUNNING")

        try:
            if job_type == "echo":
                result = handle_echo_job(job_id, payload)
            elif job_type == "claude_chat":
                result = handle_claude_chat_job(job_id, payload)
            elif job_type == "analytics":
                result = handle_analytics_job(job_id, payload)
            elif job_type == "agent_farm":
                result = handle_agent_farm_job(job_id, payload)
            else:
                raise ValueError(f"Unknown job type: {job_type}")

            update_job_status_redis(job_id, "SUCCEEDED", result)

            # Record ELF outcome
            record_elf_outcome(job_id, job_type, payload, result, success=True)

        except Exception as e:
            error_result = {
                "error": str(e),
                "worker_id": WORKER_ID,
                "failed_at": datetime.utcnow().isoformat(),
            }
            update_job_status_redis(job_id, "FAILED", error_result)
            print(f"[{WORKER_ID}] Job {job_id} failed: {e}")

            # Record ELF outcome
            record_elf_outcome(job_id, job_type, payload, error_result, success=False, error_message=str(e))


def poll_sqs():
    """Poll SQS for messages."""
    response = sqs.receive_message(
        QueueUrl=SQS_QUEUE_URL,
        MaxNumberOfMessages=1,
        WaitTimeSeconds=20,
        VisibilityTimeout=300,
    )

    messages = response.get("Messages", [])
    if not messages:
        return None

    message = messages[0]
    receipt_handle = message["ReceiptHandle"]
    body = json.loads(message["Body"])
    job_id = body.get("job_id")

    # Delete message from queue after receiving
    sqs.delete_message(QueueUrl=SQS_QUEUE_URL, ReceiptHandle=receipt_handle)

    return job_id


def main():
    print(f"[{WORKER_ID}] Worker starting in {MODE} mode")

    # If JOB_ID is provided (ECS/VPS task), process only that job and exit
    if JOB_ID_OVERRIDE:
        print(f"[{WORKER_ID}] Processing specific job: {JOB_ID_OVERRIDE}")
        process_job(JOB_ID_OVERRIDE)
        print(f"[{WORKER_ID}] Job complete, exiting")
        return

    # Otherwise, poll for jobs continuously based on mode
    if MODE == "aws":
        while not shutdown_requested:
            try:
                job_id = poll_sqs()
                if job_id:
                    print(f"[{WORKER_ID}] Processing job: {job_id}")
                    process_job(job_id)
            except Exception as e:
                print(f"[{WORKER_ID}] Error polling SQS: {e}")
                time.sleep(5)
    elif MODE == "vps":
        # VPS mode without JOB_ID: Poll SQLite for queued jobs
        print(f"[{WORKER_ID}] Polling for jobs in VPS mode...")
        while not shutdown_requested:
            try:
                with get_db_connection() as conn:
                    cursor = conn.execute("""
                        SELECT job_id FROM jobs
                        WHERE status = 'QUEUED'
                        ORDER BY created_at ASC
                        LIMIT 1
                    """)
                    row = cursor.fetchone()

                if row:
                    job_id = row["job_id"]
                    print(f"[{WORKER_ID}] Processing job: {job_id}")
                    process_job(job_id)
                else:
                    time.sleep(2)  # Wait before polling again

            except Exception as e:
                print(f"[{WORKER_ID}] Error polling jobs: {e}")
                time.sleep(5)
    else:
        # Local mode: Redis
        print(f"[{WORKER_ID}] Listening on queue: {QUEUE_NAME}")
        while not shutdown_requested:
            try:
                result = redis_client.brpop(QUEUE_NAME, timeout=5)

                if result is None:
                    continue

                _, message = result
                job_info = json.loads(message)
                job_id = job_info.get("job_id")

                if job_id:
                    print(f"[{WORKER_ID}] Processing job: {job_id}")
                    process_job(job_id)

            except Exception as e:
                print(f"[{WORKER_ID}] Error: {e}")
                time.sleep(1)

    print(f"[{WORKER_ID}] Worker shutting down gracefully")


if __name__ == "__main__":
    main()
