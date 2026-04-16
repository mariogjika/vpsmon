#!/bin/bash
# VPSMon — One-line installer
# Usage: curl -sL https://raw.githubusercontent.com/solverix/vpsmon/main/install.sh | sudo bash
set -e

echo "
  VPSMon — Server Monitoring v5.0
  ================================
"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${GREEN}[OK]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
error() { echo -e "${RED}[ERR]${NC} $1"; exit 1; }

[ "$EUID" -eq 0 ] || error "Please run as root (sudo bash install.sh)"

PYTHON=""
for cmd in python3.11 python3.10 python3.9 python3; do
    if command -v $cmd &>/dev/null; then
        if $cmd -c "import sys; exit(0 if sys.version_info >= (3,9) else 1)" 2>/dev/null; then
            PYTHON=$cmd; break
        fi
    fi
done
[ -n "$PYTHON" ] || error "Python 3.9+ required. Install with: apt install python3 python3-venv python3-pip"
info "Found $PYTHON ($($PYTHON --version 2>&1))"

INSTALL_DIR="/opt/vpsmon"
DATA_DIR="/var/lib/vpsmon"
PORT="${VPSMON_PORT:-9090}"

mkdir -p "$INSTALL_DIR" "$DATA_DIR"
cd "$INSTALL_DIR"

if [ ! -d "venv" ]; then
    info "Creating virtual environment..."; $PYTHON -m venv venv
fi
info "Installing dependencies..."
venv/bin/pip install --quiet --upgrade pip
venv/bin/pip install --quiet -r requirements.txt asyncssh 2>/dev/null || venv/bin/pip install --quiet aiohttp psutil docker bcrypt pywebpush asyncssh

apt-get install -y -qq sshpass 2>/dev/null || true

cat > /etc/systemd/system/vpsmon.service << UNIT
[Unit]
Description=VPSMon Server Monitoring
After=network.target docker.service
[Service]
Type=simple
User=root
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/venv/bin/python -m vpsmon.app
Environment=VPSMON_DATA_DIR=$DATA_DIR
Environment=VPSMON_PORT=$PORT
Restart=always
RestartSec=5
MemoryMax=256M
CPUQuota=25%
[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload && systemctl enable vpsmon && systemctl restart vpsmon

info "VPSMon installed and running!"
echo ""
echo -e "  Dashboard:  http://$(hostname -I | awk '{print $1}'):$PORT"
echo -e "  Login:      admin / admin  (change this immediately!)"
echo -e "  Status:     systemctl status vpsmon"
echo -e "  Logs:       journalctl -u vpsmon -f"
echo ""
