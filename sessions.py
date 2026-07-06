"""Session workdir management for ADB Hub."""

from __future__ import annotations

import json
import os
import shutil
import shlex
import secrets
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from adb_utils.client import ADBError, adb
from config import (
    ADB_HUB_DEVICE_SESSION_ROOT,
    ADB_HUB_SCP_HOST,
    ADB_HUB_SCP_PASSWORD,
    ADB_HUB_SCP_PORT,
    ADB_HUB_SCP_USER,
    ADB_HUB_SESSION_ROOT,
)


class SessionError(Exception):
    """Raised for invalid session lifecycle operations."""


@dataclass
class ADBSession:
    session_id: str
    serial: str
    name: str
    state: str
    host_workdir: str
    device_workdir: str
    created_at: str
    opened_at: str | None = None
    closed_at: str | None = None
    cleanup_errors: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["scp_source_configured"] = bool(ADB_HUB_SCP_HOST)
        return data


class SessionManager:
    def __init__(self, root: str = ADB_HUB_SESSION_ROOT, device_root: str = ADB_HUB_DEVICE_SESSION_ROOT):
        self.root = Path(root).expanduser().resolve()
        self.device_root = device_root.rstrip("/")
        self.sessions: dict[str, ADBSession] = {}
        self.serial_locks: dict[str, str] = {}
        self.root.mkdir(parents=True, exist_ok=True)
        self._load_existing()

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _new_id(self) -> str:
        return secrets.token_urlsafe(18).replace("-", "_")

    def _session_path(self, session_id: str) -> Path:
        path = (self.root / session_id).resolve()
        if not path.is_relative_to(self.root):
            raise SessionError("invalid session path")
        return path

    def _load_existing(self) -> None:
        """Recover sessions that still have metadata after process restart."""
        for meta_path in self.root.glob("*/session.json"):
            try:
                data = json.loads(meta_path.read_text(encoding="utf-8"))
                session = ADBSession(
                    session_id=data["session_id"],
                    serial=data["serial"],
                    name=data.get("name", ""),
                    state=data.get("state", "created"),
                    host_workdir=data["host_workdir"],
                    device_workdir=data["device_workdir"],
                    created_at=data["created_at"],
                    opened_at=data.get("opened_at"),
                    closed_at=data.get("closed_at"),
                    cleanup_errors=data.get("cleanup_errors", []),
                )
                if not Path(session.host_workdir).resolve().is_relative_to(self.root):
                    continue
                self.sessions[session.session_id] = session
                if session.state == "open" and session.serial not in self.serial_locks:
                    self.serial_locks[session.serial] = session.session_id
            except (KeyError, json.JSONDecodeError, OSError, TypeError):
                continue

    def _safe_host_path(self, session: ADBSession, rel_path: str) -> Path:
        if not rel_path or Path(rel_path).is_absolute():
            raise SessionError("host path must be relative to session workdir")
        base = Path(session.host_workdir).resolve()
        path = (base / rel_path).resolve()
        if not path.is_relative_to(base):
            raise SessionError("host path escapes session workdir")
        return path

    def _device_path(self, session: ADBSession, rel_path: str) -> str:
        if not rel_path or rel_path.startswith("/") or ".." in Path(rel_path).parts:
            raise SessionError("device path must be relative to session device workdir")
        return f"{session.device_workdir.rstrip('/')}/{rel_path}"

    def _write_meta(self, session: ADBSession) -> None:
        path = Path(session.host_workdir) / "session.json"
        path.write_text(json.dumps(session.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    def create(self, serial: str, name: str = "") -> ADBSession:
        if not serial:
            raise SessionError("serial is required")
        session_id = self._new_id()
        host_workdir = self._session_path(session_id)
        host_workdir.mkdir(parents=True, exist_ok=False)
        session = ADBSession(
            session_id=session_id,
            serial=serial,
            name=name,
            state="created",
            host_workdir=str(host_workdir),
            device_workdir=f"{self.device_root}/{session_id}",
            created_at=self._now(),
        )
        self.sessions[session_id] = session
        self._write_meta(session)
        return session

    def list(self) -> list[ADBSession]:
        return list(self.sessions.values())

    def get(self, session_id: str) -> ADBSession:
        session = self.sessions.get(session_id)
        if not session:
            raise SessionError("session not found")
        return session

    def open(self, session_id: str) -> ADBSession:
        session = self.get(session_id)
        owner = self.serial_locks.get(session.serial)
        if owner and owner != session_id:
            raise SessionError(f"device serial is already leased by session {owner}")
        Path(session.host_workdir).mkdir(parents=True, exist_ok=True)
        result = adb.shell(session.serial, f"mkdir -p {shlex.quote(session.device_workdir)}", timeout=30)
        if not result.success:
            raise SessionError(f"failed to create device workdir: {result.stderr or result.stdout}")
        session.state = "open"
        session.opened_at = self._now()
        self.serial_locks[session.serial] = session_id
        self._write_meta(session)
        return session

    def close(self, session_id: str, remove_device: bool = True) -> ADBSession:
        session = self.get(session_id)
        cleanup_errors: list[dict] = []
        session.state = "closed"
        session.closed_at = self._now()
        if remove_device:
            try:
                result = adb.shell(session.serial, f"rm -rf {shlex.quote(session.device_workdir)}", timeout=30)
                if not result.success:
                    cleanup_errors.append({
                        "stage": "device_cleanup",
                        "stdout": result.stdout,
                        "stderr": result.stderr,
                        "exit_code": result.exit_code,
                    })
            except ADBError as exc:
                cleanup_errors.append({"stage": "device_cleanup", "error": str(exc)})
        host_path = self._session_path(session_id)
        try:
            if host_path.exists():
                shutil.rmtree(host_path)
        except OSError as exc:
            cleanup_errors.append({"stage": "host_cleanup", "error": str(exc), "path": str(host_path)})
        if self.serial_locks.get(session.serial) == session_id:
            del self.serial_locks[session.serial]
        session.cleanup_errors = cleanup_errors
        if not cleanup_errors:
            self.sessions.pop(session_id, None)
        return session

    def push(self, session_id: str, src: str, dest: str):
        session = self.get(session_id)
        if session.state != "open":
            raise SessionError("session must be open before push")
        src_path = self._safe_host_path(session, src)
        if not src_path.is_file():
            raise SessionError("source file does not exist in session workdir")
        device_dest = self._device_path(session, dest)
        parent = str(Path(device_dest).parent)
        adb.shell(session.serial, f"mkdir -p {shlex.quote(parent)}", timeout=30)
        return adb.push(session.serial, str(src_path), device_dest)

    def pull(self, session_id: str, src: str, dest: str = "") -> dict:
        """Pull a file from the device session workdir into the host session workdir."""
        session = self.get(session_id)
        if session.state != "open":
            raise SessionError("session must be open before pull")
        device_src = self._device_path(session, src)
        if dest:
            host_dest = self._safe_host_path(session, dest)
        else:
            name = src.rstrip("/\\").replace("\\", "/").split("/")[-1]
            if not name or name in {".", ".."}:
                raise SessionError("dest is required when src has no basename")
            host_dest = self._safe_host_path(session, name)
        host_dest.parent.mkdir(parents=True, exist_ok=True)
        result = adb.pull(session.serial, device_src, str(host_dest))
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.exit_code,
            "success": result.success,
            "device_src": device_src,
            "host_dest": str(host_dest),
        }

    def download_path(self, session_id: str, rel_path: str) -> Path:
        """Resolve a host session file for HTTP download."""
        session = self.get(session_id)
        path = self._safe_host_path(session, rel_path)
        if not path.is_file():
            raise SessionError("file does not exist in session workdir")
        return path

    def _write_askpass(self, session: ADBSession) -> Path | None:
        if not ADB_HUB_SCP_PASSWORD:
            return None
        base = Path(session.host_workdir)
        if os.name == "nt":
            path = base / ".adb_hub_askpass.cmd"
            path.write_text(
                '@echo off\r\npowershell -NoProfile -Command "[Console]::Out.WriteLine($env:ADB_HUB_SCP_PASSWORD)"\r\n',
                encoding="utf-8",
            )
        else:
            path = base / ".adb_hub_askpass.sh"
            path.write_text("#!/bin/sh\nprintf '%s\\n' \"$ADB_HUB_SCP_PASSWORD\"\n", encoding="utf-8")
            path.chmod(0o700)
        return path

    def _scp_env(self, askpass: Path | None) -> dict:
        env = os.environ.copy()
        if askpass is not None:
            env["SSH_ASKPASS"] = str(askpass)
            env["SSH_ASKPASS_REQUIRE"] = "force"
            env["DISPLAY"] = env.get("DISPLAY", "adb-hub:0")
            env["ADB_HUB_SCP_PASSWORD"] = ADB_HUB_SCP_PASSWORD
        return env

    def fetch(self, session_id: str, src: str, dest: str = "", recursive: bool = False, timeout: int = 600) -> dict:
        """Download a file from the remote client into the host session workdir."""
        session = self.get(session_id)
        if not ADB_HUB_SCP_HOST:
            raise SessionError("ADB_HUB_SCP_HOST is required for session fetch")
        if not src or "\0" in src:
            raise SessionError("src is required")
        if dest:
            dest_path = self._safe_host_path(session, dest)
        else:
            name = src.rstrip("/\\").replace("\\", "/").split("/")[-1]
            if not name or name in {".", ".."}:
                raise SessionError("dest is required when src has no basename")
            dest_path = self._safe_host_path(session, name)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        remote_prefix = f"{ADB_HUB_SCP_USER}@" if ADB_HUB_SCP_USER else ""
        remote = f"{remote_prefix}{ADB_HUB_SCP_HOST}:{src}"
        cmd = ["scp", "-o", "BatchMode=no", "-o", "NumberOfPasswordPrompts=1", "-o", "StrictHostKeyChecking=accept-new"]
        if ADB_HUB_SCP_PORT:
            try:
                port = int(ADB_HUB_SCP_PORT)
            except ValueError as exc:
                raise SessionError("ADB_HUB_SCP_PORT must be an integer") from exc
            if port <= 0 or port > 65535:
                raise SessionError("ADB_HUB_SCP_PORT must be between 1 and 65535")
            cmd.extend(["-P", str(port)])
        if recursive:
            cmd.append("-r")
        cmd.extend([remote, str(dest_path)])
        askpass = self._write_askpass(session)
        try:
            proc = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout, env=self._scp_env(askpass))
        except subprocess.TimeoutExpired as exc:
            try:
                if askpass is not None:
                    askpass.unlink(missing_ok=True)
            except OSError:
                pass
            return {
                "stdout": exc.stdout or "",
                "stderr": exc.stderr or "",
                "exit_code": -1,
                "success": False,
                "remote": remote,
                "dest": str(dest_path),
                "timed_out": True,
                "password_auth": bool(ADB_HUB_SCP_PASSWORD),
            }
        try:
            if askpass is not None:
                askpass.unlink(missing_ok=True)
        except OSError:
            pass
        return {
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "exit_code": proc.returncode,
            "success": proc.returncode == 0,
            "remote": remote,
            "dest": str(dest_path),
            "timed_out": False,
            "password_auth": bool(ADB_HUB_SCP_PASSWORD),
        }

    def shell(self, session_id: str, command: str, timeout: int = 30):
        session = self.get(session_id)
        if session.state != "open":
            raise SessionError("session must be open before shell")
        if not command:
            raise SessionError("cmd is required")
        wrapped = f"cd {shlex.quote(session.device_workdir)} && {command}"
        return adb.shell(session.serial, wrapped, timeout=timeout)


session_manager = SessionManager()
