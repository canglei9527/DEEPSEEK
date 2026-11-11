"""Parse mixed sweep logs and calculate per-frequency performance/power results."""

from __future__ import annotations

import csv
import statistics
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .models import BatterySample
from .power import battery_power_w
from .sweep import SweepPoint


@dataclass(frozen=True)
class SweepReading:
    point_id: str
    repetition: int
    phase: str
    sample: BatterySample
    battery_temp_mc: int
    cpu_temp_mc: int
    actual_frequency_khz: Optional[int]

    @property
    def time_s(self) -> float:
        return self.sample.midpoint_s


@dataclass(frozen=True)
class BenchRun:
    point_id: str
    repetition: int
    cpu: int
    start_s: float
    end_s: float
    elapsed_s: float
    iterations: int
    operations_per_second: float


@dataclass(frozen=True)
class RunMetric:
    point_id: str
    policy: str
    cpu: int
    target_frequency_khz: int
    repetition: int
    elapsed_s: float
    iterations: int
    operations_per_second: float
    load_power_w: Optional[float]
    baseline_power_w: Optional[float]
    incremental_power_w: Optional[float]
    operations_per_joule: Optional[float]
    operations_per_total_joule: Optional[float]
    measured_energy_j: Optional[float]
    measured_coverage_s: float
    baseline_coverage_s: float
    actual_frequency_median_khz: Optional[float]
    frequency_match_fraction: Optional[float]
    max_cpu_temp_c: Optional[float]
    max_battery_temp_c: Optional[float]
    measurement_valid: bool
    valid: bool
    invalid_reason: str


def parse_sweep_log(raw: str) -> Tuple[List[SweepReading], List[BenchRun]]:
    readings: List[SweepReading] = []
    runs: List[BenchRun] = []
    current_run: Optional[Tuple[str, int, int]] = None
    starts: Dict[Tuple[str, int], float] = {}
    in_control_smoke = False

    for raw_line in raw.splitlines():
        marker = raw_line.strip()
        if marker == "CONTROL_SMOKE_BEGIN":
            in_control_smoke = True
            continue
        if marker == "SWEEP_BEGIN":
            in_control_smoke = False
            continue
        if in_control_smoke:
            continue
        fields = marker.split("|")
        if not fields:
            continue
        if fields[0] == "RUN" and len(fields) >= 4:
            current_run = (fields[1], int(fields[2]), int(fields[3]))
        elif fields[0] == "P" and len(fields) == 15:
            point_id = fields[1]
            repetition = int(fields[2])
            ci0 = float(fields[4])
            current_ua = int(fields[5])
            ci1 = float(fields[6])
            vi0 = float(fields[7])
            voltage_uv = int(fields[8])
            vi1 = float(fields[9])
            status = fields[10]
            battery_temp_mc = int(fields[11])
            cpu_temp_mc = int(fields[12])
            frequency = int(fields[13]) if fields[13].isdigit() else None
            charge_counter = int(fields[14]) if fields[14].lstrip("-").isdigit() else None
            readings.append(
                SweepReading(
                    point_id=point_id,
                    repetition=repetition,
                    phase=fields[3],
                    sample=BatterySample(
                        sequence=len(readings),
                        current_before_s=ci0,
                        current_ua=current_ua,
                        current_after_s=ci1,
                        voltage_before_s=vi0,
                        voltage_uv=voltage_uv,
                        voltage_after_s=vi1,
                        temp_deci_c=int(round(battery_temp_mc / 100.0)),
                        status=status,
                        capacity_percent=None,
                        charge_counter_uah=charge_counter,
                    ),
                    battery_temp_mc=battery_temp_mc,
                    cpu_temp_mc=cpu_temp_mc,
                    actual_frequency_khz=frequency,
                )
            )
        elif fields[0] == "B" and current_run is not None:
            point_id, repetition, cpu = current_run
            if len(fields) >= 4 and fields[1] == "MEASURE_BEGIN":
                starts[(point_id, repetition)] = float(fields[3])
            elif len(fields) >= 8 and fields[1] == "MEASURE_END":
                start = starts.get((point_id, repetition))
                if start is None:
                    continue
                runs.append(
                    BenchRun(
                        point_id=point_id,
                        repetition=repetition,
                        cpu=cpu,
                        start_s=start,
                        end_s=float(fields[3]),
                        elapsed_s=float(fields[4]),
                        iterations=int(fields[5]),
                        operations_per_second=float(fields[6]),
                    )
                )
                current_run = None
    return readings, runs


