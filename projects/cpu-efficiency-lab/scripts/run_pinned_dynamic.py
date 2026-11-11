"""All-online, natural-frequency CPU/battery comparison; no hotplug or cpufreq writes."""
from __future__ import annotations

import argparse
import csv
import json
import secrets
import shlex
import statistics
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cpu_efficiency_lab.adb import AdbClient, AdbError
from cpu_efficiency_lab.matrix_analysis import _power_segments, _power_window, parse_matrix_log
from cpu_efficiency_lab.matrix_runner import _validate_device_ready
from cpu_efficiency_lab.screen_control import DEFAULT_MATRIX_TIMEOUT_MS, read_screen_state
from cpu_efficiency_lab.sweep import deploy_benchmark


CASES = [("CPU%d" % n, (n,)) for n in range(8)] + [
    ("E6", (0, 1, 2, 3, 4, 5)),
    ("P2", (6, 7)),
    ("E1P1", (1, 6)),
    ("E2P1", (1, 2, 6)),
    ("E4P1", (1, 2, 3, 4, 6)),
    ("E6P1", (0, 1, 2, 3, 4, 5, 6)),
    ("E4P2", (1, 2, 3, 4, 6, 7)),
    ("E6P2", (0, 1, 2, 3, 4, 5, 6, 7)),
]


def _script(name: str, cpus: tuple, bench: str, cpu_paths: list, battery_path: str, logfile: str) -> str:
    values = {
        "__NAME__": shlex.quote(name), "__CPUS__": shlex.quote(",".join(map(str, cpus))),
        "__FIRST__": str(cpus[0]), "__BENCH__": shlex.quote(bench),
        "__CPU_PATHS__": " ".join(cpu_paths), "__BATTERY_PATH__": shlex.quote(battery_path),
        "__LOGFILE__": shlex.quote(logfile),
    }
    script = r'''set +e
name=__NAME__
cpus=__CPUS__
bench=__BENCH__
cpu_paths="__CPU_PATHS__"
battery_path=__BATTERY_PATH__
logfile=__LOGFILE__
exec > "$logfile" 2>&1
bench_pid=
cleanup() {
  if [ -n "$bench_pid" ]; then
    kill -TERM "$bench_pid" 2>/dev/null
    wait "$bench_pid" 2>/dev/null
    bench_pid=
  fi
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
sample() {
  phase=$1; rep=$2
  read -r ci0 _ < /proc/uptime || return 1
  read -r current < /sys/class/power_supply/battery/current_now || return 1
  read -r ci1 _ < /proc/uptime || return 1
  read -r vi0 _ < /proc/uptime || return 1
  read -r voltage < /sys/class/power_supply/battery/voltage_now || return 1
  read -r vi1 _ < /proc/uptime || return 1
  read -r status < /sys/class/power_supply/battery/status || return 1
  read -r capacity < /sys/class/power_supply/battery/capacity || return 1
  read -r battery_temp < "$battery_path" || return 1
  read -r charge < /sys/class/power_supply/battery/charge_counter 2>/dev/null || charge=NA
  read -r online < /sys/devices/system/cpu/online || return 1
  [ "$online" = 0-7 ] || { printf 'ABORT|CORE_MASK|%s\n' "$online"; return 1; }
  case "$capacity:$battery_temp" in *[!0-9:]*|:*|*:) printf 'ABORT|BATTERY_SENSOR\n'; return 1 ;; esac
  [ "$status" = Discharging ] || { printf 'ABORT|BATTERY_STATUS|%s\n' "$status"; return 1; }
  [ "$capacity" -ge 60 ] || { printf 'ABORT|BATTERY_RESERVE|%s\n' "$capacity"; return 1; }
  cpu_temp=-1
  for p in $cpu_paths; do
    read -r temp < "$p" 2>/dev/null || continue
    case "$temp" in ''|*[!0-9]*) continue ;; esac
    [ "$temp" -gt "$cpu_temp" ] && cpu_temp=$temp
  done
  [ "$cpu_temp" -ge 1000 ] && [ "$battery_temp" -ge 1000 ] || { printf 'ABORT|THERMAL_SENSOR\n'; return 1; }
  [ "$cpu_temp" -lt 80000 ] || { printf 'ABORT|CPU_TEMPERATURE|%s\n' "$cpu_temp"; return 1; }
  [ "$battery_temp" -lt 43000 ] || { printf 'ABORT|BATTERY_TEMPERATURE|%s\n' "$battery_temp"; return 1; }
  read -r f0 < /sys/devices/system/cpu/cpufreq/policy0/scaling_cur_freq 2>/dev/null || f0=NA
  read -r f6 < /sys/devices/system/cpu/cpufreq/policy6/scaling_cur_freq 2>/dev/null || f6=NA
  printf 'M|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|0,1,2,3,4,5,6,7\n' \
    "$name" "$rep" "$phase" "$ci0" "$current" "$ci1" "$vi0" "$voltage" "$vi1" \
    "$status" "$battery_temp" "$capacity" "$cpu_temp" "$f0" "$f6" "$charge"
  return 0
}
idle() {
  phase=$1; n=0
  while [ "$n" -lt 4 ]; do
    sample "$phase" 0 || return 1
    n=$((n+1)); [ "$n" -lt 4 ] && sleep 1
  done
  return 0
}
printf 'PIN_INFO|all_online|no_hotplug|no_frequency_lock|battery_side\n'
idle baseline_pre || { printf 'CONFIG_ABORT|%s|baseline_pre\n' "$name"; exit 1; }
printf 'RUN|%s|1|__FIRST__|%s\n' "$name" "$cpus"
"$bench" "$cpus" 2 12 &
bench_pid=$!
while kill -0 "$bench_pid" 2>/dev/null; do
  sample active 1 || { printf 'CONFIG_ABORT|%s|active\n' "$name"; exit 1; }
  sleep 1
done
wait "$bench_pid"; rc=$?; bench_pid=
[ "$rc" -eq 0 ] || { printf 'CONFIG_ABORT|%s|bench_exit_%s\n' "$name" "$rc"; exit 1; }
idle baseline_post || { printf 'CONFIG_ABORT|%s|baseline_post\n' "$name"; exit 1; }
printf 'CONFIG_DONE|%s\n' "$name"
'''
    for key, value in values.items():
        script = script.replace(key, value)
    return script


