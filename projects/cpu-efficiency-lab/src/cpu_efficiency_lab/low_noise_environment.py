"""Gated screen-off lease using a timed kernel wake lock, never guessed service calls.

No device access on import. A run requires a readable wake-lock list, writable
wake-lock controls and readable backlight state. Guardian liveness alone is not
proof that the kernel wake lock remains held.
"""
from __future__ import annotations

import re
import secrets
import shlex
from dataclasses import dataclass
from typing import Tuple

from .adb import AdbClient, AdbError
from .config import LOW_NOISE_HEARTBEAT_TIMEOUT_S, TWO_HOUR_TOTAL_S
from .matrix_controller import require_verified_hotplug_recovery
from .screen_control import ScreenState, read_screen_state

REMOTE_NOISE_ROOT = '/data/local/tmp/cpu-efficiency-lab/noise_'


@dataclass(frozen=True)
class NoiseGuard:
    token: str
    brightness_paths: Tuple[str, ...]

    def __post_init__(self):
        if not re.fullmatch(r'cel_[0-9a-f]{32}', self.token):
            raise ValueError('invalid wake-lock token')
        if not self.brightness_paths or any(not re.fullmatch(
                r'/sys/class/backlight/[a-zA-Z0-9_.:-]+/actual_brightness', p)
                for p in self.brightness_paths):
            raise ValueError('invalid brightness monitor path')

    @property
    def directory(self):
        return REMOTE_NOISE_ROOT + self.token[4:]

    def sample_shell(self) -> str:
        """Cheap per-sample sysfs checks; no per-sample dumpsys/ADB round trips."""
        return r'''  if [ ! -f '__DIR__/ready' ] || [ -f '__DIR__/cleaned' ]; then
    abort_reason=NOISE_LEASE_LOST; printf 'ABORT|%s\n' "$abort_reason"; return 1
  fi
  noise_locks=$(cat /sys/power/wake_lock 2>/dev/null) || noise_locks=
  case " $noise_locks " in *" __TOKEN__ "*) ;; *)
    abort_reason=WAKE_LOCK_LOST; printf 'ABORT|%s\n' "$abort_reason"; return 1 ;; esac
  read -r guardian_pid guardian_start < '__DIR__/guardian.pid' || guardian_pid=
  case "$guardian_pid:$guardian_start" in *[!0-9:]*) guardian_pid= ;; esac
  if [ -z "$guardian_pid" ] || [ -z "$guardian_start" ] || ! kill -0 "$guardian_pid" 2>/dev/null || \
     [ "$(awk '{print $22}' "/proc/$guardian_pid/stat" 2>/dev/null)" != "$guardian_start" ]; then
    abort_reason=WAKE_GUARDIAN_LOST; printf 'ABORT|%s\n' "$abort_reason"; return 1
  fi
  read -r heartbeat < '__DIR__/heartbeat' || heartbeat=
  case "$heartbeat" in ''|*[!0-9]*) abort_reason=WAKE_HEARTBEAT_INVALID; printf 'ABORT|%s\n' "$abort_reason"; return 1 ;; esac
  read -r now _ < /proc/uptime || now=
  now=${now%%.*}
  case "$now" in ''|*[!0-9]*) abort_reason=UPTIME_INVALID; printf 'ABORT|%s\n' "$abort_reason"; return 1 ;; esac
  if [ "$heartbeat" -gt "$now" ] || [ "$((now - heartbeat))" -ge __HEARTBEAT_TIMEOUT__ ]; then
    abort_reason=WAKE_HEARTBEAT_STALE; printf 'ABORT|%s\n' "$abort_reason"; return 1
  fi
  for noise_path in __PATHS__; do
    read -r noise_brightness < "$noise_path" 2>/dev/null || noise_brightness=unknown
    if [ "$noise_brightness" != 0 ]; then
      abort_reason=DISPLAY_NOT_DARK; printf 'ABORT|%s|%s\n' "$abort_reason" "$noise_brightness"; return 1
    fi
  done
'''.replace('__DIR__', self.directory).replace('__TOKEN__', self.token).replace(
            '__HEARTBEAT_TIMEOUT__', str(LOW_NOISE_HEARTBEAT_TIMEOUT_S)).replace(
            '__PATHS__', ' '.join(shlex.quote(p) for p in self.brightness_paths))


