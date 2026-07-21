#!/usr/bin/env python3
"""Offline contract test for the SCP model-to-phone agent example."""

from __future__ import annotations

import importlib.util
import json
import tempfile
from pathlib import Path
from typing import Any


EXAMPLE_DIR = Path(__file__).resolve().parent
REPO_ROOT = EXAMPLE_DIR.parents[2]
RUNNER_PATH = REPO_ROOT / "client" / "agent_session_runner.py"


def _load_runner():
    spec = importlib.util.spec_from_file_location("agent_session_runner", RUNNER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FakeADBHubClient:
    """Record runner calls while returning the response shape ADB Hub uses."""

    instances: list["FakeADBHubClient"] = []

    def __init__(self, timeout: int = 30):
        self.base_url = "http://fake-adb-hub:3588"
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.__class__.instances.append(self)

    @staticmethod
    def _ok(data: dict[str, Any]) -> dict[str, Any]:
        return {"success": True, "data": data, "error": None}

    def health(self):
        self.calls.append(("health", ()))
        return self._ok({"adb_available": True})

    def devices(self):
        self.calls.append(("devices", ()))
        return self._ok({"devices": [{"serial": "FAKE-DEVICE", "state": "device"}]})

    def create_session(self, serial: str, name: str):
        self.calls.append(("create_session", (serial, name)))
        return self._ok({"session_id": "fake-session", "serial": serial, "name": name})

    def fetch(self, session_id: str, src: str, dest: str, recursive: bool, timeout: int):
        self.calls.append(("fetch", (session_id, src, dest, recursive, timeout)))
        return self._ok({"success": True, "exit_code": 0})

    def open_session(self, session_id: str):
        self.calls.append(("open_session", (session_id,)))
        return self._ok({"state": "open"})

    def push(self, session_id: str, src: str, dest: str):
        self.calls.append(("push", (session_id, src, dest)))
        return self._ok({"success": True, "exit_code": 0})

    def shell(self, session_id: str, cmd: str, timeout: int):
        self.calls.append(("shell", (session_id, cmd, timeout)))
        return self._ok({"success": True, "exit_code": 0, "stdout": "runner completed\n", "stderr": ""})

    def pull(self, session_id: str, src: str, dest: str):
        self.calls.append(("pull", (session_id, src, dest)))
        return self._ok({"success": True, "exit_code": 0})

    def download_file(self, session_id: str, src: str, dest: str):
        self.calls.append(("download_file", (session_id, src, dest)))
        path = Path(dest)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"downloaded {src}\n", encoding="utf-8")
        return path

    def close_session(self, session_id: str):
        self.calls.append(("close_session", (session_id,)))
        return self._ok({"session_id": session_id, "cleanup_errors": []})


def main() -> int:
    plan_path = EXAMPLE_DIR / "adb_hub_plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    actions = plan["sessions"][0]["actions"]
    expected_actions = [
        "fetch", "fetch", "fetch", "fetch", "open", "push", "push", "push", "push",
        "shell", "pull", "pull", "pull", "download", "download", "download", "close",
    ]
    assert [action["type"] for action in actions] == expected_actions

    launcher = (EXAMPLE_DIR / "run_on_device.sh").read_text(encoding="utf-8")
    for required in ("LD_LIBRARY_PATH", "TMPDIR", "HOME", "XDG_CACHE_HOME", "ADB_HUB_SHELL", 'exec "$RUNNER"'):
        assert required in launcher, f"launcher is missing {required}"

    runner = _load_runner()
    original_client = runner.ADBHubClient
    FakeADBHubClient.instances.clear()
    runner.ADBHubClient = FakeADBHubClient
    try:
        with tempfile.TemporaryDirectory() as tmp:
            test_plan = json.loads(json.dumps(plan))
            download_dir = Path(tmp) / "downloads"
            for action in test_plan["sessions"][0]["actions"]:
                if action["type"] == "download":
                    action["dest"] = str(download_dir / Path(action["src"]).name)
            output_path = Path(tmp) / "report.json"
            report = runner.run_plan(
                test_plan,
                plan_dir=EXAMPLE_DIR,
                output_path=output_path,
                request_timeout=30,
                keep_session=False,
            )
            assert report["success"] is True
            assert output_path.is_file()
            assert json.loads(output_path.read_text(encoding="utf-8"))["success"] is True
            assert len(list(download_dir.glob("*"))) == 3
    finally:
        runner.ADBHubClient = original_client

    calls = [name for name, _ in FakeADBHubClient.instances[0].calls]
    assert calls == [
        "health", "devices", "create_session",
        "fetch", "fetch", "fetch", "fetch",
        "open_session",
        "push", "push", "push", "push",
        "shell",
        "pull", "pull", "pull",
        "download_file", "download_file", "download_file",
        "close_session",
    ], calls
    print("verified: SCP fetch -> open -> push -> device shell -> pull -> download -> close")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
