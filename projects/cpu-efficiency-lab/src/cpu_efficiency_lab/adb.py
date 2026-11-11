"""Small, explicit ADB subprocess adapter."""

from __future__ import annotations

import shlex
import shutil
import subprocess
from typing import Optional, Sequence


class AdbError(RuntimeError):
    pass


class AdbClient:
    def __init__(self, serial: str, adb_path: Optional[str] = None) -> None:
        if not serial.strip():
            raise ValueError("ADB serial must not be empty")
        self.serial = serial
        self.adb_path = adb_path or shutil.which("adb")
        if not self.adb_path:
            raise AdbError("adb was not found on PATH")

    def run(
        self, arguments: Sequence[str], timeout_s: float = 20.0,
        input_text: Optional[str] = None,
    ) -> str:
        command = [self.adb_path, "-s", self.serial, *arguments]
        try:
            result = subprocess.run(
                command,
                input=input_text,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise AdbError(f"ADB command timed out after {timeout_s:g}s") from exc
        except OSError as exc:
            raise AdbError(f"Could not start adb: {exc}") from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise AdbError(
                f"ADB exited with status {result.returncode}: {detail or '(no output)'}"
            )
        return result.stdout.replace("\r\n", "\n")

    def run_root_script(self, script: str, timeout_s: float = 20.0) -> str:
        """Run a shell script through su; callers own write-safety and restoration."""
        remote_command = "su -c " + shlex.quote(script)
        return self.run(["shell", remote_command], timeout_s=timeout_s)

    def run_root_script_stdin(self, script: str, timeout_s: float = 20.0) -> str:
        """Run a long root script over stdin, avoiding Windows command-line limits."""
        return self.run(["shell", "-T", "su -c sh"], timeout_s=timeout_s, input_text=script + "\n")
