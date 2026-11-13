"""Full, thermally guarded staged CPU topology/frequency experiment."""

from __future__ import annotations

import json
import statistics
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

from .adb import AdbClient, AdbError
from .config import MATRIX_SWEEP_REPETITIONS
from .matrix_analysis import (
    MatrixMetric,
    aggregate_matrix,
    analyze_matrix_runs,
    choose_retest_configs,
    matrix_metric_dicts,
    parse_matrix_log,
    rank_single_core_order,
)
from .matrix_plot import pareto_rows, render_matrix_pareto_svg, render_mixed_frequency_heatmap
from .matrix_controller import execute_matrix_config, require_verified_hotplug_recovery, verify_device_snapshot
from .matrix_plan import (
    MatrixConfig,
    build_reduced_stage_b,
    build_reduced_stage_c,
    build_reduced_stage_d,
    build_stage_a,
    build_stage_b,
    build_stage_c,
    build_stage_d,
    plan_budget_seconds,
    policy_by_cpu,
    reduced_plan_budget_seconds,
    total_plan_seconds,
    total_reduced_plan_seconds,
)
from .models import PreflightReport
from .power import battery_power_w
from .preflight import collect_preflight
from .sweep import deploy_benchmark, discover_thermal_sensors
from .topology import CpuTopology, discover_cpu_topology


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _build_retest_configs(candidates: Sequence[MatrixConfig]) -> Tuple[List[MatrixConfig], Dict[str, str]]:
    configs = []
    source_by_id = {}
    for candidate_index, candidate in enumerate(candidates, 1):
        for repetition in range(1, 4):
            config_id = "E-%02d-R%d" % (candidate_index, repetition)
            config = MatrixConfig(
                config_id=config_id,
                stage="E",
                target_cpus=candidate.target_cpus,
                policy_targets_khz=candidate.policy_targets_khz,
                warmup_s=4,
                measure_s=20,
                baseline_s=3,
            )
            configs.append(config)
            source_by_id[config_id] = candidate.config_id
    return configs, source_by_id


def _build_sustained_configs(
    retest_metrics: Sequence[MatrixMetric],
    source_by_id: Dict[str, str],
    candidates: Sequence[MatrixConfig],
) -> List[MatrixConfig]:
    grouped: Dict[str, List[MatrixMetric]] = {}
    for metric in retest_metrics:
        if metric.valid and metric.config_id in source_by_id:
            grouped.setdefault(source_by_id[metric.config_id], []).append(metric)
    if not grouped:
        return []
    summaries = []
    for source_id, rows in grouped.items():
        if len({row.config_id for row in rows}) < 2:
            continue
        efficiency = [row.operations_per_total_joule for row in rows if row.operations_per_total_joule is not None]
        if not efficiency:
            continue
        summaries.append(
            (
                source_id,
                statistics.median(efficiency),
                statistics.median(row.operations_per_second for row in rows),
            )
        )
    if not summaries:
        return []
    sources = [max(summaries, key=lambda item: item[1])[0]]
    fastest = max(summaries, key=lambda item: item[2])[0]
    if fastest not in sources:
        sources.append(fastest)
    if len(sources) < 2:
        alternatives = [item for item in sorted(summaries, key=lambda row: row[2], reverse=True) if item[0] not in sources]
        if alternatives:
            sources.append(alternatives[0][0])
    candidate_map = {config.config_id: config for config in candidates}
    result = []
    for index, source_id in enumerate(sources[:2], 1):
        source = candidate_map[source_id]
        result.append(
            MatrixConfig(
                config_id="F-%d-%s" % (index, source_id),
                stage="F",
                target_cpus=source.target_cpus,
                policy_targets_khz=source.policy_targets_khz,
                warmup_s=6,
                measure_s=120,
                baseline_s=4,
            )
        )
    return result


def _config_rows(configs: Sequence[MatrixConfig]) -> List[dict]:
    rows = []
    for config in configs:
        targets = dict(config.policy_targets_khz)
        rows.append(
            {
                "config_id": config.config_id,
                "stage": config.stage,
                "target_cpus": config.worker_csv,
                "online_cpus": ",".join(str(cpu) for cpu in config.desired_online_cpus),
                "background_online_cpus": ",".join(str(cpu) for cpu in config.keeper_cpus),
                "strict_single_core_isolation": config.strict_single_core_isolation,
                "policy0_target_khz": targets.get("policy0"),
                "policy6_target_khz": targets.get("policy6"),
                "warmup_s": config.warmup_s,
                "measure_s": config.measure_s,
                "baseline_s_each_side": config.baseline_s,
                "cooldown_s": config.cooldown_s,
                "repetitions": config.repetitions,
                "planned_seconds": config.planned_seconds,
            }
        )
    return rows


