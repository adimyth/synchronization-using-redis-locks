#!/bin/bash

# Load the crontab file
echo "[INFO] Loading crontab file"
crontab cronjobs.crontab

# Start cron
echo "[INFO] Starting cron"
crond -f
