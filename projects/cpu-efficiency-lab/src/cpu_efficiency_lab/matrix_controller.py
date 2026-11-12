"""Temporary CPU hotplug/frequency configuration with device-side restoration."""

from __future__ import annotations

import re
import shlex
import secrets
import time
from typing import TYPE_CHECKING, Dict, List, Optional, Sequence, Union

if TYPE_CHECKING:
    from .low_noise_environment import NoiseGuard

from .adb import AdbClient, AdbError
from .config import CPUFREQ_ROOT
from .matrix_plan import MatrixConfig, policy_by_cpu, validate_matrix_config
from .models import PreflightReport
from .preflight import collect_preflight
from .sweep import BATTERY_TEMP_LIMIT_MC, CPU_TEMP_LIMIT_MC, REMOTE_BENCH, REMOTE_DIR
from .topology import CpuTopology, discover_cpu_topology


# Two on-device fault-injection rounds each passed 11/11 controlled cases:
# data/phase-i-recovery-verify.* and data/phase-k-recovery-revalidate.*.
# These tests do not eliminate the sysfs check/write race with system limits;
# an indistinguishable concurrent change may be overwritten before detection.
# Keep the gate fail-closed until that remaining risk is independently resolved.
# 2026-11-12: a temporarily-opened gate run confirmed the risk is real — the
# system concurrently re-clamped policy0 to 384000-1785600 and policy6 max to
# 2649600 mid-run, so the conservative restore correctly reported RESTRICTED.
INDEPENDENT_RECOVERY_VERIFIED = False


class MatrixConfigError(AdbError):
    def __init__(self, message: str, remote_output: str = "") -> None:
        super().__init__(message)
        self.remote_output = remote_output


def require_verified_hotplug_recovery(confirmed: bool = False) -> None:
    """Require validated detached recovery plus explicit per-run operator confirmation."""
    if not INDEPENDENT_RECOVERY_VERIFIED:
        raise AdbError("matrix hotplug disabled: dynamic frequency caps and partial-lock recovery are not fully verified")
    if not confirmed:
        raise AdbError("matrix hotplug requires --confirm-independent-recovery")


