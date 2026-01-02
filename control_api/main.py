import os
import json
import uuid
import subprocess
import sqlite3
from datetime import datetime
from typing import Optional, Dict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Depends, Security
from fastapi.security import APIKeyHeader
from pydantic import BaseModel

app = FastAPI(title="Agent Runner Control API")

# ============================================================================
# Multi-Tenant Authentication
# ============================================================================

# API Key configuration
# Keys can be set via:
# 1. Environment variable: API_KEYS='{"key1": "client-a", "key2": "client-b"}'
# 2. Config file: /etc/agent-sandbox/clients.json
# 3. Default: No auth required (for local development)

API_KEYS_ENV = os.getenv("API_KEYS", "")
API_KEYS_FILE = os.getenv("API_KEYS_FILE", "/etc/agent-sandbox/clients.json")
AUTH_ENABLED = os.getenv("AUTH_ENABLED", "false").lower() == "true"

_api_keys: Dict[str, str] = {}  # api_key -> client_id mapping


def load_api_keys() -> Dict[str, str]:
    """Load API keys from environment or config file."""
    global _api_keys

    if _api_keys:
        return _api_keys

    # Try environment variable first
    if API_KEYS_ENV:
        try:
            _api_keys = json.loads(API_KEYS_ENV)
            print(f"Loaded {len(_api_keys)} API keys from environment")
            return _api_keys
        except json.JSONDecodeError:
            print("Warning: Invalid JSON in API_KEYS environment variable")

    # Try config file
    if Path(API_KEYS_FILE).exists():
        try:
            with open(API_KEYS_FILE) as f:
                _api_keys = json.load(f)
            print(f"Loaded {len(_api_keys)} API keys from {API_KEYS_FILE}")
            return _api_keys
        except (json.JSONDecodeError, IOError) as e:
            print(f"Warning: Could not load API keys from {API_KEYS_FILE}: {e}")

    return _api_keys


# API Key header
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def get_client_id(api_key: Optional[str] = Security(api_key_header)) -> Optional[str]:
    """
    Validate API key and return client_id.

    If AUTH_ENABLED is false, returns "default" client.
    If AUTH_ENABLED is true, requires valid API key.
    """
    if not AUTH_ENABLED:
        # Auth disabled - use default client or extract from header if provided
        if api_key:
            keys = load_api_keys()
            return keys.get(api_key, "default")
        return "default"

    # Auth enabled - require valid API key
    if not api_key:
        raise HTTPException(
            status_code=401,
            detail="Missing API key. Provide X-API-Key header."
        )

    keys = load_api_keys()
    client_id = keys.get(api_key)

    if not client_id:
        raise HTTPException(
            status_code=403,
            detail="Invalid API key"
        )

    return client_id


def get_client_workspace(client_id: str) -> Path:
    """Get the workspace directory for a client."""
    base = Path(os.getenv("WORKSPACE_DIR", "/workspace"))
    client_workspace = base / client_id
    client_workspace.mkdir(parents=True, exist_ok=True)
    return client_workspace


def get_client_elf_db(client_id: str) -> str:
    """Get the ELF database path for a client."""
    base = Path.home() / ".claude" / "elf"
    client_db_dir = base / client_id
    client_db_dir.mkdir(parents=True, exist_ok=True)
    return str(client_db_dir / "memory.db")

# Mode: "local" (Redis), "aws" (SQS + DynamoDB + ECS), or "vps" (SQLite + tmux)
MODE = os.getenv("MODE", "local")

# AWS Configuration
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
SQS_QUEUE_URL = os.getenv("SQS_QUEUE_URL", "")
DYNAMODB_TABLE = os.getenv("DYNAMODB_TABLE", "agent-jobs")
S3_BUCKET = os.getenv("S3_BUCKET", "")
ECS_CLUSTER = os.getenv("ECS_CLUSTER", "")
WORKER_TASK_DEFINITION = os.getenv("WORKER_TASK_DEFINITION", "")
WORKER_SUBNETS = os.getenv("WORKER_SUBNETS", "").split(",")
WORKER_SECURITY_GROUP = os.getenv("WORKER_SECURITY_GROUP", "")

