"""Configuration for MPV Subtitle Viewer Server"""

import os

# Server configuration
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
LOG_LEVEL = os.getenv("LOG_LEVEL", "info")

# WebSocket configuration
WS_HEARTBEAT_INTERVAL = 30  # seconds
WS_MAX_CLIENTS = 100

# Update configuration
TIME_UPDATE_THRESHOLD_MS = 100  # Only process updates > 100ms apart
SHUTDOWN_DELAY_SECONDS = 0.5

# File paths
STATIC_DIR_NAME = "static"
INDEX_HTML_NAME = "index.html"
