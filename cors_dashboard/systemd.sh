# ==============================================================================
# == SYSTEMD SERVICE FILES - Production Deployment                          ==
# ==============================================================================

# ---------------------------------------------------------
# FILE 1: /etc/systemd/system/cors-backend.service
# ---------------------------------------------------------
[Unit]
Description=CORS Geodetic Backend Server
After=network.target postgresql.service
Wants=network-online.target

[Service]
Type=notify
User=corsuser
Group=corsuser
WorkingDirectory=/opt/cors-geodetic/backend

# Environment
Environment="PYTHONUNBUFFERED=1"
Environment="PATH=/opt/cors-geodetic/venv/bin:$PATH"
EnvironmentFile=/opt/cors-geodetic/backend/.env

# Main process
ExecStart=/opt/cors-geodetic/venv/bin/uvicorn app.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --workers 4 \
    --log-level info \
    --access-log \
    --proxy-headers \
    --forwarded-allow-ips '*'

# Restart policy
Restart=always
RestartSec=10
StartLimitInterval=300
StartLimitBurst=5

# Resource limits
LimitNOFILE=65536
MemoryMax=2G
CPUQuota=200%

# Security
PrivateTmp=true
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/cors-geodetic/backend

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=cors-backend

[Install]
WantedBy=multi-user.target


# ---------------------------------------------------------
# FILE 2: /etc/systemd/system/geodetic-agent@.service
# ---------------------------------------------------------
[Unit]
Description=Geodetic Agent (Serial: %i)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pi
Group=pi
WorkingDirectory=/home/pi/geodetic

# Environment
Environment="PYTHONUNBUFFERED=1"
Environment="AGENT_SERIAL=%i"

# Main process
ExecStart=/usr/bin/python3 /home/pi/geodetic/agent_universal_production.py

# Restart policy - Quan trọng cho 24/7
Restart=always
RestartSec=30
StartLimitInterval=600
StartLimitBurst=10

# Resource limits
LimitNOFILE=4096
MemoryMax=512M
CPUQuota=100%

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=geodetic-agent-%i

# Watchdog (agent cần gửi sd_notify)
WatchdogSec=120

[Install]
WantedBy=multi-user.target


# ---------------------------------------------------------
# FILE 3: Installation Script - install_services.sh
# ---------------------------------------------------------
#!/bin/bash
set -euo pipefail

echo "🚀 Installing CORS Geodetic Services..."

# 1. Create user for backend (nếu chưa có)
if ! id -u corsuser > /dev/null 2>&1; then
    echo "Creating corsuser..."
    sudo useradd -r -s /bin/false -m -d /opt/cors-geodetic corsuser
fi

# 2. Install backend service
echo "Installing backend service..."
sudo cp cors-backend.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable cors-backend.service

# 3. Install agent service (trên Raspberry Pi)
if [ -f /proc/cpuinfo ] && grep -q "Raspberry Pi" /proc/cpuinfo; then
    echo "Installing agent service..."
    
    # Lấy serial number
    SERIAL=$(cat /proc/cpuinfo | grep Serial | awk '{print $3}')
    
    sudo cp geodetic-agent@.service /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable geodetic-agent@${SERIAL}.service
    
    echo "✓ Agent service installed for serial: $SERIAL"
fi

echo "✓ Services installed successfully!"
echo ""
echo "To start services:"
echo "  Backend:  sudo systemctl start cors-backend"
echo "  Agent:    sudo systemctl start geodetic-agent@<SERIAL>"


# ---------------------------------------------------------
# FILE 4: Monitoring Script - check_services.sh
# ---------------------------------------------------------
#!/bin/bash

echo "=== CORS Geodetic Services Status ==="
echo ""

# Backend
if systemctl is-active --quiet cors-backend; then
    echo "✅ Backend:  RUNNING"
    systemctl status cors-backend --no-pager -l | grep -A 2 "Active:"
else
    echo "❌ Backend:  STOPPED"
fi

echo ""

# Agent(s)
AGENT_SERVICES=$(systemctl list-units --type=service --all | grep geodetic-agent@ | awk '{print $1}')

if [ -z "$AGENT_SERVICES" ]; then
    echo "⚠️  No agent services found"
else
    for service in $AGENT_SERVICES; do
        if systemctl is-active --quiet $service; then
            echo "✅ $service:  RUNNING"
        else
            echo "❌ $service:  STOPPED"
        fi
    done
fi

echo ""
echo "=== Recent Logs ==="
sudo journalctl -u cors-backend -u 'geodetic-agent@*' -n 20 --no-pager


# ---------------------------------------------------------
# FILE 5: Restart Script - restart_all.sh
# ---------------------------------------------------------
#!/bin/bash

echo "🔄 Restarting all CORS services..."

sudo systemctl restart cors-backend
sudo systemctl restart 'geodetic-agent@*'

sleep 3

./check_services.sh