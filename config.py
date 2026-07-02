"""ADB Hub configuration."""

import os

# Server
HOST = os.environ.get("ADB_HUB_HOST", "0.0.0.0")
PORT = int(os.environ.get("ADB_HUB_PORT", "5000"))
DEBUG = os.environ.get("ADB_HUB_DEBUG", "false").lower() == "true"

# ADB binary (assumed in PATH)
ADB_PATH = os.environ.get("ADB_PATH", "adb")

# Limits
MAX_SHELL_OUTPUT_BYTES = 10 * 1024 * 1024  # 10 MB
MAX_UPLOAD_BYTES = 500 * 1024 * 1024  # 500 MB
