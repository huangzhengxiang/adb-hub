"""ADB Hub configuration."""

import os
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    """Load simple KEY=VALUE entries without adding a dependency."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = value


BASE_DIR = Path(__file__).resolve().parent
_load_dotenv(BASE_DIR / ".env")


def _path_from_env(name: str, default: str) -> str:
    path = Path(os.environ.get(name, default)).expanduser()
    if not path.is_absolute():
        path = BASE_DIR / path
    return str(path)

# Server
HOST = os.environ.get("ADB_HUB_HOST", "0.0.0.0")
PORT = int(os.environ.get("ADB_HUB_PORT", "5000"))
DEBUG = os.environ.get("ADB_HUB_DEBUG", "false").lower() == "true"

# ADB binary (assumed in PATH)
ADB_PATH = os.environ.get("ADB_PATH", "adb")

# Limits
MAX_SHELL_OUTPUT_BYTES = 10 * 1024 * 1024  # 10 MB
MAX_UPLOAD_BYTES = 500 * 1024 * 1024  # 500 MB

# Auth / encrypted request payloads
ADB_HUB_AUTH_SECRET = os.environ.get("ADB_HUB_AUTH_SECRET", "")
ADB_HUB_AUTH_REQUIRED = os.environ.get("ADB_HUB_AUTH_REQUIRED", "true").lower() == "true"
ADB_HUB_REQUIRE_ENCRYPTED_PAYLOAD = (
    os.environ.get("ADB_HUB_REQUIRE_ENCRYPTED_PAYLOAD", "true").lower() == "true"
)

# Session workspaces on the host running adb-hub and on the Android device.
ADB_HUB_SESSION_ROOT = _path_from_env("ADB_HUB_SESSION_ROOT", "session_workdirs")
ADB_HUB_DEVICE_SESSION_ROOT = os.environ.get(
    "ADB_HUB_DEVICE_SESSION_ROOT",
    "/data/local/tmp/adb-hub",
)
ADB_HUB_SCP_HOST = os.environ.get("ADB_HUB_SCP_HOST", "")
ADB_HUB_SCP_USER = os.environ.get("ADB_HUB_SCP_USER", "")
