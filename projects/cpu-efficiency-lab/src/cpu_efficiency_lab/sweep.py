"""Authorized, thermally guarded CPU frequency sweep controller."""

from __future__ import annotations

import random
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .adb import AdbClient, AdbError
from .config import CPUFREQ_ROOT
from .models import CpuPolicy, PreflightReport
from .preflight import collect_preflight

REMOTE_DIR = "/data/local/tmp/cpu-efficiency-lab"
REMOTE_BENCH = REMOTE_DIR + "/cpu_mix_bench"
CPU_TEMP_LIMIT_MC = 80000
BATTERY_TEMP_LIMIT_MC = 43000
DEFAULT_WARMUP_S = 15
DEFAULT_MEASURE_S = 25
DEFAULT_REPETITIONS = 3
DEFAULT_BASELINE_S = 15
DEFAULT_COOLDOWN_S = 15


@dataclass(frozen=True)
class SweepPoint:
    point_id: str
    policy: str
    cpu: int
    frequency_khz: int
    original_min_khz: int
    original_max_khz: int


def available_points(report: PreflightReport) -> List[SweepPoint]:
    points = []
    for policy in report.policies:
        if policy.scaling_min_khz is None or policy.scaling_max_khz is None:
            raise ValueError(
                "cpufreq limits are unavailable for %s (offline policy or unstable power state); refusing an incomplete sweep"
                % policy.name
            )
        if not re.fullmatch(r"policy[0-9]+", policy.name):
            raise ValueError("unexpected cpufreq policy name: %s" % policy.name)
        cpus = [int(value) for value in policy.related_cpus.split() if value.isdigit()]
        if not cpus:
            raise ValueError("no online CPU listed for %s" % policy.name)
        frequencies = sorted(
            {
                int(value)
                for value in policy.available_frequencies_khz.split()
                if value.isdigit()
                and max(policy.scaling_min_khz, policy.hardware_min_khz or policy.scaling_min_khz) <= int(value) <= policy.scaling_max_khz
            }
        )
        for frequency in frequencies:
            if policy.hardware_min_khz is not None and frequency < policy.hardware_min_khz:
                continue
            points.append(
                SweepPoint(
                    point_id="%s-%d" % (policy.name, frequency),
                    policy=policy.name,
                    cpu=cpus[0],
                    frequency_khz=frequency,
                    original_min_khz=policy.scaling_min_khz,
                    original_max_khz=policy.scaling_max_khz,
                )
            )
    return points


def discover_thermal_sensors(client: AdbClient) -> Tuple[List[str], str, Dict[str, int]]:
    script = r'''for zone in /sys/class/thermal/thermal_zone*; do
  if read -r kind < "$zone/type" 2>/dev/null; then
    case "$kind" in
      cpu-*|cpuss-*|cpu_therm|battery)
        if read -r value < "$zone/temp" 2>/dev/null; then
          printf 'THERM|%s|%s|%s\n' "$kind" "$zone/temp" "$value"
        fi
        ;;
    esac
  fi
done'''
    output = client.run_root_script(script)
    cpu_paths: List[str] = []
    battery_path = ""
    values: Dict[str, int] = {}
    for line in output.splitlines():
        fields = line.strip().split("|")
        if len(fields) != 4 or fields[0] != "THERM":
            continue
        kind, path, raw_value = fields[1:]
        try:
            value = int(raw_value)
        except ValueError:
            continue
        if kind == "battery":
            battery_path = path
            values[kind] = value
        elif kind == "cpu_therm" or kind.startswith("cpu-") or kind.startswith("cpuss-"):
            cpu_paths.append(path)
            values[kind] = value
    if not cpu_paths or not battery_path:
        raise AdbError("could not discover both CPU and battery thermal sensors")
    return sorted(set(cpu_paths)), battery_path, values