def _safe_policy_restore_shell() -> str:
    """Restore only journal-owned locks; never write policies with unknown ownership.

    Process interruption is recoverable from an atomically replaced journal.
    sysfs has no compare-and-swap, so an indistinguishable concurrent system
    write remains a limitation; the matrix recovery gate stays fail-closed.
    """
    return r'''write_policy_journal() {
  pj_file=$1; pj_policy=$2; pj_min=$3; pj_max=$4; pj_target=$5; pj_stage=$6
  [ "$pj_file" != NA ] || return 1
  printf '%s %s %s %s %s\n' "$pj_policy" "$pj_min" "$pj_max" "$pj_target" "$pj_stage" > "$pj_file.tmp.$$" || return 1
  mv "$pj_file.tmp.$$" "$pj_file" || return 1
}
mark_policy_tainted() {
  [ "$1" != NA ] || return 0
  printf 'OBSERVED_POLICY_DRIFT\n' > "$1.tainted" || return 1
}
safe_restore_policy() {
  safe_policy=$1; safe_path=$2; safe_original_min=$3; safe_original_max=$4; safe_target=$5; safe_journal=${6:-NA}
  read -r safe_min < "$safe_path/scaling_min_freq" 2>/dev/null || { mark_policy_tainted "$safe_journal"; return 1; }
  read -r safe_max < "$safe_path/scaling_max_freq" 2>/dev/null || { mark_policy_tainted "$safe_journal"; return 1; }
  for safe_number in "$safe_min" "$safe_max" "$safe_original_min" "$safe_original_max"; do
    case "$safe_number" in ''|*[!0-9]*) return 1 ;; esac
  done
  if [ "$safe_target" != NA ]; then
    case "$safe_target" in ''|*[!0-9]*) return 1 ;; esac
    if [ "$safe_target" -lt "$safe_original_min" ] || [ "$safe_target" -gt "$safe_original_max" ]; then return 2; fi
  fi
  safe_stage=NONE
  if [ "$safe_journal" != NA ] && [ -f "$safe_journal" ]; then
    read -r sj_policy sj_min sj_max sj_target sj_stage sj_extra < "$safe_journal" || return 2
    if [ "$sj_policy" != "$safe_policy" ] || [ "$sj_min" != "$safe_original_min" ] || \
       [ "$sj_max" != "$safe_original_max" ] || [ "$sj_target" != "$safe_target" ] || [ -n "$sj_extra" ]; then
      printf 'POLICY_JOURNAL_INVALID|%s\n' "$safe_policy"
      return 2
    fi
    case "$sj_stage" in APPLY_MAX_INTENT|APPLY_MIN_INTENT|LOCKED|RESTORE_MIN_INTENT|RESTORE_MAX_INTENT|RESTORED) safe_stage=$sj_stage ;;
      *) printf 'POLICY_JOURNAL_INVALID|%s\n' "$safe_policy"; return 2 ;;
    esac
  fi
  # A previous observation of interference remains inconclusive even if an
  # external writer has since put the numeric bounds back at the snapshot.
  # Validate the journal and taint BEFORE the ALREADY_ORIGINAL shortcut.
  if [ "$safe_journal" != NA ] && [ -f "$safe_journal.tainted" ]; then
    printf 'POLICY_RESTORE_RESTRICTED|%s|%s|%s\n' "$safe_policy" "$safe_min" "$safe_max"
    return 2
  fi
  if [ "$safe_min" = "$safe_original_min" ] && [ "$safe_max" = "$safe_original_max" ]; then
    printf 'POLICY_RESTORE|%s|%s|%s|ALREADY_ORIGINAL\n' "$safe_policy" "$safe_min" "$safe_max"
    return 0
  fi
  safe_owned=0
  if [ "$safe_target" != NA ] && [ "$safe_max" = "$safe_target" ]; then
    case "$safe_stage" in
      APPLY_MAX_INTENT|RESTORE_MAX_INTENT)
        [ "$safe_min" = "$safe_original_min" ] && safe_owned=1 ;;
      APPLY_MIN_INTENT|RESTORE_MIN_INTENT)
        if [ "$safe_min" = "$safe_original_min" ] || [ "$safe_min" = "$safe_target" ]; then safe_owned=1; fi ;;
      LOCKED) [ "$safe_min" = "$safe_target" ] && safe_owned=1 ;;
    esac
  fi
  if [ "$safe_owned" -eq 0 ]; then
    # Snapshot intersection is also a write to somebody else's shared request.
    # Do not tighten, widen, or replay any unowned state. Preserve it verbatim.
    mark_policy_tainted "$safe_journal" || { printf 'POLICY_TAINT_WRITE_FAILED|%s\n' "$safe_policy"; return 1; }
    printf 'POLICY_RESTORE_UNOWNED|%s|%s|%s\n' "$safe_policy" "$safe_min" "$safe_max"
    printf 'POLICY_RESTORE_RESTRICTED|%s|%s|%s\n' "$safe_policy" "$safe_min" "$safe_max"
    return 2
  fi
  read -r safe_check_min < "$safe_path/scaling_min_freq" 2>/dev/null || { mark_policy_tainted "$safe_journal"; return 1; }
  read -r safe_check_max < "$safe_path/scaling_max_freq" 2>/dev/null || { mark_policy_tainted "$safe_journal"; return 1; }
  if [ "$safe_check_min" != "$safe_min" ] || [ "$safe_check_max" != "$safe_max" ]; then
    mark_policy_tainted "$safe_journal" || printf 'POLICY_TAINT_WRITE_FAILED|%s\n' "$safe_policy"
    printf 'POLICY_RESTORE_CHANGED|%s|%s|%s\n' "$safe_policy" "$safe_check_min" "$safe_check_max"
    return 2
  fi
  if [ "$safe_owned" -eq 1 ]; then
    write_policy_journal "$safe_journal" "$safe_policy" "$safe_original_min" "$safe_original_max" "$safe_target" RESTORE_MIN_INTENT || { mark_policy_tainted "$safe_journal"; return 1; }
    read -r safe_check_min < "$safe_path/scaling_min_freq" 2>/dev/null || { mark_policy_tainted "$safe_journal"; return 1; }
    read -r safe_check_max < "$safe_path/scaling_max_freq" 2>/dev/null || { mark_policy_tainted "$safe_journal"; return 1; }
    if [ "$safe_check_min" != "$safe_min" ] || [ "$safe_check_max" != "$safe_target" ]; then
      mark_policy_tainted "$safe_journal" || printf 'POLICY_TAINT_WRITE_FAILED|%s\n' "$safe_policy"
      printf 'POLICY_RESTORE_CHANGED|%s|%s|%s\n' "$safe_policy" "$safe_check_min" "$safe_check_max"; return 2
    fi
    if [ "$safe_min" != "$safe_original_min" ]; then
      printf '%s\n' "$safe_original_min" > "$safe_path/scaling_min_freq" 2>/dev/null || { mark_policy_tainted "$safe_journal"; return 1; }
    fi
    read -r safe_check_min < "$safe_path/scaling_min_freq" 2>/dev/null || { mark_policy_tainted "$safe_journal"; return 1; }
    read -r safe_check_max < "$safe_path/scaling_max_freq" 2>/dev/null || { mark_policy_tainted "$safe_journal"; return 1; }
    if [ "$safe_check_min" != "$safe_original_min" ] || [ "$safe_check_max" != "$safe_target" ]; then
      mark_policy_tainted "$safe_journal" || printf 'POLICY_TAINT_WRITE_FAILED|%s\n' "$safe_policy"
      printf 'POLICY_RESTORE_CHANGED|%s|%s|%s\n' "$safe_policy" "$safe_check_min" "$safe_check_max"; return 2
    fi
    write_policy_journal "$safe_journal" "$safe_policy" "$safe_original_min" "$safe_original_max" "$safe_target" RESTORE_MAX_INTENT || { mark_policy_tainted "$safe_journal"; return 1; }
    if [ "${fault_injection:-0}" = during_restore_after_min ] || \
       { [ "${fault_injection:-0}" = during_restore_policy6_after_min ] && [ "$safe_policy" = policy6 ]; }; then
      printf 'FAULT_INJECTION|SIGKILL_DURING_POLICY_RESTORE|%s\n' "$safe_policy"
      kill -KILL "$main_pid" 2>/dev/null
      exit 67
    fi
    read -r safe_check_min < "$safe_path/scaling_min_freq" 2>/dev/null || { mark_policy_tainted "$safe_journal"; return 1; }
    read -r safe_check_max < "$safe_path/scaling_max_freq" 2>/dev/null || { mark_policy_tainted "$safe_journal"; return 1; }
    if [ "$safe_check_min" != "$safe_original_min" ] || [ "$safe_check_max" != "$safe_target" ]; then
      mark_policy_tainted "$safe_journal" || printf 'POLICY_TAINT_WRITE_FAILED|%s\n' "$safe_policy"
      printf 'POLICY_RESTORE_CHANGED|%s|%s|%s\n' "$safe_policy" "$safe_check_min" "$safe_check_max"; return 2
    fi
    if [ "$safe_target" != "$safe_original_max" ]; then
      printf '%s\n' "$safe_original_max" > "$safe_path/scaling_max_freq" 2>/dev/null || { mark_policy_tainted "$safe_journal"; return 1; }
    fi
    read -r safe_final_min < "$safe_path/scaling_min_freq" 2>/dev/null || { mark_policy_tainted "$safe_journal"; return 1; }
    read -r safe_final_max < "$safe_path/scaling_max_freq" 2>/dev/null || { mark_policy_tainted "$safe_journal"; return 1; }
    if [ "$safe_final_min" != "$safe_original_min" ] || [ "$safe_final_max" != "$safe_original_max" ]; then
      mark_policy_tainted "$safe_journal" || printf 'POLICY_TAINT_WRITE_FAILED|%s\n' "$safe_policy"
      printf 'POLICY_RESTORE_CHANGED|%s|%s|%s\n' "$safe_policy" "$safe_final_min" "$safe_final_max"; return 2
    fi
    write_policy_journal "$safe_journal" "$safe_policy" "$safe_original_min" "$safe_original_max" "$safe_target" RESTORED || return 1
    printf 'POLICY_RESTORE|%s|%s|%s|JOURNALED\n' "$safe_policy" "$safe_final_min" "$safe_final_max"
    return 0
  fi
  return 2
}
'''


