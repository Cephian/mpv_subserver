"""Configuration for MPV Subtitle Viewer Server"""

import os

# Server configuration
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8768
LOG_LEVEL = os.getenv("LOG_LEVEL", "info")

# WebSocket configuration
WS_HEARTBEAT_INTERVAL = 30  # seconds
WS_MAX_CLIENTS = 100

# Update configuration
TIME_UPDATE_THRESHOLD_MS = 100  # Only process updates > 100ms apart
SHUTDOWN_DELAY_SECONDS = 0.5

# Session management
SESSION_TIMEOUT_SECONDS = int(os.getenv("SESSION_TIMEOUT_SECONDS", "300"))  # 5 min
INACTIVITY_SHUTDOWN_SECONDS = int(os.getenv("INACTIVITY_SHUTDOWN_SECONDS", "30"))  # 30 sec
SESSION_CLEANUP_INTERVAL = 60  # Check for stale sessions every 60s
INACTIVITY_CHECK_INTERVAL = 10  # Check for global inactivity every 10s

# File paths
STATIC_DIR_NAME = "static"
INDEX_HTML_NAME = "index.html"
