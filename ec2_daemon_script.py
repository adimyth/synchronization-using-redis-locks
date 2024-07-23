import redis
import docker
import time
from loguru import logger
import sys
import socket
import os
import signal
import traceback


REDIS_HOST = os.environ.get("REDIS_HOST", "localhost")
REDIS_PORT = 6379
# Prefix for all lock keys in Redis
LOCK_PREFIX = "ec2_container_lock"
# duration in seconds for which the lock is valid without being refreshed
LOCK_TIMEOUT = 120

# Docker configuration for multiple containers
CONTAINERS = {
    "cronjobs": {
        "name": "cronjobs",
        "lock_name": f"{LOCK_PREFIX}::cronjobs",
    },
    "pyapi": {
        "name": "pyapi",
        "lock_name": f"{LOCK_PREFIX}::pyapi",
    },
}

# Initialize Redis and Docker clients
redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT)
docker_client = docker.from_env()

# Stores the current lock values for each container if this instance holds the lock
current_locks = {}
# Tracks whether each container is running on this instance
containers_running = {container_id: False for container_id in CONTAINERS}


def retry_on_exception(retries=3, delay=5):
    """
    Decorator to retry a function on exception up to a specified number of times.

    :param retries: Number of retry attempts
    :param delay: Delay between retries in seconds
    """

    def decorator(func):
        def wrapper(*args, **kwargs):
            for attempt in range(retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == retries - 1:
                        raise
                    logger.warning(
                        f"Attempt {attempt + 1} failed: {str(e)}. Retrying in {delay} seconds..."
                    )
                    time.sleep(delay)

        return wrapper

    return decorator


@retry_on_exception(retries=3, delay=5)
def acquire_lock(container_id):
    """
    Attempt to acquire the distributed lock for a specific container in Redis.

    :param container_id: ID of the container to acquire the lock for
    :return: True if lock is acquired, False otherwise
    """
    lock_name = CONTAINERS[container_id]["lock_name"]
    hostname = socket.gethostname()  # ip-172-29-89-168, ip-172-29-3-161, etc.
    lock_value = f"{hostname}:{os.getpid()}"
    # Use setnx (SET if Not eXists) to ensure atomic lock acquisition
    if redis_client.set(lock_name, lock_value, nx=True, ex=LOCK_TIMEOUT):
        current_locks[container_id] = lock_value
        return True
    return False


@retry_on_exception(retries=3, delay=5)
def extend_lock(container_id):
    """
    Attempt to extend the lifetime of the current lock for a specific container.
    This is done by updating the expiration time of the lock key in Redis.
    If an instance has acquired a lock it should keep extending the lock so that other instances do not acquire the lock.

    :param container_id: ID of the container to extend the lock for
    :return: True if lock is extended, False otherwise
    """
    lock_name = CONTAINERS[container_id]["lock_name"]
    if container_id in current_locks:
        lock_value = current_locks[container_id]
        # Check if we still own the lock before extending
        if redis_client.get(lock_name) == lock_value.encode():
            return redis_client.expire(lock_name, LOCK_TIMEOUT)
    return False


@retry_on_exception(retries=3, delay=5)
def release_lock(container_id):
    """
    Attempt to release the current lock for a specific container if we own it.

    :param container_id: ID of the container to release the lock for
    """
    lock_name = CONTAINERS[container_id]["lock_name"]
    if container_id in current_locks:
        lock_value = current_locks[container_id]
        # Delete the lock from redis only if it is still held by this instance
        if redis_client.get(lock_name) == lock_value.encode():
            redis_client.delete(lock_name)
        # Remove the lock from the current locks dictionary
        del current_locks[container_id]


def start_container(container_id):
    """
    Start the Docker container if it's not already running.

    :param container_id: ID of the container to start
    """
    container_name = CONTAINERS[container_id]["name"]
    try:
        container = docker_client.containers.get(container_name)
        if container.status != "running":
            container.start(detach=True)
            containers_running[container_id] = True
            logger.info(f"Container {container_name} started")
    except docker.errors.NotFound:
        logger.error(f"Container {container_name} not found")


def stop_container(container_id):
    """
    Stop the Docker container if it's running.

    :param container_id: ID of the container to stop
    """
    container_name = CONTAINERS[container_id]["name"]
    try:
        container = docker_client.containers.get(container_name)
        if container.status == "running":
            container.stop()
            containers_running[container_id] = False
            logger.info(f"Container {container_name} stopped")
    except docker.errors.NotFound:
        logger.error(f"Container {container_name} not found")


def cleanup():
    """
    Release all locks and stop all containers before exiting.
    """
    logger.info("Cleaning up before exit")
    for container_id in CONTAINERS:
        release_lock(container_id)
        stop_container(container_id)


def signal_handler(signum, frame):
    """
    Handle termination signals gracefully.

    :param signum: Signal number
    :param frame: Current stack frame
    """
    logger.info(f"Received signal {signum}. Exiting...")
    cleanup()
    sys.exit(0)


def manage_container(container_id):
    """
    Manage the state of a single container end-to-end.
    Acquire Lock - Start Container - Extend Lock - Stop Container - Release Lock

    :param container_id: ID of the container to manage
    """
    if container_id not in current_locks and acquire_lock(container_id):
        # We just acquired the lock, start the container
        logger.info(f"Lock acquired for {container_id}, starting container")
        start_container(container_id)
    elif container_id in current_locks and not extend_lock(container_id):
        # We failed to extend our lock, stop the container
        logger.warning(f"Failed to extend lock for {container_id}, stopping container")
        stop_container(container_id)
        release_lock(container_id)
    elif container_id not in current_locks and containers_running[container_id]:
        # Inconsistent state: container is running but we don't have the lock
        logger.warning(
            f"No lock but container {container_id} running, stopping container"
        )
        stop_container(container_id)


def main_loop():
    """
    Main loop of the daemon.
    Manages lock acquisition, container state, and periodic lock extension for all containers.
    """
    while True:
        try:
            for container_id in CONTAINERS:
                manage_container(container_id)

            # Sleep for half the lock timeout before next check - this is mostly for extending the lock
            time.sleep(LOCK_TIMEOUT / 2)
        except redis.exceptions.ConnectionError:
            logger.error("Lost connection to Redis. Retrying in 10 seconds...")
            time.sleep(10)
        except docker.errors.APIError as e:
            logger.error(f"Docker API error: {str(e)}. Retrying in 10 seconds...")
            time.sleep(10)


if __name__ == "__main__":
    # signal.signal() function allows defining custom handlers to be executed when a signal is received.
    # Register SIGTERM (Termination) and SIGINT (Keyboard Interrupt) signals to handle graceful termination of the script.
    # Can add more signals as needed - SIGKILL?
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    try:
        main_loop()
    except Exception as e:
        logger.error(f"Error Running Daemon Script: {str(e)}")
        traceback.print_exc()
        cleanup()
        sys.exit(1)
