#!/usr/bin/env python3
"""Small Python client for ADB Hub.

No third-party dependencies are required. The client uses the same shared-key
protocol as the server: an encrypted token in X-ADB-Hub-Token and encrypted JSON
request bodies for control-plane APIs.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import secrets
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

TOKEN_PLAINTEXT = (
    "adb-hub-static-token-v1:"
    "yH3s6ZcQe9N0pVk2W8rD4mTb7LxFa1UjG5qPwS0nRcEiKzAoY6MdBhXl93TfQvJ"
)
ENVELOPE_VERSION = "adb-hub-enc-v1"
REQUIRED_SCP_ENV = {
    "host": "ADB_HUB_SCP_HOST",
    "port": "ADB_HUB_SCP_PORT",
    "password": "ADB_HUB_SCP_PASSWORD",
}


class ADBHubClientError(Exception):
    """Raised when a client request or crypto operation fails."""


def _load_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _default_env() -> dict[str, str]:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    values = _load_dotenv(env_path)
    values.update(os.environ)
    return values


def _default_secret() -> str:
    return _default_env().get("ADB_HUB_AUTH_SECRET", "")


def _default_base_url() -> str:
    env = _default_env()
    if env.get("ADB_HUB_URL"):
        return env["ADB_HUB_URL"]
    if env.get("ADB_HUB_PUBLIC_HOST"):
        return f"http://{env['ADB_HUB_PUBLIC_HOST']}:{env.get('ADB_HUB_PORT', '3588')}"
    return "http://127.0.0.1:3588"


def _default_scp() -> dict[str, str]:
    env = _default_env()
    return {
        "host": env.get("ADB_HUB_SCP_HOST", ""),
        "port": env.get("ADB_HUB_SCP_PORT", ""),
        "user": env.get("ADB_HUB_SCP_USER", ""),
        "password": env.get("ADB_HUB_SCP_PASSWORD", ""),
    }


def _session_scp_config() -> dict[str, str]:
    scp = _default_scp()
    missing = [env_name for key, env_name in REQUIRED_SCP_ENV.items() if not scp.get(key)]
    if missing:
        raise ADBHubClientError(
            "missing required SCP configuration: "
            + ", ".join(missing)
            + "; copy .env-internal to .env or set these variables for the current AutoDL host"
        )
    return scp


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _derive_key(secret: str) -> bytes:
    if not secret:
        raise ADBHubClientError("missing ADB_HUB_AUTH_SECRET; set it in the environment or .env")
    return hashlib.sha256(b"adb-hub-secret-v1\0" + secret.encode("utf-8")).digest()


def _keystream(key: bytes, nonce: bytes, size: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < size:
        out.extend(hmac.new(key, b"stream\0" + nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest())
        counter += 1
    return bytes(out[:size])


def _tag(key: bytes, aad: bytes, nonce: bytes, ciphertext: bytes) -> bytes:
    return hmac.new(key, b"tag\0" + aad + nonce + ciphertext, hashlib.sha256).digest()


def encrypt_bytes(plaintext: bytes, secret: str, aad: bytes) -> dict[str, str]:
    key = _derive_key(secret)
    nonce = secrets.token_bytes(16)
    stream = _keystream(key, nonce, len(plaintext))
    ciphertext = bytes(a ^ b for a, b in zip(plaintext, stream))
    return {
        "v": ENVELOPE_VERSION,
        "nonce": _b64e(nonce),
        "ciphertext": _b64e(ciphertext),
        "tag": _b64e(_tag(key, aad, nonce, ciphertext)),
    }


def encrypt_token(secret: str) -> str:
    envelope = encrypt_bytes(TOKEN_PLAINTEXT.encode("utf-8"), secret=secret, aad=b"token")
    return "v1.{nonce}.{ciphertext}.{tag}".format(**envelope)


def encrypt_json_payload(data: Any, secret: str) -> dict[str, str]:
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return encrypt_bytes(raw, secret=secret, aad=b"json-payload")


class ADBHubClient:
    def __init__(self, timeout: int = 30):
        self.base_url = _default_base_url().rstrip("/")
        self.secret = _default_secret()
        self.timeout = timeout

    def _headers(self, encrypted: bool = True) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.secret:
            headers["X-ADB-Hub-Token"] = encrypt_token(self.secret)
        if encrypted:
            headers["Content-Type"] = "application/json"
        return headers

    def request(self, method: str, path: str, data: Any | None = None, encrypted: bool = True) -> dict[str, Any]:
        url = self.base_url + path
        body = None
        headers = self._headers(encrypted=encrypted)
        if data is not None:
            payload = encrypt_json_payload(data, self.secret) if encrypted else data
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as parse_exc:
                raise ADBHubClientError(f"HTTP {exc.code}: {raw}") from parse_exc
            raise ADBHubClientError(f"HTTP {exc.code}: {parsed.get('error') or parsed}; data={parsed.get('data')}") from exc
        except urllib.error.URLError as exc:
            raise ADBHubClientError(str(exc)) from exc
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ADBHubClientError(f"non-JSON response: {raw[:200]}") from exc
        if not parsed.get("success", False):
            raise ADBHubClientError(f"{parsed.get('error') or 'request failed'}; data={parsed.get('data')}")
        return parsed

    def download_file(self, session_id: str, path: str, local_path: str | Path, encrypted: bool = True) -> Path:
        """Download a host-session file to the local remote-client filesystem."""
        url = self.base_url + f"/api/v1/sessions/{session_id}/download"
        payload = {"path": path}
        body_obj = encrypt_json_payload(payload, self.secret) if encrypted else payload
        body = json.dumps(body_obj, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers=self._headers(encrypted=encrypted), method="POST")
        dest = Path(local_path)
        if dest.is_dir():
            dest = dest / Path(path).name
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                dest.write_bytes(resp.read())
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as parse_exc:
                raise ADBHubClientError(f"HTTP {exc.code}: {raw}") from parse_exc
            raise ADBHubClientError(f"HTTP {exc.code}: {parsed.get('error') or parsed}; data={parsed.get('data')}") from exc
        except urllib.error.URLError as exc:
            raise ADBHubClientError(str(exc)) from exc
        return dest

    def health(self) -> dict[str, Any]:
        return self.request("GET", "/api/v1/health")

    def devices(self) -> dict[str, Any]:
        return self.request("GET", "/api/v1/devices")

    def create_session(self, serial: str, name: str = "") -> dict[str, Any]:
        payload: dict[str, Any] = {"serial": serial, "name": name}
        payload["scp"] = _session_scp_config()
        return self.request("POST", "/api/v1/sessions", payload)

    def list_sessions(self) -> dict[str, Any]:
        return self.request("GET", "/api/v1/sessions")

    def get_session(self, session_id: str) -> dict[str, Any]:
        return self.request("GET", f"/api/v1/sessions/{session_id}")

    def open_session(self, session_id: str) -> dict[str, Any]:
        return self.request("POST", f"/api/v1/sessions/{session_id}/open")

    def push(self, session_id: str, src: str, dest: str | None = None) -> dict[str, Any]:
        return self.request(
            "POST",
            f"/api/v1/sessions/{session_id}/push",
            {"src": src, "dest": dest or src},
        )

    def fetch(self, session_id: str, src: str, dest: str = "", recursive: bool = False, timeout: int = 600) -> dict[str, Any]:
        return self.request(
            "POST",
            f"/api/v1/sessions/{session_id}/fetch",
            {"src": src, "dest": dest, "recursive": recursive, "timeout": timeout},
        )

    def pull(self, session_id: str, src: str, dest: str = "") -> dict[str, Any]:
        return self.request("POST", f"/api/v1/sessions/{session_id}/pull", {"src": src, "dest": dest})

    def shell(self, session_id: str, cmd: str, timeout: int = 30) -> dict[str, Any]:
        return self.request("POST", f"/api/v1/sessions/{session_id}/shell", {"cmd": cmd, "timeout": timeout})

    def close_session(self, session_id: str) -> dict[str, Any]:
        return self.request("DELETE", f"/api/v1/sessions/{session_id}")


def _print_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ADB Hub encrypted client")
    parser.add_argument("--timeout", type=int, default=30)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("health")
    sub.add_parser("devices")
    sub.add_parser("sessions")

    create = sub.add_parser("create-session")
    create.add_argument("--serial", required=True)
    create.add_argument("--name", default="")

    get = sub.add_parser("get-session")
    get.add_argument("session_id")

    open_cmd = sub.add_parser("open-session")
    open_cmd.add_argument("session_id")

    push = sub.add_parser("push")
    push.add_argument("session_id")
    push.add_argument("src", help="Path relative to the host session workdir")
    push.add_argument("dest", nargs="?", help="Path relative to the device session workdir")

    fetch = sub.add_parser("fetch")
    fetch.add_argument("session_id")
    fetch.add_argument("src", help="Path on the configured remote client, used as scp source")
    fetch.add_argument("dest", nargs="?", default="", help="Path relative to the host session workdir")
    fetch.add_argument("--recursive", action="store_true")
    fetch.add_argument("--fetch-timeout", type=int, default=600)

    pull = sub.add_parser("pull")
    pull.add_argument("session_id")
    pull.add_argument("src", help="Path relative to the device session workdir")
    pull.add_argument("dest", nargs="?", default="", help="Path relative to the host session workdir")

    download = sub.add_parser("download")
    download.add_argument("session_id")
    download.add_argument("path", help="Path relative to the host session workdir")
    download.add_argument("local_path", help="Local output file or directory")

    shell = sub.add_parser("shell")
    shell.add_argument("session_id")
    shell.add_argument("cmd", nargs=argparse.REMAINDER)
    shell.add_argument("--shell-timeout", type=int, default=30)

    close = sub.add_parser("close-session")
    close.add_argument("session_id")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    client = ADBHubClient(timeout=args.timeout)
    try:
        if args.command == "health":
            result = client.health()
        elif args.command == "devices":
            result = client.devices()
        elif args.command == "sessions":
            result = client.list_sessions()
        elif args.command == "create-session":
            result = client.create_session(args.serial, args.name)
        elif args.command == "get-session":
            result = client.get_session(args.session_id)
        elif args.command == "open-session":
            result = client.open_session(args.session_id)
        elif args.command == "push":
            result = client.push(args.session_id, args.src, args.dest)
        elif args.command == "fetch":
            result = client.fetch(args.session_id, args.src, args.dest, recursive=args.recursive, timeout=args.fetch_timeout)
        elif args.command == "pull":
            result = client.pull(args.session_id, args.src, args.dest)
        elif args.command == "download":
            path = client.download_file(args.session_id, args.path, args.local_path)
            result = {"success": True, "data": {"local_path": str(path)}, "error": None}
        elif args.command == "shell":
            cmd_parts = args.cmd[1:] if args.cmd and args.cmd[0] == "--" else args.cmd
            if not cmd_parts:
                raise ADBHubClientError("shell command is required")
            result = client.shell(args.session_id, " ".join(cmd_parts), timeout=args.shell_timeout)
        elif args.command == "close-session":
            result = client.close_session(args.session_id)
        else:
            raise ADBHubClientError(f"unknown command: {args.command}")
    except ADBHubClientError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print_json(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