def build_point_script(
    point: SweepPoint,
    cpu_temp_paths: Sequence[str],
    battery_temp_path: str,
    warmup_s: int = DEFAULT_WARMUP_S,
    measure_s: int = DEFAULT_MEASURE_S,
    repetitions: int = DEFAULT_REPETITIONS,
    baseline_s: int = DEFAULT_BASELINE_S,
    cooldown_s: int = DEFAULT_COOLDOWN_S,
    cpu_limit_mc: int = CPU_TEMP_LIMIT_MC,
    battery_limit_mc: int = BATTERY_TEMP_LIMIT_MC,
    bench_path: str = REMOTE_BENCH,
) -> str:
    values = [warmup_s, measure_s, repetitions, baseline_s, cooldown_s, cpu_limit_mc, battery_limit_mc]
    if any(value <= 0 for value in values):
        raise ValueError("sweep durations, repetitions, and limits must be positive")
    if not cpu_temp_paths or not battery_temp_path:
        raise ValueError("thermal sensor paths are required")
    if point.frequency_khz > point.original_max_khz:
        raise ValueError("target frequency exceeds the original scaling maximum")
    if not re.fullmatch(r"policy[0-9]+", point.policy):
        raise ValueError("invalid policy name")
    if bench_path != REMOTE_BENCH and not re.fullmatch(
        re.escape(REMOTE_DIR + "/cpu_mix_bench_") + r"[0-9a-f]{32}", bench_path
    ):
        raise ValueError("unexpected benchmark path")

    if any(not re.fullmatch(r"/sys/class/thermal/thermal_zone[0-9]+/temp", path) for path in cpu_temp_paths):
        raise ValueError("unexpected CPU thermal sensor path")
    if not re.fullmatch(r"/sys/class/thermal/thermal_zone[0-9]+/temp", battery_temp_path):
        raise ValueError("unexpected battery thermal sensor path")
    cpu_path_list = " ".join(cpu_temp_paths)
    battery_path = "'" + battery_temp_path.replace("'", "'\\''") + "'"
    policy_path = "'" + (CPUFREQ_ROOT + "/" + point.policy) + "'"
    bench_path = "'" + bench_path + "'"
    point_id = "'" + point.point_id + "'"

    script = r'''set +e
policy_path=__POLICY_PATH__
bench=__BENCH_PATH__
point_id=__POINT_ID__
cpu=__CPU__
target=__TARGET__
expected_min=__EXPECTED_MIN__
expected_max=__EXPECTED_MAX__
warmup=__WARMUP__
measure=__MEASURE__
repetitions=__REPETITIONS__
baseline_seconds=__BASELINE__
cooldown_seconds=__COOLDOWN__
cpu_limit=__CPU_LIMIT__
battery_limit=__BATTERY_LIMIT__
battery_temp_path=__BATTERY_TEMP_PATH__
cpu_temp_paths="__CPU_TEMP_PATHS__"
restore_needed=0
abort_reason=

restore_policy() {
  if [ "$restore_needed" = 1 ]; then
    printf '%s\n' "$original_max" > "$policy_path/scaling_max_freq" 2>/dev/null
    printf '%s\n' "$original_min" > "$policy_path/scaling_min_freq" 2>/dev/null
    read -r restored_min < "$policy_path/scaling_min_freq" 2>/dev/null
    read -r restored_max < "$policy_path/scaling_max_freq" 2>/dev/null
    printf 'RESTORE|%s|%s|%s\n' "$policy_path" "$restored_min" "$restored_max"
    restore_needed=0
  fi
}
trap 'restore_policy' EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

read -r original_min < "$policy_path/scaling_min_freq" || exit 20
read -r original_max < "$policy_path/scaling_max_freq" || exit 21
if [ "$original_min" != "$expected_min" ] || [ "$original_max" != "$expected_max" ]; then
  printf 'STALE_POLICY|%s|%s|%s\n' "$policy_path" "$original_min" "$original_max"
  exit 22
fi
if [ "$target" -gt "$original_max" ]; then
  printf 'CAP_VIOLATION|%s|%s|%s\n' "$policy_path" "$target" "$original_max"
  exit 23
fi
restore_needed=1
printf '%s\n' "$original_max" > "$policy_path/scaling_max_freq" || exit 24
printf '%s\n' "$target" > "$policy_path/scaling_min_freq" || exit 25
printf '%s\n' "$target" > "$policy_path/scaling_max_freq" || exit 26
read -r applied_min < "$policy_path/scaling_min_freq" || exit 27
read -r applied_max < "$policy_path/scaling_max_freq" || exit 28
if [ "$applied_min" != "$target" ] || [ "$applied_max" != "$target" ]; then
  printf 'LOCK_MISMATCH|%s|%s|%s|%s\n' "$policy_path" "$applied_min" "$applied_max" "$target"
  exit 29
fi
printf 'POINT|%s|%s|%s|%s\n' "$point_id" "$policy_path" "$cpu" "$target"

sample_once() {
  sample_phase=$1
  sample_rep=$2
  read -r ci0 _ < /proc/uptime || return 30
  read -r current < /sys/class/power_supply/battery/current_now || return 31
  read -r ci1 _ < /proc/uptime || return 32
  read -r vi0 _ < /proc/uptime || return 33
  read -r voltage < /sys/class/power_supply/battery/voltage_now || return 34
  read -r vi1 _ < /proc/uptime || return 35
  read -r status < /sys/class/power_supply/battery/status 2>/dev/null || status=Unknown
  read -r charge < /sys/class/power_supply/battery/charge_counter 2>/dev/null || charge=
  read -r battery_temp < "$battery_temp_path" 2>/dev/null || battery_temp=-1
  case "$battery_temp" in ''|*[!0-9]*) battery_temp=-1 ;; esac
  case "$current" in -*) current_digits=${current#-} ;; *) current_digits=$current ;; esac
  case "$current_digits" in ''|*[!0-9]*) abort_reason=BATTERY_CURRENT_INVALID; printf 'ABORT|%s\n' "$abort_reason"; return 1 ;; esac
  case "$voltage" in ''|*[!0-9]*) abort_reason=BATTERY_VOLTAGE_INVALID; printf 'ABORT|%s\n' "$abort_reason"; return 1 ;; esac
  cpu_temp=-1
  for temp_path in $cpu_temp_paths; do
    read -r one_temp < "$temp_path" 2>/dev/null || continue
    case "$one_temp" in ''|*[!0-9]*) continue ;; esac
    if [ "$one_temp" -gt "$cpu_temp" ]; then cpu_temp=$one_temp; fi
  done
  read -r actual_freq < "$policy_path/scaling_cur_freq" 2>/dev/null || actual_freq=
  printf 'P|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s\n' \
    "$point_id" "$sample_rep" "$sample_phase" "$ci0" "$current" "$ci1" \
    "$vi0" "$voltage" "$vi1" "$status" "$battery_temp" "$cpu_temp" "$actual_freq" "$charge"
  if [ "$battery_temp" -lt 1000 ] || [ "$cpu_temp" -lt 1000 ]; then
    abort_reason=THERMAL_SENSOR_INVALID
    printf 'ABORT|%s|%s|%s\n' "$abort_reason" "$battery_temp" "$cpu_temp"
    return 1
  fi
  if [ "$cpu_temp" -ge "$cpu_limit" ]; then
    abort_reason=CPU_TEMPERATURE_LIMIT
    printf 'ABORT|%s|%s|%s\n' "$abort_reason" "$cpu_temp" "$cpu_limit"
    return 1
  fi
  if [ "$battery_temp" -ge "$battery_limit" ]; then
    abort_reason=BATTERY_TEMPERATURE_LIMIT
    printf 'ABORT|%s|%s|%s\n' "$abort_reason" "$battery_temp" "$battery_limit"
    return 1
  fi
  return 0
}

sample_idle() {
  idle_phase=$1
  idle_rep=$2
  idle_seconds=$3
  idle_i=0
  while [ "$idle_i" -lt "$idle_seconds" ]; do
    sample_once "$idle_phase" "$idle_rep" || return 1
    idle_i=$((idle_i + 1))
    if [ "$idle_i" -lt "$idle_seconds" ]; then sleep 1; fi
  done
  return 0
}

run_benchmark() {
  run_rep=$1
  printf 'RUN|%s|%s|%s\n' "$point_id" "$run_rep" "$cpu"
  "$bench" "$cpu" "$warmup" "$measure" &
  bench_pid=$!
  while kill -0 "$bench_pid" 2>/dev/null; do
    sample_once active "$run_rep"
    sample_result=$?
    if [ "$sample_result" -ne 0 ]; then
      kill -TERM "$bench_pid" 2>/dev/null
      wait "$bench_pid" 2>/dev/null
      return 1
    fi
    sleep 1
  done
  wait "$bench_pid"
  bench_status=$?
  if [ "$bench_status" -ne 0 ]; then
    printf 'BENCH_ERROR|%s|%s\n' "$run_rep" "$bench_status"
    return 1
  fi
  return 0
}

if ! sample_idle baseline_pre 0 "$baseline_seconds"; then
  printf 'POINT_ABORT|%s\n' "$abort_reason"
  exit 0
fi
rep=1
while [ "$rep" -le "$repetitions" ]; do
  if ! run_benchmark "$rep"; then
    printf 'POINT_ABORT|%s\n' "${abort_reason:-BENCHMARK_FAILED}"
    exit 0
  fi
  if [ "$rep" -lt "$repetitions" ]; then
    if ! sample_idle cooldown "$rep" "$cooldown_seconds"; then
      printf 'POINT_ABORT|%s\n' "$abort_reason"
      exit 0
    fi
  fi
  rep=$((rep + 1))
done
if ! sample_idle baseline_post 0 "$baseline_seconds"; then
  printf 'POINT_ABORT|%s\n' "$abort_reason"
  exit 0
fi
printf 'POINT_DONE|%s\n' "$point_id"
'''

    substitutions = {
        "__POLICY_PATH__": policy_path,
        "__BENCH_PATH__": bench_path,
        "__POINT_ID__": point_id,
        "__CPU__": str(point.cpu),
        "__TARGET__": str(point.frequency_khz),
        "__EXPECTED_MIN__": str(point.original_min_khz),
        "__EXPECTED_MAX__": str(point.original_max_khz),
        "__WARMUP__": str(warmup_s),
        "__MEASURE__": str(measure_s),
        "__REPETITIONS__": str(repetitions),
        "__BASELINE__": str(baseline_s),
        "__COOLDOWN__": str(cooldown_s),
        "__CPU_LIMIT__": str(cpu_limit_mc),
        "__BATTERY_LIMIT__": str(battery_limit_mc),
        "__BATTERY_TEMP_PATH__": battery_path,
        "__CPU_TEMP_PATHS__": cpu_path_list,
    }
    for marker, value in substitutions.items():
        script = script.replace(marker, value)
    return script


