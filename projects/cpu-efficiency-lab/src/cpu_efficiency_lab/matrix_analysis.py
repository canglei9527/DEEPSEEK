"""Parse synchronized multi-core matrix logs and rank measured configurations."""

from __future__ import annotations

import csv
import statistics
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .matrix_plan import MatrixConfig
from .models import BatterySample
from .power import battery_power_w


@dataclass(frozen=True)
class MatrixReading:
    config_id: str
    repetition: int
    phase: str
    sample: BatterySample
    battery_temp_mc: int
    cpu_temp_mc: int
    policy0_frequency_khz: Optional[int]
    policy6_frequency_khz: Optional[int]
    online_cpus: Tuple[int, ...]

    @property
    def time_s(self) -> float:
        return self.sample.midpoint_s


@dataclass(frozen=True)
class WorkerResult:
    target_cpu: int
    observed_cpu: int
    warmup_iterations: int
    measure_iterations: int
    elapsed_s: float
    operations_per_second: float
    checksum: int


@dataclass(frozen=True)
class MatrixRun:
    config_id: str
    repetition: int
    cpus: Tuple[int, ...]
    start_s: float
    end_s: float
    elapsed_s: float
    total_iterations: int
    operations_per_second: float
    workers: Tuple[WorkerResult, ...]

    @property
    def worker_csv(self) -> str:
        return ",".join(str(cpu) for cpu in self.cpus)


@dataclass(frozen=True)
class MatrixMetric:
    config_id: str
    stage: str
    target_cpus: str
    online_cpus: str
    background_online_cpus: str
    strict_single_core_isolation: bool
    worker_count: int
    policy0_frequency_khz: Optional[int]
    policy6_frequency_khz: Optional[int]
    repetition: int
    required_repetitions: int
    elapsed_s: float
    total_iterations: int
    operations_per_second: float
    load_power_w: Optional[float]
    baseline_power_w: Optional[float]
    baseline_valid: bool
    incremental_power_w: Optional[float]
    incremental_valid: bool
    incremental_invalid_reason: str
    operations_per_total_joule: Optional[float]
    operations_per_incremental_joule: Optional[float]
    measured_coverage_s: float
    baseline_coverage_s: float
    actual_policy0_frequency_khz: Optional[float]
    actual_policy6_frequency_khz: Optional[float]
    policy0_match_fraction: Optional[float]
    policy6_match_fraction: Optional[float]
    core_mask_match_fraction: Optional[float]
    max_cpu_temp_c: Optional[float]
    max_battery_temp_c: Optional[float]
    workers_affinity_valid: bool
    valid: bool
    invalid_reason: str


