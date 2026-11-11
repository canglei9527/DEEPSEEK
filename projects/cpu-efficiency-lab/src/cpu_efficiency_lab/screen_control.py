"""Temporary screen-awake handling that preserves the user's timeout setting."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .adb import AdbClient, AdbError


DEFAULT_MATRIX_TIMEOUT_MS = 2147483647


@dataclass(frozen=True)
class ScreenState:
    timeout_ms: int
    wakefulness: str
    display_on: bool


def read_screen_state(client: AdbClient) -> ScreenState:
    script = r'''timeout=$(settings get system screen_off_timeout 2>/dev/null)
wake=$(dumpsys power 2>/dev/null | grep -m1 'mWakefulness=' | sed 's/.*mWakefulness=//' | cut -d' ' -f1)
display=0
dumpsys display 2>/dev/null | grep -q 'mState=ON' && display=1
printf 'SCREEN|%s|%s|%s\n' "$timeout" "$wake" "$display"'''
    output = client.run_root_script(script)
    for line in output.splitlines():
        fields = line.strip().split("|")
        if len(fields) == 4 and fields[0] == "SCREEN":
            try:
                timeout = int(fields[1])
            except ValueError:
                break
            return ScreenState(timeout, fields[2], fields[3] == "1")
    raise AdbError("could not inspect screen timeout/power state")


def keep_screen_awake(
    client: AdbClient,
    timeout_ms: int = DEFAULT_MATRIX_TIMEOUT_MS,
    expected_current_timeout_ms: Optional[int] = None,
) -> ScreenState:
    if timeout_ms < 60_000:
        raise ValueError("screen timeout must be at least one minute")
    before = read_screen_state(client)
    if expected_current_timeout_ms is not None and before.timeout_ms != expected_current_timeout_ms:
        raise AdbError(
            "screen timeout changed since it was recorded: expected %d, found %d"
            % (expected_current_timeout_ms, before.timeout_ms)
        )
    script = r'''expected=__EXPECTED__
target=__TARGET__
current=$(settings get system screen_off_timeout 2>/dev/null)
if [ "$current" != "$expected" ]; then printf 'SCREEN_STALE|%s|%s\n' "$current" "$expected"; exit 20; fi
settings put system screen_off_timeout "$target" || exit 21
input keyevent 224 || exit 22
sleep 2
actual=$(settings get system screen_off_timeout 2>/dev/null)
wake=$(dumpsys power 2>/dev/null | grep -m1 'mWakefulness=' | sed 's/.*mWakefulness=//' | cut -d' ' -f1)
display=0
dumpsys display 2>/dev/null | grep -q 'mState=ON' && display=1
printf 'SCREEN_APPLIED|%s|%s|%s|%s\n' "$current" "$actual" "$wake" "$display"
'''.replace("__EXPECTED__", str(before.timeout_ms)).replace("__TARGET__", str(timeout_ms))
    try:
        output = client.run_root_script(script, timeout_s=15.0)
        if "SCREEN_APPLIED|%d|%d|Awake|1" % (before.timeout_ms, timeout_ms) not in output:
            raise AdbError("screen did not remain awake with display on: " + output)
    except (AdbError, OSError):
        try:
            client.run_root_script("settings put system screen_off_timeout %d" % before.timeout_ms)
        except (AdbError, OSError):
            pass
        raise
    return before


def restore_screen_timeout(
    client: AdbClient,
    original_timeout_ms: int,
    expected_temporary_timeout_ms: int = DEFAULT_MATRIX_TIMEOUT_MS,
) -> ScreenState:
    current = read_screen_state(client)
    if current.timeout_ms != expected_temporary_timeout_ms:
        raise AdbError(
            "screen timeout was changed during the experiment; leaving it untouched (current=%d, expected temporary=%d)"
            % (current.timeout_ms, expected_temporary_timeout_ms)
        )
    script = r'''original=__ORIGINAL__
expected=__EXPECTED__
current=$(settings get system screen_off_timeout 2>/dev/null)
[ "$current" = "$expected" ] || { printf 'SCREEN_STALE|%s|%s\n' "$current" "$expected"; exit 20; }
settings put system screen_off_timeout "$original" || exit 21
actual=$(settings get system screen_off_timeout 2>/dev/null)
printf 'SCREEN_RESTORED|%s|%s\n' "$current" "$actual"
'''.replace("__ORIGINAL__", str(original_timeout_ms)).replace(
        "__EXPECTED__", str(expected_temporary_timeout_ms)
    )
    output = client.run_root_script(script)
    if "SCREEN_RESTORED|%d|%d" % (expected_temporary_timeout_ms, original_timeout_ms) not in output:
        raise AdbError("screen timeout restoration failed: " + output)
    return read_screen_state(client)