def _restore_policy(client: AdbClient, policy: CpuPolicy) -> str:
    if policy.scaling_min_khz is None or policy.scaling_max_khz is None:
        raise AdbError("cannot restore missing cpufreq bounds for %s" % policy.name)
    path = CPUFREQ_ROOT + "/" + policy.name
    script = """policy='%s'
printf '%%s\\n' '%s' > \"$policy/scaling_max_freq\" || exit 2
printf '%%s\\n' '%s' > \"$policy/scaling_min_freq\" || exit 3
read -r min < \"$policy/scaling_min_freq\"
read -r max < \"$policy/scaling_max_freq\"
printf 'RESTORE|%%s|%%s|%%s\\n' \"$policy\" \"$min\" \"$max\"""" % (
        path,
        policy.scaling_max_khz,
        policy.scaling_min_khz,
    )
    return client.run_root_script(script)


def _verify_restored(client: AdbClient, original: PreflightReport) -> None:
    current = collect_preflight(client)
    mismatches = []
    for expected in original.policies:
        actual = next((p for p in current.policies if p.name == expected.name), None)
        if actual is None or (
            actual.scaling_min_khz != expected.scaling_min_khz
            or actual.scaling_max_khz != expected.scaling_max_khz
        ):
            mismatches.append(expected.name)
    if mismatches:
        raise AdbError("cpufreq restoration mismatch: " + ", ".join(mismatches))