# VPS Configuration
WORKSPACE_DIR = os.getenv("WORKSPACE_DIR", "/workspace")
TMUX_SESSION = os.getenv("TMUX_SESSION", "agents")
VPS_DB_PATH = os.getenv("VPS_DB_PATH", "/var/lib/agent-sandbox/jobs.db")
WORKER_SCRIPT_PATH = os.getenv("WORKER_SCRIPT_PATH", "/opt/multi-agent-sandbox/worker/main.py")

# Initialize clients based on mode
if MODE == "aws":
    import boto3
    sqs = boto3.client("sqs", region_name=AWS_REGION)
    dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
    ecs = boto3.client("ecs", region_name=AWS_REGION)
    table = dynamodb.Table(DYNAMODB_TABLE)
elif MODE == "vps":
    # Use SQLite for job storage on VPS
    db_dir = Path(VPS_DB_PATH).parent
    db_dir.mkdir(parents=True, exist_ok=True)

    def get_db_connection():
        conn = sqlite3.connect(VPS_DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn

    # Initialize database schema
    with get_db_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                client_id TEXT NOT NULL DEFAULT 'default',
                job_type TEXT NOT NULL,
                status TEXT NOT NULL,
                payload TEXT NOT NULL,
                result TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        # Add client_id column if it doesn't exist (migration for existing DBs)
        try:
            conn.execute("ALTER TABLE jobs ADD COLUMN client_id TEXT NOT NULL DEFAULT 'default'")
        except sqlite3.OperationalError:
            pass  # Column already exists

        conn.execute("""
            CREATE TABLE IF NOT EXISTS agents (
                agent_id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                client_id TEXT NOT NULL DEFAULT 'default',
                tmux_pane TEXT,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                last_heartbeat TEXT,
                FOREIGN KEY (job_id) REFERENCES jobs (job_id)
            )
        """)
        # Add client_id column if it doesn't exist (migration for existing DBs)
        try:
            conn.execute("ALTER TABLE agents ADD COLUMN client_id TEXT NOT NULL DEFAULT 'default'")
        except sqlite3.OperationalError:
            pass  # Column already exists

        # Create index for client_id queries
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_client ON jobs(client_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_agents_client ON agents(client_id)")
        conn.commit()
else:
    # Local mode uses Redis
    import redis
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    QUEUE_NAME = "job_queue"
    JOB_PREFIX = "job:"


def spawn_vps_worker(job_id: str, job_type: str, payload: dict, client_id: str = "default") -> str:
    """Spawn a worker in a tmux pane on the VPS."""
    # Generate a unique window name (include client prefix for visibility)
    window_name = f"{client_id[:8]}-{job_id[:8]}"

    # Get client-scoped workspace and ELF database
    client_workspace = get_client_workspace(client_id)
    client_elf_db = get_client_elf_db(client_id)

    # Environment variables to pass to the worker
    env_vars = (
        f"MODE=vps "
        f"JOB_ID={job_id} "
        f"CLIENT_ID={client_id} "
        f"WORKSPACE_DIR={client_workspace} "
        f"ELF_DB_PATH={client_elf_db}"
    )

    # Command to run the worker
    worker_cmd = f"{env_vars} python3 {WORKER_SCRIPT_PATH}"

    # Check if tmux session exists, create if not
    check_session = subprocess.run(
        ["tmux", "has-session", "-t", TMUX_SESSION],
        capture_output=True
    )

    if check_session.returncode != 0:
        # Create new session
        subprocess.run(
            ["tmux", "new-session", "-d", "-s", TMUX_SESSION, "-n", "main"],
            check=True
        )

    # Create new window for this job
    result = subprocess.run(
        ["tmux", "new-window", "-t", TMUX_SESSION, "-n", window_name, worker_cmd],
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        print(f"Failed to spawn worker: {result.stderr}")
        return ""

    # Record agent in database
    with get_db_connection() as conn:
        conn.execute("""
            INSERT INTO agents (agent_id, job_id, client_id, tmux_pane, status, started_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (job_id, job_id, client_id, f"{TMUX_SESSION}:{window_name}", "starting", datetime.utcnow().isoformat()))
        conn.commit()

    return f"{TMUX_SESSION}:{window_name}"


class JobRequest(BaseModel):
    job_type: str
    payload: dict


class JobResponse(BaseModel):
    job_id: str
    client_id: str
    job_type: str
    status: str
    payload: dict
    created_at: str
    updated_at: str
    result: Optional[dict] = None


@app.get("/health")
def health():
    if MODE == "aws":
        return {"status": "healthy", "mode": "aws"}
    elif MODE == "vps":
        try:
            with get_db_connection() as conn:
                conn.execute("SELECT 1")
            # Check if tmux is available
            tmux_check = subprocess.run(["which", "tmux"], capture_output=True)
            tmux_available = tmux_check.returncode == 0
            return {
                "status": "healthy",
                "mode": "vps",
                "database": "connected",
                "tmux": "available" if tmux_available else "not found",
                "workspace": WORKSPACE_DIR,
            }
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"VPS health check failed: {str(e)}")
    else:
        try:
            redis_client.ping()
            return {"status": "healthy", "mode": "local", "redis": "connected"}
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"Redis unavailable: {str(e)}")


@app.post("/jobs", response_model=JobResponse)
def create_job(request: JobRequest, client_id: str = Depends(get_client_id)):
    job_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()

    # Get client-scoped paths
    client_workspace = get_client_workspace(client_id)
    client_elf_db = get_client_elf_db(client_id)

    if MODE == "aws":
        # Store job in DynamoDB
        item = {
            "job_id": job_id,
            "client_id": client_id,
            "job_type": request.job_type,
            "status": "QUEUED",
            "payload": request.payload,
            "created_at": now,
            "updated_at": now,
        }
        table.put_item(Item=item)

        # Send message to SQS
        sqs.send_message(
            QueueUrl=SQS_QUEUE_URL,
            MessageBody=json.dumps({"job_id": job_id, "client_id": client_id}),
        )

        # Start a worker task in ECS
        try:
            ecs.run_task(
                cluster=ECS_CLUSTER,
                taskDefinition=WORKER_TASK_DEFINITION,
                launchType="FARGATE",
                networkConfiguration={
                    "awsvpcConfiguration": {
                        "subnets": [s.strip() for s in WORKER_SUBNETS if s.strip()],
                        "securityGroups": [WORKER_SECURITY_GROUP],
                        "assignPublicIp": "ENABLED",
                    }
                },
                overrides={
                    "containerOverrides": [
                        {
                            "name": "worker",
                            "environment": [
                                {"name": "JOB_ID", "value": job_id},
                                {"name": "CLIENT_ID", "value": client_id},
                                {"name": "WORKSPACE_DIR", "value": str(client_workspace)},
                                {"name": "ELF_DB_PATH", "value": client_elf_db},
                            ],
                        }
                    ]
                },
            )
        except Exception as e:
            print(f"Failed to start worker task: {e}")

    elif MODE == "vps":
        # Store job in SQLite
        with get_db_connection() as conn:
            conn.execute("""
                INSERT INTO jobs (job_id, client_id, job_type, status, payload, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (job_id, client_id, request.job_type, "QUEUED", json.dumps(request.payload), now, now))
            conn.commit()

        # Spawn worker in tmux
        try:
            pane = spawn_vps_worker(job_id, request.job_type, request.payload, client_id)
            if pane:
                print(f"Spawned worker for job {job_id} (client: {client_id}) in {pane}")
            else:
                print(f"Failed to spawn worker for job {job_id}")
        except Exception as e:
            print(f"Failed to spawn VPS worker: {e}")

    else:
        # Store job in Redis (local mode)
        job_data = {
            "job_id": job_id,
            "client_id": client_id,
            "job_type": request.job_type,
            "status": "QUEUED",
            "payload": json.dumps(request.payload),
            "created_at": now,
            "updated_at": now,
            "result": "",
        }
        redis_client.hset(f"{JOB_PREFIX}{job_id}", mapping=job_data)

        # Add to queue with client_id
        queue_message = json.dumps({"job_id": job_id, "client_id": client_id})
        redis_client.lpush(QUEUE_NAME, queue_message)

    return JobResponse(
        job_id=job_id,
        client_id=client_id,
        job_type=request.job_type,
        status="QUEUED",
        payload=request.payload,
        created_at=now,
        updated_at=now,
    )


@app.get("/jobs/{job_id}", response_model=JobResponse)
def get_job(job_id: str, client_id: str = Depends(get_client_id)):
    if MODE == "aws":
        response = table.get_item(Key={"job_id": job_id})
        item = response.get("Item")

        if not item:
            raise HTTPException(status_code=404, detail="Job not found")

        # Verify client owns this job (if auth enabled)
        if AUTH_ENABLED and item.get("client_id") != client_id:
            raise HTTPException(status_code=403, detail="Access denied to this job")

        return JobResponse(
            job_id=item["job_id"],
            client_id=item.get("client_id", "default"),
            job_type=item["job_type"],
            status=item["status"],
            payload=item["payload"],
            created_at=item["created_at"],
            updated_at=item["updated_at"],
            result=item.get("result"),
        )
    elif MODE == "vps":
        with get_db_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
            )
            row = cursor.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Job not found")

        # Verify client owns this job (if auth enabled)
        if AUTH_ENABLED and row["client_id"] != client_id:
            raise HTTPException(status_code=403, detail="Access denied to this job")

        result = None
        if row["result"]:
            result = json.loads(row["result"])

        return JobResponse(
            job_id=row["job_id"],
            client_id=row["client_id"],
            job_type=row["job_type"],
            status=row["status"],
            payload=json.loads(row["payload"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            result=result,
        )
    else:
        job_data = redis_client.hgetall(f"{JOB_PREFIX}{job_id}")

        if not job_data:
            raise HTTPException(status_code=404, detail="Job not found")

        # Verify client owns this job (if auth enabled)
        if AUTH_ENABLED and job_data.get("client_id") != client_id:
            raise HTTPException(status_code=403, detail="Access denied to this job")

        result = None
        if job_data.get("result"):
            result = json.loads(job_data["result"])

        return JobResponse(
            job_id=job_data["job_id"],
            client_id=job_data.get("client_id", "default"),
            job_type=job_data["job_type"],
            status=job_data["status"],
            payload=json.loads(job_data["payload"]),
            created_at=job_data["created_at"],
            updated_at=job_data["updated_at"],
            result=result,
        )


@app.get("/jobs")
def list_jobs(client_id: str = Depends(get_client_id), limit: int = 50):
    """List jobs for the authenticated client."""
    if MODE == "vps":
        with get_db_connection() as conn:
            if AUTH_ENABLED:
                cursor = conn.execute("""
                    SELECT * FROM jobs WHERE client_id = ?
                    ORDER BY created_at DESC LIMIT ?
                """, (client_id, limit))
            else:
                cursor = conn.execute("""
                    SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?
                """, (limit,))
            jobs = []
            for row in cursor.fetchall():
                job = dict(row)
                if job.get("payload"):
                    try:
                        job["payload"] = json.loads(job["payload"])
                    except json.JSONDecodeError:
                        job["payload"] = {"raw": job["payload"]}
                if job.get("result"):
                    try:
                        job["result"] = json.loads(job["result"])
                    except json.JSONDecodeError:
                        # Handle non-JSON results (e.g., plain text error messages)
                        job["result"] = {"message": job["result"]}
                jobs.append(job)
        return {"jobs": jobs, "count": len(jobs), "client_id": client_id}
    else:
        raise HTTPException(status_code=400, detail="Job listing only available in VPS mode currently")


@app.get("/agents")
def list_agents(client_id: str = Depends(get_client_id)):
    """List agents for the authenticated client (VPS mode only)."""
    if MODE != "vps":
        raise HTTPException(status_code=400, detail="Agent listing only available in VPS mode")

    with get_db_connection() as conn:
        if AUTH_ENABLED:
            cursor = conn.execute("""
                SELECT a.*, j.job_type, j.status as job_status
                FROM agents a
                JOIN jobs j ON a.job_id = j.job_id
                WHERE a.client_id = ?
                ORDER BY a.started_at DESC
            """, (client_id,))
        else:
            cursor = conn.execute("""
                SELECT a.*, j.job_type, j.status as job_status
                FROM agents a
                JOIN jobs j ON a.job_id = j.job_id
                ORDER BY a.started_at DESC
            """)
        agents = [dict(row) for row in cursor.fetchall()]

    return {"agents": agents, "count": len(agents), "client_id": client_id}


@app.get("/agents/{agent_id}")
def get_agent(agent_id: str):
    """Get agent details (VPS mode only)."""
    if MODE != "vps":
        raise HTTPException(status_code=400, detail="Agent details only available in VPS mode")

    with get_db_connection() as conn:
        cursor = conn.execute(
            "SELECT * FROM agents WHERE agent_id = ?", (agent_id,)
        )
        row = cursor.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Agent not found")

    return dict(row)


# ============================================================================
# ELF Dashboard (Client-Scoped)
# ============================================================================

# Cache of ELF memory instances per client
_elf_memories: Dict[str, any] = {}


def get_elf_memory_for_client(client_id: str):
    """Get ELF memory interface for a specific client."""
    if client_id not in _elf_memories:
        try:
            import sys
            sys.path.insert(0, str(Path(__file__).parent.parent / "elf"))
            from memory import ELFMemory
            client_db_path = get_client_elf_db(client_id)
            _elf_memories[client_id] = ELFMemory(db_path=client_db_path)
        except Exception as e:
            print(f"Warning: Could not initialize ELF memory for client {client_id}: {e}")
            return None
    return _elf_memories[client_id]


@app.get("/elf/stats")
def get_elf_stats(client_id: str = Depends(get_client_id)):
    """Get ELF memory statistics for the authenticated client."""
    memory = get_elf_memory_for_client(client_id)
    if not memory:
        raise HTTPException(status_code=503, detail="ELF memory not available")

    stats = memory.get_stats()
    stats["client_id"] = client_id
    return stats


@app.get("/elf/heuristics")
def get_elf_heuristics(
    client_id: str = Depends(get_client_id),
    domain: Optional[str] = None,
    project_path: Optional[str] = None,
    limit: int = 50
):
    """Get heuristics from ELF memory for the authenticated client."""
    memory = get_elf_memory_for_client(client_id)
    if not memory:
        raise HTTPException(status_code=503, detail="ELF memory not available")

    return {
        "client_id": client_id,
        "heuristics": memory.get_heuristics(
            domain=domain,
            project_path=project_path,
            limit=limit
        )
    }


@app.get("/elf/golden-rules")
def get_elf_golden_rules(client_id: str = Depends(get_client_id), project_path: Optional[str] = None):
    """Get golden rules (high-confidence heuristics) for the authenticated client."""
    memory = get_elf_memory_for_client(client_id)
    if not memory:
        raise HTTPException(status_code=503, detail="ELF memory not available")

    return {"client_id": client_id, "golden_rules": memory.get_golden_rules(project_path=project_path)}


from fastapi.responses import HTMLResponse, PlainTextResponse


# ============================================================================
# Workspace & Git Diff Endpoints (for pulling changes back)
# ============================================================================

@app.get("/workspace/diff")
def get_workspace_diff(
    path: str,
    client_id: str = Depends(get_client_id)
):
    """
    Get git diff of changes in a workspace directory.

    Args:
        path: Path to the workspace (relative to WORKSPACE_DIR or absolute)

    Returns:
        Git diff output showing all uncommitted changes
    """
    if MODE != "vps":
        raise HTTPException(status_code=400, detail="Diff only available in VPS mode")

    # Resolve path
    if path.startswith("/"):
        workspace_path = Path(path)
    else:
        workspace_path = Path(WORKSPACE_DIR) / client_id / path

    if not workspace_path.exists():
        raise HTTPException(status_code=404, detail=f"Workspace not found: {workspace_path}")

    # Check if it's a git repo
    git_dir = workspace_path / ".git"
    if not git_dir.exists():
        raise HTTPException(status_code=400, detail="Not a git repository")

    # Get diff (staged + unstaged)
    result = subprocess.run(
        ["git", "diff", "HEAD"],
        cwd=str(workspace_path),
        capture_output=True,
        text=True
    )

    # Also get untracked files
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=str(workspace_path),
        capture_output=True,
        text=True
    )

    return {
        "path": str(workspace_path),
        "diff": result.stdout,
        "untracked_files": untracked.stdout.strip().split("\n") if untracked.stdout.strip() else [],
        "has_changes": bool(result.stdout.strip() or untracked.stdout.strip())
    }


@app.get("/workspace/patch", response_class=PlainTextResponse)
def get_workspace_patch(
    path: str,
    client_id: str = Depends(get_client_id)
):
    """
    Generate a git patch file from all changes in workspace.

    This creates commits for any uncommitted changes and generates
    a format-patch that can be applied elsewhere.

    Args:
        path: Path to the workspace

    Returns:
        Git format-patch content (can be applied with `git am`)
    """
    if MODE != "vps":
        raise HTTPException(status_code=400, detail="Patch only available in VPS mode")

    # Resolve path
    if path.startswith("/"):
        workspace_path = Path(path)
    else:
        workspace_path = Path(WORKSPACE_DIR) / client_id / path

    if not workspace_path.exists():
        raise HTTPException(status_code=404, detail=f"Workspace not found: {workspace_path}")

    # Get diff for patch (including staged and unstaged)
    result = subprocess.run(
        ["git", "diff", "HEAD"],
        cwd=str(workspace_path),
        capture_output=True,
        text=True
    )

    if not result.stdout.strip():
        return "# No changes to patch"

    return result.stdout


@app.get("/workspace/status")
def get_workspace_status(
    path: str,
    client_id: str = Depends(get_client_id)
):
    """
    Get git status of a workspace.

    Args:
        path: Path to the workspace

    Returns:
        Git status information including changed files
    """
    if MODE != "vps":
        raise HTTPException(status_code=400, detail="Status only available in VPS mode")

    # Resolve path
    if path.startswith("/"):
        workspace_path = Path(path)
    else:
        workspace_path = Path(WORKSPACE_DIR) / client_id / path

    if not workspace_path.exists():
        raise HTTPException(status_code=404, detail=f"Workspace not found: {workspace_path}")

    # Get status
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(workspace_path),
        capture_output=True,
        text=True
    )

    # Get current branch
    branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=str(workspace_path),
        capture_output=True,
        text=True
    )

    # Get last commit
    last_commit = subprocess.run(
        ["git", "log", "-1", "--format=%H %s"],
        cwd=str(workspace_path),
        capture_output=True,
        text=True
    )

    # Parse status
    files = []
    for line in status.stdout.strip().split("\n"):
        if line:
            status_code = line[:2]
            filename = line[3:]
            files.append({"status": status_code.strip(), "file": filename})

    return {
        "path": str(workspace_path),
        "branch": branch.stdout.strip(),
        "last_commit": last_commit.stdout.strip(),
        "changed_files": files,
        "total_changes": len(files)
    }


@app.get("/workspace/file")
def get_workspace_file(
    path: str,
    file: str,
    client_id: str = Depends(get_client_id)
):
    """
    Get contents of a specific file from workspace.

    Args:
        path: Path to the workspace
        file: Relative path to the file within the workspace

    Returns:
        File contents
    """
    if MODE != "vps":
        raise HTTPException(status_code=400, detail="File access only available in VPS mode")

    # Resolve path
    if path.startswith("/"):
        workspace_path = Path(path)
    else:
        workspace_path = Path(WORKSPACE_DIR) / client_id / path

    file_path = workspace_path / file

    # Security: ensure file is within workspace
    try:
        file_path.resolve().relative_to(workspace_path.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied: path traversal detected")

    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {file}")

    if not file_path.is_file():
        raise HTTPException(status_code=400, detail="Path is not a file")

    try:
        content = file_path.read_text()
        return {"file": file, "content": content, "size": len(content)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading file: {str(e)}")


@app.get("/dashboard", response_class=HTMLResponse)
def get_dashboard(client_id: str = Depends(get_client_id)):
    """ELF Dashboard - Visual interface for memory and learning (client-scoped)."""
    memory = get_elf_memory_for_client(client_id)

    # Get stats
    stats = memory.get_stats() if memory else {}

    # Get recent heuristics
    heuristics = memory.get_heuristics(limit=20) if memory else []

    # Get golden rules
    golden_rules = memory.get_golden_rules() if memory else []

    # Build heuristics table rows
    heuristics_rows = ""
    for h in heuristics:
        conf = h.get('confidence', 0) * 100
        is_golden = "⭐" if h.get('is_golden') else ""
        heuristics_rows += f"""
        <tr>
            <td>{is_golden}</td>
            <td>{h.get('domain', 'general')}</td>
            <td>{h.get('rule', '')[:80]}...</td>
            <td>{conf:.0f}%</td>
            <td>{h.get('times_validated', 0)}</td>
            <td>{h.get('times_violated', 0)}</td>
        </tr>
        """

    # Build golden rules list
    golden_rules_html = ""
    for r in golden_rules:
        golden_rules_html += f"<li><strong>{r.get('domain', 'general')}:</strong> {r.get('rule', '')}</li>"

    if not golden_rules_html:
        golden_rules_html = "<li><em>No golden rules yet. Heuristics with 90%+ confidence become golden rules.</em></li>"

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>ELF Dashboard - Multi-Agent Sandbox</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            * {{ box-sizing: border-box; }}
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
                margin: 0;
                padding: 20px;
                background: #f5f5f5;
                color: #333;
            }}
            h1 {{ color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 10px; }}
            h2 {{ color: #34495e; margin-top: 30px; }}
            .stats-grid {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
                gap: 15px;
                margin: 20px 0;
            }}
            .stat-card {{
                background: white;
                padding: 20px;
                border-radius: 8px;
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);
                text-align: center;
            }}
            .stat-value {{
                font-size: 2em;
                font-weight: bold;
                color: #3498db;
            }}
            .stat-label {{
                color: #7f8c8d;
                margin-top: 5px;
            }}
            table {{
                width: 100%;
                border-collapse: collapse;
                background: white;
                border-radius: 8px;
                overflow: hidden;
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            }}
            th, td {{
                padding: 12px 15px;
                text-align: left;
                border-bottom: 1px solid #eee;
            }}
            th {{
                background: #3498db;
                color: white;
            }}
            tr:hover {{
                background: #f8f9fa;
            }}
            .golden-rules {{
                background: white;
                padding: 20px;
                border-radius: 8px;
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            }}
            .golden-rules ul {{
                margin: 0;
                padding-left: 20px;
            }}
            .golden-rules li {{
                margin: 10px 0;
                line-height: 1.5;
            }}
            .refresh-note {{
                color: #7f8c8d;
                font-size: 0.9em;
                margin-top: 30px;
            }}
            .mode-badge {{
                display: inline-block;
                background: #27ae60;
                color: white;
                padding: 4px 12px;
                border-radius: 20px;
                font-size: 0.8em;
                margin-left: 10px;
            }}
        </style>
    </head>
    <body>
        <h1>ELF Dashboard <span class="mode-badge">Mode: {MODE}</span> <span class="mode-badge" style="background:#3498db">Client: {client_id}</span></h1>

        <h2>Memory Statistics</h2>
        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-value">{stats.get('total_heuristics', 0)}</div>
                <div class="stat-label">Total Heuristics</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{stats.get('golden_rules', 0)}</div>
                <div class="stat-label">Golden Rules</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{stats.get('total_outcomes', 0)}</div>
                <div class="stat-label">Job Outcomes</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{stats.get('successful_jobs', 0)}</div>
                <div class="stat-label">Successful Jobs</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{stats.get('failed_jobs', 0)}</div>
                <div class="stat-label">Failed Jobs</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{stats.get('active_trails', 0)}</div>
                <div class="stat-label">Active Trails</div>
            </div>
        </div>

        <h2>Golden Rules</h2>
        <div class="golden-rules">
            <ul>
                {golden_rules_html}
            </ul>
        </div>

        <h2>Learned Heuristics</h2>
        <table>
            <tr>
                <th></th>
                <th>Domain</th>
                <th>Rule</th>
                <th>Confidence</th>
                <th>Validated</th>
                <th>Violated</th>
            </tr>
            {heuristics_rows if heuristics_rows else '<tr><td colspan="6"><em>No heuristics yet. Run some jobs to start learning!</em></td></tr>'}
        </table>

        <p class="refresh-note">Refresh the page to see updated statistics. Data is stored in ~/.claude/elf/{client_id}/memory.db</p>
    </body>
    </html>
    """

    return html