def _optional_int(value: str) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_matrix_log(raw: str) -> Tuple[List[MatrixReading], List[MatrixRun]]:
    readings: List[MatrixReading] = []
    runs: List[MatrixRun] = []
    current_config = ""
    current_rep = 0
    current_cpus: Tuple[int, ...] = ()
    starts: Dict[Tuple[str, int], float] = {}
    workers: Dict[Tuple[str, int], List[WorkerResult]] = {}

    for raw_line in raw.splitlines():
        fields = raw_line.strip().split("|")
        if not fields:
            continue
        if fields[0] == "RUN" and len(fields) >= 5:
            current_config = fields[1]
            current_rep = int(fields[2])
            current_cpus = tuple(int(item) for item in fields[4].split(",") if item.isdigit())
            workers[(current_config, current_rep)] = []
        elif fields[0] == "M" and len(fields) in (17, 18):
            has_capacity = len(fields) == 18
            shift = 1 if has_capacity else 0
            try:
                ci0, current, ci1 = float(fields[4]), int(fields[5]), float(fields[6])
                vi0, voltage, vi1 = float(fields[7]), int(fields[8]), float(fields[9])
                battery_temp = int(fields[11])
                battery_capacity = int(fields[12]) if has_capacity else None
                cpu_temp = int(fields[12 + shift])
            except ValueError:
                continue
            charge = _optional_int(fields[15 + shift])
            readings.append(
                MatrixReading(
                    config_id=fields[1],
                    repetition=int(fields[2]),
                    phase=fields[3],
                    sample=BatterySample(
                        sequence=len(readings),
                        current_before_s=ci0,
                        current_ua=current,
                        current_after_s=ci1,
                        voltage_before_s=vi0,
                        voltage_uv=voltage,
                        voltage_after_s=vi1,
                        temp_deci_c=int(round(battery_temp / 100.0)),
                        status=fields[10],
                        capacity_percent=battery_capacity,
                        charge_counter_uah=charge,
                    ),
                    battery_temp_mc=battery_temp,
                    cpu_temp_mc=cpu_temp,
                    policy0_frequency_khz=_optional_int(fields[13 + shift]),
                    policy6_frequency_khz=_optional_int(fields[14 + shift]),
                    online_cpus=tuple(int(item) for item in fields[16 + shift].split(",") if item.isdigit()),
                )
            )
        elif fields[0] == "B" and len(fields) >= 5 and fields[1] == "MEASURE_BEGIN":
            if current_config:
                starts[(current_config, current_rep)] = float(fields[3])
        elif fields[0] == "B" and len(fields) >= 9 and fields[1] == "WORKER":
            if current_config:
                workers.setdefault((current_config, current_rep), []).append(
                    WorkerResult(
                        target_cpu=int(fields[2]),
                        observed_cpu=int(fields[3]),
                        warmup_iterations=int(fields[4]),
                        measure_iterations=int(fields[5]),
                        elapsed_s=float(fields[6]),
                        operations_per_second=float(fields[7]),
                        checksum=int(fields[8]),
                    )
                )
        elif fields[0] == "B" and len(fields) >= 8 and fields[1] == "MEASURE_END":
            if not current_config:
                continue
            key = (current_config, current_rep)
            start = starts.get(key)
            if start is None:
                continue
            runs.append(
                MatrixRun(
                    config_id=current_config,
                    repetition=current_rep,
                    cpus=current_cpus,
                    start_s=start,
                    end_s=float(fields[3]),
                    elapsed_s=float(fields[4]),
                    total_iterations=int(fields[5]),
                    operations_per_second=float(fields[6]),
                    workers=tuple(workers.get(key, ())),
                )
            )
            current_config = ""
    return readings, runs


def _power_segments(
    readings: Sequence[MatrixReading], start_s: float, end_s: float, max_gap_s: float = 5.0
) -> Tuple[float, float]:
    valid = []
    for reading in sorted(readings, key=lambda item: item.time_s):
        power = battery_power_w(reading.sample.current_ua, reading.sample.voltage_uv, reading.sample.status)
        if power is not None:
            valid.append((reading.time_s, power))
    energy_j = 0.0
    covered_s = 0.0
    for (left_t, left_p), (right_t, right_p) in zip(valid, valid[1:]):
        dt = right_t - left_t
        if dt <= 0.0 or dt > max_gap_s:
            continue
        a, b = max(left_t, start_s), min(right_t, end_s)
        if b <= a:
            continue
        p_a = left_p + (right_p - left_p) * ((a - left_t) / dt)
        p_b = left_p + (right_p - left_p) * ((b - left_t) / dt)
        energy_j += (p_a + p_b) * 0.5 * (b - a)
        covered_s += b - a
    return energy_j, covered_s


def _power_window(readings: Sequence[MatrixReading]) -> Tuple[Optional[float], float, float]:
    ordered = sorted(readings, key=lambda item: item.time_s)
    if len(ordered) < 2:
        return None, 0.0, 0.0
    energy, covered = _power_segments(ordered, ordered[0].time_s, ordered[-1].time_s)
    return (energy / covered if covered else None), energy, covered


