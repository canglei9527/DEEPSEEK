"""Deterministic staged experiment plan for per-core and mixed-cluster tests."""

from __future__ import annotations

from dataclasses import dataclass
import random
from typing import Dict, List, Optional, Sequence, Tuple

from .config import (
    MATRIX_SWEEP_BASELINE_S, MATRIX_SWEEP_COOLDOWN_S, MATRIX_SWEEP_MEASURE_S,
    MATRIX_SWEEP_REPETITIONS, MATRIX_SWEEP_WARMUP_S,
)
from .models import PreflightReport
from .topology import CpuTopology


@dataclass(frozen=True)
class MatrixConfig:
    config_id: str
    stage: str
    target_cpus: Tuple[int, ...]
    policy_targets_khz: Tuple[Tuple[str, int], ...]
    warmup_s: int
    measure_s: int
    baseline_s: int
    cooldown_s: int = 0
    repetitions: int = 1

    @property
    def planned_seconds(self) -> int:
        return (
            2 * self.baseline_s
            + self.repetitions * (self.warmup_s + self.measure_s)
            + max(0, self.repetitions - 1) * self.cooldown_s
        )

    @property
    def worker_csv(self) -> str:
        return ",".join(str(cpu) for cpu in self.target_cpus)

    @property
    def desired_online_cpus(self) -> Tuple[int, ...]:
        return tuple(sorted(set(self.target_cpus) | {0}))

    @property
    def keeper_cpus(self) -> Tuple[int, ...]:
        return tuple(cpu for cpu in self.desired_online_cpus if cpu not in self.target_cpus)

    @property
    def strict_single_core_isolation(self) -> bool:
        return len(self.target_cpus) == 1 and self.desired_online_cpus == self.target_cpus

    def target_for(self, policy: str) -> Optional[int]:
        return dict(self.policy_targets_khz).get(policy)


def policy_by_cpu(report: PreflightReport) -> Dict[int, str]:
    result = {}
    for policy in report.policies:
        for item in policy.related_cpus.split():
            if item.isdigit():
                cpu = int(item)
                if cpu in result:
                    raise ValueError("CPU%d appears in multiple cpufreq policies" % cpu)
                result[cpu] = policy.name
    return result


def frequency_levels(report: PreflightReport, policy_name: str, count: int) -> Tuple[int, ...]:
    policy = next((item for item in report.policies if item.name == policy_name), None)
    if policy is None or policy.scaling_min_khz is None or policy.scaling_max_khz is None:
        raise ValueError("cpufreq limits unavailable for %s" % policy_name)
    values = sorted(
        {
            int(value)
            for value in policy.available_frequencies_khz.split()
            if value.isdigit()
            and policy.scaling_min_khz <= int(value) <= policy.scaling_max_khz
            and (policy.hardware_min_khz is None or int(value) >= policy.hardware_min_khz)
        }
    )
    if not values:
        raise ValueError("no available frequency steps for %s inside current limits" % policy_name)
    if count >= len(values):
        return tuple(values)
    indices = []
    for index in range(count):
        selected = int(round(index * (len(values) - 1) / float(count - 1))) if count > 1 else 0
        if selected not in indices:
            indices.append(selected)
    if len(indices) < count:
        for selected in range(len(values)):
            if selected not in indices:
                indices.append(selected)
                if len(indices) == count:
                    break
    return tuple(values[index] for index in sorted(indices))


def all_frequency_levels(report: PreflightReport, policy_name: str) -> Tuple[int, ...]:
    policy = next((item for item in report.policies if item.name == policy_name), None)
    if policy is None:
        raise ValueError("cpufreq policy not found: %s" % policy_name)
    return frequency_levels(report, policy_name, max(1, len(policy.available_frequencies_khz.split())))