def build_lease_guardian(guard: NoiseGuard, screen: ScreenState, duration_s: int) -> str:
    """Bounded detached guardian: release the owned lock and restore display on exit."""
    if isinstance(duration_s, bool) or not isinstance(duration_s, int) or not 1 <= duration_s <= TWO_HOUR_TOTAL_S:
        raise ValueError('invalid screen lease duration')
    if screen.wakefulness not in ('Awake', 'Asleep'):
        raise ValueError('unreviewed initial wakefulness state')
    restore_key = 224 if screen.display_on else 223  # WAKEUP/SLEEP, never toggle.
    return r'''set +e
d='__DIR__'
token='__TOKEN__'
cleanup() {
  trap '' HUP INT TERM
  rc=0
  input keyevent __RESTORE_KEY__ || rc=1
  printf '%s\n' "$token" > /sys/power/wake_unlock || rc=1
  printf '%s\n' "$rc" > "$d/cleaned"
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM
read -r started _ < /proc/uptime || exit 1
started=${started%%.*}
deadline=$((started + __DURATION__))
# Kernel timeout is in nanoseconds, so even guardian SIGKILL cannot leak an
# indefinite wake lock. Require active-token readback; a successful write alone
# or an alive guardian does not prove that a wake lock is currently held.
printf '%s %s\n' "$token" '__TIMEOUT_NS__' > /sys/power/wake_lock || exit 1
locks=$(cat /sys/power/wake_lock) || exit 1
case " $locks " in *" $token "*) ;; *) exit 1 ;; esac
input keyevent 223 || exit 1
sleep 2
for path in __PATHS__; do
  read -r brightness < "$path" || exit 1
  [ "$brightness" = 0 ] || exit 1
done
# Kernel brightness and Android display state must agree before the lease is ready.
power=$(dumpsys power) || exit 1
printf '%s\n' "$power" | grep -q 'mWakefulness=Asleep' || exit 1
guardian_start=$(awk '{print $22}' "/proc/$$/stat") || exit 1
[ -n "$guardian_start" ] || exit 1
printf '%s %s\n' "$$" "$guardian_start" > "$d/guardian.pid" || exit 1
printf '%s\n' "$started" > "$d/heartbeat" || exit 1
printf 'READY\n' > "$d/ready" || exit 1
while [ ! -f "$d/stop" ]; do
  read -r now _ < /proc/uptime || exit 1
  now=${now%%.*}
  [ "$now" -lt "$deadline" ] || exit 1
  read -r heartbeat < "$d/heartbeat" || exit 1
  case "$heartbeat" in ''|*[!0-9]*) exit 1 ;; esac
  [ "$((now - heartbeat))" -lt __HEARTBEAT_TIMEOUT__ ] || exit 1
  sleep 2
done
'''.replace('__DIR__', guard.directory).replace('__TOKEN__', guard.token).replace(
        '__RESTORE_KEY__', str(restore_key)).replace('__DURATION__', str(duration_s)).replace(
        '__TIMEOUT_NS__', str(duration_s * 1000000000)).replace(
        '__HEARTBEAT_TIMEOUT__', str(LOW_NOISE_HEARTBEAT_TIMEOUT_S)).replace(
        '__PATHS__', ' '.join(shlex.quote(p) for p in guard.brightness_paths))