def analyze_matrix_runs(
    configs: Sequence[MatrixConfig], readings: Sequence[MatrixReading], runs: Sequence[MatrixRun]
) -> List[MatrixMetric]:
    config_map = {config.config_id: config for config in configs}
    metrics = []
    for run in runs:
        config = config_map.get(run.config_id)
        if config is None:
            continue
        config_readings = [row for row in readings if row.config_id == run.config_id]
        active = [
            row for row in config_readings
            if row.repetition == run.repetition and row.phase == "active"
            and run.start_s - 2 <= row.time_s <= run.end_s + 2
        ]
        pre = [row for row in config_readings if row.phase == "baseline_pre"]
        post = [row for row in config_readings if row.phase == "baseline_post"]
        load_energy, load_coverage = _power_segments(active, run.start_s, run.end_s)
        load_power = load_energy / load_coverage if load_coverage else None
        _, pre_energy, pre_coverage = _power_window(pre)
        _, post_energy, post_coverage = _power_window(post)
        baseline_coverage = pre_coverage + post_coverage
        # A paired baseline requires usable samples on both sides of the load.
        baseline = (pre_energy + post_energy) / baseline_coverage if pre_coverage > 0 and post_coverage > 0 else None
        incremental = load_power - baseline if load_power is not None and baseline is not None else None
        measured = [row for row in active if run.start_s <= row.time_s <= run.end_s]
        expected = dict(config.policy_targets_khz)
        f0_values = [row.policy0_frequency_khz for row in measured if row.policy0_frequency_khz is not None]
        f6_values = [row.policy6_frequency_khz for row in measured if row.policy6_frequency_khz is not None]
        f0_match = _match_fraction(f0_values, expected.get("policy0"))
        f6_match = _match_fraction(f6_values, expected.get("policy6"))
        workers_valid = (
            tuple(sorted(run.cpus)) == config.target_cpus
            and tuple(sorted(worker.target_cpu for worker in run.workers)) == config.target_cpus
            and all(worker.observed_cpu == worker.target_cpu for worker in run.workers)
        )
        mask_rows = [row for row in measured if row.online_cpus]
        mask_match = (
            sum(row.online_cpus == config.desired_online_cpus for row in mask_rows) / float(len(mask_rows))
            if mask_rows else None
        )
        reasons = []
        if load_coverage < run.elapsed_s * 0.65:
            reasons.append("insufficient battery-sample coverage")
        if load_power is None or load_power <= 0:
            reasons.append("missing or non-positive load battery power")
        if not workers_valid:
            reasons.append("worker affinity/count mismatch")
        if mask_match is None or mask_match < 0.98:
            reasons.append("CPU online mask not held")
        for policy, target in expected.items():
            fraction = f0_match if policy == "policy0" else f6_match
            if fraction is None or fraction < 0.70:
                reasons.append("%s target frequency not held" % policy)
        primary_valid = not reasons
        if not primary_valid:
            incremental_reason = "primary measurement invalid"
        elif baseline is None:
            incremental_reason = "missing paired idle baseline"
        elif incremental is None or incremental <= 0:
            incremental_reason = "non-positive incremental battery power"
        else:
            incremental_reason = ""
        incremental_valid = not incremental_reason
        cpu_temps = [row.cpu_temp_mc for row in measured if row.cpu_temp_mc >= 0]
        battery_temps = [row.battery_temp_mc for row in measured if row.battery_temp_mc >= 0]
        metrics.append(
            MatrixMetric(
                config_id=run.config_id,
                stage=config.stage,
                target_cpus=run.worker_csv,
                online_cpus=",".join(str(cpu) for cpu in config.desired_online_cpus),
                background_online_cpus=",".join(str(cpu) for cpu in config.keeper_cpus),
                strict_single_core_isolation=config.strict_single_core_isolation,
                worker_count=len(run.workers),
                policy0_frequency_khz=expected.get("policy0"),
                policy6_frequency_khz=expected.get("policy6"),
                repetition=run.repetition,
                required_repetitions=config.repetitions,
                elapsed_s=run.elapsed_s,
                total_iterations=run.total_iterations,
                operations_per_second=run.operations_per_second,
                load_power_w=load_power,
                baseline_power_w=baseline,
                baseline_valid=baseline is not None,
                incremental_power_w=incremental,
                incremental_valid=incremental_valid,
                incremental_invalid_reason=incremental_reason,
                operations_per_total_joule=(
                    run.operations_per_second / load_power if load_power is not None and load_power > 0 else None
                ),
                operations_per_incremental_joule=(
                    run.operations_per_second / incremental if incremental_valid else None
                ),
                measured_coverage_s=load_coverage,
                baseline_coverage_s=baseline_coverage,
                actual_policy0_frequency_khz=float(statistics.median(f0_values)) if f0_values else None,
                actual_policy6_frequency_khz=float(statistics.median(f6_values)) if f6_values else None,
                policy0_match_fraction=f0_match,
                policy6_match_fraction=f6_match,
                core_mask_match_fraction=mask_match,
                max_cpu_temp_c=max(cpu_temps) / 1000.0 if cpu_temps else None,
                max_battery_temp_c=max(battery_temps) / 1000.0 if battery_temps else None,
                workers_affinity_valid=workers_valid,
                valid=primary_valid,
                invalid_reason="; ".join(reasons),
            )
        )
    return metrics