def validate_matrix_config(
    config: MatrixConfig,
    topology: CpuTopology,
    report: PreflightReport,
) -> None:
    if not config.config_id or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for ch in config.config_id):
        raise ValueError("unsafe config id")
    if not config.target_cpus or len(set(config.target_cpus)) != len(config.target_cpus):
        raise ValueError("worker CPU list must be nonempty and unique")
    if tuple(sorted(config.target_cpus)) != config.target_cpus:
        raise ValueError("worker CPU ids must be sorted")
    topology.require_hotplug_controls(config.target_cpus)
    if min(config.warmup_s, config.measure_s, config.baseline_s, config.repetitions) < 1 or config.cooldown_s < 0:
        raise ValueError("invalid matrix timing")
    cpu_policy = policy_by_cpu(report)
    required_policies = {cpu_policy[cpu] for cpu in config.target_cpus if cpu in cpu_policy}
    targets = dict(config.policy_targets_khz)
    if len(targets) != len(config.policy_targets_khz) or required_policies != set(targets):
        raise ValueError("exactly one locked frequency is required for each worker policy")
    for policy_name, frequency in targets.items():
        policy = next((item for item in report.policies if item.name == policy_name), None)
        if policy is None or policy.scaling_min_khz is None or policy.scaling_max_khz is None:
            raise ValueError("missing current policy limits for %s" % policy_name)
        if frequency < policy.scaling_min_khz or frequency > policy.scaling_max_khz:
            raise ValueError("%s target %d exceeds its pre-test frequency range" % (policy_name, frequency))
        available = {int(value) for value in policy.available_frequencies_khz.split() if value.isdigit()}
        if frequency not in available:
            raise ValueError("%s target %d is not an available frequency step" % (policy_name, frequency))


def _clamp_frequency(report: PreflightReport, policy_name: str, frequency: int) -> int:
    """Clamp a target into the policy's current scaling range.

    Concurrent system limit changes can narrow the usable window between the
    preflight read and the actual lock. Clamping to the live scaling min/max
    keeps the target writable instead of hitting an out-of-range sysfs write.
    """
    policy = next((item for item in report.policies if item.name == policy_name), None)
    if policy is None or policy.scaling_min_khz is None or policy.scaling_max_khz is None:
        return frequency
    return max(policy.scaling_min_khz, min(policy.scaling_max_khz, frequency))