class ScreenOffLease:
    """Explicit prepare/check/close so the caller can persist cleanup failures."""
    def __init__(self, client: AdbClient):
        self.client = client
        self.before = None
        self.guard = None

    def prepare(self, duration_s: int, confirmed: bool = False) -> NoiseGuard:
        require_verified_hotplug_recovery(confirmed)  # before even read-only ADB
        self.before = read_screen_state(self.client)
        if (self.before.wakefulness, self.before.display_on) not in (('Awake', True), ('Asleep', False)):
            raise AdbError('screen is dozing or inconsistent; refusing screen-off preparation')
        output = self.client.run_root_script(r'''test -r /sys/power/wake_lock && test -w /sys/power/wake_lock &&
test -w /sys/power/wake_unlock || { echo NO_WAKE_LOCK_BACKEND; exit 1; }
for p in /sys/class/backlight/*/actual_brightness; do
  [ -r "$p" ] && printf 'BRIGHTNESS|%s\n' "$p"
done''')
        paths = tuple(line.split('|', 1)[1] for line in output.splitlines() if line.startswith('BRIGHTNESS|'))
        guard = NoiseGuard('cel_' + secrets.token_hex(16), paths)
        # Build/validate everything before creating remote state.
        script = build_lease_guardian(guard, self.before, duration_s)
        self.guard = guard  # cleanup remains possible even if the transport fails.
        launcher = r'''mkdir -p /data/local/tmp/cpu-efficiency-lab || exit 1
mkdir -m 700 '__DIR__' || exit 1
nohup setsid sh -c __SCRIPT__ </dev/null >'__DIR__/guardian.log' 2>&1 &
i=0
while [ ! -f '__DIR__/ready' ] && [ ! -f '__DIR__/cleaned' ] && [ "$i" -lt 10 ]; do
  sleep 1; i=$((i + 1))
done
[ -f '__DIR__/ready' ] && [ ! -f '__DIR__/cleaned' ] || exit 1
printf 'NOISE_READY\n'
'''.replace('__DIR__', guard.directory).replace('__SCRIPT__', shlex.quote(script))
        result = self.client.run_root_script_stdin(launcher, timeout_s=20)
        if 'NOISE_READY' not in result:
            raise AdbError('screen-off guardian was not ready')
        self.check()
        return guard

    def check(self) -> None:
        if self.guard is None:
            raise AdbError('no prepared screen-off lease')
        screen = read_screen_state(self.client)
        if screen.display_on or screen.wakefulness != 'Asleep' or screen.timeout_ms != self.before.timeout_ms:
            raise AdbError('display/wakefulness/timeout changed during screen-off lease')
        g = self.guard
        # Heartbeat replacement is atomic; guardian never reads a partially-written number.
        script = ("check_noise() {\n" + g.sample_shell() + "}\ncheck_noise || exit 1\n"
                  + "read -r now _ < /proc/uptime || exit 1\n"
                  + "printf '%s\\n' \"${now%%.*}\" > '%s/heartbeat.tmp' && " % g.directory
                  + "mv '%s/heartbeat.tmp' '%s/heartbeat' && echo NOISE_OK" % (g.directory, g.directory))
        if 'NOISE_OK' not in self.client.run_root_script(script):
            raise AdbError('screen-off lease check failed')

    def close(self) -> ScreenState:
        if self.guard is not None:
            directory = self.guard.directory
            script = r'''d='__DIR__'
[ -d "$d" ] || exit 1
: > "$d/stop" || exit 1
i=0
while [ ! -f "$d/cleaned" ] && [ "$i" -lt 10 ]; do sleep 1; i=$((i + 1)); done
read -r result < "$d/cleaned" || exit 1
[ "$result" = 0 ] || exit 1
locks=$(cat /sys/power/wake_lock) || exit 1
case " $locks " in *" __TOKEN__ "*) exit 1 ;; esac
echo NOISE_CLEAN
'''.replace('__DIR__', directory).replace('__TOKEN__', self.guard.token)
            if 'NOISE_CLEAN' not in self.client.run_root_script(script, timeout_s=15):
                raise AdbError('screen-off guardian cleanup not verified: ' + directory)
        after = read_screen_state(self.client)
        if self.before is not None and after != self.before:
            raise AdbError('screen state restoration mismatch: %r != %r' % (after, self.before))
        return after
