## Script

## Running as a daemon
Create the systemd file - `/etc/systemd/system/ec2-daemon.service`

```bash
[Unit]
Description=EC2 Daemon for Container Lock Management
After=network.target

[Service]
ExecStart=/usr/bin/python3 ec2_daemon_script.py
Restart=always
User=root
Group=root
Environment=PYTHONUNBUFFERED=1
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

Enable the service and start it
```bash
sudo systemctl daemon-reload
sudo systemctl enable ec2-daemon
sudo systemctl start ec2-daemon
```

## Questions
1. What to do when we really want to stop the containers - so that it isn't running on any of the instances?
2. Can't keep too high a timeout for the lock since if one of the instances goes down, the lock will not be released till the timeout is reached.
3. Can't keep it too low since we then need to extend the timeout for the lock to be held by the current instance.