def deploy_benchmark(client: AdbClient, binary_path: Path) -> str:
    if not binary_path.is_file():
        raise ValueError("benchmark binary does not exist: %s" % binary_path)
    token = uuid.uuid4().hex
    remote_bench = REMOTE_DIR + "/cpu_mix_bench_" + token
    remote_staging = "/data/local/tmp/.cpu-efficiency-lab-bench-" + token
    client.run_root_script("mkdir -p '%s' && test ! -e '%s'" % (REMOTE_DIR, remote_bench))
    try:
        client.run(["push", str(binary_path), remote_staging], timeout_s=45.0)
    except (AdbError, OSError):
        try:
            client.run_root_script("rm -f '%s'" % remote_staging)
        except (AdbError, OSError):
            pass
        raise
    try:
        client.run_root_script(
            "test -f '%s' && test ! -e '%s' && mv '%s' '%s' && chmod 755 '%s' && test -x '%s'"
            % (remote_staging, remote_bench, remote_staging, remote_bench, remote_bench, remote_bench)
        )
    except (AdbError, OSError):
        try:
            client.run_root_script("rm -f '%s'" % remote_staging)
        except (AdbError, OSError):
            pass
        raise
    return remote_bench


def randomized_points(report: PreflightReport, seed: int = 20261110) -> List[SweepPoint]:
    points = available_points(report)
    random.Random(seed).shuffle(points)
    return points