def _watchdog_shell_script() -> str:
    return 'set +e\n' + _safe_policy_restore_shell() + r'''
main_pid=$1; main_start=$2; watchdog_seconds=$3; recovery_dir=$4; bench=$5
fault_injection=0
core_states=$6; policy_states=$7; ready_file=$8; done_file=$9
request_file=${10}; recovered_file=${11}; bench_pid_file=${12}; log_file=${13}; policy_targets=${14}
watchdog_pid=$$
proc_start() { awk '{print $22}' "/proc/$1/stat" 2>/dev/null; }
main_alive() {
  current=$(proc_start "$main_pid")
  [ -n "$current" ] && [ "$current" = "$main_start" ]
}
kill_benchmark() {
  [ -f "$bench_pid_file" ] || return 0
  read -r candidate_pid candidate_start < "$bench_pid_file" 2>/dev/null || return 0
  case "$candidate_pid" in ''|*[!0-9]*) return 0 ;; esac
  [ -n "$candidate_start" ] || return 0
  current=$(proc_start "$candidate_pid")
  [ "$current" = "$candidate_start" ] || return 0
  kill -TERM "$candidate_pid" 2>/dev/null
  sleep 2
  current=$(proc_start "$candidate_pid")
  if [ "$current" = "$candidate_start" ]; then kill -KILL "$candidate_pid" 2>/dev/null; fi
}
restore_snapshot() {
  errors=0
  restricted=0
  for wanted in 1 0; do
    for record in $core_states; do
      cpu=${record%%:*}; state=${record#*:}
      [ "$cpu" = 0 ] && continue
      [ "$state" = "$wanted" ] || continue
      path=/sys/devices/system/cpu/cpu$cpu/online
      if [ -e "$path" ]; then
        printf '%s\n' "$state" > "$path" 2>/dev/null || errors=1
        read -r actual < "$path" 2>/dev/null || actual=unknown
        printf 'WATCHDOG_CORE|%s|%s\n' "$cpu" "$actual" >> "$log_file"
        [ "$actual" = "$state" ] || errors=1
      else
        errors=1
      fi
    done
  done
  for record in $policy_states; do
    policy=${record%%:*}; rest=${record#*:}; min=${rest%%:*}; max=${rest#*:}
    path=/sys/devices/system/cpu/cpufreq/$policy
    wait_i=0
    while [ ! -d "$path" ] && [ "$wait_i" -lt 15 ]; do sleep 1; wait_i=$((wait_i + 1)); done
    if [ ! -d "$path" ]; then errors=1; printf 'WATCHDOG_POLICY_MISSING|%s\n' "$policy" >> "$log_file"; continue; fi
    target=NA
    for spec in $policy_targets; do
      spec_policy=${spec%%:*}; spec_target=${spec##*:}
      [ "$spec_policy" = "$policy" ] && target=$spec_target
    done
    safe_restore_policy "$policy" "$path" "$min" "$max" "$target" "$recovery_dir/policy-$policy.state" >> "$log_file" 2>&1
    policy_result=$?
    if [ "$policy_result" -eq 2 ]; then restricted=1; elif [ "$policy_result" -ne 0 ]; then errors=1; fi
  done
  [ "$errors" -eq 0 ] || return 1
  [ "$restricted" -eq 0 ] || return 2
  return 0
}
printf 'WATCHDOG_READY %s\n' "$watchdog_pid" > "$ready_file"
printf 'WATCHDOG_STARTED|%s|%s\n' "$main_pid" "$main_start" >> "$log_file"
elapsed=0
while :; do
  [ -f "$done_file" ] && { printf 'WATCHDOG_DONE\n' >> "$log_file"; exit 0; }
  if [ -f "$request_file" ] || ! main_alive; then
    printf 'WATCHDOG_RECOVERY_START|%s\n' "$elapsed" >> "$log_file"
    kill_benchmark
    attempt=0
    while [ "$attempt" -lt 60 ]; do
      restore_snapshot
      snapshot_status=$?
      if [ "$snapshot_status" -eq 0 ]; then
        printf 'WATCHDOG_RECOVERY_OK|%s\n' "$attempt" >> "$log_file"
        printf 'OK\n' > "$recovered_file"
        exit 0
      fi
      if [ "$snapshot_status" -eq 2 ]; then
        printf 'WATCHDOG_RECOVERY_RESTRICTED|%s\n' "$attempt" >> "$log_file"
        printf 'RESTRICTED\n' > "$recovered_file"
        exit 1
      fi
      attempt=$((attempt + 1))
      sleep 1
    done
    printf 'WATCHDOG_RECOVERY_FAILED\n' >> "$log_file"
    exit 1
  fi
  if [ "$elapsed" -ge "$watchdog_seconds" ]; then
    printf 'WATCHDOG_TIMEOUT|%s\n' "$elapsed" >> "$log_file"
    kill_benchmark
    kill -TERM "$main_pid" 2>/dev/null
    term_wait=0
    while main_alive && [ "$term_wait" -lt 12 ]; do sleep 1; term_wait=$((term_wait + 1)); done
    if main_alive; then kill -KILL "$main_pid" 2>/dev/null; fi
    sleep 1
    continue
  fi
  sleep 1
  elapsed=$((elapsed + 1))
done
'''