def _summarize(raw: str) -> list:
    readings, runs = parse_matrix_log(raw)
    output = []
    expected = dict(CASES)
    for run in runs:
        matching = [r for r in readings if r.config_id == run.config_id]
        active = [r for r in matching if r.phase == "active" and r.repetition == run.repetition]
        energy, covered = _power_segments(active, run.start_s, run.end_s)
        load = energy / covered if covered else None
        _, e0, t0 = _power_window([r for r in matching if r.phase == "baseline_pre"])
        _, e1, t1 = _power_window([r for r in matching if r.phase == "baseline_post"])
        baseline = (e0 + e1) / (t0 + t1) if t0 + t1 else None
        incremental = load - baseline if load is not None and baseline is not None else None
        measured = [r for r in active if run.start_s <= r.time_s <= run.end_s]
        mask_ok = bool(measured) and all(r.online_cpus == tuple(range(8)) for r in measured)
        affinity_ok = (run.cpus == expected.get(run.config_id) and
                       tuple(sorted(w.target_cpu for w in run.workers)) == run.cpus and
                       all(w.target_cpu == w.observed_cpu for w in run.workers))
        valid = bool(mask_ok and affinity_ok and load is not None and load > 0 and covered >= run.elapsed_s * .65)
        def median_freq(field):
            values = [getattr(r, field) for r in measured if getattr(r, field) is not None]
            return statistics.median(values) if values else None
        output.append({
            "config_id": run.config_id, "worker_cpus": run.worker_csv,
            "online_cpus": "0,1,2,3,4,5,6,7", "strict_isolation": False,
            "frequency_locked": False, "elapsed_s": run.elapsed_s,
            "operations_per_second": run.operations_per_second, "device_battery_load_power_w": load,
            "device_battery_idle_power_w": baseline, "device_battery_incremental_power_w": incremental,
            "operations_per_total_joule": run.operations_per_second / load if valid else None,
            "operations_per_incremental_joule": (run.operations_per_second / incremental
                                                 if valid and incremental is not None and incremental > 0 else None),
            "power_coverage_s": covered, "power_coverage_fraction": covered / run.elapsed_s,
            "baseline_coverage_s": t0 + t1, "policy0_median_khz": median_freq("policy0_frequency_khz"),
            "policy6_median_khz": median_freq("policy6_frequency_khz"),
            "cpu_temp_max_c": max((r.cpu_temp_mc for r in measured), default=0) / 1000,
            "battery_temp_max_c": max((r.battery_temp_mc for r in measured), default=0) / 1000,
            "online_mask_valid": mask_ok, "worker_affinity_valid": affinity_ok, "valid": valid,
        })
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--serial", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--reverse-order", action="store_true", help="run the same cases in reverse order")
    args = parser.parse_args()
    cases = list(reversed(CASES)) if args.reverse_order else CASES
    prefix = args.output
    raw_path = prefix.with_name(prefix.name + ".raw.log")
    csv_path = prefix.with_name(prefix.name + ".summary.csv")
    json_path = prefix.with_name(prefix.name + ".summary.json")
    if any(p.exists() for p in (raw_path, csv_path, json_path)):
        raise FileExistsError("output prefix already exists")
    client = AdbClient(args.serial)
    report, topology, cpu_paths, battery_path, temperatures = _validate_device_ready(client, 60)
    state = read_screen_state(client)
    if not state.display_on or state.wakefulness != "Awake" or state.timeout_ms != DEFAULT_MATRIX_TIMEOUT_MS:
        raise AdbError("screen must already be awake with temporary long timeout")
    bench = deploy_benchmark(client, ROOT / "build" / "cpu_mix_bench")
    print("START|%s|battery=%s|cases=%d|measure=12s|all-online|unlocked" %
          (report.serial, report.nodes["capacity"]["value"], len(CASES)), flush=True)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    failed = None
    with raw_path.open("x", encoding="utf-8", newline="\n") as raw:
        raw.write("PLAN|dynamic-all-online|%s|%s\n" % (report.serial, bench)); raw.flush()
        for index, (name, cpus) in enumerate(cases, 1):
            current_screen = read_screen_state(client)
            if not current_screen.display_on or current_screen.timeout_ms != DEFAULT_MATRIX_TIMEOUT_MS:
                failed = "screen no longer on or timeout changed"; break
            remote_log = "/data/local/tmp/cpu-efficiency-lab/pinned_dynamic_" + secrets.token_hex(16) + ".log"
            print("BEGIN|%d/%d|%s|%s" % (index, len(CASES), name, ",".join(map(str, cpus))), flush=True)
            raw.write("HOST_BEGIN|%s|%s\n" % (name, remote_log)); raw.flush()
            error = None
            try:
                client.run_root_script(_script(name, cpus, bench, cpu_paths, battery_path, remote_log), timeout_s=120)
            except (AdbError, OSError) as exc:
                error = str(exc)
            try:
                remote = client.run_root_script("if [ -f %s ]; then cat %s; else echo REMOTE_LOG_MISSING; fi" %
                                                (shlex.quote(remote_log), shlex.quote(remote_log)), timeout_s=20)
            except (AdbError, OSError) as exc:
                remote = "REMOTE_LOG_UNAVAILABLE|%s\n" % exc
            raw.write(remote)
            if not remote.endswith("\n"): raw.write("\n")
            if error or "CONFIG_DONE|%s" % name not in remote:
                failed = error or "configuration incomplete: " + name
                raw.write("HOST_ERROR|%s|%s\n" % (name, failed)); raw.flush(); break
            raw.write("HOST_END|%s\n" % name); raw.flush()
            rows = _summarize(raw_path.read_text(encoding="utf-8", errors="replace"))
            with csv_path.open("w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
            latest = rows[-1]
            print("DONE|%d/%d|%s|ops_s=%.0f|power_w=%s|valid=%s" %
                  (index, len(CASES), name, latest["operations_per_second"],
                   latest["device_battery_load_power_w"], latest["valid"]), flush=True)
    rows = _summarize(raw_path.read_text(encoding="utf-8", errors="replace"))
    summary = {
        "completed": failed is None and len(rows) == len(CASES), "error": failed,
        "serial": report.serial, "model": report.model, "soc": report.soc,
        "benchmark": "scalar_mix_v2 custom workload; not Geekbench",
        "scope": "all eight CPUs online, pinned workers, dynamic frequency, whole-device battery-side power",
        "strict_single_core_isolation": False, "frequency_locked": False,
        "battery_percent_initial": report.nodes["capacity"]["value"],
        "initial_temperatures_mc": temperatures, "initial_policies": [asdict(p) for p in report.policies],
        "configs_planned": len(CASES), "configs_completed": len(rows),
        "execution_order": "reverse" if args.reverse_order else "forward",
        "valid_configs": sum(bool(row["valid"]) for row in rows), "results": rows,
        "raw_log": str(raw_path), "summary_csv": str(csv_path),
    }
    with json_path.open("x", encoding="utf-8") as f: json.dump(summary, f, indent=2, ensure_ascii=False)
    print("FINISHED|completed=%s|valid=%d/%d|error=%s" %
          (summary["completed"], summary["valid_configs"], len(CASES), failed), flush=True)
    return 0 if summary["completed"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AdbError, OSError, ValueError) as exc:
        print("ERROR|%s" % exc, file=sys.stderr, flush=True)
        raise SystemExit(2)