def _power_segments(
    readings: Sequence[SweepReading], start_s: float, end_s: float, max_gap_s: float = 5.0
) -> Tuple[float, float]:
    valid = []
    for reading in sorted(readings, key=lambda item: item.time_s):
        power = battery_power_w(
            reading.sample.current_ua,
            reading.sample.voltage_uv,
            reading.sample.status,
        )
        if power is not None:
            valid.append((reading.time_s, power))

    energy_j = 0.0
    covered_s = 0.0
    for (left_t, left_p), (right_t, right_p) in zip(valid, valid[1:]):
        dt = right_t - left_t
        if dt <= 0.0 or dt > max_gap_s:
            continue
        a = max(left_t, start_s)
        b = min(right_t, end_s)
        if b <= a:
            continue
        p_a = left_p + (right_p - left_p) * ((a - left_t) / dt)
        p_b = left_p + (right_p - left_p) * ((b - left_t) / dt)
        energy_j += (p_a + p_b) * 0.5 * (b - a)
        covered_s += b - a
    return energy_j, covered_s


def _power_window(readings: Sequence[SweepReading]) -> Tuple[Optional[float], float, float]:
    ordered = sorted(readings, key=lambda item: item.time_s)
    if len(ordered) < 2:
        return None, 0.0, 0.0
    energy, covered = _power_segments(ordered, ordered[0].time_s, ordered[-1].time_s)
    return (energy / covered if covered > 0.0 else None), energy, covered


def analyze_runs(
    points: Sequence[SweepPoint], readings: Sequence[SweepReading], bench_runs: Sequence[BenchRun]
) -> List[RunMetric]:
    point_map = {point.point_id: point for point in points}
    metrics = []
    for run in bench_runs:
        point = point_map.get(run.point_id)
        if point is None:
            continue
        point_readings = [reading for reading in readings if reading.point_id == run.point_id]
        active_readings = [
            reading
            for reading in point_readings
            if reading.repetition == run.repetition
            and reading.phase == "active"
            and run.start_s - 2.0 <= reading.time_s <= run.end_s + 2.0
        ]
        pre_readings = [
            reading for reading in point_readings if reading.phase == "baseline_pre"
        ]
        post_readings = [
            reading for reading in point_readings if reading.phase == "baseline_post"
        ]

        load_energy, load_coverage = _power_segments(
            active_readings, run.start_s, run.end_s
        )
        load_power = load_energy / load_coverage if load_coverage > 0.0 else None
        pre_power, pre_energy, pre_coverage = _power_window(pre_readings)
        post_power, post_energy, post_coverage = _power_window(post_readings)
        baseline_coverage = pre_coverage + post_coverage
        baseline_power = (
            (pre_energy + post_energy) / baseline_coverage
            if baseline_coverage > 0.0
            else None
        )
        delta_power = (
            load_power - baseline_power
            if load_power is not None and baseline_power is not None
            else None
        )
        ops_per_joule = (
            run.operations_per_second / delta_power
            if delta_power is not None and delta_power > 0.0
            else None
        )
        ops_per_total_joule = (
            run.operations_per_second / load_power
            if load_power is not None and load_power > 0.0
            else None
        )
        measured_readings = [
            reading
            for reading in active_readings
            if run.start_s <= reading.time_s <= run.end_s
        ]
        observed_frequencies = [
            reading.actual_frequency_khz
            for reading in measured_readings
            if reading.actual_frequency_khz is not None
        ]
        match_fraction = (
            sum(value == point.frequency_khz for value in observed_frequencies)
            / len(observed_frequencies)
            if observed_frequencies
            else None
        )
        cpu_temps = [reading.cpu_temp_mc for reading in measured_readings if reading.cpu_temp_mc >= 0]
        battery_temps = [reading.battery_temp_mc for reading in measured_readings if reading.battery_temp_mc >= 0]

        measurement_reasons = []
        if load_coverage < run.elapsed_s * 0.65:
            measurement_reasons.append("insufficient power-sample coverage")
        if match_fraction is None or match_fraction < 0.70:
            measurement_reasons.append("target frequency not held for enough samples")
        measurement_valid = not measurement_reasons
        reasons = list(measurement_reasons)
        if baseline_power is None:
            reasons.append("missing paired idle baseline")
        if delta_power is None or delta_power <= 0.0:
            reasons.append("non-positive incremental power")
        metrics.append(
            RunMetric(
                point_id=run.point_id,
                policy=point.policy,
                cpu=run.cpu,
                target_frequency_khz=point.frequency_khz,
                repetition=run.repetition,
                elapsed_s=run.elapsed_s,
                iterations=run.iterations,
                operations_per_second=run.operations_per_second,
                load_power_w=load_power,
                baseline_power_w=baseline_power,
                incremental_power_w=delta_power,
                operations_per_joule=ops_per_joule,
                operations_per_total_joule=ops_per_total_joule,
                measured_energy_j=load_energy if load_coverage > 0.0 else None,
                measured_coverage_s=load_coverage,
                baseline_coverage_s=baseline_coverage,
                actual_frequency_median_khz=(
                    float(statistics.median(observed_frequencies)) if observed_frequencies else None
                ),
                frequency_match_fraction=match_fraction,
                max_cpu_temp_c=max(cpu_temps) / 1000.0 if cpu_temps else None,
                max_battery_temp_c=max(battery_temps) / 1000.0 if battery_temps else None,
                measurement_valid=measurement_valid,
                valid=measurement_valid and not any(
                    reason in reasons for reason in ("missing paired idle baseline", "non-positive incremental power")
                ),
                invalid_reason="; ".join(reasons),
            )
        )
    return metrics


