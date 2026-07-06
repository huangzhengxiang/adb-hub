#!/usr/bin/env python3
"""Single-session ADB Hub tool for agents.

Agents may call ADB Hub operations step by step, but each ledger owns exactly
one session lifecycle: `start` creates it, all operations reuse it implicitly,
and `finish` closes it. `cleanup` is a defensive finalizer for interrupted runs.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import time
from pathlib import Path
from typing import Any

CLIENT_PATH = Path(__file__).resolve().with_name("adb_hub_client.py")
spec = importlib.util.spec_from_file_location("adb_hub_client", CLIENT_PATH)
adb_hub_client = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(adb_hub_client)

ADBHubClient = adb_hub_client.ADBHubClient
ADBHubClientError = adb_hub_client.ADBHubClientError


def _now() -> float:
    return time.time()


def _default_ledger() -> Path:
    value = os.environ.get("ADB_HUB_SESSION_LEDGER")
    if value:
        return Path(value)
    return Path.cwd() / "adb_hub_session_ledger.json"


def _new_ledger() -> dict[str, Any]:
    return {"version": 2, "created_at": _now(), "updated_at": _now(), "session": None, "events": []}


def _read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_ledger(path: Path) -> dict[str, Any]:
    data = _read_json(path, _new_ledger())
    if not isinstance(data, dict):
        data = _new_ledger()
    if data.get("version") != 2:
        # Older multi-session ledgers are intentionally not accepted in the
        # single-session tool; cleanup them manually with an older checkout.
        raise ADBHubClientError("ledger version is not single-session v2; use a fresh ledger path")
    data.setdefault("created_at", _now())
    data.setdefault("updated_at", _now())
    data.setdefault("session", None)
    data.setdefault("events", [])
    return data


def _save_ledger(path: Path, ledger: dict[str, Any]) -> None:
    ledger["updated_at"] = _now()
    _write_json(path, ledger)


def _resp_data(resp: dict[str, Any]) -> dict[str, Any]:
    data = resp.get("data", resp)
    return data if isinstance(data, dict) else {"value": data}


def _session_id_from_create(data: dict[str, Any]) -> str | None:
    return data.get("session_id") or data.get("session", {}).get("session_id")


def _pick_serial(client: ADBHubClient, requested: str | None) -> tuple[str, list[dict[str, Any]]]:
    resp = client.devices()
    devices = resp.get("data", {}).get("devices", [])
    if requested:
        for device in devices:
            if device.get("serial") == requested:
                if device.get("state") != "device":
                    raise ADBHubClientError(f"requested device is not online: {requested} state={device.get('state')}")
                return requested, devices
        raise ADBHubClientError(f"requested device serial not found: {requested}")
    online = [device for device in devices if device.get("state") == "device"]
    if not online:
        raise ADBHubClientError("no online adb device reported by adb-hub")
    return online[0]["serial"], devices


def _session_state(ledger: dict[str, Any]) -> str | None:
    session = ledger.get("session")
    return session.get("state") if isinstance(session, dict) else None


def _session_id(ledger: dict[str, Any]) -> str | None:
    session = ledger.get("session")
    return session.get("session_id") if isinstance(session, dict) else None


def _active_session_id(path: Path) -> str:
    ledger = _load_ledger(path)
    sid = _session_id(ledger)
    state = _session_state(ledger)
    if not sid or state == "closed":
        raise ADBHubClientError("no active session in ledger; run start first")
    return sid


def _record_event(path: Path, event: str, data: dict[str, Any] | None = None) -> None:
    ledger = _load_ledger(path)
    ledger.setdefault("events", []).append({"time": _now(), "event": event, "data": data or {}})
    if event == "open" and isinstance(ledger.get("session"), dict):
        ledger["session"]["state"] = "open"
    elif event in {"finish", "cleanup", "closed"} and isinstance(ledger.get("session"), dict):
        ledger["session"]["state"] = "closed"
    if data is not None and isinstance(ledger.get("session"), dict):
        ledger["session"]["last_data"] = data
    _save_ledger(path, ledger)


def _record_start(path: Path, data: dict[str, Any], serial: str, name: str) -> str:
    sid = _session_id_from_create(data)
    if not sid:
        raise ADBHubClientError("create-session did not return session_id")
    ledger = _new_ledger()
    ledger["session"] = {
        "session_id": sid,
        "state": data.get("state", "created"),
        "serial": data.get("serial", serial),
        "name": data.get("name", name),
        "created_at": _now(),
        "last_data": data,
    }
    ledger["events"].append({"time": _now(), "event": "start", "data": data})
    _save_ledger(path, ledger)
    return sid


def _client(args: argparse.Namespace) -> ADBHubClient:
    return ADBHubClient(
        args.base_url or adb_hub_client._default_base_url(),
        secret=args.secret if args.secret is not None else adb_hub_client._default_secret(),
        timeout=args.timeout,
    )


def _print(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _run_close(client: ADBHubClient, ledger_path: Path, event: str) -> dict[str, Any]:
    ledger = _load_ledger(ledger_path)
    sid = _session_id(ledger)
    state = _session_state(ledger)
    if not sid:
        return {"success": True, "data": {"closed": False, "reason": "no session in ledger"}, "error": None}
    if state == "closed":
        return {"success": True, "data": {"closed": False, "reason": "session already closed", "session_id": sid}, "error": None}
    resp = client.close_session(sid)
    data = _resp_data(resp)
    _record_event(ledger_path, event, data)
    return resp


def run_command(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    client = _client(args)
    ledger_path = Path(args.ledger).resolve()
    result: dict[str, Any] = {"command": args.command, "ledger": str(ledger_path), "base_url": client.base_url, "started_at": _now()}
    try:
        if args.command == "health":
            result["response"] = client.health()
        elif args.command == "devices":
            result["response"] = client.devices()
        elif args.command == "sessions":
            result["response"] = client.list_sessions()
        elif args.command == "start":
            if ledger_path.exists():
                ledger = _load_ledger(ledger_path)
                sid = _session_id(ledger)
                state = _session_state(ledger)
                if sid and state != "closed":
                    raise ADBHubClientError(f"ledger already has active session {sid}; run finish or cleanup before start")
                if sid and state == "closed" and not args.force:
                    raise ADBHubClientError("ledger already contains a closed session; pass --force to overwrite or use a new ledger")
            serial, devices = _pick_serial(client, args.serial)
            result["selected_serial"] = serial
            result["devices"] = devices
            resp = client.create_session(serial, args.name)
            data = _resp_data(resp)
            sid = _record_start(ledger_path, data, serial, args.name)
            result["session_id"] = sid
            result["response"] = resp
        elif args.command == "get-session":
            sid = _active_session_id(ledger_path)
            result["session_id"] = sid
            result["response"] = client.get_session(sid)
        elif args.command == "open-session":
            sid = _active_session_id(ledger_path)
            resp = client.open_session(sid)
            _record_event(ledger_path, "open", _resp_data(resp))
            result["session_id"] = sid
            result["response"] = resp
        elif args.command == "fetch":
            sid = _active_session_id(ledger_path)
            resp = client.fetch(sid, args.src, args.dest, recursive=args.recursive, timeout=args.fetch_timeout)
            _record_event(ledger_path, "fetch", _resp_data(resp))
            result["session_id"] = sid
            result["response"] = resp
        elif args.command == "push":
            sid = _active_session_id(ledger_path)
            resp = client.push(sid, args.src, args.dest)
            _record_event(ledger_path, "push", _resp_data(resp))
            result["session_id"] = sid
            result["response"] = resp
        elif args.command == "shell":
            sid = _active_session_id(ledger_path)
            cmd_parts = args.cmd[1:] if args.cmd and args.cmd[0] == "--" else args.cmd
            if not cmd_parts:
                raise ADBHubClientError("shell command is required")
            resp = client.shell(sid, " ".join(cmd_parts), timeout=args.shell_timeout)
            _record_event(ledger_path, "shell", _resp_data(resp))
            result["session_id"] = sid
            result["response"] = resp
        elif args.command == "pull":
            sid = _active_session_id(ledger_path)
            resp = client.pull(sid, args.src, args.dest)
            _record_event(ledger_path, "pull", _resp_data(resp))
            result["session_id"] = sid
            result["response"] = resp
        elif args.command == "download":
            sid = _active_session_id(ledger_path)
            local = client.download_file(sid, args.path, args.local_path)
            data = {"local_path": str(local)}
            _record_event(ledger_path, "download", data)
            result["session_id"] = sid
            result["response"] = {"success": True, "data": data, "error": None}
        elif args.command == "finish":
            resp = _run_close(client, ledger_path, "finish")
            result["response"] = resp
            if args.remove_ledger and resp.get("success", False):
                ledger_path.unlink(missing_ok=True)
                result["ledger_removed"] = True
        elif args.command == "cleanup":
            resp = _run_close(client, ledger_path, "cleanup")
            result["response"] = resp
            if args.remove_ledger and resp.get("success", False):
                ledger_path.unlink(missing_ok=True)
                result["ledger_removed"] = True
        else:
            raise ADBHubClientError(f"unknown command: {args.command}")
        result["success"] = bool(result.get("response", {}).get("success", True))
        result["finished_at"] = _now()
        return (0 if result["success"] else 1), result
    except Exception as exc:
        result.update({"success": False, "exception": type(exc).__name__, "error": str(exc), "finished_at": _now()})
        return 1, result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Single-session ADB Hub tool for agents")
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--secret", default=None)
    parser.add_argument("--timeout", type=int, default=300, help="HTTP request timeout seconds")
    parser.add_argument("--ledger", default=str(_default_ledger()), help="Single-session ledger JSON path")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("health")
    sub.add_parser("devices")
    sub.add_parser("sessions")

    start = sub.add_parser("start", help="Create the single session for this ledger")
    start.add_argument("--serial", default="", help="Device serial; defaults to the first online device")
    start.add_argument("--name", default="")
    start.add_argument("--force", action="store_true", help="Overwrite a closed ledger with a new session")

    sub.add_parser("get-session")
    sub.add_parser("open-session")

    fetch = sub.add_parser("fetch")
    fetch.add_argument("src")
    fetch.add_argument("dest", nargs="?", default="")
    fetch.add_argument("--recursive", action="store_true")
    fetch.add_argument("--fetch-timeout", type=int, default=600)

    push = sub.add_parser("push")
    push.add_argument("src")
    push.add_argument("dest", nargs="?")

    shell = sub.add_parser("shell")
    shell.add_argument("cmd", nargs=argparse.REMAINDER)
    shell.add_argument("--shell-timeout", type=int, default=300)

    pull = sub.add_parser("pull")
    pull.add_argument("src")
    pull.add_argument("dest", nargs="?", default="")

    download = sub.add_parser("download")
    download.add_argument("path")
    download.add_argument("local_path")

    finish = sub.add_parser("finish", help="Close the single session")
    finish.add_argument("--remove-ledger", action="store_true")

    cleanup = sub.add_parser("cleanup", help="Defensively close the single session if still active")
    cleanup.add_argument("--remove-ledger", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    code, result = run_command(args)
    _print(result)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
