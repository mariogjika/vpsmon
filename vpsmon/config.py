import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("VPSMON_DATA_DIR", "/var/lib/vpsmon"))
STATIC_DIR = BASE_DIR / "static"

# Server
HOST = os.environ.get("VPSMON_HOST", "0.0.0.0")
PORT = int(os.environ.get("VPSMON_PORT", "9090"))

# Database
DB_PATH = DATA_DIR / "vpsmon.db"

# Collection intervals (seconds)
SYSTEM_INTERVAL = 5
DOCKER_INTERVAL = 5
PROCESS_INTERVAL = 10
NETWORK_INTERVAL = 10

# Retention (seconds)
RAW_RETENTION = 24 * 3600        # 24 hours
ONE_MIN_RETENTION = 7 * 86400    # 7 days
FIVE_MIN_RETENTION = 30 * 86400  # 30 days
# 1h tier: never auto-deleted

# Downsampling schedule (seconds)
DOWNSAMPLE_1M_INTERVAL = 60
DOWNSAMPLE_5M_INTERVAL = 300
DOWNSAMPLE_1H_INTERVAL = 3600
PURGE_INTERVAL = 600             # 10 minutes

# Auth
SESSION_SECRET = os.environ.get("VPSMON_SECRET", secrets.token_hex(32))
SESSION_MAX_AGE = 24 * 3600     # 24 hours
LOGIN_RATE_LIMIT = 5            # attempts per window
LOGIN_RATE_WINDOW = 300         # 5 minutes

# Logs
LOG_BUFFER_SIZE = 1000
LOG_RATE_LIMIT = 50             # lines/sec per ws client

# WebSocket push intervals (seconds)
WS_SYSTEM_INTERVAL = 2
WS_DOCKER_INTERVAL = 3
WS_PROCESS_INTERVAL = 5
WS_NETWORK_INTERVAL = 5

# Alerting
ALERT_CPU_THRESHOLD = float(os.environ.get("VPSMON_ALERT_CPU", "90"))
ALERT_MEM_THRESHOLD = float(os.environ.get("VPSMON_ALERT_MEM", "90"))
ALERT_DISK_THRESHOLD = float(os.environ.get("VPSMON_ALERT_DISK", "85"))
ALERT_CHECK_INTERVAL = 30

# API
API_RATE_LIMIT = 100  # requests per minute per IP
CORS_ORIGINS = os.environ.get("VPSMON_CORS", "*")

# Export
METRICS_EXPORT_FORMATS = ["json", "csv"]