def aggregate_curve(metrics: Sequence[RunMetric]) -> List[dict]:
    groups: Dict[str, List[RunMetric]] = {}
    for metric in metrics:
        groups.setdefault(metric.point_id, []).append(metric)

    curve = []
    for point_id, rows in groups.items():
        measured = [row for row in rows if row.measurement_valid]
        incremental_valid = [row for row in rows if row.valid]

        def med(subset: Sequence[RunMetric], field: str) -> Optional[float]:
            values = [getattr(row, field) for row in subset if getattr(row, field) is not None]
            return float(statistics.median(values)) if values else None

        def sd(subset: Sequence[RunMetric], field: str) -> Optional[float]:
            values = [getattr(row, field) for row in subset if getattr(row, field) is not None]
            return float(statistics.stdev(values)) if len(values) >= 2 else None

        baseline_rows = [row for row in measured if row.baseline_power_w is not None]
        delta_rows = [row for row in measured if row.incremental_power_w is not None]
        first = rows[0]
        curve.append(
            {
                "point_id": point_id,
                "policy": first.policy,
                "cpu": first.cpu,
                "target_frequency_khz": first.target_frequency_khz,
                "measured_repetitions": len(measured),
                "valid_repetitions": len(incremental_valid),
                "attempted_repetitions": len(rows),
                "operations_per_second_median": med(measured, "operations_per_second"),
                "operations_per_second_sd": sd(measured, "operations_per_second"),
                "load_power_w_median": med(measured, "load_power_w"),
                "load_power_w_sd": sd(measured, "load_power_w"),
                "baseline_power_w_median": med(baseline_rows, "baseline_power_w"),
                "incremental_power_w_median": med(delta_rows, "incremental_power_w"),
                "incremental_power_w_sd": sd(delta_rows, "incremental_power_w"),
                "operations_per_joule_median": med(incremental_valid, "operations_per_joule"),
                "operations_per_total_joule_median": med(measured, "operations_per_total_joule"),
                "actual_frequency_khz_median": med(measured, "actual_frequency_median_khz"),
                "frequency_match_fraction_median": med(measured, "frequency_match_fraction"),
                "measurement_valid": len(measured) > 0,
                "incremental_valid": len(incremental_valid) > 0,
                "valid": len(measured) > 0,
            }
        )
    return sorted(curve, key=lambda row: (row["policy"], row["target_frequency_khz"]))


def write_dict_csv(path: Path, rows: Sequence[dict], fieldnames: Sequence[str]) -> None:
    if path.exists():
        raise FileExistsError("refusing to overwrite %s" % path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def run_metrics_dicts(metrics: Sequence[RunMetric]) -> List[dict]:
    return [asdict(metric) for metric in metrics]
