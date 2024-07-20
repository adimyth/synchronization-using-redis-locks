import os
import redis
from redis.exceptions import LockError
import requests
from loguru import logger
import time

# Connect to Redis on the host machine
redis_host = os.environ.get("REDIS_HOST", "localhost")
redis_client = redis.Redis(host=redis_host, port=6379, db=0)


def run_job(instance_id):
    # Create a lock with ttl of 300 seconds.
    # This lock will be used to ensure that only one instance of the script runs at a time. Redis will automatically release the lock after 300 seconds.
    # This is an idempotent operation, so can be called multiple times safely.
    job_lock = redis_client.lock("job_lock", timeout=300)

    try:
        # Attempt to acquire the lock. If the lock is already held by another instance, this will return False.
        # blocking=False means it will immediately return rather than wait if the lock is unavailable
        have_lock = job_lock.acquire(blocking=False)

        # If the lock was successfully acquired, run the job
        if have_lock:
            logger.info(f"Instance {instance_id}: Acquired job lock")
            jobs = [
                ("joke_api", "https://official-joke-api.appspot.com/random_joke"),
                ("cat_fact_api", "https://catfact.ninja/fact"),
                ("ip_info_api", "https://ipapi.co/json/"),
                ("random_user_api", "https://randomuser.me/api/"),
            ]

            for job_name, api_call in jobs:
                try:
                    response = requests.get(api_call)
                    if response.status_code == 200:
                        logger.info(
                            f"Instance {instance_id}: Successfully called {job_name} - Status: {response.status_code}"
                        )
                    else:
                        logger.info(
                            f"Instance {instance_id}: Failed to call {job_name} - Status: {response.status_code}"
                        )
                except requests.RequestException as e:
                    logger.info(
                        f"Instance {instance_id}: Error making API call for {job_name}: {e}"
                    )
        else:
            # If the lock was not acquired, log a message and skip this run
            logger.error(
                f"Instance {instance_id}: Failed to acquire job lock, skipping this run"
            )
    except LockError:
        # Catch any errors acquiring the lock
        logger.exception(f"Instance {instance_id}: Error acquiring job lock")
    finally:
        # Release the lock if it was acquired now that the job is complete
        # Only one instance of the script will have acquired the lock, so only that instance will release it
        if have_lock:
            job_lock.release()
            logger.info(f"Instance {instance_id}: Released job lock")


if __name__ == "__main__":
    instance_id = os.environ.get("INSTANCE_ID", "unknown")
    logger.info(
        f"Instance {instance_id}: Script started at {time.strftime('%Y-%m-%d %H:%M:%S')}"
    )
    run_job(instance_id)
    logger.info(
        f"Instance {instance_id}: Script ended at {time.strftime('%Y-%m-%d %H:%M:%S')}"
    )