def _write_json_exclusive(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def firmware_vote_locked_policies(report: PreflightReport, required_levels: int) -> List[str]:
    """Return descriptions of policies too frequency-pinned to host a sweep.

    A policy whose live scaling range exposes fewer in-range frequency steps
    than the plan needs cannot be swept. On this device a stuck SCMI min/max
    vote (e.g. min=max=2400000) cannot be cleared by root sysfs writes or by
    stopping userspace perf HALs — only a reboot clears it. Detect that up
    front so the operator is told to reboot instead of crashing mid-run.
    """
    locked: List[str] = []
    for policy in report.policies:
        if policy.scaling_min_khz is None or policy.scaling_max_khz is None:
            continue
        try:
            in_range = {
                int(v) for v in policy.available_frequencies_khz.split()
                if v.isdigit() and policy.scaling_min_khz <= int(v) <= policy.scaling_max_khz
            }
        except (TypeError, ValueError):
            continue
        if len(in_range) < required_levels:
            locked.append(
                "%s exposes only %d in-range frequency step(s) (%d-%d kHz)"
                % (policy.name, len(in_range), policy.scaling_min_khz, policy.scaling_max_khz)
            )
    return locked


def _validate_device_ready(
    client: AdbClient,
    minimum_battery_percent: int,
    reduced: bool = False,
) -> Tuple[PreflightReport, CpuTopology, List[str], str, Dict[str, int]]:
    report = collect_preflight(client)
    if not report.ready_for_capture:
        raise AdbError("preflight failed: root and battery current/voltage/status are required")
    if report.selinux.strip().lower() != "enforcing":
        raise AdbError("refusing a long hotplug experiment unless SELinux is Enforcing")
    if report.nodes.get("status", {}).get("value", "").strip().lower() != "discharging":
        raise AdbError("battery must be discharging for a battery-side experiment")
    capacity = report.nodes.get("capacity", {}).get("value")
    try:
        battery_percent = int(capacity)
    except (TypeError, ValueError):
        raise AdbError("battery capacity is unavailable; refusing a 1-2 hour run")
    if battery_percent < minimum_battery_percent:
        raise AdbError(
            "battery is at %d%%; at least %d%% is required for this long run"
            % (battery_percent, minimum_battery_percent)
        )

    topology = discover_cpu_topology(client)
    if topology.online_cpus != tuple(core.cpu_id for core in topology.cores):
        raise AdbError("all discovered CPUs must be online before the matrix starts")
    if tuple(core.cpu_id for core in topology.cores) != tuple(range(8)):
        raise AdbError("expected CPUs 0-7; refusing to apply an unreviewed topology plan")
    groups = topology.capacity_groups()
    if len(groups["E"]) != 6 or len(groups["P"]) != 2:
        raise AdbError("expected a 6+2 capacity topology for this plan")
    topology.require_hotplug_controls(tuple(range(1, 8)))
    policies = {policy.name: policy for policy in report.policies}
    cpu_policy = policy_by_cpu(report)
    if set(cpu_policy) != set(range(8)) or not {"policy0", "policy6"}.issubset(policies):
        raise AdbError("incomplete cpufreq policy mapping")
    for policy in policies.values():
        if policy.scaling_min_khz is None or policy.scaling_max_khz is None:
            raise AdbError("incomplete scaling limits for %s" % policy.name)
        if not policy.available_frequencies_khz.strip():
            raise AdbError("no discrete frequency table for %s" % policy.name)

    # Firmware-vote lock self-check (see helper for rationale).
    required_levels = 3 if reduced else 2
    locked = firmware_vote_locked_policies(report, required_levels)
    if locked:
        raise AdbError(
            "%s: a firmware/SCMI min/max vote appears to pin this policy. "
            "Root sysfs writes and stopping perf HALs cannot clear it — "
            "reboot the device to reset the firmware vote, then re-run."
            % "; ".join(locked)
        )

    cpu_paths, battery_path, temperatures = discover_thermal_sensors(client)
    cpu_temps = [value for name, value in temperatures.items() if name != "battery"]
    if not cpu_temps or max(cpu_temps) >= 80000 or temperatures.get("battery", 100000) >= 43000:
        raise AdbError("initial CPU/battery temperature exceeds the 80C/43C guard")
    return report, topology, cpu_paths, battery_path, temperatures


def run_full_matrix(
    client: AdbClient,
    binary_path: Path,
    output_prefix: Path,
    minimum_battery_percent: int = 60,
    confirm_independent_recovery: bool = False,
    reduced: bool = False,
) -> dict:
    if not 1 <= minimum_battery_percent <= 100:
        raise ValueError("minimum battery percentage must be between 1 and 100")
    require_verified_hotplug_recovery(confirm_independent_recovery)
    suffixes = (
        ".raw.log",
        ".samples.csv",
        ".runs.csv",
        ".curve.csv",
        ".configs.csv",
        ".workers.csv",
        ".plan.json",
        ".summary.json",
        ".pareto.svg",
        ".single-core-frequency.svg",
        ".mixed-heatmap.svg",
    )
    artifacts = {suffix: output_prefix.with_name(output_prefix.name + suffix) for suffix in suffixes}
    existing = [str(path) for path in artifacts.values() if path.exists()]
    if existing:
        raise FileExistsError("refusing to overwrite matrix outputs: " + ", ".join(existing))
    if not binary_path.is_file():
        raise ValueError("benchmark binary not found: %s" % binary_path)

    report, topology, cpu_temp_paths, battery_temp_path, initial_temperatures = _validate_device_ready(
        client, minimum_battery_percent, reduced=reduced
    )
    stage_a = build_stage_a(topology, report)
    if reduced:
        stage_b = build_reduced_stage_b(topology, report)
        stage_c = build_reduced_stage_c(topology, report)
        stage_d = build_reduced_stage_d(topology, report)
        stage_c_count = len(stage_c)
        stage_d_count = len(stage_d)
        budget = reduced_plan_budget_seconds(topology, report)
        budget_total = total_reduced_plan_seconds(topology, report)
    else:
        stage_b = build_stage_b(topology, report)
        capacity_groups = topology.capacity_groups()
        stage_c_count = len(build_stage_c(topology, report, capacity_groups["E"], capacity_groups["P"]))
        stage_d_count = len(build_stage_d(topology, report, capacity_groups["E"], capacity_groups["P"]))
        budget = plan_budget_seconds(topology, report)
        budget_total = total_plan_seconds(topology, report)
    remote_bench = deploy_benchmark(client, binary_path)
    started_utc = _utc_now()
    raw_path = artifacts[".raw.log"]
    all_configs: List[MatrixConfig] = []
    final_candidates: List[MatrixConfig] = []
    retest_configs: List[MatrixConfig] = []
    sustained_configs: List[MatrixConfig] = []
    source_by_retest: Dict[str, str] = {}

    plan = {
        "protocol_version": 1,
        "started_utc": started_utc,
        "serial": report.serial,
        "device": report.device,
        "model": report.model,
        "soc": report.soc,
        "selinux": report.selinux,
        "minimum_battery_percent": minimum_battery_percent,
        "initial_battery_percent": int(report.nodes["capacity"]["value"]),
        "initial_temperatures_mc": initial_temperatures,
        "core_topology": [asdict(core) for core in topology.cores],
        "capacity_groups": topology.capacity_groups(),
        "cpufreq_policies": [asdict(policy) for policy in report.policies],
        "planned_config_counts": (
            {"A": len(stage_a), "B": len(stage_b), "C": stage_c_count, "D": stage_d_count}
            if reduced
            else {"A": len(stage_a), "B": len(stage_b), "C": stage_c_count, "D": stage_d_count, "E": 9, "F": 2}
        ),
        "budget_seconds": budget,
        "budget_total_seconds": budget_total,
        "benchmark": "scalar_mix_v2 custom synchronized integer mixing throughput",
        "measurement_scope": "whole-device battery-side; not CPU-rail power",
        "core0_policy": "CPU0 is never offlined; non-CPU0 single-worker runs keep CPU0 online. Only CPU0-only has strict one-online-core isolation.",
        "remote_benchmark": remote_bench,
        "sample_interval_s": 1,
        "fixed_frequency_repetitions": MATRIX_SWEEP_REPETITIONS,
    }
    if reduced:
        plan["matrix_mode"] = "reduced"
        plan["reduced_matrix"] = {
            "representative_e_cpu": 1,
            "representative_p_cpu": 6,
            "frequency_levels_per_policy": 3,
            "warmup_s": 4,
            "measure_s": 15,
            "baseline_s": 3,
            "repetitions": 1,
            "note": "Representative low/mid/high points only; not the full per-core/full-grid matrix. Retest stages E/F are skipped.",
        }
    _write_json_exclusive(artifacts[".plan.json"], plan)
    if reduced:
        print(
            "Reduced matrix plan: %d representative single-core configs, %d cluster-scaling configs, %d E/P mixed pairs; planned budget %.1f min."
            % (len(stage_b), stage_c_count, stage_d_count, budget_total / 60.0),
            flush=True,
        )
    else:
        print(
            "Matrix plan: %d per-core frequency configs, %d pure-cluster frequency configs, %d full E/P frequency pairs; planned budget %.1f min." % (len(stage_b), stage_c_count, stage_d_count, total_plan_seconds(topology, report) / 60.0),
            flush=True,
        )

    with raw_path.open("x", encoding="utf-8", newline="\n") as raw:
        raw.write("PLAN|%s|%s|%s\n" % (report.serial, report.soc, remote_bench))
        raw.flush()

        def run_batch(configs: Sequence[MatrixConfig]) -> None:
            for config in configs:
                print(
                    "[%s] %s workers=%s online=%s freq=%s planned=%ds"
                    % (
                        config.stage,
                        config.config_id,
                        config.worker_csv,
                        ",".join(str(cpu) for cpu in config.desired_online_cpus),
                        dict(config.policy_targets_khz),
                        config.planned_seconds,
                    ),
                    flush=True,
                )
                raw.write("HOST_CONFIG_BEGIN|%s|%s\n" % (config.config_id, _utc_now()))
                raw.flush()
                try:
                    output = execute_matrix_config(
                        client,
                        config,
                        topology,
                        report,
                        cpu_temp_paths,
                        battery_temp_path,
                        remote_bench,
                        confirm_independent_recovery=confirm_independent_recovery,
                        minimum_battery_percent=minimum_battery_percent,
                    )
                except BaseException as exc:
                    raw.write("HOST_CONFIG_ERROR|%s|%r\n" % (config.config_id, exc))
                    remote_output = getattr(exc, "remote_output", "")
                    if remote_output:
                        raw.write("REMOTE_DIAGNOSTICS_BEGIN|%s\n" % config.config_id)
                        raw.write(remote_output)
                        if not remote_output.endswith("\n"):
                            raw.write("\n")
                        raw.write("REMOTE_DIAGNOSTICS_END|%s\n" % config.config_id)
                    raw.flush()
                    raise
                raw.write(output)
                if not output.endswith("\n"):
                    raw.write("\n")
                raw.write("HOST_CONFIG_END|%s|%s\n" % (config.config_id, _utc_now()))
                raw.flush()
                all_configs.append(config)

        run_batch(stage_a)
        run_batch(stage_b)
        raw_text = raw_path.read_text(encoding="utf-8", errors="replace")
        readings, runs = parse_matrix_log(raw_text)
        current_metrics = analyze_matrix_runs(all_configs, readings, runs)
        if reduced:
            required_workers = {config.target_cpus[0] for config in stage_b}
            valid_core_points: Dict[int, int] = {cpu: 0 for cpu in required_workers}
            for row in aggregate_matrix(current_metrics, stage_b):
                if row["stage"] == "B" and row["valid"]:
                    cpu = int(row["target_cpus"])
                    if cpu in valid_core_points:
                        valid_core_points[cpu] += 1
            missing = [cpu for cpu, count in valid_core_points.items() if count < 1]
            if missing:
                raise AdbError(
                    "reduced stage B has no valid frequency point for representative CPUs %s; stopping before multicore matrix"
                    % sorted(missing)
                )
            stage_c = build_reduced_stage_c(topology, report)
            stage_d = build_reduced_stage_d(topology, report)
            run_batch(stage_c)
            run_batch(stage_d)
            e_order: Tuple[int, ...] = ()
            p_order: Tuple[int, ...] = ()
        else:
            valid_core_points = {cpu: 0 for cpu in range(8)}
            for row in aggregate_matrix(current_metrics, stage_b):
                if row["stage"] == "B" and row["valid"]:
                    cpu = int(row["target_cpus"])
                    valid_core_points[cpu] = valid_core_points.get(cpu, 0) + 1
            missing = [cpu for cpu, count in valid_core_points.items() if count < 2]
            if missing:
                raise AdbError(
                    "stage B has fewer than two valid frequency points for CPUs %s; stopping before multicore matrix"
                    % missing
                )
            e_order, p_order = rank_single_core_order(stage_b, current_metrics, topology)
            print("Stage B efficiency order: E=%s P=%s" % (e_order, p_order), flush=True)

            stage_c = build_stage_c(topology, report, e_order, p_order)
            stage_d = build_stage_d(topology, report, e_order, p_order)
            run_batch(stage_c)
            run_batch(stage_d)
            raw_text = raw_path.read_text(encoding="utf-8", errors="replace")
            readings, runs = parse_matrix_log(raw_text)
            current_metrics = analyze_matrix_runs(all_configs, readings, runs)
            final_candidates = choose_retest_configs(all_configs, current_metrics, limit=3)
            if not final_candidates:
                raise AdbError("no valid pure/mixed candidate survived battery/frequency/affinity validity checks")
            retest_configs, source_by_retest = _build_retest_configs(final_candidates)
            run_batch(retest_configs)
            raw_text = raw_path.read_text(encoding="utf-8", errors="replace")
            readings, runs = parse_matrix_log(raw_text)
            current_metrics = analyze_matrix_runs(all_configs, readings, runs)
            sustained_configs = _build_sustained_configs(
                current_metrics, source_by_retest, final_candidates
            )
            if not sustained_configs:
                raise AdbError("no valid retest candidate available for sustained validation")
            run_batch(sustained_configs)

    verify_device_snapshot(client, topology, report)
    raw_text = raw_path.read_text(encoding="utf-8", errors="replace")
    readings, runs = parse_matrix_log(raw_text)
    metrics = analyze_matrix_runs(all_configs, readings, runs)
    aggregate = aggregate_matrix(metrics, all_configs)

    sample_rows = []
    for reading in readings:
        sample = reading.sample
        sample_rows.append(
            {
                "config_id": reading.config_id,
                "repetition": reading.repetition,
                "phase": reading.phase,
                "uptime_midpoint_s": "%.3f" % reading.time_s,
                "current_before_s": "%.2f" % sample.current_before_s,
                "current_now_uA": sample.current_ua,
                "current_after_s": "%.2f" % sample.current_after_s,
                "voltage_before_s": "%.2f" % sample.voltage_before_s,
                "voltage_now_uV": sample.voltage_uv,
                "voltage_after_s": "%.2f" % sample.voltage_after_s,
                "battery_status": sample.status,
                "battery_capacity_percent": sample.capacity_percent,
                "battery_temp_mC": reading.battery_temp_mc,
                "cpu_temp_mC": reading.cpu_temp_mc,
                "policy0_frequency_khz": reading.policy0_frequency_khz,
                "policy6_frequency_khz": reading.policy6_frequency_khz,
                "online_cpus": ",".join(str(cpu) for cpu in reading.online_cpus),
                "charge_counter_uAh": sample.charge_counter_uah,
                "battery_power_W": battery_power_w(sample.current_ua, sample.voltage_uv, sample.status),
            }
        )
    sample_fields = [
        "config_id", "repetition", "phase", "uptime_midpoint_s", "current_before_s", "current_now_uA",
        "current_after_s", "voltage_before_s", "voltage_now_uV", "voltage_after_s", "battery_status",
        "battery_capacity_percent", "battery_temp_mC", "cpu_temp_mC", "policy0_frequency_khz", "policy6_frequency_khz",
        "online_cpus", "charge_counter_uAh", "battery_power_W",
    ]
    from .matrix_analysis import write_matrix_csv

    write_matrix_csv(artifacts[".samples.csv"], sample_rows, sample_fields)
    metric_rows = matrix_metric_dicts(metrics)
    metric_fields = list(metrics[0].__dataclass_fields__) if metrics else []
    write_matrix_csv(artifacts[".runs.csv"], metric_rows, metric_fields)
    aggregate_fields = list(aggregate[0].keys()) if aggregate else []
    write_matrix_csv(artifacts[".curve.csv"], aggregate, aggregate_fields)
    all_used_configs = stage_a + stage_b + stage_c + stage_d + retest_configs + sustained_configs
    config_rows = _config_rows(all_used_configs)
    config_fields = list(config_rows[0].keys()) if config_rows else []
    write_matrix_csv(artifacts[".configs.csv"], config_rows, config_fields)

    worker_rows = []
    for run in runs:
        for worker in run.workers:
            worker_rows.append(
                {
                    "config_id": run.config_id,
                    "repetition": run.repetition,
                    "target_cpu": worker.target_cpu,
                    "observed_cpu": worker.observed_cpu,
                    "warmup_iterations": worker.warmup_iterations,
                    "measure_iterations": worker.measure_iterations,
                    "elapsed_s": worker.elapsed_s,
                    "operations_per_second": worker.operations_per_second,
                    "checksum": worker.checksum,
                }
            )
    worker_fields = [
        "config_id", "repetition", "target_cpu", "observed_cpu", "warmup_iterations",
        "measure_iterations", "elapsed_s", "operations_per_second", "checksum",
    ]
    write_matrix_csv(artifacts[".workers.csv"], worker_rows, worker_fields)
    render_matrix_pareto_svg(aggregate, artifacts[".pareto.svg"])
    render_single_core_frequency_curves(aggregate, artifacts[".single-core-frequency.svg"])
    render_mixed_frequency_heatmap(aggregate, artifacts[".mixed-heatmap.svg"])

    summary = {
        "protocol_version": 1,
        "serial": report.serial,
        "model": report.model,
        "device": report.device,
        "soc": report.soc,
        "android_release": report.android_release,
        "selinux": report.selinux,
        "started_utc": started_utc,
        "completed_utc": _utc_now(),
        "benchmark": "scalar_mix_v2 (custom synchronized per-worker 64-bit integer mixing; not Geekbench)",
        "measurement_scope": "whole-device battery-side current_now x voltage_now; not CPU rail power",
        "power_method": "total device battery draw, with paired same-mask/same-frequency idle baselines as a secondary net-power estimate",
        "temperature_abort_c": {"cpu": 80, "battery": 43},
        "fixed_frequency_repetitions": MATRIX_SWEEP_REPETITIONS,
        "single_core_frequency_curve": str(artifacts[".single-core-frequency.svg"]),
        "frequency_policy": "only available discrete steps inside pre-test scaling limits; original min/max restored and verified after every configuration",
        "topology": {"E_capacity_group": topology.capacity_groups()["E"], "P_capacity_group": topology.capacity_groups()["P"]},
        "core0_limitation": "CPU0 is intentionally kept online whenever it is not a benchmark worker. Only the CPU0-only run has strict single-online-core isolation; CPU1-7 runs are pinned one-worker measurements with CPU0 available for Android/background work.",
        "run_counts": {
            "configurations": len(all_used_configs),
            "benchmark_runs": len(runs),
            "valid_runs": sum(1 for metric in metrics if metric.valid),
            "invalid_runs": sum(1 for metric in metrics if not metric.valid),
            "valid_configurations": sum(1 for row in aggregate if row["valid"]),
        },
        "stage_b_single_worker_valid_points": valid_core_points,
        "stage_b_efficiency_order": {"E": list(e_order), "P": list(p_order)},
        "selected_retest_source_configs": [config.config_id for config in final_candidates],
        "sustained_configs": [config.config_id for config in sustained_configs],
        "matrix_mode": "reduced" if reduced else "full",
        "cpu_mask_and_cpufreq_restoration_verified": True,
        "artifacts": {key: str(value) for key, value in artifacts.items()},
        "best_by_total_battery_efficiency": max(
            (row for row in aggregate if row.get("operations_per_total_joule_median") is not None and row.get("valid")),
            key=lambda row: row["operations_per_total_joule_median"],
            default=None,
        ),
        "pareto_candidates": pareto_rows(aggregate),
        "disclaimer": "Conclusions apply only to this device, Android build, thermal/power state, and scalar_mix_v2 workload. Not directly comparable to Geekbench or third-party power tests.",
    }
    _write_json_exclusive(artifacts[".summary.json"], summary)
    print("Matrix complete: %s" % artifacts[".summary.json"], flush=True)
    return summary
