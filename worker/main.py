import os
import json
import time
import signal
import sys
from datetime import datetime

import redis

# Redis connection
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
redis_client = redis.from_url(REDIS_URL, decode_responses=True)

# Constants
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


def update_job_status(job_id: str, status: str, result: dict = None):
    now = datetime.utcnow().isoformat()
    updates = {"status": status, "updated_at": now}
    if result is not None:
        updates["result"] = json.dumps(result)
    redis_client.hset(f"{JOB_PREFIX}{job_id}", mapping=updates)
    print(f"[{WORKER_ID}] Job {job_id} -> {status}")


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

    # In AWS mode, this would write to S3
    # For local, we just return the result
    return result


def process_job(job_id: str):
    """Process a single job."""
    job_key = f"{JOB_PREFIX}{job_id}"
    job_data = redis_client.hgetall(job_key)

    if not job_data:
        print(f"[{WORKER_ID}] Job {job_id} not found, skipping")
        return

    job_type = job_data.get("job_type", "")
    payload = json.loads(job_data.get("payload", "{}"))

    # Mark as running
    update_job_status(job_id, "RUNNING")

    try:
        if job_type == "echo":
            result = handle_echo_job(job_id, payload)
        else:
            raise ValueError(f"Unknown job type: {job_type}")

        update_job_status(job_id, "SUCCEEDED", result)

    except Exception as e:
        error_result = {
            "error": str(e),
            "worker_id": WORKER_ID,
            "failed_at": datetime.utcnow().isoformat(),
        }
        update_job_status(job_id, "FAILED", error_result)
        print(f"[{WORKER_ID}] Job {job_id} failed: {e}")


def main():
    print(f"[{WORKER_ID}] Worker starting, listening on queue: {QUEUE_NAME}")

    while not shutdown_requested:
        try:
            # Blocking pop with 5 second timeout
            result = redis_client.brpop(QUEUE_NAME, timeout=5)

            if result is None:
                continue

            _, message = result
            job_info = json.loads(message)
            job_id = job_info.get("job_id")

            if job_id:
                print(f"[{WORKER_ID}] Processing job: {job_id}")
                process_job(job_id)

        except redis.ConnectionError as e:
            print(f"[{WORKER_ID}] Redis connection error: {e}, retrying in 5s...")
            time.sleep(5)
        except Exception as e:
            print(f"[{WORKER_ID}] Unexpected error: {e}")
            time.sleep(1)

    print(f"[{WORKER_ID}] Worker shutting down gracefully")


if __name__ == "__main__":
    main()
