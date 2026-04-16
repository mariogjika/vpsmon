#!/bin/bash
# VPSMon Agent Installer — Ubuntu, Debian, Rocky Linux, AlmaLinux
# Usage: curl -sSL https://get.vpsmon.io/agent | bash -s -- --token TOKEN --server URL --name NAME
set -euo pipefail

VERSION="1.0.0"
AGENT_DIR="/opt/vpsmon-agent"
AGENT_BIN="$AGENT_DIR/vpsmon-agent.py"
CONFIG_DIR="/etc/vpsmon"
LOG_FILE="/var/log/vpsmon-agent-install.log"

# Colors
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
info()  { echo -e "${GREEN}[OK]${NC} $1" | tee -a "$LOG_FILE"; }
warn()  { echo -e "${YELLOW}[!]${NC} $1" | tee -a "$LOG_FILE"; }
error() { echo -e "${RED}[ERR]${NC} $1" | tee -a "$LOG_FILE"; exit 1; }
step()  { echo -e "${BLUE}[>>]${NC} $1" | tee -a "$LOG_FILE"; }

# ==================== Parse args ====================
TOKEN=""
SERVER=""
NAME=""
TAGS=""
ACTION="install"

while [[ $# -gt 0 ]]; do
    case $1 in
        --token)       TOKEN="$2"; shift 2;;
        --server)      SERVER="$2"; shift 2;;
        --name)        NAME="$2"; shift 2;;
        --tags)        TAGS="$2"; shift 2;;
        --uninstall)   ACTION="uninstall"; shift;;
        --update)      ACTION="update"; shift;;
        --status)      ACTION="status"; shift;;
        --repair)      ACTION="repair"; shift;;
        --help|-h)
            echo "VPSMon Agent Installer v$VERSION"
            echo ""
            echo "Usage:"
            echo "  install:   $0 --token TOKEN --server URL [--name NAME] [--tags TAGS]"
            echo "  update:    $0 --update"
            echo "  uninstall: $0 --uninstall"
            echo "  status:    $0 --status"
            echo "  repair:    $0 --repair"
            exit 0;;
        *) error "Unknown argument: $1";;
    esac
done

# ==================== Detect OS ====================
detect_os() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        OS_ID="${ID:-unknown}"
        OS_VERSION="${VERSION_ID:-unknown}"
        OS_NAME="${PRETTY_NAME:-unknown}"
    elif [ -f /etc/redhat-release ]; then
        OS_ID="rhel"
        OS_VERSION=$(cat /etc/redhat-release | grep -oP '\d+\.\d+')
        OS_NAME=$(cat /etc/redhat-release)
    else
        OS_ID="unknown"
        OS_VERSION="unknown"
        OS_NAME="Unknown Linux"
    fi

    case "$OS_ID" in
        ubuntu|debian)
            PKG_MGR="apt-get"
            PKG_INSTALL="apt-get install -y -qq"
            ;;
        rocky|almalinux|rhel|centos|fedora)
            PKG_MGR="dnf"
            PKG_INSTALL="dnf install -y -q"
            if ! command -v dnf &>/dev/null; then
                PKG_MGR="yum"
                PKG_INSTALL="yum install -y -q"
            fi
            ;;
        *)
            warn "Unsupported distro: $OS_ID. Will try to continue..."
            PKG_MGR="apt-get"
            PKG_INSTALL="apt-get install -y -qq"
            ;;
    esac

    info "Detected: $OS_NAME ($OS_ID $OS_VERSION)"
}

# ==================== Dependency checks ====================
check_deps() {
    step "Checking dependencies..."
    local missing=()

    # Python 3
    PYTHON=""
    for cmd in python3.12 python3.11 python3.10 python3.9 python3; do
        if command -v $cmd &>/dev/null; then
            if $cmd -c "import sys; exit(0 if sys.version_info >= (3,8) else 1)" 2>/dev/null; then
                PYTHON=$cmd
                break
            fi
        fi
    done
    if [ -z "$PYTHON" ]; then
        warn "Python 3.8+ not found — installing..."
        $PKG_INSTALL python3 2>&1 | tail -2
        PYTHON=python3
    fi
    info "Python: $($PYTHON --version 2>&1)"

    # curl
    command -v curl &>/dev/null || { $PKG_INSTALL curl 2>&1 | tail -1; }

    # systemd
    if ! command -v systemctl &>/dev/null; then
        error "systemd not found — this installer requires systemd"
    fi

    # Network access
    if ! curl -sf --connect-timeout 5 "$SERVER/api/health" >/dev/null 2>&1; then
        warn "Cannot reach $SERVER — check URL and network"
    else
        info "Server reachable: $SERVER"
    fi

    info "All dependencies satisfied"
}