def build_matrix_config_script(
    config: MatrixConfig,
    topology: CpuTopology,
    report: PreflightReport,
    cpu_temp_paths: Sequence[str],
    battery_temp_path: str,
    bench_path: str,
    recovery_id: Optional[str] = None,
    fault_injection: Union[bool, str] = False,
    minimum_battery_percent: int = 60,
    environment_guard: Optional["NoiseGuard"] = None,
) -> str:
    validate_matrix_config(config, topology, report)
    fault_mode = "before_benchmark" if fault_injection is True else ("0" if fault_injection is False else fault_injection)
    if fault_mode not in (
        "0", "before_benchmark", "during_benchmark", "before_registration",
        "after_registration", "after_go", "after_first_offline", "after_policy_max",
        "after_policy_min", "during_restore_after_min", "after_policy6_max",
        "after_policy6_min", "during_restore_policy6_after_min",
    ):
        raise ValueError("invalid fault injection mode")
    if not 1 <= minimum_battery_percent <= 100:
        raise ValueError("minimum battery percentage must be between 1 and 100")
    recovery_id = recovery_id or secrets.token_hex(16)
    if not re.fullmatch(r"[0-9a-f]{32}", recovery_id):
        raise ValueError("invalid recovery id")
    if not cpu_temp_paths or any(
        not re.fullmatch(r"/sys/class/thermal/thermal_zone[0-9]+/temp", path)
        for path in cpu_temp_paths
    ):
        raise ValueError("invalid CPU thermal sensor paths")
    if not re.fullmatch(r"/sys/class/thermal/thermal_zone[0-9]+/temp", battery_temp_path):
        raise ValueError("invalid battery thermal sensor path")
    if bench_path != REMOTE_BENCH and not re.fullmatch(
        re.escape(REMOTE_DIR + "/cpu_mix_bench_") + r"[0-9a-f]{32}", bench_path
    ):
        raise ValueError("unexpected benchmark path")

    all_cpus = tuple(core.cpu_id for core in topology.cores)
    if topology.online_cpus != all_cpus:
        raise ValueError("strict matrix testing requires all discovered CPUs online at baseline")
    policies = {policy.name: policy for policy in report.policies}
    cpu_policy = policy_by_cpu(report)
    policy_specs = []
    for policy_name, target in config.policy_targets_khz:
        policy = policies[policy_name]
        policy_specs.append(
            "%s:%d:%d:%d"
            % (policy_name, policy.scaling_min_khz, policy.scaling_max_khz, target)
        )
    target_cpus = " ".join(str(cpu) for cpu in config.target_cpus)
    worker_csv = config.worker_csv
    all_cpu_list = " ".join(str(cpu) for cpu in all_cpus)
    expected_online = " ".join(str(cpu) for cpu in topology.online_cpus)
    desired_online = " ".join(str(cpu) for cpu in config.desired_online_cpus)
    cpu_path_list = " ".join(cpu_temp_paths)
    battery_path = "'" + battery_temp_path + "'"
    policy_specs_value = " ".join(policy_specs)
    bench_quoted = "'" + bench_path + "'"
    config_quoted = "'" + config.config_id + "'"
    min_specs = " ".join(
        "%s:%d:%d" % (policy.name, policy.scaling_min_khz, policy.scaling_max_khz)
        for policy in report.policies
        if policy.scaling_min_khz is not None and policy.scaling_max_khz is not None
    )

    script = r'''set +e
__SAFE_POLICY_RESTORE__
config_id=__CONFIG_ID__
workers="__TARGET_CPUS__"
worker_csv=__WORKER_CSV__
all_cpus="__ALL_CPUS__"
expected_online="__EXPECTED_ONLINE__"
desired_online="__DESIRED_ONLINE__"
policy_specs="__POLICY_SPECS__"
expected_policy_specs="__EXPECTED_POLICY_SPECS__"
bench=__BENCH__
warmup=__WARMUP__
measure=__MEASURE__
repetitions=__REPETITIONS__
baseline_seconds=__BASELINE__
cooldown_seconds=__COOLDOWN__
cpu_limit=__CPU_LIMIT__
battery_limit=__BATTERY_LIMIT__
minimum_battery_percent=__MINIMUM_BATTERY_PERCENT__
cpu_temp_paths="__CPU_TEMP_PATHS__"
battery_temp_path=__BATTERY_TEMP_PATH__
original_core_states=
original_policy_states="__EXPECTED_POLICY_SPECS__"
locked_policy_states=
restore_needed=0
abort_reason=
restore_errors=0
watchdog_pid=
bench_pid=
bench_start=
watchdog_seconds=__WATCHDOG_SECONDS__
fault_injection=__FAULT_INJECTION__
recovery_dir='__RECOVERY_DIR__'
watchdog_ready="$recovery_dir/ready"
watchdog_done="$recovery_dir/done"
watchdog_request="$recovery_dir/recover"
watchdog_recovered="$recovery_dir/recovered"
bench_pid_file="$recovery_dir/bench.pid"
watchdog_log="$recovery_dir/watchdog.log"
controller_log="$recovery_dir/controller.log"
watchdog_script=__WATCHDOG_SCRIPT__

restore_policies() {
  for record in $original_policy_states; do
    policy=${record%%:*}
    rest=${record#*:}
    original_min=${rest%%:*}
    original_max=${rest#*:}
    path='__CPUFREQ_ROOT__'/$policy
    wait_i=0
    while [ ! -d "$path" ] && [ "$wait_i" -lt 10 ]; do sleep 1; wait_i=$((wait_i + 1)); done
    [ -d "$path" ] || { restore_errors=1; continue; }
    target=NA
    for spec in $policy_specs; do
      spec_policy=${spec%%:*}; spec_target=${spec##*:}
      [ "$spec_policy" = "$policy" ] && target=$spec_target
    done
    safe_restore_policy "$policy" "$path" "$original_min" "$original_max" "$target" "$recovery_dir/policy-$policy.state" || restore_errors=1
  done
  locked_policy_states=
}

restore_cores() {
  for desired_state in 1 0; do
    for record in $original_core_states; do
      cpu=${record%%:*}
      state=${record#*:}
      [ "$cpu" = 0 ] && continue
      [ "$state" = "$desired_state" ] || continue
      path=/sys/devices/system/cpu/cpu$cpu/online
      printf '%s\n' "$state" > "$path" 2>/dev/null || restore_errors=1
      read -r restored < "$path" 2>/dev/null || restored=unknown
      printf 'CORE_RESTORE|%s|%s\n' "$cpu" "$restored"
      if [ "$restored" != "$state" ]; then restore_errors=1; fi
    done
  done
}

stop_benchmark() {
  if [ -n "$bench_pid" ] && [ -n "$bench_start" ]; then
    current_start=$(awk '{print $22}' "/proc/$bench_pid/stat" 2>/dev/null)
    if [ "$current_start" = "$bench_start" ]; then
      kill -TERM "$bench_pid" 2>/dev/null
      wait_i=0
      while [ -e "/proc/$bench_pid" ] && [ "$wait_i" -lt 3 ]; do sleep 1; wait_i=$((wait_i + 1)); done
      current_start=$(awk '{print $22}' "/proc/$bench_pid/stat" 2>/dev/null)
      if [ "$current_start" = "$bench_start" ]; then kill -KILL "$bench_pid" 2>/dev/null; fi
    fi
    wait "$bench_pid" 2>/dev/null
  fi
  bench_pid=; bench_start=
  [ -n "$bench_pid_file" ] && printf '\n' > "$bench_pid_file" 2>/dev/null
}
restore_device() {
  if [ "$restore_needed" = 1 ]; then
    stop_benchmark
    restore_cores
    restore_policies
    restore_needed=0
    if [ "$restore_errors" -ne 0 ]; then
      printf 'RECOVER\n' > "$watchdog_request"
      wait_i=0
      while [ ! -f "$watchdog_recovered" ] && [ "$wait_i" -lt 20 ]; do sleep 1; wait_i=$((wait_i + 1)); done
      if [ -f "$watchdog_recovered" ]; then
        read -r recovery_status < "$watchdog_recovered" || recovery_status=UNKNOWN
        [ "$recovery_status" = OK ] && restore_errors=0
      fi
    fi
    if [ "$restore_errors" -eq 0 ]; then
      printf 'DONE\n' > "$watchdog_done"
      wait_i=0
      while [ -n "$watchdog_pid" ] && kill -0 "$watchdog_pid" 2>/dev/null && [ "$wait_i" -lt 3 ]; do sleep 1; wait_i=$((wait_i + 1)); done
    else
      printf 'RESTORE_ERROR|%s\n' "$config_id"
    fi
  fi
}
trap 'restore_device' EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

# Verify the complete baseline before arming the cleanup trap or writing sysfs.
for cpu in $all_cpus; do
  path=/sys/devices/system/cpu/cpu$cpu/online
  online=1
  if [ -e "$path" ]; then read -r online < "$path" || exit 20; fi
  case " $expected_online " in *" $cpu "*) expected=1 ;; *) expected=0 ;; esac
  if [ "$online" != "$expected" ]; then
    printf 'STALE_CORE|%s|%s|%s\n' "$cpu" "$online" "$expected"
    exit 21
  fi
  original_core_states="$original_core_states $cpu:$online"
done
for record in $expected_policy_specs; do
  policy=${record%%:*}
  rest=${record#*:}
  expected_min=${rest%%:*}
  expected_max=${rest#*:}
  path='__CPUFREQ_ROOT__'/$policy
  read -r observed_min < "$path/scaling_min_freq" || exit 22
  read -r observed_max < "$path/scaling_max_freq" || exit 23
  if [ "$observed_min" != "$expected_min" ] || [ "$observed_max" != "$expected_max" ]; then
    printf 'STALE_POLICY|%s|%s|%s|%s|%s\n' "$policy" "$observed_min" "$observed_max" "$expected_min" "$expected_max"
    exit 24
  fi
done
main_pid=$$
main_start=$(awk '{print $22}' "/proc/$main_pid/stat" 2>/dev/null)
if [ -z "$main_start" ]; then printf 'WATCHDOG_SETUP_FAIL|%s\n' "$config_id"; exit 44; fi
mkdir -m 700 "$recovery_dir" 2>/dev/null || { printf 'WATCHDOG_DIR_FAIL|%s\n' "$config_id"; exit 45; }
exec > "$controller_log" 2>&1
: > "$bench_pid_file"
restore_needed=1
nohup setsid sh -c "$watchdog_script" matrix-watchdog \
  "$main_pid" "$main_start" "$watchdog_seconds" "$recovery_dir" "$bench" \
  "$original_core_states" "$original_policy_states" "$watchdog_ready" "$watchdog_done" \
  "$watchdog_request" "$watchdog_recovered" "$bench_pid_file" "$watchdog_log" "$policy_specs" \
  </dev/null >/dev/null 2>&1 &
watchdog_launcher=$!
watchdog_pid=$watchdog_launcher
ready_wait=0
while [ ! -f "$watchdog_ready" ] && [ "$ready_wait" -lt 8 ]; do sleep 1; ready_wait=$((ready_wait + 1)); done
if [ ! -f "$watchdog_ready" ]; then printf 'WATCHDOG_SETUP_FAIL|%s\n' "$config_id"; exit 46; fi
read -r watchdog_marker watchdog_pid < "$watchdog_ready" || exit 47
[ "$watchdog_marker" = WATCHDOG_READY ] || { printf 'WATCHDOG_SETUP_FAIL|%s\n' "$config_id"; exit 48; }
case "$watchdog_pid" in ''|*[!0-9]*) printf 'WATCHDOG_SETUP_FAIL|%s\n' "$config_id"; exit 49 ;; esac
printf 'RECOVERY_PATH|%s\n' "$recovery_dir"

# CPU0 is deliberately never offlined: it remains the Android/system keeper.
for cpu in $workers; do
  [ "$cpu" = 0 ] && continue
  path=/sys/devices/system/cpu/cpu$cpu/online
  read -r online < "$path" || exit 25
  if [ "$online" != 1 ]; then
    printf '1\n' > "$path" || exit 26
  fi
  read -r online < "$path" || exit 27
  [ "$online" = 1 ] || exit 28
done
for cpu in $all_cpus; do
  [ "$cpu" = 0 ] && continue
  case " $desired_online " in *" $cpu "*) continue ;; esac
  path=/sys/devices/system/cpu/cpu$cpu/online
  read -r online < "$path" || exit 29
  if [ "$online" != 0 ]; then
    printf '0\n' > "$path" || exit 30
    if [ "$fault_injection" = after_first_offline ]; then
      printf 'FAULT_INJECTION|SIGKILL_AFTER_FIRST_OFFLINE|%s|%s\n' "$config_id" "$cpu"
      kill -KILL "$main_pid" 2>/dev/null
      exit 65
    fi
  fi
  read -r online < "$path" || exit 31
  [ "$online" = 0 ] || { printf 'CORE_OFFLINE_FAIL|%s|%s\n' "$cpu" "$online"; exit 32; }
done
for cpu in $all_cpus; do
  path=/sys/devices/system/cpu/cpu$cpu/online
  online=1
  if [ -e "$path" ]; then read -r online < "$path" || exit 33; fi
  case " $desired_online " in *" $cpu "*) expected=1 ;; *) expected=0 ;; esac
  printf 'CORE_STATE|%s|%s|%s\n' "$config_id" "$cpu" "$online"
  [ "$online" = "$expected" ] || exit 34
done
printf 'CORE_MASK_APPLIED|%s|%s|%s\n' "$config_id" "$workers" "$desired_online"

# Lock only policies hosting benchmark workers; keep every target within the pre-test range.
for record in $policy_specs; do
  policy=${record%%:*}
  rest=${record#*:}
  expected_min=${rest%%:*}
  rest=${rest#*:}
  expected_max=${rest%%:*}
  target=${rest#*:}
  path='__CPUFREQ_ROOT__'/$policy
  read -r current_min < "$path/scaling_min_freq" || exit 35
  read -r current_max < "$path/scaling_max_freq" || exit 36
  if [ "$current_min" -ne "$expected_min" ] || [ "$current_max" -ne "$expected_max" ]; then
    printf 'STALE_POLICY_AFTER_HOTPLUG|%s|%s|%s|%s|%s\n' "$policy" "$current_min" "$current_max" "$expected_min" "$expected_max"
    exit 37
  fi
  if [ "$target" -lt "$current_min" ] || [ "$target" -gt "$current_max" ] || [ "$target" -gt "$expected_max" ]; then
    printf 'CAP_VIOLATION|%s|%s|%s|%s\n' "$policy" "$target" "$current_min" "$current_max"
    exit 38
  fi
  locked_policy_states="$locked_policy_states $policy:$current_min:$current_max"
  policy_journal="$recovery_dir/policy-$policy.state"
  write_policy_journal "$policy_journal" "$policy" "$expected_min" "$expected_max" "$target" APPLY_MAX_INTENT || exit 68
  read -r check_min < "$path/scaling_min_freq" || { mark_policy_tainted "$policy_journal"; exit 35; }
  read -r check_max < "$path/scaling_max_freq" || { mark_policy_tainted "$policy_journal"; exit 36; }
  if [ "$check_min" != "$expected_min" ] || [ "$check_max" != "$expected_max" ]; then
    mark_policy_tainted "$policy_journal" || printf 'POLICY_TAINT_WRITE_FAILED|%s\n' "$policy"
    printf 'LOCK_INPUT_CHANGED|%s|%s|%s\n' "$policy" "$check_min" "$check_max"; exit 37
  fi
  printf '%s\n' "$target" > "$path/scaling_max_freq" || { mark_policy_tainted "$policy_journal"; exit 39; }
  if [ "$fault_injection" = after_policy_max ] || \
     { [ "$fault_injection" = after_policy6_max ] && [ "$policy" = policy6 ]; }; then
    printf 'FAULT_INJECTION|SIGKILL_AFTER_POLICY_MAX|%s|%s\n' "$config_id" "$policy"
    kill -KILL "$main_pid" 2>/dev/null
    exit 66
  fi
  read -r check_min < "$path/scaling_min_freq" || { mark_policy_tainted "$policy_journal"; exit 41; }
  read -r check_max < "$path/scaling_max_freq" || { mark_policy_tainted "$policy_journal"; exit 42; }
  if [ "$check_min" != "$expected_min" ] || [ "$check_max" != "$target" ]; then
    mark_policy_tainted "$policy_journal" || printf 'POLICY_TAINT_WRITE_FAILED|%s\n' "$policy"
    printf 'LOCK_MISMATCH|%s|%s|%s|%s\n' "$policy" "$check_min" "$check_max" "$target"; exit 43
  fi
  write_policy_journal "$policy_journal" "$policy" "$expected_min" "$expected_max" "$target" APPLY_MIN_INTENT || exit 68
  read -r check_min < "$path/scaling_min_freq" || { mark_policy_tainted "$policy_journal"; exit 41; }
  read -r check_max < "$path/scaling_max_freq" || { mark_policy_tainted "$policy_journal"; exit 42; }
  if [ "$check_min" != "$expected_min" ] || [ "$check_max" != "$target" ]; then
    mark_policy_tainted "$policy_journal" || printf 'POLICY_TAINT_WRITE_FAILED|%s\n' "$policy"
    printf 'LOCK_INPUT_CHANGED|%s|%s|%s\n' "$policy" "$check_min" "$check_max"; exit 37
  fi
  printf '%s\n' "$target" > "$path/scaling_min_freq" || { mark_policy_tainted "$policy_journal"; exit 40; }
  if [ "$fault_injection" = after_policy_min ] || \
     { [ "$fault_injection" = after_policy6_min ] && [ "$policy" = policy6 ]; }; then
    printf 'FAULT_INJECTION|SIGKILL_AFTER_POLICY_MIN|%s|%s\n' "$config_id" "$policy"
    kill -KILL "$main_pid" 2>/dev/null
    exit 69
  fi
  read -r applied_min < "$path/scaling_min_freq" || { mark_policy_tainted "$policy_journal"; exit 41; }
  read -r applied_max < "$path/scaling_max_freq" || { mark_policy_tainted "$policy_journal"; exit 42; }
  if [ "$applied_min" != "$target" ] || [ "$applied_max" != "$target" ]; then
    mark_policy_tainted "$policy_journal" || printf 'POLICY_TAINT_WRITE_FAILED|%s\n' "$policy"
    printf 'LOCK_MISMATCH|%s|%s|%s|%s\n' "$policy" "$applied_min" "$applied_max" "$target"
    exit 43
  fi
  write_policy_journal "$policy_journal" "$policy" "$expected_min" "$expected_max" "$target" LOCKED || exit 68
done
printf 'CONFIG_BEGIN|%s|%s|%s\n' "$config_id" "$workers" "$policy_specs"
if [ "$fault_injection" = during_restore_after_min ] || [ "$fault_injection" = during_restore_policy6_after_min ]; then
  printf 'FAULT_INJECTION|RESTORE_ARMED|%s\n' "$config_id"
  exit 0
fi
if [ "$fault_injection" = before_benchmark ]; then
  printf 'FAULT_INJECTION|SIGKILL_MAIN|%s\n' "$config_id"
  sleep 2
  kill -KILL "$main_pid" 2>/dev/null
  exit 60
fi

sample_once() {
  sample_phase=$1
  sample_rep=$2
__ENVIRONMENT_GUARD__
  read -r ci0 _ < /proc/uptime || return 50
  read -r current < /sys/class/power_supply/battery/current_now || return 51
  read -r ci1 _ < /proc/uptime || return 52
  read -r vi0 _ < /proc/uptime || return 53
  read -r voltage < /sys/class/power_supply/battery/voltage_now || return 54
  read -r vi1 _ < /proc/uptime || return 55
  read -r status < /sys/class/power_supply/battery/status 2>/dev/null || status=Unknown
  if [ "$status" != Discharging ]; then
    abort_reason=BATTERY_STATUS_CHANGED
    printf 'ABORT|%s|%s\n' "$abort_reason" "$status"
    return 1
  fi
  read -r battery_capacity < /sys/class/power_supply/battery/capacity 2>/dev/null || battery_capacity=-1
  case "$battery_capacity" in ''|*[!0-9]*) battery_capacity=-1 ;; esac
  read -r charge < /sys/class/power_supply/battery/charge_counter 2>/dev/null || charge=
  read -r battery_temp < "$battery_temp_path" 2>/dev/null || battery_temp=-1
  case "$battery_temp" in ''|*[!0-9]*) battery_temp=-1 ;; esac
  case "$current" in -*) current_digits=${current#-} ;; *) current_digits=$current ;; esac
  case "$current_digits" in ''|*[!0-9]*) abort_reason=CURRENT_INVALID; printf 'ABORT|%s\n' "$abort_reason"; return 1 ;; esac
  case "$voltage" in ''|*[!0-9]*) abort_reason=VOLTAGE_INVALID; printf 'ABORT|%s\n' "$abort_reason"; return 1 ;; esac
  for sample_spec in $policy_specs; do
    sample_policy=${sample_spec%%:*}
    sample_target=${sample_spec##*:}
    sample_path='__CPUFREQ_ROOT__'/$sample_policy
    read -r sample_min < "$sample_path/scaling_min_freq" 2>/dev/null || sample_min=unknown
    read -r sample_max < "$sample_path/scaling_max_freq" 2>/dev/null || sample_max=unknown
    if [ "$sample_min" != "$sample_target" ] || [ "$sample_max" != "$sample_target" ]; then
      mark_policy_tainted "$recovery_dir/policy-$sample_policy.state" || printf 'POLICY_TAINT_WRITE_FAILED|%s\n' "$sample_policy"
      abort_reason=POLICY_BOUNDS_CHANGED
      printf 'ABORT|%s|%s|%s|%s|%s\n' "$abort_reason" "$sample_policy" "$sample_min" "$sample_max" "$sample_target"
      return 1
    fi
  done
  sample_online=
  for sample_cpu in $all_cpus; do
    online_path=/sys/devices/system/cpu/cpu$sample_cpu/online
    online_state=1
    if [ -e "$online_path" ]; then read -r online_state < "$online_path" 2>/dev/null || online_state=unknown; fi
    case " $desired_online " in *" $sample_cpu "*) expected_state=1 ;; *) expected_state=0 ;; esac
    if [ "$online_state" != "$expected_state" ]; then
      abort_reason=CORE_MASK_CHANGED
      printf 'CORE_MASK_MISMATCH|%s|%s|%s|%s|%s\n' "$config_id" "$sample_phase" "$sample_cpu" "$online_state" "$expected_state"
      return 1
    fi
    if [ "$online_state" = 1 ]; then
      if [ -z "$sample_online" ]; then sample_online=$sample_cpu; else sample_online="$sample_online,$sample_cpu"; fi
    fi
  done
  cpu_temp=-1
  for temp_path in $cpu_temp_paths; do
    read -r one_temp < "$temp_path" 2>/dev/null || continue
    case "$one_temp" in ''|*[!0-9]*) continue ;; esac
    if [ "$one_temp" -gt "$cpu_temp" ]; then cpu_temp=$one_temp; fi
  done
  freq0=NA; freq6=NA
  read -r freq0 < '__CPUFREQ_ROOT__/policy0/scaling_cur_freq' 2>/dev/null || freq0=NA
  read -r freq6 < '__CPUFREQ_ROOT__/policy6/scaling_cur_freq' 2>/dev/null || freq6=NA
  case "$freq0" in ''|*[!0-9]*) freq0=NA ;; esac
  case "$freq6" in ''|*[!0-9]*) freq6=NA ;; esac
  printf 'M|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s\n' \
    "$config_id" "$sample_rep" "$sample_phase" "$ci0" "$current" "$ci1" \
    "$vi0" "$voltage" "$vi1" "$status" "$battery_temp" "$battery_capacity" \
    "$cpu_temp" "$freq0" "$freq6" "$charge" "$sample_online"
  if [ "$battery_capacity" -lt "$minimum_battery_percent" ]; then
    abort_reason=BATTERY_RESERVE
    printf 'ABORT|%s|%s|%s\n' "$abort_reason" "$battery_capacity" "$minimum_battery_percent"
    return 1
  fi
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
  printf 'RUN|%s|%s|%s|%s\n' "$config_id" "$run_rep" "${workers%% *}" "$worker_csv"
  # The child cannot exec the load until its PID/starttime is atomically
  # registered with the watchdog. If the controller dies earlier, it exits
  # without running the load, closing the unregistered-orphan race.
  bench_go="$recovery_dir/bench-go-$run_rep"
  (
    while [ ! -f "$bench_go" ]; do
      parent_start=$(awk '{print $22}' "/proc/$main_pid/stat" 2>/dev/null)
      [ -n "$parent_start" ] && [ "$parent_start" = "$main_start" ] || exit 71
      [ ! -f "$watchdog_request" ] || exit 72
      sleep 0.1
    done
    exec "$bench" "$worker_csv" "$warmup" "$measure"
  ) &
  bench_pid=$!
  if [ "$fault_injection" = before_registration ]; then
    printf 'FAULT_INJECTION|SIGKILL_BEFORE_REGISTRATION|%s|%s\n' "$config_id" "$bench_pid"
    sleep 1
    kill -KILL "$main_pid" 2>/dev/null
    exit 62
  fi
  bench_start=$(awk '{print $22}' "/proc/$bench_pid/stat" 2>/dev/null)
  if [ -z "$bench_start" ] || ! printf '%s %s\n' "$bench_pid" "$bench_start" > "$bench_pid_file.tmp" || ! mv "$bench_pid_file.tmp" "$bench_pid_file"; then
    abort_reason=BENCH_REGISTRATION_FAILED
    kill -TERM "$bench_pid" 2>/dev/null; wait "$bench_pid" 2>/dev/null
    bench_pid=; bench_start=
    return 1
  fi
  if [ "$fault_injection" = after_registration ]; then
    printf 'FAULT_INJECTION|SIGKILL_AFTER_REGISTRATION|%s|%s\n' "$config_id" "$bench_pid"
    sleep 1
    kill -KILL "$main_pid" 2>/dev/null
    exit 63
  fi
  if ! : > "$bench_go"; then
    abort_reason=BENCH_START_FAILED
    kill -TERM "$bench_pid" 2>/dev/null; wait "$bench_pid" 2>/dev/null
    bench_pid=; bench_start=
    printf '\n' > "$bench_pid_file"
    return 1
  fi
  if [ "$fault_injection" = after_go ]; then
    printf 'FAULT_INJECTION|SIGKILL_AFTER_GO|%s|%s\n' "$config_id" "$bench_pid"
    kill -KILL "$main_pid" 2>/dev/null
    exit 64
  fi
  if [ "$fault_injection" = during_benchmark ]; then
    printf 'FAULT_INJECTION|SIGKILL_MAIN_DURING_BENCH|%s|%s\n' "$config_id" "$bench_pid"
    sleep 1
    kill -KILL "$main_pid" 2>/dev/null
    exit 61
  fi
  while kill -0 "$bench_pid" 2>/dev/null; do
    sample_once active "$run_rep"
    sample_result=$?
    if [ "$sample_result" -ne 0 ]; then
      kill -TERM "$bench_pid" 2>/dev/null
      wait "$bench_pid" 2>/dev/null
      bench_pid=; bench_start=
      printf '\n' > "$bench_pid_file"
      return 1
    fi
    sleep 1
  done
  wait "$bench_pid"
  bench_status=$?
  bench_pid=; bench_start=
  printf '\n' > "$bench_pid_file"
  if [ "$bench_status" -ne 0 ]; then
    printf 'BENCH_ERROR|%s|%s\n' "$run_rep" "$bench_status"
    return 1
  fi
  # A boundary sample lets the existing time-window integrator bracket the
  # end of the measurement; samples beyond its two-second bound are ignored.
  sample_once active "$run_rep" || return 1
  return 0
}

if ! sample_idle baseline_pre 0 "$baseline_seconds"; then
  printf 'CONFIG_ABORT|%s|%s\n' "$config_id" "$abort_reason"
  exit 0
fi
rep=1
while [ "$rep" -le "$repetitions" ]; do
  if ! run_benchmark "$rep"; then
    printf 'CONFIG_ABORT|%s|%s\n' "$config_id" "${abort_reason:-BENCHMARK_FAILED}"
    exit 0
  fi
  if [ "$rep" -lt "$repetitions" ]; then
    if ! sample_idle cooldown "$rep" "$cooldown_seconds"; then
      printf 'CONFIG_ABORT|%s|%s\n' "$config_id" "$abort_reason"
      exit 0
    fi
  fi
  rep=$((rep + 1))
done
if ! sample_idle baseline_post 0 "$baseline_seconds"; then
  printf 'CONFIG_ABORT|%s|%s\n' "$config_id" "$abort_reason"
  exit 0
fi
printf 'CONFIG_DONE|%s\n' "$config_id"
'''
    substitutions = {
        "__SAFE_POLICY_RESTORE__": _safe_policy_restore_shell(),
        "__ENVIRONMENT_GUARD__": environment_guard.sample_shell() if environment_guard else "",
        "__CONFIG_ID__": config_quoted,
        "__TARGET_CPUS__": target_cpus,
        "__WORKER_CSV__": "'" + worker_csv + "'",
        "__ALL_CPUS__": all_cpu_list,
        "__EXPECTED_ONLINE__": expected_online,
        "__DESIRED_ONLINE__": desired_online,
        "__POLICY_SPECS__": policy_specs_value,
        "__EXPECTED_POLICY_SPECS__": min_specs,
        "__BENCH__": bench_quoted,
        "__WARMUP__": str(config.warmup_s),
        "__MEASURE__": str(config.measure_s),
        "__REPETITIONS__": str(config.repetitions),
        "__BASELINE__": str(config.baseline_s),
        "__COOLDOWN__": str(config.cooldown_s),
        "__CPU_LIMIT__": str(CPU_TEMP_LIMIT_MC),
        "__WATCHDOG_SECONDS__": str(config.planned_seconds + 60),
        "__FAULT_INJECTION__": fault_mode,
        "__RECOVERY_DIR__": REMOTE_DIR + "/matrix_recovery_" + recovery_id,
        "__WATCHDOG_SCRIPT__": shlex.quote(_watchdog_shell_script()),
        "__BATTERY_LIMIT__": str(BATTERY_TEMP_LIMIT_MC),
        "__MINIMUM_BATTERY_PERCENT__": str(minimum_battery_percent),
        "__CPU_TEMP_PATHS__": cpu_path_list,
        "__BATTERY_TEMP_PATH__": battery_path,
        "__CPUFREQ_ROOT__": "'" + CPUFREQ_ROOT + "'",
    }
    for marker, value in substitutions.items():
        script = script.replace(marker, value)
    return script