def _match_fraction(values: Sequence[int], target: Optional[int]) -> Optional[float]:
    if target is None:
        return None
    if not values:
        return None
    return sum(value == target for value in values) / float(len(values))


def aggregate_matrix(
    metrics: Sequence[MatrixMetric], configs: Optional[Sequence[MatrixConfig]] = None
) -> List[dict]:
    required_by_id = {config.config_id: config.repetitions for config in configs or ()}
    groups: Dict[str, List[MatrixMetric]] = {}
    for metric in metrics:
        groups.setdefault(metric.config_id, []).append(metric)
    result = []
    for config_id, rows in groups.items():
        valid = [row for row in rows if row.valid]

        def median(field: str) -> Optional[float]:
            values = [getattr(row, field) for row in valid if getattr(row, field) is not None]
            return float(statistics.median(values)) if values else None

        def incremental_median(field: str) -> Optional[float]:
            values = [getattr(row, field) for row in valid
                      if row.incremental_valid and getattr(row, field) is not None]
            return float(statistics.median(values)) if values else None

        def sd(field: str) -> Optional[float]:
            values = [getattr(row, field) for row in valid if getattr(row, field) is not None]
            return float(statistics.stdev(values)) if len(values) > 1 else None

        first = rows[0]
        required = required_by_id.get(config_id, rows[0].required_repetitions)
        repetition_ids = {row.repetition for row in rows}
        duplicate_repetitions = len(repetition_ids) != len(rows)
        repetition_sequence_valid = repetition_ids == set(range(1, required + 1))
        inconsistent_repetition_plan = any(row.required_repetitions != required for row in rows)
        result.append(
            {
                "config_id": config_id,
                "stage": first.stage,
                "target_cpus": first.target_cpus,
                "online_cpus": first.online_cpus,
                "background_online_cpus": first.background_online_cpus,
                "strict_single_core_isolation": first.strict_single_core_isolation,
                "worker_count": first.worker_count,
                "policy0_frequency_khz": first.policy0_frequency_khz,
                "policy6_frequency_khz": first.policy6_frequency_khz,
                "core_mask_match_fraction": median("core_mask_match_fraction"),
                "required_repetitions": required,
                "measured_repetitions": len(valid),
                "valid_incremental_repetitions": sum(row.incremental_valid for row in rows),
                "attempted_repetitions": len(rows),
                "duplicate_repetitions": duplicate_repetitions,
                "repetition_sequence_valid": repetition_sequence_valid,
                "inconsistent_repetition_plan": inconsistent_repetition_plan,
                "operations_per_second_median": median("operations_per_second"),
                "operations_per_second_sd": sd("operations_per_second"),
                "load_power_w_median": median("load_power_w"),
                "baseline_power_w_median": median("baseline_power_w"),
                "incremental_power_w_median": incremental_median("incremental_power_w"),
                "operations_per_total_joule_median": median("operations_per_total_joule"),
                "operations_per_total_joule_sd": sd("operations_per_total_joule"),
                "operations_per_total_joule_min": min((r.operations_per_total_joule for r in valid if r.operations_per_total_joule is not None), default=None),
                "operations_per_total_joule_max": max((r.operations_per_total_joule for r in valid if r.operations_per_total_joule is not None), default=None),
                "operations_per_incremental_joule_median": incremental_median("operations_per_incremental_joule"),
                "valid": len(valid) == required and len(rows) == required and not duplicate_repetitions and repetition_sequence_valid and not inconsistent_repetition_plan,
            }
        )
    return result


