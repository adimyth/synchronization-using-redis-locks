FROM alpine:3.15

# Install required packages
RUN apk add --update --no-cache bash dos2unix curl python3=3.9.18-r0 py3-pip
RUN apk update && apk add postgresql-dev gcc musl-dev

WORKDIR /app

# Copy crontab, sync & start script
COPY cronjobs.crontab .
COPY sync.py .

COPY requirements.txt .
RUN python3 -m ensurepip \
    && pip3 install --upgrade pip \
    && pip3 install --no-cache-dir -r requirements.txt

# Fix line endings && execute permissions
RUN dos2unix cronjobs.crontab \
    && \
    find . -type f -iname "*.sh" -exec chmod +x {} \;

# Run cron on container startup
COPY start.sh .
RUN chmod +x start.sh
CMD ["./start.sh"]