def execute_point(
    client: AdbClient,
    point: SweepPoint,
    cpu_temp_paths: Sequence[str],
    battery_temp_path: str,
    warmup_s: int = DEFAULT_WARMUP_S,
    measure_s: int = DEFAULT_MEASURE_S,
    repetitions: int = DEFAULT_REPETITIONS,
    baseline_s: int = DEFAULT_BASELINE_S,
    cooldown_s: int = DEFAULT_COOLDOWN_S,
    bench_path: str = REMOTE_BENCH,
) -> str:
    script = build_point_script(
        point,
        cpu_temp_paths,
        battery_temp_path,
        warmup_s,
        measure_s,
        repetitions,
        baseline_s,
        cooldown_s,
        bench_path=bench_path,
    )
    planned_s = baseline_s * 2 + repetitions * (warmup_s + measure_s) + max(0, repetitions - 1) * cooldown_s
    return client.run_root_script(script, timeout_s=planned_s * 1.6 + 45.0)


def run_sweep(
    client: AdbClient,
    binary_path: Path,
    raw_log_path: Path,
    warmup_s: int = DEFAULT_WARMUP_S,
    measure_s: int = DEFAULT_MEASURE_S,
    repetitions: int = DEFAULT_REPETITIONS,
    baseline_s: int = DEFAULT_BASELINE_S,
    cooldown_s: int = DEFAULT_COOLDOWN_S,
    preflight: Optional[PreflightReport] = None,
) -> Tuple[PreflightReport, List[SweepPoint], str]:
    report = preflight or collect_preflight(client)
    if not report.ready_for_capture:
        raise AdbError("device preflight failed for root battery capture")
    if report.nodes.get("status", {}).get("value", "").lower() != "discharging":
        raise AdbError("CPU sweep requires the battery to be discharging and unplugged")
    points = randomized_points(report)
    if not points:
        raise AdbError("no frequency points are available under current scaling caps")
    active_work_s = len(points) * repetitions * (warmup_s + measure_s)
    if repetitions < 1 or min(warmup_s, measure_s, baseline_s, cooldown_s) < 1:
        raise ValueError("sweep durations and repetitions must be positive")
    if active_work_s < 1800:
        raise ValueError("CPU sweep must contain at least 30 minutes of active benchmark work")
    cpu_paths, battery_path, thermal_values = discover_thermal_sensors(client)
    cpu_initial = [value for kind, value in thermal_values.items() if kind != "battery"]
    battery_initial = thermal_values.get("battery")
    if not cpu_initial or battery_initial is None or min(cpu_initial + [battery_initial]) < 1000:
        raise AdbError("thermal sensor values are invalid or not in expected millidegrees Celsius")
    if max(cpu_initial) >= CPU_TEMP_LIMIT_MC or battery_initial >= BATTERY_TEMP_LIMIT_MC:
        raise AdbError("device is already at or above a thermal abort threshold")

    if raw_log_path.exists():
        raise FileExistsError("raw log already exists: %s" % raw_log_path)
    raw_log_path.parent.mkdir(parents=True, exist_ok=True)
    raw_log_path.open("x", encoding="utf-8").close()
    bench_path = deploy_benchmark(client, binary_path)
    with raw_log_path.open("a", encoding="utf-8") as handle:
        handle.write("DEPLOYED|%s\n" % bench_path)

    with raw_log_path.open("a", encoding="utf-8") as handle:
        handle.write("SMOKE_EXEC_BEGIN\n")
    smoke_output = client.run_root_script("'%s' 0 0.2 1" % bench_path, timeout_s=10.0)
    with raw_log_path.open("a", encoding="utf-8") as handle:
        handle.write(smoke_output)
        if not smoke_output.endswith("\n"):
            handle.write("\n")
    if "B|MEASURE_END|" not in smoke_output:
        raise AdbError("deployed benchmark failed its 1-second execution smoke test")

    smoke_point = min(
        (point for point in points if point.policy == "policy0"),
        key=lambda point: point.frequency_khz,
        default=points[0],
    )
    try:
        smoke_script_output = execute_point(
            client,
            smoke_point,
            cpu_paths,
            battery_path,
            warmup_s=1,
            measure_s=2,
            repetitions=1,
            baseline_s=1,
            cooldown_s=1,
            bench_path=bench_path,
        )
    finally:
        _restore_policy(client, next(policy for policy in report.policies if policy.name == smoke_point.policy))
    with raw_log_path.open("a", encoding="utf-8") as handle:
        handle.write("CONTROL_SMOKE_BEGIN\n")
        handle.write(smoke_script_output)
        if not smoke_script_output.endswith("\n"):
            handle.write("\n")
    if "POINT_DONE|%s" % smoke_point.point_id not in smoke_script_output:
        raise AdbError("frequency lock/restore smoke test failed; see %s" % raw_log_path)
    _verify_restored(client, report)
    with raw_log_path.open("a", encoding="utf-8") as handle:
        handle.write("SWEEP_BEGIN\n")

    abort = None
    active_point = None
    try:
        for index, point in enumerate(points, 1):
            print(
                "[%02d/%02d] %s CPU%d %d kHz (current cap %d kHz)"
                % (index, len(points), point.policy, point.cpu, point.frequency_khz, point.original_max_khz),
                flush=True,
            )
            active_point = point
            try:
                output = execute_point(
                    client,
                    point,
                    cpu_paths,
                    battery_path,
                    warmup_s,
                    measure_s,
                    repetitions,
                    baseline_s,
                    cooldown_s,
                    bench_path=bench_path,
                )
            except (AdbError, OSError, ValueError) as exc:
                abort = "%s: %s" % (point.point_id, exc)
                with raw_log_path.open("a", encoding="utf-8") as handle:
                    handle.write("HOST_ERROR|%s\n" % abort)
                break
            with raw_log_path.open("a", encoding="utf-8") as handle:
                handle.write(output)
                if not output.endswith("\n"):
                    handle.write("\n")
                handle.flush()
            if "ABORT|" in output or "BENCH_ERROR|" in output or "POINT_ABORT|" in output:
                abort = point.point_id + " aborted; see raw log"
                break
            if "POINT_DONE|%s" % point.point_id not in output:
                abort = point.point_id + " did not report POINT_DONE"
                break
            try:
                _verify_restored(client, report)
            except AdbError as exc:
                abort = "restore verification failed after %s: %s" % (point.point_id, exc)
                break
            active_point = None
    finally:
        if active_point is not None:
            original_policy = next(policy for policy in report.policies if policy.name == active_point.policy)
            try:
                _restore_policy(client, original_policy)
            except (AdbError, OSError) as exc:
                abort = (abort + "; " if abort else "") + "restore error for %s: %s" % (active_point.policy, exc)

    _verify_restored(client, report)
    return report, points, abort or ""
