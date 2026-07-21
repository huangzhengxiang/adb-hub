from __future__ import annotations

import subprocess
import tempfile
import unittest
from unittest.mock import patch

from sessions import SessionManager


class SessionFetchRetryTests(unittest.TestCase):
    def _manager(self, root: str, retries: int = 3) -> tuple[SessionManager, str]:
        manager = SessionManager(root=root, scp_fetch_retries=retries)
        session = manager.create(
            "test-device",
            "scp-retry-test",
            scp={"host": "remote.example", "user": "tester"},
        )
        return manager, session.session_id

    def test_fetch_retries_three_times_with_the_same_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager, session_id = self._manager(tmp)
            failure = subprocess.CompletedProcess(["scp"], 255, "", "connection reset")
            success = subprocess.CompletedProcess(["scp"], 0, "copied", "")
            with patch("sessions.subprocess.run", side_effect=[failure, failure, failure, success]) as run:
                result = manager.fetch(session_id, "/remote/model.bin", "model.bin", timeout=17)

        self.assertTrue(result["success"])
        self.assertEqual(result["attempt_count"], 4)
        self.assertEqual(result["retry_count"], 3)
        self.assertEqual(result["max_retries"], 3)
        self.assertEqual(len(result["attempts"]), 4)
        self.assertEqual(run.call_count, 4)
        self.assertEqual([call.kwargs["timeout"] for call in run.call_args_list], [17, 17, 17, 17])

    def test_fetch_retries_a_timeout_with_the_same_timeout_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager, session_id = self._manager(tmp)
            timeout = subprocess.TimeoutExpired(["scp"], 23, output="partial", stderr="timed out")
            success = subprocess.CompletedProcess(["scp"], 0, "copied", "")
            with patch("sessions.subprocess.run", side_effect=[timeout, success]) as run:
                result = manager.fetch(session_id, "/remote/model.bin", "model.bin", timeout=23)

        self.assertTrue(result["success"])
        self.assertEqual(result["attempt_count"], 2)
        self.assertEqual(result["retry_count"], 1)
        self.assertEqual(result["attempts"][0]["timed_out"], True)
        self.assertEqual([call.kwargs["timeout"] for call in run.call_args_list], [23, 23])


if __name__ == "__main__":
    unittest.main()