def build_stage_a(
    topology: CpuTopology, report: PreflightReport
) -> List[MatrixConfig]:
    groups = topology.capacity_groups()
    policies = policy_by_cpu(report)
    e_cpu = next(cpu for cpu in groups["E"] if cpu != 0)
    p_cpu = groups["P"][0]
    e_policy = policies[e_cpu]
    p_policy = policies[p_cpu]
    # Use the mid-range available step inside the *current* scaling window, not
    # the absolute lowest step. A concurrent system clamp can make the lowest
    # step fall outside the live writable range and abort the smoke with an
    # out-of-range sysfs write.
    e_levels = frequency_levels(report, e_policy, 3)
    p_levels = frequency_levels(report, p_policy, 3)
    e_low = _clamp_frequency(report, e_policy, e_levels[len(e_levels) // 2])
    p_low = _clamp_frequency(report, p_policy, p_levels[len(p_levels) // 2])
    specs = [
        ("A-CPU0", (0,), ((policies[0], e_low),)),
        ("A-CPU-E", (e_cpu,), ((e_policy, e_low),)),
        ("A-CPU-P", (p_cpu,), ((p_policy, p_low),)),
        ("A-MIXED", tuple(sorted((0, e_cpu, p_cpu))), ((e_policy, e_low), (p_policy, p_low))),
    ]
    result = [
        MatrixConfig(name, "A", cpus, freqs, warmup_s=1, measure_s=3, baseline_s=2)
        for name, cpus, freqs in specs
    ]
    for config in result:
        validate_matrix_config(config, topology, report)
    return result


def build_stage_b(topology: CpuTopology, report: PreflightReport) -> List[MatrixConfig]:
    policies = policy_by_cpu(report)
    per_policy_levels = {name: all_frequency_levels(report, name) for name in sorted(set(policies.values()))}
    result = []
    for cpu in sorted(topology.by_id):
        policy = policies.get(cpu)
        if policy is None:
            raise ValueError("CPU%d has no active cpufreq policy" % cpu)
        for index, frequency in enumerate(per_policy_levels[policy], 1):
            config = MatrixConfig(
                config_id="B-CPU%d-F%d-%d" % (cpu, index, frequency),
                stage="B",
                target_cpus=(cpu,),
                policy_targets_khz=((policy, frequency),),
                warmup_s=MATRIX_SWEEP_WARMUP_S,
                measure_s=MATRIX_SWEEP_MEASURE_S,
                baseline_s=MATRIX_SWEEP_BASELINE_S,
                cooldown_s=MATRIX_SWEEP_COOLDOWN_S,
                repetitions=MATRIX_SWEEP_REPETITIONS,
            )
            validate_matrix_config(config, topology, report)
            result.append(config)
    random.Random(20261110).shuffle(result)
    return result


def build_stage_c(
    topology: CpuTopology,
    report: PreflightReport,
    e_order: Sequence[int],
    p_order: Sequence[int],
) -> List[MatrixConfig]:
    groups = topology.capacity_groups()
    policies = policy_by_cpu(report)
    e_policy = policies[groups["E"][0]]
    p_policy = policies[groups["P"][0]]
    e_freqs = all_frequency_levels(report, e_policy)
    p_freqs = all_frequency_levels(report, p_policy)
    e_nonboot = [cpu for cpu in e_order if cpu != 0]
    if len(e_nonboot) < 5:
        e_nonboot.extend(cpu for cpu in groups["E"] if cpu not in e_nonboot and cpu != 0)
    p_sorted = list(p_order) + [cpu for cpu in groups["P"] if cpu not in p_order]
    specs = []
    for count in (2, 4):
        specs.extend(("E%d" % count, tuple(sorted(e_nonboot[:count])), e_policy, frequency) for frequency in e_freqs)
    specs.extend(("E6", tuple(sorted(groups["E"])), e_policy, frequency) for frequency in e_freqs)
    specs.extend(("P2", tuple(sorted(p_sorted[:2])), p_policy, frequency) for frequency in p_freqs)

    result = []
    for label, cpus, policy, frequency in specs:
        config = MatrixConfig(
            config_id="C-%s-F%d" % (label, frequency),
            stage="C",
            target_cpus=cpus,
            policy_targets_khz=((policy, frequency),),
            warmup_s=MATRIX_SWEEP_WARMUP_S,
            measure_s=MATRIX_SWEEP_MEASURE_S,
            baseline_s=MATRIX_SWEEP_BASELINE_S,
            cooldown_s=MATRIX_SWEEP_COOLDOWN_S,
            repetitions=MATRIX_SWEEP_REPETITIONS,
        )
        validate_matrix_config(config, topology, report)
        result.append(config)
    random.Random(20261111).shuffle(result)
    return result


def build_stage_d(
    topology: CpuTopology,
    report: PreflightReport,
    e_order: Sequence[int],
    p_order: Sequence[int],
) -> List[MatrixConfig]:
    groups = topology.capacity_groups()
    policies = policy_by_cpu(report)
    e_policy = policies[groups["E"][0]]
    p_policy = policies[groups["P"][0]]
    e_freqs = all_frequency_levels(report, e_policy)
    p_freqs = all_frequency_levels(report, p_policy)
    e_nonboot = [cpu for cpu in e_order if cpu != 0]
    e_nonboot.extend(cpu for cpu in groups["E"] if cpu != 0 and cpu not in e_nonboot)
    p_sorted = list(p_order) + [cpu for cpu in groups["P"] if cpu not in p_order]
    specs = ((1, 1), (2, 1), (4, 1), (6, 1), (4, 2), (6, 2))
    result = []
    for e_count, p_count in specs:
        if e_count == 6:
            e_cpus = tuple(sorted(groups["E"]))
        else:
            e_cpus = tuple(sorted(e_nonboot[:e_count]))
        p_cpus = tuple(sorted(p_sorted[:p_count]))
        cpus = tuple(sorted(e_cpus + p_cpus))
        for e_frequency in e_freqs:
            for p_frequency in p_freqs:
                config = MatrixConfig(
                    config_id="D-E%dP%d-EF%d-PF%d" % (e_count, p_count, e_frequency, p_frequency),
                    stage="D",
                    target_cpus=cpus,
                    policy_targets_khz=((e_policy, e_frequency), (p_policy, p_frequency)),
                    warmup_s=MATRIX_SWEEP_WARMUP_S,
                    measure_s=MATRIX_SWEEP_MEASURE_S,
                    baseline_s=MATRIX_SWEEP_BASELINE_S,
                    cooldown_s=MATRIX_SWEEP_COOLDOWN_S,
                    repetitions=MATRIX_SWEEP_REPETITIONS,
                )
                validate_matrix_config(config, topology, report)
                result.append(config)
    random.Random(20261112).shuffle(result)
    return result


def plan_budget_seconds(topology: CpuTopology, report: PreflightReport) -> Dict[str, int]:
    return {
        "A_preflight_and_recovery": 600,
        "A_smokes": sum(c.planned_seconds for c in build_stage_a(topology, report)),
        "B_all_single_core_frequencies": sum(c.planned_seconds for c in build_stage_b(topology, report)),
        "C_all_pure_cluster_frequencies": sum(c.planned_seconds for c in build_stage_c(topology, report, topology.capacity_groups()["E"], topology.capacity_groups()["P"])),
        "D_full_mixed_frequency_grid": sum(c.planned_seconds for c in build_stage_d(topology, report, topology.capacity_groups()["E"], topology.capacity_groups()["P"])),
        "E_candidate_retests": 9 * (2 * 3 + 4 + 20),
        "F_sustained_top_candidates": 2 * (2 * 4 + 6 + 120),
        "G_cooling_and_margin": 480,
        "H_final_restore_and_artifacts": 240,
    }


def total_plan_seconds(topology: CpuTopology, report: PreflightReport) -> int:
    return sum(plan_budget_seconds(topology, report).values())


# Reduced representative matrix (CLI "--reduced-matrix"): one canonical worker CPU
# per cluster, low/mid/high in-range frequency levels, a single repetition with a
# longer measurement window. Curve stages B/C/D keep their canonical config-id
# schemes so the existing analysis, ranking, plotting and validity rules apply
# unchanged; only the point count and timings differ from the full matrix.
REDUCED_MATRIX_REPRESENTATIVE_E_CPU = 1
REDUCED_MATRIX_REPRESENTATIVE_P_CPU = 6
REDUCED_MATRIX_LEVELS = 3
REDUCED_MATRIX_WARMUP_S = 4
REDUCED_MATRIX_MEASURE_S = 15
REDUCED_MATRIX_BASELINE_S = 3
REDUCED_MATRIX_REPETITIONS = 1
REDUCED_MATRIX_C_PANEL_COUNTS = ((2, 0), (4, 0), (6, 0), (0, 1), (0, 2))
REDUCED_MATRIX_D_PANELS = ((1, 1), (2, 1), (4, 1), (6, 1), (4, 2), (6, 2))


def _reduced_timing_kwargs() -> Dict[str, int]:
    return {
        "warmup_s": REDUCED_MATRIX_WARMUP_S,
        "measure_s": REDUCED_MATRIX_MEASURE_S,
        "baseline_s": REDUCED_MATRIX_BASELINE_S,
        "repetitions": REDUCED_MATRIX_REPETITIONS,
    }


def _reduced_e_cpus(e_order: Sequence[int], count: int) -> Tuple[int, ...]:
    ordered = [cpu for cpu in e_order if cpu != 0]
    if count == len(ordered) + 1:
        return tuple(sorted([0] + ordered))
    selected = ordered[:count]
    if len(selected) < count:
        raise ValueError("not enough ranked E cores for a reduced C/D panel")
    return tuple(sorted(selected))


def build_reduced_stage_b(
    topology: CpuTopology, report: PreflightReport
) -> List[MatrixConfig]:
    policies = policy_by_cpu(report)
    result = []
    for cpu in (REDUCED_MATRIX_REPRESENTATIVE_E_CPU, REDUCED_MATRIX_REPRESENTATIVE_P_CPU):
        policy = policies.get(cpu)
        if policy is None:
            raise ValueError("CPU%d has no active cpufreq policy" % cpu)
        levels = frequency_levels(report, policy, REDUCED_MATRIX_LEVELS)
        if len(levels) < REDUCED_MATRIX_LEVELS:
            raise ValueError(
                "%s exposes only %d in-range frequency steps; need %d for the reduced matrix"
                % (policy, len(levels), REDUCED_MATRIX_LEVELS)
            )
        for index, frequency in enumerate(levels, 1):
            config = MatrixConfig(
                config_id="B-CPU%d-F%d-%d" % (cpu, index, frequency),
                stage="B",
                target_cpus=(cpu,),
                policy_targets_khz=((policy, frequency),),
                **_reduced_timing_kwargs(),
            )
            validate_matrix_config(config, topology, report)
            result.append(config)
    return result


def build_reduced_stage_c(
    topology: CpuTopology, report: PreflightReport
) -> List[MatrixConfig]:
    groups = topology.capacity_groups()
    policies = policy_by_cpu(report)
    e_policy = policies[groups["E"][0]]
    p_policy = policies[groups["P"][0]]
    e_levels = frequency_levels(report, e_policy, REDUCED_MATRIX_LEVELS)
    p_levels = frequency_levels(report, p_policy, REDUCED_MATRIX_LEVELS)
    if len(e_levels) < REDUCED_MATRIX_LEVELS or len(p_levels) < REDUCED_MATRIX_LEVELS:
        raise ValueError("reduced matrix needs at least %d frequency levels per policy" % REDUCED_MATRIX_LEVELS)
    e_all = tuple(sorted(groups["E"]))
    p_all = tuple(sorted(groups["P"]))
    specs: List[Tuple[str, Tuple[int, ...], str, Tuple[int, ...]]] = []
    for e_count, p_count in REDUCED_MATRIX_C_PANEL_COUNTS:
        if e_count:
            label = "E%d" % e_count
            cpus = e_all[:e_count]
            specs.extend((label, cpus, e_policy, (frequency,)) for frequency in e_levels)
        if p_count:
            label = "P%d" % p_count
            cpus = p_all[:p_count]
            specs.extend((label, cpus, p_policy, (frequency,)) for frequency in p_levels)
    result = []
    for label, cpus, policy, frequencies in specs:
        frequency = frequencies[0]
        config = MatrixConfig(
            config_id="C-%s-F%d" % (label, frequency),
            stage="C",
            target_cpus=cpus,
            policy_targets_khz=((policy, frequency),),
            **_reduced_timing_kwargs(),
        )
        validate_matrix_config(config, topology, report)
        result.append(config)
    return result


def build_reduced_stage_d(
    topology: CpuTopology, report: PreflightReport
) -> List[MatrixConfig]:
    groups = topology.capacity_groups()
    policies = policy_by_cpu(report)
    e_policy = policies[groups["E"][0]]
    p_policy = policies[groups["P"][0]]
    e_levels = frequency_levels(report, e_policy, REDUCED_MATRIX_LEVELS)
    p_levels = frequency_levels(report, p_policy, REDUCED_MATRIX_LEVELS)
    if len(e_levels) < REDUCED_MATRIX_LEVELS or len(p_levels) < REDUCED_MATRIX_LEVELS:
        raise ValueError("reduced matrix needs at least %d frequency levels per policy" % REDUCED_MATRIX_LEVELS)
    e_all = tuple(sorted(groups["E"]))
    p_all = tuple(sorted(groups["P"]))
    result = []
    for e_count, p_count in REDUCED_MATRIX_D_PANELS:
        cpus = tuple(sorted(e_all[:e_count] + p_all[:p_count]))
        for e_frequency in e_levels:
            for p_frequency in p_levels:
                config = MatrixConfig(
                    config_id="D-E%dP%d-EF%d-PF%d" % (e_count, p_count, e_frequency, p_frequency),
                    stage="D",
                    target_cpus=cpus,
                    policy_targets_khz=((e_policy, e_frequency), (p_policy, p_frequency)),
                    **_reduced_timing_kwargs(),
                )
                validate_matrix_config(config, topology, report)
                result.append(config)
    return result


def reduced_plan_budget_seconds(topology: CpuTopology, report: PreflightReport) -> Dict[str, int]:
    return {
        "A_preflight_and_recovery": 600,
        "A_smokes": sum(c.planned_seconds for c in build_stage_a(topology, report)),
        "B_representative_single_core": sum(c.planned_seconds for c in build_reduced_stage_b(topology, report)),
        "C_cluster_scaling": sum(c.planned_seconds for c in build_reduced_stage_c(topology, report)),
        "D_reduced_mixed_grid": sum(c.planned_seconds for c in build_reduced_stage_d(topology, report)),
        "H_final_restore_and_artifacts": 240,
    }


def total_reduced_plan_seconds(topology: CpuTopology, report: PreflightReport) -> int:
    return sum(reduced_plan_budget_seconds(topology, report).values())