def restore_device_snapshot(
    client: AdbClient, topology: CpuTopology, report: PreflightReport
) -> None:
    states = " ".join("%d:%d" % (core.cpu_id, int(core.online)) for core in topology.cores)
    bounds = " ".join(
        "%s:%d:%d" % (policy.name, policy.scaling_min_khz, policy.scaling_max_khz)
        for policy in report.policies
        if policy.scaling_min_khz is not None and policy.scaling_max_khz is not None
    )
    script = r'''set +e
__SAFE_POLICY_RESTORE__
states="__STATES__"
bounds="__BOUNDS__"
errors=0
for desired_state in 1 0; do
  for record in $states; do
    cpu=${record%%:*}; state=${record#*:}
    [ "$cpu" = 0 ] && continue
    [ "$state" = "$desired_state" ] || continue
    path=/sys/devices/system/cpu/cpu$cpu/online
    printf '%s\n' "$state" > "$path" 2>/dev/null || errors=1
  done
done
for record in $bounds; do
  policy=${record%%:*}; rest=${record#*:}; original_min=${rest%%:*}; original_max=${rest#*:}
  path='__CPUFREQ_ROOT__'/$policy
  wait_i=0
  while [ ! -d "$path" ] && [ "$wait_i" -lt 10 ]; do sleep 1; wait_i=$((wait_i + 1)); done
  [ -e "$path/scaling_max_freq" ] || { errors=1; continue; }
  safe_restore_policy "$policy" "$path" "$original_min" "$original_max" NA || errors=1
done
[ "$errors" -eq 0 ] || { printf 'HOST_RESTORE_INCOMPLETE\n'; exit 1; }
printf 'HOST_RESTORE_DONE\n'
'''
    script = script.replace("__SAFE_POLICY_RESTORE__", _safe_policy_restore_shell())
    script = script.replace("__STATES__", states).replace("__BOUNDS__", bounds).replace(
        "__CPUFREQ_ROOT__", "'" + CPUFREQ_ROOT + "'"
    )
    client.run_root_script(script)


