"""Configuration for MPV Subtitle Viewer Server"""

import os

# Server configuration
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8768
LOG_LEVEL = os.getenv("LOG_LEVEL", "info")

# WebSocket configuration
WS_MAX_CLIENTS = 100

# Server configuration
SHUTDOWN_DELAY_SECONDS = 0.5

# Session management
SESSION_TIMEOUT_SECONDS = int(os.getenv("SESSION_TIMEOUT_SECONDS", "900"))
INACTIVITY_SHUTDOWN_SECONDS = int(os.getenv("INACTIVITY_SHUTDOWN_SECONDS", "30"))

# File paths
STATIC_DIR_NAME = "static"
INDEX_HTML_NAME = "index.html"
