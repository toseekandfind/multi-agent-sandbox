import os
import json
import uuid
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import redis

app = FastAPI(title="Agent Runner Control API")

# Redis connection
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
redis_client = redis.from_url(REDIS_URL, decode_responses=True)

# Constants
QUEUE_NAME = "job_queue"
JOB_PREFIX = "job:"


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
    try:
        redis_client.ping()
        return {"status": "healthy", "redis": "connected"}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Redis unavailable: {str(e)}")


@app.post("/jobs", response_model=JobResponse)
def create_job(request: JobRequest):
    job_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()

    job_data = {
        "job_id": job_id,
        "job_type": request.job_type,
        "status": "QUEUED",
        "payload": json.dumps(request.payload),
        "created_at": now,
        "updated_at": now,
        "result": "",
    }

    # Store job in Redis
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
