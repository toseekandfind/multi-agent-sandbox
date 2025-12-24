import os
import json
import uuid
import subprocess
import sqlite3
from datetime import datetime
from typing import Optional
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Agent Runner Control API")

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
                job_type TEXT NOT NULL,
                status TEXT NOT NULL,
                payload TEXT NOT NULL,
                result TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS agents (
                agent_id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                tmux_pane TEXT,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                last_heartbeat TEXT,
                FOREIGN KEY (job_id) REFERENCES jobs (job_id)
            )
        """)
        conn.commit()
else:
    # Local mode uses Redis
    import redis
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    QUEUE_NAME = "job_queue"
    JOB_PREFIX = "job:"


def spawn_vps_worker(job_id: str, job_type: str, payload: dict) -> str:
    """Spawn a worker in a tmux pane on the VPS."""
    # Generate a unique window name
    window_name = f"job-{job_id[:8]}"

    # Environment variables to pass to the worker
    env_vars = f"MODE=vps JOB_ID={job_id} WORKSPACE_DIR={WORKSPACE_DIR}"

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
            INSERT INTO agents (agent_id, job_id, tmux_pane, status, started_at)
            VALUES (?, ?, ?, ?, ?)
        """, (job_id, job_id, f"{TMUX_SESSION}:{window_name}", "starting", datetime.utcnow().isoformat()))
        conn.commit()

    return f"{TMUX_SESSION}:{window_name}"


class JobRequest(BaseModel):
    job_type: str
    payload: dict


class JobResponse(BaseModel):
    job_id: str
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
def create_job(request: JobRequest):
    job_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()

    if MODE == "aws":
        # Store job in DynamoDB
        item = {
            "job_id": job_id,
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
            MessageBody=json.dumps({"job_id": job_id}),
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
                INSERT INTO jobs (job_id, job_type, status, payload, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (job_id, request.job_type, "QUEUED", json.dumps(request.payload), now, now))
            conn.commit()

        # Spawn worker in tmux
        try:
            pane = spawn_vps_worker(job_id, request.job_type, request.payload)
            if pane:
                print(f"Spawned worker for job {job_id} in {pane}")
            else:
                print(f"Failed to spawn worker for job {job_id}")
        except Exception as e:
            print(f"Failed to spawn VPS worker: {e}")

    else:
        # Store job in Redis (local mode)
        job_data = {
            "job_id": job_id,
            "job_type": request.job_type,
            "status": "QUEUED",
            "payload": json.dumps(request.payload),
            "created_at": now,
            "updated_at": now,
            "result": "",
        }
        redis_client.hset(f"{JOB_PREFIX}{job_id}", mapping=job_data)

        # Add to queue
        queue_message = json.dumps({"job_id": job_id})
        redis_client.lpush(QUEUE_NAME, queue_message)

    return JobResponse(
        job_id=job_id,
        job_type=request.job_type,
        status="QUEUED",
        payload=request.payload,
        created_at=now,
        updated_at=now,
    )


@app.get("/jobs/{job_id}", response_model=JobResponse)
def get_job(job_id: str):
    if MODE == "aws":
        response = table.get_item(Key={"job_id": job_id})
        item = response.get("Item")

        if not item:
            raise HTTPException(status_code=404, detail="Job not found")

        return JobResponse(
            job_id=item["job_id"],
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

        result = None
        if row["result"]:
            result = json.loads(row["result"])

        return JobResponse(
            job_id=row["job_id"],
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

        result = None
        if job_data.get("result"):
            result = json.loads(job_data["result"])

        return JobResponse(
            job_id=job_data["job_id"],
            job_type=job_data["job_type"],
            status=job_data["status"],
            payload=json.loads(job_data["payload"]),
            created_at=job_data["created_at"],
            updated_at=job_data["updated_at"],
            result=result,
        )


@app.get("/agents")
def list_agents():
    """List all agents (VPS mode only)."""
    if MODE != "vps":
        raise HTTPException(status_code=400, detail="Agent listing only available in VPS mode")

    with get_db_connection() as conn:
        cursor = conn.execute("""
            SELECT a.*, j.job_type, j.status as job_status
            FROM agents a
            JOIN jobs j ON a.job_id = j.job_id
            ORDER BY a.started_at DESC
        """)
        agents = [dict(row) for row in cursor.fetchall()]

    return {"agents": agents, "count": len(agents)}


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