def verify_device_snapshot(
    client: AdbClient, topology: CpuTopology, report: PreflightReport
) -> None:
    actual_topology = discover_cpu_topology(client)
    if actual_topology.online_cpus != topology.online_cpus:
        raise AdbError(
            "CPU online mask restoration mismatch: expected %s got %s"
            % (topology.online_cpus, actual_topology.online_cpus)
        )
    actual_report = collect_preflight(client)
    mismatches = []
    for expected in report.policies:
        actual = next((policy for policy in actual_report.policies if policy.name == expected.name), None)
        if actual is None or (
            expected.scaling_min_khz is None or expected.scaling_max_khz is None
            or not expected.governor or not expected.related_cpus.strip()
            or actual.scaling_min_khz != expected.scaling_min_khz
            or actual.scaling_max_khz != expected.scaling_max_khz
            or actual.governor != expected.governor
            or actual.related_cpus.split() != expected.related_cpus.split()
        ):
            mismatches.append(expected.name)
    if mismatches:
        raise AdbError("cpufreq restoration mismatch: " + ", ".join(mismatches))


def _read_recovery_log(client: AdbClient, recovery_dir: str) -> str:
    path = shlex.quote(recovery_dir)
    script = "for f in controller.log watchdog.log recovered ready policy-policy0.state policy-policy6.state policy-policy0.state.tainted policy-policy6.state.tainted; do p=%s/$f; if [ -f $p ]; then printf 'RECOVERY_FILE|%%s\\n' $f; cat $p; fi; done" % path
    try:
        return client.run_root_script(script, timeout_s=8.0)
    except (AdbError, OSError):
        return "RECOVERY_LOG_UNAVAILABLE|%s" % recovery_dir


