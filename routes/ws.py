"""WebSocket endpoints for streaming shell and logcat."""

import json
import logging
import threading

from flask_sock import Sock

from adb_utils.client import adb, ADBError

logger = logging.getLogger(__name__)


def register_ws_routes(sock: Sock):
    """Register WebSocket handlers on the given Sock instance."""

    @sock.route("/ws/v1/shell/<serial>")
    def ws_shell(ws, serial: str):
        """Bidirectional interactive shell via WebSocket.

        Client sends: '{"cmd": "ls"}'
        Server responds: '{"stdout": "...", "exit_code": 0}'
        """
        try:
            proc = adb.shell_stream(serial)
        except ADBError as e:
            ws.send(json.dumps({"error": str(e)}))
            return

        def read_stdout():
            """Read adb stdout and send back to client."""
            try:
                for line in iter(proc.stdout.readline, b""):
                    if ws.connected:
                        ws.send(json.dumps({
                            "stdout": line.decode("utf-8", errors="replace").rstrip("\n"),
                        }))
            except Exception:
                pass
            finally:
                if ws.connected:
                    ws.send(json.dumps({"eof": True}))

        reader_thread = threading.Thread(target=read_stdout, daemon=True)
        reader_thread.start()

        try:
            while ws.connected:
                msg = ws.receive()
                if msg is None:
                    break
                try:
                    data = json.loads(msg)
                except json.JSONDecodeError:
                    data = {"cmd": msg}

                cmd = data.get("cmd", "")
                if cmd:
                    try:
                        proc.stdin.write((cmd + "\n").encode("utf-8"))
                        proc.stdin.flush()
                    except (BrokenPipeError, OSError):
                        break
        finally:
            try:
                proc.terminate()
            except Exception:
                pass
            reader_thread.join(timeout=2)

    @sock.route("/ws/v1/logcat/<serial>")
    def ws_logcat(ws, serial: str):
        """Stream logcat output to client in real time.

        Client connects and receives JSON lines:
        '{"line": "07-02 16:00:00.123  1234  5678 D Tag: message"}'
        """
        try:
            proc = adb.logcat_stream(serial, filters=None)
        except ADBError as e:
            ws.send(json.dumps({"error": str(e)}))
            return

        try:
            for line in iter(proc.stdout.readline, b""):
                if not ws.connected:
                    break
                ws.send(json.dumps({
                    "line": line.decode("utf-8", errors="replace").rstrip("\n"),
                }))
        except Exception:
            pass
        finally:
            try:
                proc.terminate()
            except Exception:
                pass
