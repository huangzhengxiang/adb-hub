#!/usr/bin/env python3
"""Manifest-driven ADB Hub session runner for evaluation agents.

The runner is intentionally small and dependency-free. Agents should generate a
JSON plan, then call this script instead of open-coding the ADB Hub workflow.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
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


def _duration(start: float) -> float:
    return round(_now() - start, 3)


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("plan root must be a JSON object")
    return data


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _resolve_remote_src(value: str, plan_dir: Path, cwd: Path) -> str:
    path = Path(value)
    if path.is_absolute():
        return str(path)
    base = plan_dir if (plan_dir / path).exists() else cwd
    return str((base / path).resolve())


def _command_ok(data: dict[str, Any]) -> bool:
    if data.get("success") is False:
        return False
    exit_code = data.get("exit_code")
    return exit_code in (None, 0)


def _response_data(resp: dict[str, Any]) -> dict[str, Any]:
    data = resp.get("data", resp)
    return data if isinstance(data, dict) else {"value": data}


def _pick_serial(devices_resp: dict[str, Any], requested: str | None) -> str:
    devices = devices_resp.get("data", {}).get("devices", [])
    if requested:
        for device in devices:
            if device.get("serial") == requested:
                return requested
        raise RuntimeError(f"requested device serial not found: {requested}")
    online = [device for device in devices if device.get("state") == "device"]
    if not online:
        raise RuntimeError("no online adb device reported by adb-hub")
    return online[0]["serial"]


def _run_step(report: dict[str, Any], name: str, fn) -> dict[str, Any]:
    start = _now()
    try:
        resp = fn()
        data = _response_data(resp)
        item = {
            "step": name,
            "elapsed_s": _duration(start),
            "success": resp.get("success", True),
            "data": data,
        }
        if not _command_ok(data):
            item["success"] = False
        report.setdefault("steps", []).append(item)
        if not item["success"]:
            raise RuntimeError(f"step failed: {name}")
        return data
    except Exception as exc:
        item = {
            "step": name,
            "elapsed_s": _duration(start),
            "success": False,
            "exception": type(exc).__name__,
            "error": str(exc),
        }
        data = getattr(exc, "data", None)
        if data is not None:
            item["error_data"] = data
        report.setdefault("steps", []).append(item)
        raise



def _action_kind(action: dict[str, Any]) -> str:
    return str(action.get("type") or action.get("kind") or "").replace("_", "-").lower()


def _action_value(action: dict[str, Any], *names: str, default: Any = None, required: bool = True) -> Any:
    for name in names:
        value = action.get(name)
        if value not in (None, ""):
            return value
    if required:
        kind = action.get("type") or action.get("kind") or "<missing type>"
        raise ValueError(f"action {kind} missing one of: {', '.join(names)}")
    return default


def _extract_actions(plan: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None] | None:
    if isinstance(plan.get("actions"), list):
        return list(plan["actions"]), None
    sessions = plan.get("sessions")
    if sessions is None:
        return None
    if not isinstance(sessions, list):
        raise ValueError("sessions must be a list when using action schema")
    if len(sessions) != 1:
        raise ValueError("action schema supports exactly one session")
    session = sessions[0]
    if not isinstance(session, dict):
        raise ValueError("session entry must be a JSON object")
    actions = session.get("actions")
    if not isinstance(actions, list):
        raise ValueError("session.actions must be a list")
    session_name = session.get("id") or session.get("name")
    return list(actions), str(session_name) if session_name else None


def _normalize_plan(plan: dict[str, Any]) -> tuple[dict[str, Any], str]:
    extracted = _extract_actions(plan)
    if extracted is None:
        return dict(plan), "flat"

    actions, session_name = extracted
    normalized = {
        key: value
        for key, value in plan.items()
        if key not in {"actions", "sessions", "fetch", "push", "shell", "pull", "download"}
    }
    if session_name and not normalized.get("name"):
        normalized["name"] = session_name
    normalized.setdefault("fetch", [])
    normalized.setdefault("push", [])
    normalized.setdefault("shell", [])
    normalized.setdefault("pull", [])
    normalized.setdefault("download", [])

    for index, action in enumerate(actions):
        if not isinstance(action, dict):
            raise ValueError(f"action[{index}] must be a JSON object")
        kind = _action_kind(action)
        if not kind:
            raise ValueError(f"action[{index}] missing type")

        if kind == "fetch":
            item = {
                "src": _action_value(action, "src", "source"),
                "dest": _action_value(action, "dest", "destination", default="", required=False),
            }
            if "timeout" in action or "timeout_seconds" in action:
                item["timeout"] = int(_action_value(action, "timeout", "timeout_seconds"))
            if action.get("recursive") is not None:
                item["recursive"] = bool(action.get("recursive"))
            normalized["fetch"].append(item)
        elif kind == "open":
            normalized["open"] = True
        elif kind == "push":
            src = _action_value(action, "src", "source")
            item = {
                "src": src,
                "dest": _action_value(action, "dest", "destination", default=src, required=False),
            }
            normalized["push"].append(item)
        elif kind == "shell":
            item = {"cmd": _action_value(action, "cmd", "command")}
            if "timeout" in action or "timeout_seconds" in action:
                item["timeout"] = int(_action_value(action, "timeout", "timeout_seconds"))
            normalized["shell"].append(item)
        elif kind == "pull":
            normalized["pull"].append({
                "src": _action_value(action, "src", "source"),
                "dest": _action_value(action, "dest", "destination", default="", required=False),
            })
        elif kind == "download":
            normalized["download"].append({
                "src": _action_value(action, "src", "source"),
                "dest": _action_value(action, "dest", "destination"),
            })
        elif kind == "close":
            normalized["close"] = True
        else:
            raise ValueError(f"unsupported action type: {kind}")

    return normalized, "actions"


def run_plan(plan: dict[str, Any], *, plan_dir: Path, output_path: Path | None, request_timeout: int, keep_session: bool) -> dict[str, Any]:
    plan, plan_schema = _normalize_plan(plan)
    client = ADBHubClient(timeout=request_timeout)
    cwd = Path.cwd()
    report: dict[str, Any] = {
        "plan_name": plan.get("name", "adb-hub-agent-plan"),
        "base_url": client.base_url,
        "started_at": _now(),
        "steps": [],
        "plan_schema": plan_schema,
    }
    session_id: str | None = None
    try:
        health = _run_step(report, "health", client.health)
        report["health"] = health
        devices_resp = client.devices()
        report["devices"] = devices_resp.get("data", {}).get("devices", [])
        serial = _pick_serial(devices_resp, plan.get("serial"))
        report["serial"] = serial
        created = _run_step(report, "create-session", lambda: client.create_session(serial, plan.get("name", "adb-hub-agent-plan")))
        session_id = created.get("session_id") or created.get("session", {}).get("session_id")
        if not session_id:
            raise RuntimeError("create-session did not return session_id")
        report["session_id"] = session_id

        for i, spec in enumerate(plan.get("fetch", [])):
            src = _resolve_remote_src(spec["src"], plan_dir, cwd)
            dest = spec.get("dest", "")
            timeout = int(spec.get("timeout", plan.get("fetch_timeout", 600)))
            recursive = bool(spec.get("recursive", False))
            _run_step(report, f"fetch[{i}]:{dest or Path(src).name}", lambda src=src, dest=dest, recursive=recursive, timeout=timeout: client.fetch(session_id, src, dest, recursive=recursive, timeout=timeout))

        if plan.get("open", True):
            _run_step(report, "open-session", lambda: client.open_session(session_id))

        for i, spec in enumerate(plan.get("push", [])):
            src = spec["src"]
            dest = spec.get("dest", src)
            _run_step(report, f"push[{i}]:{dest}", lambda src=src, dest=dest: client.push(session_id, src, dest))

        for i, spec in enumerate(plan.get("shell", [])):
            cmd = spec["cmd"]
            timeout = int(spec.get("timeout", plan.get("shell_timeout", 300)))
            _run_step(report, f"shell[{i}]", lambda cmd=cmd, timeout=timeout: client.shell(session_id, cmd, timeout=timeout))

        for i, spec in enumerate(plan.get("pull", [])):
            src = spec["src"]
            dest = spec.get("dest", "")
            _run_step(report, f"pull[{i}]:{src}", lambda src=src, dest=dest: client.pull(session_id, src, dest))

        for i, spec in enumerate(plan.get("download", [])):
            src = spec["src"]
            dest = spec["dest"]
            start = _now()
            local = client.download_file(session_id, src, dest)
            report.setdefault("steps", []).append({
                "step": f"download[{i}]:{src}",
                "elapsed_s": _duration(start),
                "success": True,
                "data": {"local_path": str(local)},
            })

        report["success"] = True
    except Exception as exc:
        report["success"] = False
        report["exception"] = type(exc).__name__
        report["error"] = str(exc)
    finally:
        if session_id and not keep_session and plan.get("close", True):
            start = _now()
            try:
                closed = client.close_session(session_id)
                data = _response_data(closed)
                report.setdefault("steps", []).append({
                    "step": "close-session",
                    "elapsed_s": _duration(start),
                    "success": closed.get("success", True) and not data.get("cleanup_errors"),
                    "data": data,
                })
                report["close"] = data
            except Exception as exc:
                report.setdefault("steps", []).append({
                    "step": "close-session",
                    "elapsed_s": _duration(start),
                    "success": False,
                    "exception": type(exc).__name__,
                    "error": str(exc),
                })
                report["close_error"] = str(exc)
                report["success"] = False
        report["finished_at"] = _now()
        report["total_elapsed_s"] = _duration(report["started_at"])
        if output_path:
            _write_json(output_path, report)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run an ADB Hub agent test plan")
    parser.add_argument("--plan", required=True, help="JSON plan path")
    parser.add_argument("--output", required=True, help="JSON report output path")
    parser.add_argument("--timeout", type=int, default=300, help="HTTP request timeout seconds")
    parser.add_argument("--keep-session", action="store_true", help="Do not close the session on exit")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    plan_path = Path(args.plan).resolve()
    output_path = Path(args.output).resolve()
    plan = _load_json(plan_path)
    report = run_plan(
        plan,
        plan_dir=plan_path.parent,
        output_path=output_path,
        request_timeout=args.timeout,
        keep_session=args.keep_session,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
