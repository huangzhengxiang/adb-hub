"""REST API blueprint — the core of ADB Hub."""

import os
import json
import tempfile
import logging
from functools import wraps

from flask import Blueprint, request, jsonify, send_file, current_app
from werkzeug.utils import secure_filename

from adb_utils.client import adb, ADBError
from adb_utils.parser import get_devices_with_details, parse_packages

logger = logging.getLogger(__name__)

api_bp = Blueprint("api_v1", __name__, url_prefix="/api/v1")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def api_response(success: bool, data=None, error: str | None = None, status: int = 200):
    """Build uniform JSON response."""
    body = {"success": success, "data": data, "error": error}
    return jsonify(body), status


def require_json(f):
    """Decorator that ensures request has JSON body."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not request.is_json:
            return api_response(False, error="Content-Type must be application/json", status=400)
        return f(*args, **kwargs)
    return wrapper


# ---------------------------------------------------------------------------
# Health / Info
# ---------------------------------------------------------------------------

@api_bp.route("/health")
def health():
    """Service health check."""
    result = adb.devices()
    adb_ok = True
    adb_version = ""
    if result.success:
        adb_version = result.stdout.split("\n")[0] if result.stdout else ""
    else:
        adb_ok = False
        adb_version = result.stderr

    return api_response(True, data={
        "status": "ok",
        "adb_available": adb_ok,
        "adb_version": adb_version,
    })


# ---------------------------------------------------------------------------
# Devices
# ---------------------------------------------------------------------------

@api_bp.route("/devices")
def list_devices():
    """List all connected devices with details."""
    try:
        devices = get_devices_with_details()
        return api_response(True, data={
            "devices": devices,
            "count": len(devices),
        })
    except Exception as e:
        return api_response(False, error=str(e), status=500)


@api_bp.route("/devices/<serial>")
def device_detail(serial: str):
    """Get details for a single device."""
    devices = get_devices_with_details()
    for d in devices:
        if d["serial"] == serial:
            return api_response(True, data=d)
    return api_response(False, error=f"Device '{serial}' not found", status=404)


# ---------------------------------------------------------------------------
# Shell
# ---------------------------------------------------------------------------

@api_bp.route("/devices/<serial>/shell", methods=["POST"])
@require_json
def device_shell(serial: str):
    """Execute a shell command on a device.

    Body: {"cmd": "ls -la /sdcard", "timeout": 30}
    """
    data = request.get_json()
    cmd = data.get("cmd", "")
    if not cmd:
        return api_response(False, error="Missing 'cmd' field", status=400)

    timeout = int(data.get("timeout", 30))
    try:
        result = adb.shell(serial, cmd, timeout=timeout)
        return api_response(result.success, data=result.to_dict())
    except ADBError as e:
        return api_response(False, error=str(e), status=500)


# ---------------------------------------------------------------------------
# Arbitrary adb command passthrough
# ---------------------------------------------------------------------------

@api_bp.route("/devices/<serial>/exec", methods=["POST"])
@require_json
def device_exec(serial: str):
    """Execute an arbitrary adb -s <serial> command.

    Body: {"args": ["push", "/local/file", "/sdcard/file"], "timeout": 60}
    """
    data = request.get_json()
    args = data.get("args", [])
    if not args or not isinstance(args, list):
        return api_response(False, error="Missing or invalid 'args' (must be a list)", status=400)

    timeout = int(data.get("timeout", 30))
    try:
        result = adb.exec_device(serial, args, timeout=timeout)
        return api_response(result.success, data=result.to_dict())
    except ADBError as e:
        return api_response(False, error=str(e), status=500)


@api_bp.route("/raw", methods=["POST"])
@require_json
def raw_adb():
    """Execute an arbitrary adb command (no -s prefix).

    Body: {"args": ["devices", "-l"], "timeout": 30}
    """
    data = request.get_json()
    args = data.get("args", [])
    if not args or not isinstance(args, list):
        return api_response(False, error="Missing or invalid 'args' (must be a list)", status=400)

    timeout = int(data.get("timeout", 30))
    try:
        result = adb.exec_global(args, timeout=timeout)
        return api_response(result.success, data=result.to_dict())
    except ADBError as e:
        return api_response(False, error=str(e), status=500)


# ---------------------------------------------------------------------------
# Packages
# ---------------------------------------------------------------------------

@api_bp.route("/devices/<serial>/packages")
def list_device_packages(serial: str):
    """List installed packages on a device.

    Query params:
        filter: '-3' (third-party), '-s' (system), '-d' (disabled), etc.
    """
    filt = request.args.get("filter", "")
    try:
        result = adb.list_packages(serial, filt=filt if filt else None)
        packages = parse_packages(result.stdout) if result.success else []
        return api_response(True, data={"packages": packages, "count": len(packages)})
    except ADBError as e:
        return api_response(False, error=str(e), status=500)


# ---------------------------------------------------------------------------
# APK Install / Uninstall
# ---------------------------------------------------------------------------

@api_bp.route("/devices/<serial>/install", methods=["POST"])
def device_install(serial: str):
    """Install an APK on a device.

    Multipart form: file=apk_file, or JSON: {"path": "/path/on/server/apk.apk"}
    Optional: opts=["-r", "-d"] for reinstall / downgrade.
    """
    opts = request.form.getlist("opts") or []

    if "file" in request.files:
        apk_file = request.files["file"]
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".apk")
        try:
            apk_file.save(tmp.name)
            tmp.close()
            result = adb.install(serial, tmp.name, opts=opts if opts else None)
            return api_response(result.success, data=result.to_dict())
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
    else:
        data = request.get_json(silent=True) or {}
        apk_path = data.get("path", "")
        if not apk_path or not os.path.isfile(apk_path):
            return api_response(False, error="Provide 'file' upload or valid server 'path'", status=400)
        try:
            result = adb.install(serial, apk_path, opts=opts if opts else None)
            return api_response(result.success, data=result.to_dict())
        except ADBError as e:
            return api_response(False, error=str(e), status=500)


@api_bp.route("/devices/<serial>/uninstall", methods=["POST"])
@require_json
def device_uninstall(serial: str):
    """Uninstall a package from a device.

    Body: {"package": "com.example.app"}
    """
    data = request.get_json()
    package = data.get("package", "")
    if not package:
        return api_response(False, error="Missing 'package' field", status=400)
    try:
        result = adb.uninstall(serial, package)
        return api_response(result.success, data=result.to_dict())
    except ADBError as e:
        return api_response(False, error=str(e), status=500)


# ---------------------------------------------------------------------------
# File Transfer
# ---------------------------------------------------------------------------

@api_bp.route("/devices/<serial>/push", methods=["POST"])
def device_push(serial: str):
    """Push a file to a device.

    Multipart form: file=<upload>, dest=/sdcard/file.txt
    """
    dest = request.form.get("dest", "")
    if not dest:
        return api_response(False, error="Missing 'dest' field", status=400)

    if "file" not in request.files:
        return api_response(False, error="Missing 'file' upload", status=400)

    f = request.files["file"]
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=f"_{secure_filename(f.filename)}")
    try:
        f.save(tmp.name)
        tmp.close()
        result = adb.push(serial, tmp.name, dest)
        return api_response(result.success, data=result.to_dict())
    except ADBError as e:
        return api_response(False, error=str(e), status=500)
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


@api_bp.route("/devices/<serial>/pull", methods=["POST"])
@require_json
def device_pull(serial: str):
    """Pull a file from a device. Returns the file as a download.

    Body: {"src": "/sdcard/file.txt"}
    """
    data = request.get_json()
    src = data.get("src", "")
    if not src:
        return api_response(False, error="Missing 'src' field", status=400)

    try:
        # pull to a temp file, then send
        tmp = tempfile.NamedTemporaryFile(delete=False)
        tmp.close()
        result = adb.pull(serial, src, tmp.name)
        if result.success:
            return send_file(tmp.name, as_attachment=True,
                             download_name=os.path.basename(src))
        else:
            os.unlink(tmp.name)
            return api_response(False, data=result.to_dict())
    except ADBError as e:
        return api_response(False, error=str(e), status=500)


# ---------------------------------------------------------------------------
# Screenshot
# ---------------------------------------------------------------------------

@api_bp.route("/devices/<serial>/screenshot")
def device_screenshot(serial: str):
    """Capture and return a screenshot from a device (PNG)."""
    try:
        png_data = adb.screenshot(serial)
        from io import BytesIO
        return send_file(BytesIO(png_data), mimetype="image/png")
    except ADBError as e:
        return api_response(False, error=str(e), status=500)


# ---------------------------------------------------------------------------
# Connectivity
# ---------------------------------------------------------------------------

@api_bp.route("/connect", methods=["POST"])
@require_json
def connect_device():
    """Connect to a device via TCP/IP.

    Body: {"address": "192.168.1.100:5555"}
    """
    data = request.get_json()
    address = data.get("address", "")
    if not address:
        return api_response(False, error="Missing 'address' field", status=400)
    try:
        result = adb.connect(address)
        return api_response(result.success, data=result.to_dict())
    except ADBError as e:
        return api_response(False, error=str(e), status=500)


@api_bp.route("/disconnect", methods=["POST"])
@require_json
def disconnect_device():
    """Disconnect a TCP/IP device.

    Body: {"address": "192.168.1.100:5555"}
    """
    data = request.get_json()
    address = data.get("address", "")
    if not address:
        return api_response(False, error="Missing 'address' field", status=400)
    try:
        result = adb.disconnect(address)
        return api_response(result.success, data=result.to_dict())
    except ADBError as e:
        return api_response(False, error=str(e), status=500)


@api_bp.route("/devices/<serial>/tcpip", methods=["POST"])
@require_json
def device_tcpip(serial: str):
    """Restart adbd on a device in TCP/IP mode.

    Body: {"port": 5555}
    """
    data = request.get_json()
    port = int(data.get("port", 5555))
    try:
        result = adb.tcpip(serial, port)
        return api_response(result.success, data=result.to_dict())
    except ADBError as e:
        return api_response(False, error=str(e), status=500)
