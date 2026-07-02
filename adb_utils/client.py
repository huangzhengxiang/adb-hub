"""ADB client — wraps all adb command invocations."""

import subprocess
import logging
from dataclasses import dataclass, field
from typing import Optional

from config import ADB_PATH, MAX_SHELL_OUTPUT_BYTES

logger = logging.getLogger(__name__)


@dataclass
class ADBResult:
    """Result of an adb command execution."""
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    success: bool = True

    def to_dict(self) -> dict:
        return {
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
        }


class ADBError(Exception):
    """Raised when an adb command fails unexpectedly."""
    def __init__(self, message: str, result: Optional[ADBResult] = None):
        super().__init__(message)
        self.result = result


class ADBClient:
    """Encapsulates all adb interactions."""

    def __init__(self, adb_path: str = ADB_PATH):
        self.adb = adb_path

    # ------------------------------------------------------------------
    # Low-level helpers
    # ------------------------------------------------------------------

    def _run(self, args: list[str], timeout: int = 30, input_data: Optional[bytes] = None) -> ADBResult:
        """Run an adb command and return structured result."""
        cmd = [self.adb] + args
        logger.debug("Running: %s", " ".join(cmd))
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                timeout=timeout,
                input=input_data,
            )
            stdout = proc.stdout.decode("utf-8", errors="replace").strip()
            stderr = proc.stderr.decode("utf-8", errors="replace").strip()
            return ADBResult(
                stdout=stdout,
                stderr=stderr,
                exit_code=proc.returncode,
                success=proc.returncode == 0,
            )
        except subprocess.TimeoutExpired as e:
            out = (e.stdout or b"").decode("utf-8", errors="replace").strip()
            err = (e.stderr or b"").decode("utf-8", errors="replace").strip()
            return ADBResult(stdout=out, stderr=err, exit_code=-1, success=False)
        except FileNotFoundError:
            raise ADBError(
                f"adb binary not found: {self.adb}. Make sure adb is in PATH.",
            )

    def _spawn(self, args: list[str]) -> subprocess.Popen:
        """Spawn a long-running adb process (for streaming)."""
        cmd = [self.adb] + args
        logger.debug("Spawning: %s", " ".join(cmd))
        try:
            return subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=0,
            )
        except FileNotFoundError:
            raise ADBError(
                f"adb binary not found: {self.adb}. Make sure adb is in PATH.",
            )

    # ------------------------------------------------------------------
    # Device listing
    # ------------------------------------------------------------------

    def devices(self) -> ADBResult:
        """Return `adb devices -l` output."""
        return self._run(["devices", "-l"])

    def get_device_info(self, serial: str) -> dict:
        """Collect key device properties."""
        props = {
            "ro.product.model": "model",
            "ro.product.brand": "brand",
            "ro.build.version.release": "android_version",
            "ro.build.version.sdk": "sdk_version",
            "ro.product.cpu.abi": "abi",
        }
        info = {}
        for prop, key in props.items():
            r = self._run(["-s", serial, "shell", "getprop", prop])
            if r.success and r.stdout:
                info[key] = r.stdout
        return info

    # ------------------------------------------------------------------
    # Shell commands
    # ------------------------------------------------------------------

    def shell(self, serial: str, command: str, timeout: int = 30) -> ADBResult:
        """Execute a shell command on a device."""
        return self._run(
            ["-s", serial, "shell", command],
            timeout=timeout,
        )

    def shell_stream(self, serial: str, command: str = "sh") -> subprocess.Popen:
        """Spawn an interactive shell on a device. Returns a Popen with
        stdin/stdout pipes for bidirectional communication."""
        args = ["-s", serial, "shell", command]
        return self._spawn(args)

    # ------------------------------------------------------------------
    # Arbitrary adb command pass-through
    # ------------------------------------------------------------------

    def exec_device(self, serial: str, args: list[str], timeout: int = 30) -> ADBResult:
        """Execute arbitrary adb -s <serial> <args...>."""
        return self._run(["-s", serial] + args, timeout=timeout)

    def exec_global(self, args: list[str], timeout: int = 30) -> ADBResult:
        """Execute arbitrary adb <args...> (no -s prefix)."""
        return self._run(args, timeout=timeout)

    # ------------------------------------------------------------------
    # Package management
    # ------------------------------------------------------------------

    def install(self, serial: str, apk_path: str, opts: list[str] | None = None) -> ADBResult:
        """Install an APK on a device."""
        cmd = ["-s", serial, "install"]
        if opts:
            cmd.extend(opts)
        cmd.append(apk_path)
        return self._run(cmd, timeout=120)

    def uninstall(self, serial: str, package: str) -> ADBResult:
        """Uninstall a package from a device."""
        return self._run(["-s", serial, "uninstall", package], timeout=30)

    def list_packages(self, serial: str, filt: str | None = None) -> ADBResult:
        """List packages on a device. Pass `filt` to filter (e.g. `-3` for third-party)."""
        cmd = ["-s", serial, "shell", "pm", "list", "packages"]
        if filt:
            cmd.append(filt)
        return self._run(cmd, timeout=15)

    # ------------------------------------------------------------------
    # File transfer
    # ------------------------------------------------------------------

    def push(self, serial: str, src: str, dst: str) -> ADBResult:
        """Push a file to the device."""
        return self._run(["-s", serial, "push", src, dst], timeout=120)

    def pull(self, serial: str, src: str, dst: str) -> ADBResult:
        """Pull a file from the device."""
        return self._run(["-s", serial, "pull", src, dst], timeout=120)

    # ------------------------------------------------------------------
    # Screenshot
    # ------------------------------------------------------------------

    def screenshot(self, serial: str) -> bytes:
        """Capture a screenshot from a device, returning PNG bytes."""
        proc = self._spawn(["-s", serial, "exec-out", "screencap", "-p"])
        stdout, _ = proc.communicate(timeout=15)
        if proc.returncode != 0:
            raise ADBError(f"Screenshot failed (exit {proc.returncode})")
        return stdout

    # ------------------------------------------------------------------
    # Logcat
    # ------------------------------------------------------------------

    def logcat_stream(self, serial: str, filters: list[str] | None = None) -> subprocess.Popen:
        """Spawn `adb logcat` and return the Popen for streaming."""
        args = ["-s", serial, "logcat"]
        if filters:
            args.extend(filters)
        return self._spawn(args)

    # ------------------------------------------------------------------
    # Connectivity
    # ------------------------------------------------------------------

    def connect(self, address: str) -> ADBResult:
        """Connect to a device via TCP/IP."""
        return self._run(["connect", address], timeout=10)

    def disconnect(self, address: str) -> ADBResult:
        """Disconnect from a TCP/IP device."""
        return self._run(["disconnect", address], timeout=10)

    def tcpip(self, serial: str, port: int = 5555) -> ADBResult:
        """Restart adbd on device in TCP/IP mode."""
        return self._run(["-s", serial, "tcpip", str(port)], timeout=10)


# Singleton
adb = ADBClient()
