"""Command-line interface for read-only device preflight and capture."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .adb import AdbClient, AdbError
from .analysis import aggregate_curve, analyze_runs, parse_sweep_log, run_metrics_dicts, write_dict_csv
from .collector import build_sampler_script, parse_samples, value_change_intervals, write_csv
from .config import MAX_CAPTURE_DURATION_S, MIN_SAMPLE_INTERVAL_S
from .plot import (
    render_efficiency_svg,
    render_performance_power_svg,
    render_total_battery_power_svg,
    render_total_efficiency_svg,
)
from .preflight import collect_preflight, report_dict, render_report
from .power import (
    average_discharge_voltage_uv,
    charge_counter_average_current_ma,
    charge_counter_energy_j,
    integrate_energy_j,
    battery_power_w,
)
from .matrix_controller import require_verified_hotplug_recovery
from .matrix_runner import run_full_matrix
from .screen_control import (
    DEFAULT_MATRIX_TIMEOUT_MS,
    keep_screen_awake,
    read_screen_state,
    restore_screen_timeout,
)
from .sweep import (
    BATTERY_TEMP_LIMIT_MC,
    CPU_TEMP_LIMIT_MC,
    DEFAULT_BASELINE_S,
    DEFAULT_COOLDOWN_S,
    DEFAULT_MEASURE_S,
    DEFAULT_REPETITIONS,
    DEFAULT_WARMUP_S,
    randomized_points,
    run_sweep,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cpu-efficiency-lab")
    subparsers = parser.add_subparsers(dest="command", required=True)

    preflight = subparsers.add_parser("preflight", help="read device/sensor capabilities")
    preflight.add_argument("--serial", required=True)
    preflight.add_argument("--output", type=Path, help="optional JSON path (created exclusively)")

    capture = subparsers.add_parser("capture-power", help="read-only battery sensor capture")
    capture.add_argument("--serial", required=True)
    capture.add_argument("--duration", type=float, default=10.0, help="capture duration in seconds")
    capture.add_argument("--interval", type=float, default=1.0, help="poll interval in seconds")
    capture.add_argument("--output", type=Path, required=True, help="new local CSV path")

    sweep = subparsers.add_parser("cpu-sweep", help="run an authorized, thermally guarded CPU frequency sweep")
    sweep.add_argument("--serial", required=True)
    sweep.add_argument("--binary", type=Path, default=Path("build/cpu_mix_bench"))
    sweep.add_argument("--output", type=Path, required=True, help="new output prefix, without extension")
    sweep.add_argument("--warmup", type=int, default=DEFAULT_WARMUP_S)
    sweep.add_argument("--measure", type=int, default=DEFAULT_MEASURE_S)
    sweep.add_argument("--repetitions", type=int, default=DEFAULT_REPETITIONS)
    sweep.add_argument("--baseline", type=int, default=DEFAULT_BASELINE_S)
    sweep.add_argument("--cooldown", type=int, default=DEFAULT_COOLDOWN_S)
    sweep.add_argument("--confirm-device-writes", action="store_true", help="confirm temporary binary push and cpufreq writes")

    matrix = subparsers.add_parser("matrix-sweep", help="experimental matrix tests (fail-closed pending recovery verification)")
    matrix.add_argument("--serial", required=True)
    matrix.add_argument("--binary", type=Path, default=Path("build/cpu_mix_bench"))
    matrix.add_argument("--output", type=Path, required=True, help="new output prefix, without extension")
    matrix.add_argument("--minimum-battery-percent", type=int, default=60)
    matrix.add_argument("--restore-screen-timeout-ms", type=int, help="original screen timeout to restore after the run (needed if it was already changed manually)")
    matrix.add_argument("--confirm-device-writes", action="store_true", help="confirm temporary benchmark deployment and cpufreq writes")
    matrix.add_argument("--confirm-core-hotplug", action="store_true", help="confirm the test will temporarily online/offline CPUs 1-7; CPU0 remains online")
    matrix.add_argument("--confirm-independent-recovery", action="store_true", help="confirm the detached watchdog was fault-tested and may recover device state")
    return parser


def _save_json_exclusive(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def _saved_screen_timeout(path: Path, serial: str) -> Optional[int]:
    """Read a prior keep-awake record, tolerating its known appended adb transcript."""
    try:
        content = path.read_text(encoding="utf-8")
        document, end = json.JSONDecoder().raw_decode(content.lstrip())
        trailing = content.lstrip()[end:].lstrip()
        if trailing and not (trailing == "APPLY_OUTPUT" or trailing.startswith("APPLY_OUTPUT\n")):
            return None
        if not isinstance(document, dict) or document.get("serial") != serial:
            return None
        if document.get("requested_temporary_screen_off_timeout_ms") != DEFAULT_MATRIX_TIMEOUT_MS:
            return None
        original = document.get("original_screen_off_timeout_ms")
        if isinstance(original, bool) or not isinstance(original, int) or original < 0:
            return None
        return original
    except (OSError, ValueError, TypeError):
        return None


def _run_preflight(args: argparse.Namespace) -> int:
    client = AdbClient(args.serial)
    report = collect_preflight(client)
    rendered = render_report(report)
    if args.output:
        _save_json_exclusive(args.output, report_dict(report))
        print("Preflight saved: %s" % args.output)
    print(rendered)
    if not report.ready_for_capture:
        print("Not ready: root and readable current/voltage/status nodes are required.", file=sys.stderr)
        return 2
    return 0


def _run_capture(args: argparse.Namespace) -> int:
    if not math.isfinite(args.duration) or args.duration <= 0:
        raise ValueError("duration must be a positive finite number")
    if args.duration > MAX_CAPTURE_DURATION_S:
        raise ValueError("duration exceeds configured maximum")
    if not math.isfinite(args.interval) or args.interval < MIN_SAMPLE_INTERVAL_S:
        raise ValueError("interval must be finite and at least %.3f seconds" % MIN_SAMPLE_INTERVAL_S)

    count = max(2, int(math.ceil(args.duration / args.interval)) + 1)
    effective_duration = (count - 1) * args.interval
    if effective_duration > MAX_CAPTURE_DURATION_S:
        raise ValueError("duration and interval exceed configured maximum")

    summary_path = args.output.with_name(args.output.stem + ".summary.json")
    if args.output.exists() or summary_path.exists():
        raise FileExistsError("capture CSV or summary already exists; refusing to overwrite")

    started_utc = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    client = AdbClient(args.serial)
    report = collect_preflight(client)
    if not report.ready_for_capture:
        raise AdbError("preflight failed: root/current_now/voltage_now/status not readable")

    script = build_sampler_script(count, args.interval)
    timeout = effective_duration + max(15.0, effective_duration * 0.25)
    raw_output = client.run_root_script(script, timeout_s=timeout)
    samples = parse_samples(raw_output)
    if len(samples) != count:
        raise AdbError("expected %d samples but received %d" % (count, len(samples)))

    written = write_csv(args.output, samples)
    energy_j, covered_s = integrate_energy_j(samples, max_gap_s=max(5.0, args.interval * 2.5))
    timestamps = [sample.midpoint_s for sample in samples]
    observed_intervals = [right - left for left, right in zip(timestamps, timestamps[1:])]
    status_counts = {}
    for sample in samples:
        status_counts[sample.status] = status_counts.get(sample.status, 0) + 1
    current_values = [sample.current_ua for sample in samples]
    voltage_values = [sample.voltage_uv for sample in samples]
    charge_values = [sample.charge_counter_uah for sample in samples if sample.charge_counter_uah is not None]
    current_change_intervals = value_change_intervals(samples, "current_ua")
    voltage_change_intervals = value_change_intervals(samples, "voltage_uv")
    observed_span = timestamps[-1] - timestamps[0]
    charge_delta = (
        charge_values[-1] - charge_values[0]
        if len(charge_values) == len(samples) and len(charge_values) >= 2
        else None
    )
    all_discharging = all(sample.status.strip().lower() == "discharging" for sample in samples)
    average_voltage_uv = average_discharge_voltage_uv(samples, max_gap_s=max(5.0, args.interval * 2.5))
    counter_window_valid = (
        all_discharging
        and charge_delta is not None
        and covered_s >= observed_span * 0.99
    )
    counter_energy_j = (
        charge_counter_energy_j(charge_delta, average_voltage_uv, "Discharging")
        if counter_window_valid and average_voltage_uv is not None
        else None
    )
    counter_current_ma = (
        charge_counter_average_current_ma(charge_delta, covered_s)
        if counter_window_valid
        else None
    )
    counter_energy_difference_pct = (
        abs(counter_energy_j - energy_j) / energy_j * 100.0
        if counter_energy_j is not None and energy_j > 0.0
        else None
    )
    summary = {
        "protocol_version": 1,
        "serial": report.serial,
        "model": report.model,
        "device": report.device,
        "soc": report.soc,
        "android_release": report.android_release,
        "root_context": report.root_context,
        "selinux": report.selinux,
        "power_now_node_present": report.nodes.get("power_now", {}).get("present", False),
        "started_utc": started_utc,
        "completed_utc": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "samples": written,
        "battery_status_counts": status_counts,
        "requested_duration_s": args.duration,
        "planned_sample_span_s": effective_duration,
        "observed_sample_span_s": observed_span,
        "requested_interval_s": args.interval,
        "timestamp_source": "device /proc/uptime (centisecond display resolution)",
        "observed_interval_median_s": statistics.median(observed_intervals),
        "observed_interval_min_s": min(observed_intervals),
        "observed_interval_max_s": max(observed_intervals),
        "max_current_voltage_read_span_s": max(sample.read_skew_s for sample in samples),
        "current_now_median_uA": statistics.median(current_values),
        "current_value_transitions": sum(a != b for a, b in zip(current_values, current_values[1:])),
        "current_transition_period_median_s": statistics.median(current_change_intervals) if current_change_intervals else None,
        "voltage_value_transitions": sum(a != b for a, b in zip(voltage_values, voltage_values[1:])),
        "voltage_transition_period_median_s": statistics.median(voltage_change_intervals) if voltage_change_intervals else None,
        "charge_counter_delta_uAh": charge_delta,
        "charge_counter_average_current_mA": counter_current_ma,
        "charge_counter_energy_estimate_j": counter_energy_j,
        "charge_counter_energy_difference_pct": counter_energy_difference_pct,
        "charge_counter_comparison_note": "same fuel-gauge subsystem may feed both sources; consistency check, not independent calibration",
        "integrated_energy_j": energy_j,
        "integrated_covered_s": covered_s,
        "average_power_w_over_covered_s": energy_j / covered_s if covered_s else None,
        "csv": str(args.output),
        "measurement_scope": "whole-device battery-side; not CPU-rail power",
    }
    _save_json_exclusive(summary_path, summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


def _run_cpu_sweep(args: argparse.Namespace) -> int:
    if not args.confirm_device_writes:
        raise ValueError("cpu-sweep requires --confirm-device-writes")
    if args.repetitions < 1 or min(args.warmup, args.measure, args.baseline, args.cooldown) < 1:
        raise ValueError("sweep durations and repetitions must be positive")
    if not args.binary.is_file():
        raise ValueError("benchmark binary not found: %s" % args.binary)

    suffixes = [
        ".raw.log",
        ".samples.csv",
        ".runs.csv",
        ".curve.csv",
        ".summary.json",
        ".performance-power.svg",
        ".performance-battery-power.svg",
        ".efficiency.svg",
        ".total-efficiency.svg",
    ]
    artifacts = {suffix: args.output.with_name(args.output.name + suffix) for suffix in suffixes}
    existing = [str(path) for path in artifacts.values() if path.exists()]
    if existing:
        raise FileExistsError("refusing to overwrite sweep outputs: " + ", ".join(existing))

    client = AdbClient(args.serial)
    initial = collect_preflight(client)
    if not initial.ready_for_capture:
        raise AdbError("preflight failed: root/current_now/voltage_now/status not readable")
    points = randomized_points(initial)
    active_work_s = len(points) * args.repetitions * (args.warmup + args.measure)
    if active_work_s < 1800:
        raise ValueError(
            "planned CPU benchmark work is only %.1f minutes; must be at least 30 minutes"
            % (active_work_s / 60.0)
        )
    print(
        "Plan: %d capped frequency points × %d repetitions, %.1f minutes active benchmark work; no frequency above current caps."
        % (len(points), args.repetitions, active_work_s / 60.0),
        flush=True,
    )
    started_utc = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    report, points, abort = run_sweep(
        client,
        args.binary,
        artifacts[".raw.log"],
        warmup_s=args.warmup,
        measure_s=args.measure,
        repetitions=args.repetitions,
        baseline_s=args.baseline,
        cooldown_s=args.cooldown,
        preflight=initial,
    )

    raw = artifacts[".raw.log"].read_text(encoding="utf-8", errors="replace")
    readings, bench_runs = parse_sweep_log(raw)
    metrics = analyze_runs(points, readings, bench_runs)
    curve = aggregate_curve(metrics)

    sample_rows = []
    for reading in readings:
        sample = reading.sample
        sample_rows.append(
            {
                "point_id": reading.point_id,
                "repetition": reading.repetition,
                "phase": reading.phase,
                "uptime_midpoint_s": "%.3f" % reading.time_s,
                "current_before_s": "%.2f" % sample.current_before_s,
                "current_now_uA": sample.current_ua,
                "current_after_s": "%.2f" % sample.current_after_s,
                "voltage_before_s": "%.2f" % sample.voltage_before_s,
                "voltage_now_uV": sample.voltage_uv,
                "voltage_after_s": "%.2f" % sample.voltage_after_s,
                "read_span_s": "%.3f" % sample.read_skew_s,
                "battery_status": sample.status,
                "charge_counter_uAh": sample.charge_counter_uah,
                "battery_temp_mC": reading.battery_temp_mc,
                "cpu_temp_mC": reading.cpu_temp_mc,
                "actual_frequency_khz": reading.actual_frequency_khz,
                "battery_power_W": battery_power_w(sample.current_ua, sample.voltage_uv, sample.status),
            }
        )
    sample_fields = [
        "point_id", "repetition", "phase", "uptime_midpoint_s",
        "current_before_s", "current_now_uA", "current_after_s",
        "voltage_before_s", "voltage_now_uV", "voltage_after_s", "read_span_s",
        "battery_status", "charge_counter_uAh", "battery_temp_mC", "cpu_temp_mC",
        "actual_frequency_khz", "battery_power_W",
    ]
    write_dict_csv(artifacts[".samples.csv"], sample_rows, sample_fields)
    run_rows = run_metrics_dicts(metrics)
    run_fields = list(metrics[0].__dataclass_fields__) if metrics else [
        "point_id", "policy", "cpu", "target_frequency_khz", "repetition", "elapsed_s",
        "iterations", "operations_per_second", "load_power_w", "baseline_power_w",
        "incremental_power_w", "operations_per_joule", "operations_per_total_joule",
        "measured_energy_j", "measured_coverage_s", "baseline_coverage_s",
        "actual_frequency_median_khz", "frequency_match_fraction", "max_cpu_temp_c",
        "max_battery_temp_c", "measurement_valid", "valid", "invalid_reason",
    ]
    write_dict_csv(artifacts[".runs.csv"], run_rows, run_fields)
    curve_fields = list(curve[0].keys()) if curve else [
        "point_id", "policy", "cpu", "target_frequency_khz", "measured_repetitions",
        "valid_repetitions", "attempted_repetitions", "operations_per_second_median", "operations_per_second_sd",
        "load_power_w_median", "load_power_w_sd", "baseline_power_w_median",
        "incremental_power_w_median", "incremental_power_w_sd", "operations_per_joule_median",
        "operations_per_total_joule_median", "actual_frequency_khz_median",
        "frequency_match_fraction_median", "measurement_valid", "incremental_valid", "valid",
    ]
    write_dict_csv(artifacts[".curve.csv"], curve, curve_fields)
    render_performance_power_svg(curve, artifacts[".performance-power.svg"])
    render_total_battery_power_svg(curve, artifacts[".performance-battery-power.svg"])
    render_efficiency_svg(curve, artifacts[".efficiency.svg"])
    render_total_efficiency_svg(curve, artifacts[".total-efficiency.svg"])

    summary = {
        "protocol_version": 1,
        "serial": report.serial,
        "model": report.model,
        "soc": report.soc,
        "android_release": report.android_release,
        "started_utc": started_utc,
        "completed_utc": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "benchmark": "scalar_mix_v2 (custom synchronized 64-bit integer mixing throughput; not Geekbench)",
        "active_benchmark_minutes_planned": active_work_s / 60.0,
        "frequency_points": len(points),
        "repetitions_per_point": args.repetitions,
        "warmup_s": args.warmup,
        "measured_s_per_repetition": args.measure,
        "idle_baseline_s_each_side": args.baseline,
        "cooldown_s_between_repetitions": args.cooldown,
        "cpu_temperature_abort_c": CPU_TEMP_LIMIT_MC / 1000.0,
        "battery_temperature_abort_c": BATTERY_TEMP_LIMIT_MC / 1000.0,
        "current_frequency_caps_preserved": True,
        "cpufreq_restoration_verified": True,
        "measured_frequency_points": sum(bool(row.get("measurement_valid")) for row in curve),
        "incremental_efficiency_frequency_points": sum(bool(row.get("incremental_valid")) for row in curve),
        "preflight_policies": [policy.__dict__ for policy in report.policies],
        "completed_benchmark_runs": len(bench_runs),
        "aborted": bool(abort),
        "abort_reason": abort or None,
        "measurement_scope": "whole-device battery-side; incremental power is load minus paired idle; not CPU-rail power",
        "limitations": [
            "custom scalar workload is not directly comparable to Geekbench/Geekwan scores",
            "battery current and charge counter share the phone fuel-gauge subsystem and are not externally calibrated",
            "only available frequencies at or below pre-test scaling_max_freq were included",
        ],
        "files": {key: str(path) for key, path in artifacts.items()},
    }
    _save_json_exclusive(artifacts[".summary.json"], summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 1 if abort or not any(row.get("measurement_valid") for row in curve) else 0


def _run_matrix_sweep(args: argparse.Namespace) -> int:
    if not args.confirm_device_writes or not args.confirm_core_hotplug:
        raise ValueError("matrix-sweep requires --confirm-device-writes and --confirm-core-hotplug")
    require_verified_hotplug_recovery(args.confirm_independent_recovery)
    if not args.binary.is_file():
        raise ValueError("benchmark binary not found: %s" % args.binary)
    if args.restore_screen_timeout_ms is not None and args.restore_screen_timeout_ms < 0:
        raise ValueError("restore screen timeout must be nonnegative")
    suffixes = (
        ".raw.log", ".samples.csv", ".runs.csv", ".curve.csv", ".configs.csv",
        ".workers.csv", ".plan.json", ".summary.json", ".pareto.svg",
        ".mixed-heatmap.svg", ".screen-state.json",
    )
    artifacts = [args.output.with_name(args.output.name + suffix) for suffix in suffixes]
    existing = [str(path) for path in artifacts if path.exists()]
    if existing:
        raise FileExistsError("refusing to overwrite matrix outputs: " + ", ".join(existing))

    client = AdbClient(args.serial)
    screen_before = read_screen_state(client)
    restore_timeout = args.restore_screen_timeout_ms
    if restore_timeout is None:
        restore_timeout = screen_before.timeout_ms
        saved_state = Path(__file__).resolve().parents[2] / "data" / "phase-d-screen-keep-awake-state.json"
        if screen_before.timeout_ms == DEFAULT_MATRIX_TIMEOUT_MS and saved_state.is_file():
            saved_timeout = _saved_screen_timeout(saved_state, args.serial)
            if saved_timeout is None:
                raise AdbError(
                    "saved screen timeout record cannot be safely matched; "
                    "pass --restore-screen-timeout-ms explicitly"
                )
            restore_timeout = saved_timeout
    state_path = args.output.with_name(args.output.name + ".screen-state.json")
    screen_record = {
        "serial": args.serial,
        "recorded_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "screen_before": screen_before.__dict__,
        "restore_timeout_ms": restore_timeout,
        "temporary_timeout_ms": DEFAULT_MATRIX_TIMEOUT_MS,
        "restore_after_run": True,
        "prepared": False,
        "restored": False,
    }
    _save_json_exclusive(state_path, screen_record)
    prepared = False
    summary = None
    restore_error = None
    try:
        keep_screen_awake(
            client,
            timeout_ms=DEFAULT_MATRIX_TIMEOUT_MS,
            expected_current_timeout_ms=screen_before.timeout_ms,
        )
        prepared = True
        screen_record["prepared"] = True
        summary = run_full_matrix(
            client,
            args.binary,
            args.output,
            minimum_battery_percent=args.minimum_battery_percent,
            confirm_independent_recovery=args.confirm_independent_recovery,
        )
    except BaseException as exc:
        screen_record["run_error"] = repr(exc)
        raise
    finally:
        if prepared:
            try:
                after = restore_screen_timeout(
                    client,
                    original_timeout_ms=restore_timeout,
                    expected_temporary_timeout_ms=DEFAULT_MATRIX_TIMEOUT_MS,
                )
                screen_record["restored"] = True
                screen_record["screen_after"] = after.__dict__
            except BaseException as exc:
                restore_error = exc
                screen_record["restore_error"] = repr(exc)
        screen_record["completed_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        state_path.write_text(json.dumps(screen_record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if restore_error is not None:
        raise AdbError("matrix finished but screen setting restoration failed: %s" % restore_error)
    if summary is None:
        raise AdbError("matrix run returned no summary")
    summary["screen_timeout_restored"] = restore_timeout
    summary["screen_remains_awake_after_timeout_restore"] = True
    summary["screen_state_file"] = str(state_path)
    summary_path = args.output.with_name(args.output.name + ".summary.json")
    if summary_path.exists():
        summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


def main(argv=None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "preflight":
            return _run_preflight(args)
        if args.command == "capture-power":
            return _run_capture(args)
        if args.command == "cpu-sweep":
            return _run_cpu_sweep(args)
        if args.command == "matrix-sweep":
            return _run_matrix_sweep(args)
    except KeyboardInterrupt:
        print("interrupted; device restoration was requested", file=sys.stderr)
        return 130
    except (AdbError, OSError, ValueError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    parser.error("unknown command")
    return 2
