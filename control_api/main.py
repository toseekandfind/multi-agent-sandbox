import os
import json
import uuid
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Agent Runner Control API")

# Mode: "local" (Redis) or "aws" (SQS + DynamoDB + ECS)
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

# Initialize clients based on mode
if MODE == "aws":
    import boto3
    sqs = boto3.client("sqs", region_name=AWS_REGION)
    dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
    ecs = boto3.client("ecs", region_name=AWS_REGION)
    table = dynamodb.Table(DYNAMODB_TABLE)
else:
    import redis
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)
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
    if MODE == "aws":
        return {"status": "healthy", "mode": "aws"}
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

    else:
        # Store job in Redis
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