def _wait_for_device_snapshot(
    client: AdbClient, topology: CpuTopology, report: PreflightReport, timeout_s: float = 35.0
) -> Optional[str]:
    deadline = time.monotonic() + timeout_s
    last_error = None
    while time.monotonic() < deadline:
        try:
            verify_device_snapshot(client, topology, report)
            return None
        except (AdbError, OSError) as exc:
            last_error = exc
            time.sleep(1.0)
    return str(last_error or "device snapshot verification timed out")


def execute_matrix_config(
    client: AdbClient,
    config: MatrixConfig,
    topology: CpuTopology,
    report: PreflightReport,
    cpu_temp_paths: Sequence[str],
    battery_temp_path: str,
    bench_path: str,
    confirm_independent_recovery: bool = False,
    minimum_battery_percent: int = 60,
    environment_guard: Optional["NoiseGuard"] = None,
) -> str:
    require_verified_hotplug_recovery(confirm_independent_recovery)
    recovery_id = secrets.token_hex(16)
    recovery_dir = REMOTE_DIR + "/matrix_recovery_" + recovery_id
    script = build_matrix_config_script(
        config, topology, report, cpu_temp_paths, battery_temp_path, bench_path,
        recovery_id=recovery_id,
        minimum_battery_percent=minimum_battery_percent,
        environment_guard=environment_guard,
    )
    timeout_s = config.planned_seconds * 1.6 + 60.0
    try:
        transport_output = client.run_root_script_stdin(script, timeout_s=timeout_s)
        recovery_log = _read_recovery_log(client, recovery_dir)
        output = transport_output + ("\n" if transport_output and not transport_output.endswith("\n") else "") + recovery_log
    except (AdbError, OSError) as exc:
        snapshot_error = _wait_for_device_snapshot(client, topology, report)
        recovery_output = _read_recovery_log(client, recovery_dir)
        detail = "remote configuration interrupted: %s" % exc
        # Distinguish concurrent system frequency-capping from a transport/ADB
        # failure. A non-zero controller exit (e.g. status 39) surfaced through
        # adb is NOT necessarily an adb disconnect: when the recovery log shows
        # an out-of-range lock or concurrent-limit evidence, name that instead
        # so the failure is not misread as a flaky connection.
        cap_evidence = (
            "POLICY_RESTORE_RESTRICTED|" in recovery_output
            or "POLICY_RESTORE_UNOWNED|" in recovery_output
            or "CAP_VIOLATION|" in recovery_output
            or "STALE_POLICY_AFTER_HOTPLUG|" in recovery_output
            or "LOCK_INPUT_CHANGED|" in recovery_output
        )
        if cap_evidence:
            detail += (
                "; concurrent system cpufreq limiting detected (lock target fell "
                "outside the live system-clamped range); this is device thermal/"
                "perfd arbitration, not an ADB transport failure"
            )
        if snapshot_error is None:
            detail += "; snapshot currently matches, but watchdog recovery is not established by snapshot alone"
        else:
            detail += "; recovery snapshot not yet verified: %s" % snapshot_error
        raise MatrixConfigError(detail, recovery_output) from exc

    recovery_output = _read_recovery_log(client, recovery_dir)
    incomplete = "CONFIG_DONE|%s" % config.config_id not in output
    recovery_evidence = output + "\n" + recovery_output
    restore_failed = "RESTORE_ERROR|" in recovery_evidence or "WATCHDOG_RECOVERY_FAILED" in recovery_evidence
    watchdog_complete = any(line == 'WATCHDOG_DONE' or re.fullmatch(r'WATCHDOG_RECOVERY_OK\|[0-9]+', line)
                            for line in (item.strip() for item in recovery_evidence.splitlines()))
    interference = any(marker in recovery_evidence for marker in (
        "POLICY_RESTORE_RESTRICTED|", "POLICY_RESTORE_UNOWNED|", "POLICY_RESTORE_CHANGED|",
        "POLICY_JOURNAL_INVALID|", "POLICY_TAINT_WRITE_FAILED|", "WATCHDOG_RECOVERY_RESTRICTED|",
        ".state.tainted",
    ))
    snapshot_error = _wait_for_device_snapshot(client, topology, report, timeout_s=8.0)
    if incomplete or restore_failed or interference or not watchdog_complete or snapshot_error is not None:
        reasons = []
        if incomplete:
            reasons.append("configuration aborted or incomplete")
        if restore_failed:
            reasons.append("device/watchdog reported restore failure")
        if interference:
            reasons.append("policy ownership/interference evidence remains inconclusive")
        if not watchdog_complete:
            reasons.append("watchdog completion evidence missing")
        if snapshot_error is not None:
            reasons.append("pre-run snapshot mismatch: " + snapshot_error)
        raise MatrixConfigError("; ".join(reasons), output + "\n" + recovery_output)
    return output