# ==================== Install ====================
do_install() {
    [ "$EUID" -eq 0 ] || error "Please run as root (sudo)"
    [ -n "$TOKEN" ] || error "--token required"
    [ -n "$SERVER" ] || error "--server required"

    echo "" > "$LOG_FILE"
    echo ""
    echo -e "${BLUE}  VPSMon Agent Installer v${VERSION}${NC}"
    echo "  ================================"
    echo ""

    detect_os
    check_deps

    # Create directories
    step "Installing agent..."
    mkdir -p "$AGENT_DIR" "$CONFIG_DIR"

    # Download agent script (or copy from local)
    if curl -sfL "$SERVER/static/agent/vpsmon-agent.py" -o "$AGENT_BIN" 2>/dev/null; then
        info "Downloaded agent from server"
    elif [ -f "$(dirname "$0")/vpsmon-agent.py" ]; then
        cp "$(dirname "$0")/vpsmon-agent.py" "$AGENT_BIN"
        info "Copied agent from local directory"
    else
        error "Cannot download agent — check server URL"
    fi
    chmod +x "$AGENT_BIN"

    # Enroll
    step "Enrolling with server..."
    ENROLL_ARGS="--enroll --server $SERVER --enroll-token $TOKEN"
    [ -n "$NAME" ] && ENROLL_ARGS="$ENROLL_ARGS --name $NAME"
    [ -n "$TAGS" ] && ENROLL_ARGS="$ENROLL_ARGS --tags $TAGS"
    $PYTHON "$AGENT_BIN" $ENROLL_ARGS 2>&1 | tee -a "$LOG_FILE"
    if [ $? -ne 0 ]; then
        error "Enrollment failed — check token and server URL"
    fi
    info "Enrollment successful"

    # Create systemd service
    step "Creating systemd service..."
    cat > /etc/systemd/system/vpsmon-agent.service << UNIT
[Unit]
Description=VPSMon Monitoring Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=$PYTHON $AGENT_BIN
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
MemoryMax=64M
CPUQuota=10%

[Install]
WantedBy=multi-user.target
UNIT

    systemctl daemon-reload
    systemctl enable vpsmon-agent
    systemctl start vpsmon-agent

    sleep 3

    # Validate
    step "Validating installation..."
    if systemctl is-active vpsmon-agent &>/dev/null; then
        info "Service is running"
    else
        error "Service failed to start — check: journalctl -u vpsmon-agent -n 20"
    fi

    echo ""
    echo -e "  ${GREEN}VPSMon Agent installed successfully!${NC}"
    echo ""
    echo -e "  Agent ID:     $(grep -o '"agent_id": "[^"]*"' $CONFIG_DIR/agent.json 2>/dev/null | head -1 | cut -d'"' -f4)"
    echo -e "  Config:       $CONFIG_DIR/agent.json"
    echo -e "  Service:      systemctl status vpsmon-agent"
    echo -e "  Logs:         journalctl -u vpsmon-agent -f"
    echo -e "  Install log:  $LOG_FILE"
    echo ""
}

# ==================== Uninstall ====================
do_uninstall() {
    [ "$EUID" -eq 0 ] || error "Please run as root (sudo)"
    step "Uninstalling VPSMon Agent..."
    systemctl stop vpsmon-agent 2>/dev/null || true
    systemctl disable vpsmon-agent 2>/dev/null || true
    rm -f /etc/systemd/system/vpsmon-agent.service
    systemctl daemon-reload
    rm -rf "$AGENT_DIR"
    rm -rf "$CONFIG_DIR"
    rm -f "$LOG_FILE"
    info "VPSMon Agent uninstalled"
}

# ==================== Update ====================
do_update() {
    [ "$EUID" -eq 0 ] || error "Please run as root (sudo)"
    local cfg="$CONFIG_DIR/agent.json"
    [ -f "$cfg" ] || error "Agent not installed — run install first"
    SERVER=$(grep -o '"server": "[^"]*"' "$cfg" | head -1 | cut -d'"' -f4)
    [ -n "$SERVER" ] || error "Cannot determine server URL from config"

    step "Updating agent..."
    if curl -sfL "$SERVER/static/agent/vpsmon-agent.py" -o "$AGENT_BIN.new" 2>/dev/null; then
        mv "$AGENT_BIN.new" "$AGENT_BIN"
        chmod +x "$AGENT_BIN"
        systemctl restart vpsmon-agent
        info "Agent updated and restarted"
    else
        error "Download failed"
    fi
}

# ==================== Status ====================
do_status() {
    echo "VPSMon Agent Status"
    echo "==================="
    if [ -f "$CONFIG_DIR/agent.json" ]; then
        echo "Config: $CONFIG_DIR/agent.json"
        echo "Agent ID: $(grep -o '"agent_id": "[^"]*"' $CONFIG_DIR/agent.json 2>/dev/null | head -1 | cut -d'"' -f4)"
        echo "Server: $(grep -o '"server": "[^"]*"' $CONFIG_DIR/agent.json 2>/dev/null | head -1 | cut -d'"' -f4)"
    else
        echo "Not installed"
        return
    fi
    echo ""
    systemctl status vpsmon-agent --no-pager 2>&1 | head -15
}

# ==================== Main ====================
case "$ACTION" in
    install)   do_install;;
    uninstall) do_uninstall;;
    update)    do_update;;
    status)    do_status;;
    repair)    do_uninstall; do_install;;
    *)         error "Unknown action: $ACTION";;
esac