def rank_single_core_order(
    configs: Sequence[MatrixConfig], metrics: Sequence[MatrixMetric], topology: CpuTopology
) -> Tuple[Tuple[int, ...], Tuple[int, ...]]:
    scores: Dict[int, List[float]] = {}
    config_map = {config.config_id: config for config in configs if config.stage == "B"}
    rows_by_config: Dict[str, List[MatrixMetric]] = {}
    for metric in metrics:
        if metric.config_id in config_map:
            rows_by_config.setdefault(metric.config_id, []).append(metric)
    for config_id, rows in rows_by_config.items():
        config = config_map[config_id]
        valid_rows = [row for row in rows if row.valid and row.operations_per_total_joule is not None]
        if (len(valid_rows) == config.repetitions and len(rows) == config.repetitions
                and {row.repetition for row in rows} == set(range(1, config.repetitions + 1))
                and len(config.target_cpus) == 1):
            scores.setdefault(config.target_cpus[0], []).append(
                statistics.median(row.operations_per_total_joule for row in valid_rows)
            )
    groups = topology.capacity_groups()

    def ordered(cpus: Sequence[int]) -> Tuple[int, ...]:
        return tuple(
            sorted(
                cpus,
                key=lambda cpu: (
                    -statistics.median(scores[cpu]) if scores.get(cpu) else float("inf"),
                    cpu,
                ),
            )
        )

    return ordered(groups["E"]), ordered(groups["P"])


def choose_retest_configs(
    configs: Sequence[MatrixConfig], metrics: Sequence[MatrixMetric], limit: int = 6
) -> List[MatrixConfig]:
    config_map = {config.config_id: config for config in configs if config.stage in ("C", "D")}
    unique = {}
    for metric in metrics:
        if metric.config_id in config_map:
            unique.setdefault(metric.config_id, []).append(metric)
    summaries = []
    for config_id, all_rows in unique.items():
        config = config_map[config_id]
        rows = [row for row in all_rows if row.valid]
        if (len(rows) != config.repetitions or len(all_rows) != config.repetitions
                or {row.repetition for row in all_rows} != set(range(1, config.repetitions + 1))):
            continue
        power = statistics.median(row.load_power_w for row in rows if row.load_power_w is not None)
        throughput = statistics.median(row.operations_per_second for row in rows)
        efficiency = statistics.median(
            row.operations_per_total_joule for row in rows if row.operations_per_total_joule is not None
        )
        summaries.append((config, power, throughput, efficiency))
    if not summaries:
        return []
    summaries.sort(key=lambda item: item[3], reverse=True)
    chosen: List[MatrixConfig] = []

    def add(item) -> None:
        config = item[0]
        if config.config_id not in {existing.config_id for existing in chosen} and len(chosen) < limit:
            chosen.append(config)

    add(summaries[0])
    add(max(summaries, key=lambda item: item[2]))
    powers = sorted(item[1] for item in summaries)
    for percentile in (0.33, 0.66, 1.0):
        threshold_index = min(len(powers) - 1, int(round((len(powers) - 1) * percentile)))
        threshold = powers[threshold_index]
        candidates = [item for item in summaries if item[1] <= threshold]
        if candidates:
            add(max(candidates, key=lambda item: item[2]))
    for item in summaries:
        add(item)
        if len(chosen) >= limit:
            break
    return chosen[:limit]


def write_matrix_csv(path: Path, rows: Sequence[dict], fieldnames: Sequence[str]) -> None:
    if path.exists():
        raise FileExistsError("refusing to overwrite %s" % path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def matrix_metric_dicts(metrics: Sequence[MatrixMetric]) -> List[dict]:
    return [asdict(metric) for metric in metrics]
