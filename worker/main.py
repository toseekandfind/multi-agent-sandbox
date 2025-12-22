import os
import json
import time
import signal
import sys
from datetime import datetime

# Mode: "local" (Redis) or "aws" (SQS + DynamoDB + S3)
MODE = os.getenv("MODE", "local")

# AWS Configuration
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
SQS_QUEUE_URL = os.getenv("SQS_QUEUE_URL", "")
DYNAMODB_TABLE = os.getenv("DYNAMODB_TABLE", "agent-jobs")
S3_BUCKET = os.getenv("S3_BUCKET", "")
JOB_ID_OVERRIDE = os.getenv("JOB_ID", "")  # For ECS task with specific job

# Initialize clients based on mode
if MODE == "aws":
    import boto3
    sqs = boto3.client("sqs", region_name=AWS_REGION)
    dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
    s3 = boto3.client("s3", region_name=AWS_REGION)
    table = dynamodb.Table(DYNAMODB_TABLE)
else:
    import redis
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    QUEUE_NAME = "job_queue"
    JOB_PREFIX = "job:"

WORKER_ID = os.getenv("HOSTNAME", f"worker-{os.getpid()}")

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


def upload_result_to_s3(job_id: str, result: dict):
    result_json = json.dumps(result, indent=2)
    s3.put_object(
        Bucket=S3_BUCKET,
        Key=f"jobs/{job_id}/result.json",
        Body=result_json,
        ContentType="application/json",
    )
    print(f"[{WORKER_ID}] Uploaded result to s3://{S3_BUCKET}/jobs/{job_id}/result.json")


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


def get_job_data_aws(job_id: str) -> dict:
    response = table.get_item(Key={"job_id": job_id})
    return response.get("Item", {})


def get_job_data_redis(job_id: str) -> dict:
    return redis_client.hgetall(f"{JOB_PREFIX}{job_id}")


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
            else:
                raise ValueError(f"Unknown job type: {job_type}")

            # Upload result to S3
            upload_result_to_s3(job_id, result)

            # Update status
            update_job_status_aws(job_id, "SUCCEEDED", result)

        except Exception as e:
            error_result = {
                "error": str(e),
                "worker_id": WORKER_ID,
                "failed_at": datetime.utcnow().isoformat(),
            }
            update_job_status_aws(job_id, "FAILED", error_result)
            print(f"[{WORKER_ID}] Job {job_id} failed: {e}")

    else:
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
            else:
                raise ValueError(f"Unknown job type: {job_type}")

            update_job_status_redis(job_id, "SUCCEEDED", result)

        except Exception as e:
            error_result = {
                "error": str(e),
                "worker_id": WORKER_ID,
                "failed_at": datetime.utcnow().isoformat(),
            }
            update_job_status_redis(job_id, "FAILED", error_result)
            print(f"[{WORKER_ID}] Job {job_id} failed: {e}")


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

    # If JOB_ID is provided (ECS task override), process only that job and exit
    if MODE == "aws" and JOB_ID_OVERRIDE:
        print(f"[{WORKER_ID}] Processing specific job: {JOB_ID_OVERRIDE}")
        process_job(JOB_ID_OVERRIDE)
        print(f"[{WORKER_ID}] Job complete, exiting")
        return

    # Otherwise, poll for jobs continuously
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
    else:
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
