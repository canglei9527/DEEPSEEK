"""Offline, bounded 8-worker/6+2 plan. Never reads or writes a device."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from itertools import zip_longest
from typing import Tuple

from . import config as settings
from .matrix_plan import (
    MatrixConfig, all_frequency_levels, frequency_levels, policy_by_cpu,
    validate_matrix_config,
)
from .models import CpuPolicy, PreflightReport
from .topology import CpuCore, CpuTopology


@dataclass(frozen=True)
class TwoHourSettings:
    total_s: int = settings.TWO_HOUR_TOTAL_S
    finish_reserve_s: int = settings.TWO_HOUR_FINISH_RESERVE_S
    grid_levels: int = settings.TWO_HOUR_GRID_LEVELS
    warmup_s: int = settings.TWO_HOUR_WARMUP_S
    measure_s: int = settings.TWO_HOUR_MEASURE_S
    baseline_s: int = settings.TWO_HOUR_BASELINE_S
    cooldown_s: int = settings.TWO_HOUR_COOLDOWN_S
    repetitions: int = settings.TWO_HOUR_REPETITIONS

    def __post_init__(self):
        for name, value in asdict(self).items():
            if isinstance(value, bool) or not isinstance(value, int) or value < (0 if name == 'cooldown_s' else 1):
                raise ValueError('invalid two-hour setting: ' + name)
        if self.total_s > settings.TWO_HOUR_TOTAL_S or self.finish_reserve_s >= self.total_s:
            raise ValueError('invalid two-hour deadline/reserve')
        if self.repetitions != 2 or not 3 <= self.grid_levels <= 5:
            raise ValueError('screening requires two repetitions and 3-5 grid levels')


@dataclass(frozen=True)
class TwoHourPlan:
    configs: Tuple[MatrixConfig, ...]
    coverage_ids: Tuple[str, ...]
    settings: TwoHourSettings
    frequencies: dict

    def document(self) -> dict:
        counts = {stage: sum(c.stage == stage for c in self.configs) for stage in ('B', 'C', 'D')}
        nominal = sum(c.planned_seconds for c in self.configs)
        overhead = (settings.TWO_HOUR_SETUP_BUDGET_S + settings.TWO_HOUR_TRANSPORT_BUDGET_S
                    + settings.TWO_HOUR_COOLING_BUDGET_S + settings.TWO_HOUR_ARTIFACT_BUDGET_S)
        return {
            'schema_version': 1, 'mode': 'two-hour-low-noise',
            'settings': asdict(self.settings), 'frequencies_khz': self.frequencies,
            'counts': counts, 'config_count': len(self.configs),
            'scoring_windows': sum(c.repetitions for c in self.configs),
            'nominal_measurement_s': nominal, 'estimated_with_allowances_s': nominal + overhead,
            'fits_estimate': nominal + overhead <= self.settings.total_s,
            'launch_cutoff_s': self.settings.total_s - self.settings.finish_reserve_s,
            'coverage_ids': list(self.coverage_ids),
            'scope': 'all single-worker frequencies; E6+P2 selected grid; E6/P2 controls',
            'measurement_scope': 'whole-device battery-side; not CPU-rail power',
            'recovery_gate_required': True,
            'configs': [asdict(c) for c in self.configs],
        }


def _spread(values, count):
    return tuple(values[round(i * (len(values) - 1) / (count - 1))]
                 for i in range(min(count, len(values)))) if len(values) >= count else tuple(values)


def build_two_hour_plan(topology: CpuTopology, report: PreflightReport,
                        options: TwoHourSettings = TwoHourSettings()) -> TwoHourPlan:
    """All single-core steps; five selected steps/policy for E6+P2 and controls."""
    groups = topology.capacity_groups()
    mapping = policy_by_cpu(report)
    if (groups['E'] != tuple(range(6)) or groups['P'] != (6, 7)
            or mapping != {**{c: 'policy0' for c in range(6)}, 6: 'policy6', 7: 'policy6'}):
        raise ValueError('two-hour plan requires reviewed policy0 CPU0-5 / policy6 CPU6-7 topology')
    if topology.online_cpus != tuple(range(8)):
        raise ValueError('all eight CPUs must be online at baseline')
    levels = {p: all_frequency_levels(report, p) for p in ('policy0', 'policy6')}
    selected = {p: frequency_levels(report, p, options.grid_levels) for p in levels}

    def make(name, stage, cpus, targets):
        c = MatrixConfig(name, stage, cpus, targets, options.warmup_s, options.measure_s,
                         options.baseline_s, options.cooldown_s, options.repetitions)
        validate_matrix_config(c, topology, report)
        return c

    singles = [[] for _ in range(8)]
    for cpu in range(8):
        p = mapping[cpu]
        for index, freq in enumerate(levels[p], 1):
            singles[cpu].append(make('B-CPU%d-F%d-%d' % (cpu, index, freq), 'B',
                                     (cpu,), ((p, freq),)))
    mixed = [make('D-E6P2-EF%d-PF%d' % (e, p), 'D', tuple(range(8)),
                  (('policy0', e), ('policy6', p)))
             for e in selected['policy0'] for p in selected['policy6']]
    pure = [make('C-%s-F%d' % (label, f), 'C', cpus, ((p, f),))
            for label, cpus, p in (('E6', tuple(range(6)), 'policy0'), ('P2', (6, 7), 'policy6'))
            for f in selected[p]]
    # Cover every CPU and a low/mid/high mixed subgrid before filling the plan.
    core_levels = {p: set(_spread(levels[p], 3)) for p in levels}
    grid_levels = {p: set(_spread(selected[p], 3)) for p in levels}
    single_cover = [[c for c in cs if c.policy_targets_khz[0][1] in core_levels[mapping[i]]]
                    for i, cs in enumerate(singles)]
    core_cover = [c for group in zip_longest(*single_cover) for c in group if c is not None]
    mixed_cover = [c for c in mixed if all(f in grid_levels[p] for p, f in c.policy_targets_khz)]
    pure_cover = [c for c in pure if c.policy_targets_khz[0][1] in grid_levels[c.policy_targets_khz[0][0]]]
    coverage = [c for group in zip_longest(core_cover, mixed_cover, pure_cover)
                for c in group if c is not None]
    done = {c.config_id for c in coverage}
    remainder = [[c for group in zip_longest(*singles) for c in group
                  if c is not None and c.config_id not in done],
                 [c for c in mixed if c.config_id not in done],
                 [c for c in pure if c.config_id not in done]]
    ordered = coverage + [c for group in zip_longest(*remainder) for c in group if c is not None]
    return TwoHourPlan(tuple(ordered), tuple(c.config_id for c in coverage), options,
                       {p: {'single': list(levels[p]), 'grid': list(selected[p])} for p in levels})


def load_two_hour_plan(document: dict) -> TwoHourPlan:
    """Load our saved local plan for offline analysis only, never device execution."""
    if document.get('schema_version') != 1 or document.get('mode') != 'two-hour-low-noise':
        raise ValueError('unsupported two-hour plan schema')
    try:
        options = TwoHourSettings(**document['settings'])
        configs = tuple(MatrixConfig(**{**c, 'target_cpus': tuple(c['target_cpus']),
                                        'policy_targets_khz': tuple(tuple(t) for t in c['policy_targets_khz'])})
                        for c in document['configs'])
        coverage = tuple(document['coverage_ids'])
        identifiers = {c.config_id for c in configs}
        if len(identifiers) != len(configs) or not set(coverage) <= identifiers:
            raise ValueError('duplicate configs or unknown coverage IDs')
        return TwoHourPlan(configs, coverage, options, document['frequencies_khz'])
    except (KeyError, TypeError) as exc:
        raise ValueError('invalid two-hour plan: %s' % exc) from exc


def load_offline_preflight(document: dict) -> Tuple[CpuTopology, PreflightReport]:
    """Interpret saved preflight JSON only; the topology here is an explicit assumption.

    Live runs discover and validate actual capacity/hotplug controls independently.
    """
    fields = dict(document)
    fields.pop('ready_for_capture', None)  # CLI report adds this derived, non-constructor field.
    fields['policies'] = tuple(CpuPolicy(**p) for p in document['policies'])
    report = PreflightReport(**fields)
    topology = CpuTopology(tuple(CpuCore(cpu, True, cpu != 0, 792 if cpu < 6 else 1024,
                                         0 if cpu < 6 else 1, cpu if cpu < 6 else cpu - 6,
                                         'offline-plan-assumption') for cpu in range(8)))
    return topology, report
