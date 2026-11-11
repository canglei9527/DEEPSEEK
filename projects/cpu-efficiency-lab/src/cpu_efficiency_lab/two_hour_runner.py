"""Deadline scheduler, exclusive checkpoints and gated low-noise device adapter."""
from __future__ import annotations

import json
import shlex
import time
from dataclasses import asdict, replace
from pathlib import Path
from typing import Callable, Sequence

from .adb import AdbClient, AdbError
from .config import (
    TWO_HOUR_CONFIG_TIMEOUT_FACTOR, TWO_HOUR_CONFIG_TIMEOUT_EXTRA_S, TWO_HOUR_COOLING_BUDGET_S,
    LOW_NOISE_START_CPU_MC, LOW_NOISE_START_BATTERY_MC, LOW_NOISE_COOL_POLL_S,
)
from .low_noise_environment import ScreenOffLease
from .matrix_analysis import (
    aggregate_matrix, analyze_matrix_runs, matrix_metric_dicts, parse_matrix_log, write_matrix_csv,
)
from .matrix_controller import (
    execute_matrix_config, require_verified_hotplug_recovery, verify_device_snapshot,
)
from .matrix_plan import MatrixConfig, build_stage_a
from .matrix_plot import efficiency_plot_rows, render_mixed_frequency_heatmap, render_single_core_frequency_curves
from .matrix_runner import _validate_device_ready, _write_json_exclusive
from .sweep import BATTERY_TEMP_LIMIT_MC, CPU_TEMP_LIMIT_MC, deploy_benchmark
from .power import battery_power_w
from .two_hour_plan import TwoHourPlan, TwoHourSettings, build_two_hour_plan
from .two_hour_plot import render_frequency_slices


def config_admission_s(config: MatrixConfig) -> float:
    """Controller transport upper timeout plus log/snapshot allowance, not nominal work."""
    return config.planned_seconds * TWO_HOUR_CONFIG_TIMEOUT_FACTOR + TWO_HOUR_CONFIG_TIMEOUT_EXTRA_S


def _screening_metrics(configs, readings, runs):
    """Stricter screening checks without changing legacy matrix analysis policy."""
    metrics = analyze_matrix_runs(configs, readings, runs)
    result = []
    for metric in metrics:
        reasons = []
        for fraction in (metric.policy0_match_fraction, metric.policy6_match_fraction):
            if fraction is not None and fraction < 1.0:
                reasons.append('fixed frequency deviated during scoring')
        if metric.max_cpu_temp_c is not None and metric.max_cpu_temp_c >= CPU_TEMP_LIMIT_MC / 1000:
            reasons.append('CPU temperature limit')
        if metric.max_battery_temp_c is not None and metric.max_battery_temp_c >= BATTERY_TEMP_LIMIT_MC / 1000:
            reasons.append('battery temperature limit')
        idle = {phase: [battery_power_w(r.sample.current_ua, r.sample.voltage_uv, r.sample.status)
                        for r in readings if r.config_id == metric.config_id and r.phase == phase]
                for phase in ('baseline_pre', 'baseline_post')}
        idle_values = [v for values in idle.values() for v in values if v is not None]
        # Conservative observed noise floor, not a confidence interval. Correlated
        # fuel-gauge polls must not masquerade as many independent observations.
        noise_floor = max(idle_values) - min(idle_values) if idle_values else 0.0
        if reasons:
            metric = replace(metric, valid=False, invalid_reason='; '.join(filter(None, [metric.invalid_reason] + reasons)),
                             incremental_valid=False, incremental_invalid_reason='primary measurement invalid',
                             operations_per_incremental_joule=None)
        elif metric.incremental_valid and metric.incremental_power_w <= noise_floor:
            metric = replace(metric, incremental_valid=False,
                             incremental_invalid_reason='incremental power not above observed idle range',
                             operations_per_incremental_joule=None)
        result.append(metric)
    return result


def schedule_configs(configs: Sequence[MatrixConfig], execute: Callable[[MatrixConfig], str],
                     raw, events, started_s: float, options: TwoHourSettings,
                     clock: Callable[[], float] = time.monotonic, progress=None) -> dict:
    """Dependency-injected scheduler; fake clocks/backends can test this without ADB.

    Stop on controller/transport/cleanup exceptions. Analysis-invalid points with
    verified execution/restoration may be followed by other configurations.
    """
    states = {c.config_id: {'status': 'NOT_RUN', 'reason': 'not reached'} for c in configs}
    reason = None
    cutoff = started_s + options.total_s - options.finish_reserve_s

    def event(value):
        events.write(json.dumps(value, ensure_ascii=False) + '\n')
        events.flush()
        if progress is not None:
            progress(value)

    for config in configs:
        if clock() + config_admission_s(config) > cutoff:
            reason = 'DEADLINE'
            break
        state = states[config.config_id]
        state.update(status='STARTED', reason='')
        event({'event': 'CONFIG_START', 'config_id': config.config_id, 'elapsed_s': clock() - started_s})
        try:
            output = execute(config)
            raw.write(output + ('\n' if not output.endswith('\n') else ''))
            raw.flush()  # persist evidence before attempting analysis.
            readings, runs = parse_matrix_log(output)
            metrics = _screening_metrics((config,), readings, runs)
            rows = aggregate_matrix(metrics, (config,))
            valid = len(rows) == 1 and rows[0]['valid']
            state.update(status='VALID' if valid else 'INVALID',
                         reason='' if valid else 'incomplete or invalid measurement repetitions')
        except (AdbError, OSError, ValueError, KeyboardInterrupt) as exc:
            output = getattr(exc, 'remote_output', '')
            raw.write(output + '\nEXECUTION_ERROR|' + config.config_id + '|' + repr(exc) + '\n')
            raw.flush()
            state.update(status='FAILED', reason=repr(exc))
            reason = 'INTERRUPTED' if isinstance(exc, KeyboardInterrupt) else 'EXECUTION_FAILED'
        event({'event': 'CONFIG_RESULT', 'config_id': config.config_id,
               **state, 'elapsed_s': clock() - started_s})
        if reason:
            break
    for state in states.values():
        if state['status'] == 'NOT_RUN':
            state['reason'] = reason or 'not reached'
    event({'event': 'SCHEDULE_END', 'reason': reason, 'states': states})
    return {'states': states, 'stop_reason': reason, 'elapsed_s': clock() - started_s}


def analyze_two_hour_results(plan: TwoHourPlan, raw_text: str, states: dict) -> tuple:
    """Include every planned row, so missing frequencies stay gaps in plots."""
    readings, runs = parse_matrix_log(raw_text)
    metrics = _screening_metrics(plan.configs, readings, runs)
    aggregated = {r['config_id']: r for r in aggregate_matrix(metrics, plan.configs)}
    rows = []
    for c in plan.configs:
        targets = dict(c.policy_targets_khz)
        row = aggregated.get(c.config_id, {
            'config_id': c.config_id, 'stage': c.stage, 'target_cpus': c.worker_csv,
            'worker_count': len(c.target_cpus), 'required_repetitions': c.repetitions,
            'policy0_frequency_khz': targets.get('policy0'),
            'policy6_frequency_khz': targets.get('policy6'),
            'measured_repetitions': 0, 'attempted_repetitions': 0,
            'valid_incremental_repetitions': 0, 'valid': False,
        })
        row.update(states.get(c.config_id, {'status': 'NOT_RUN', 'reason': 'no execution evidence'}))
        # Samples from an interrupted/unverified controller cannot become valid
        # merely because native MEASURE_END happened to be present in its log.
        if row['status'] != 'VALID':
            row['valid'] = False
        rows.append(row)
    return metrics, rows


def write_two_hour_artifacts(directory: Path, plan: TwoHourPlan, raw_text: str, states: dict) -> dict:
    metrics, rows = analyze_two_hour_results(plan, raw_text, states)
    run_rows = matrix_metric_dicts(metrics)
    run_fields = list(metrics[0].__dataclass_fields__) if metrics else ['config_id', 'valid', 'invalid_reason']
    write_matrix_csv(directory / 'runs.csv', run_rows, run_fields)
    fields = sorted({key for row in rows for key in row})
    write_matrix_csv(directory / 'curve.csv', rows, fields)
    readings, _ = parse_matrix_log(raw_text)
    sample_rows = [{
        'config_id': r.config_id, 'repetition': r.repetition, 'phase': r.phase,
        **asdict(r.sample), 'cpu_temp_mc': r.cpu_temp_mc, 'battery_temp_mc': r.battery_temp_mc,
        'policy0_frequency_khz': r.policy0_frequency_khz, 'policy6_frequency_khz': r.policy6_frequency_khz,
        'online_cpus': ','.join(str(cpu) for cpu in r.online_cpus),
    } for r in readings]
    write_matrix_csv(directory / 'samples.csv', sample_rows,
                     list(sample_rows[0]) if sample_rows else ['config_id', 'phase'])
    render_single_core_frequency_curves(rows, directory / 'single-core.svg')
    render_mixed_frequency_heatmap(rows, directory / 'mixed-heatmap.svg', panels=((6, 2),))
    render_frequency_slices(rows, directory / 'mixed-slices.svg')
    render_frequency_slices(rows, directory / 'pure-clusters.svg', mixed=False)
    render_single_core_frequency_curves(rows, directory / 'single-core-incremental.svg', incremental=True)
    render_frequency_slices(rows, directory / 'mixed-slices-incremental.svg', incremental=True)
    incremental = efficiency_plot_rows(rows, incremental=True)
    valid_ids = {r['config_id'] for r in rows if r['valid']}
    return {'planned_configs': len(rows), 'valid_configs': len(valid_ids),
            'invalid_or_unmeasured_configs': len(rows) - len(valid_ids),
            'coverage_complete': set(plan.coverage_ids) <= valid_ids,
            'all_planned_configs_valid': len(valid_ids) == len(rows),
            'valid_incremental_configs': sum(r['valid'] for r in incremental),
            'measurement_scope': 'whole-device battery-side; not CPU-rail power',
            'best_scope': 'measured valid points only, not a global optimum'}


def wait_for_cool_start(client, lease, cpu_paths, battery_path, deadline,
                        clock=time.monotonic, sleep=time.sleep):
    """Bounded initial settling; no artificial limit/thermal override or app kills."""
    end = min(deadline, clock() + TWO_HOUR_COOLING_BUDGET_S)
    script = 'for p in %s; do cat "$p" || exit 1; done' % ' '.join(
        shlex.quote(p) for p in list(cpu_paths) + [battery_path])
    while clock() < end:
        lease.check()
        try:
            values = [int(v) for v in client.run_root_script(script).splitlines()]
        except ValueError as exc:
            raise AdbError('invalid thermal readings while settling') from exc
        if len(values) != len(cpu_paths) + 1 or any(v < 1000 for v in values):
            raise AdbError('missing/invalid settling thermal sensor')
        cpu, battery = max(values[:-1]), values[-1]
        if cpu >= CPU_TEMP_LIMIT_MC or battery >= BATTERY_TEMP_LIMIT_MC:
            raise AdbError('temperature safety guard reached while settling')
        if cpu <= LOW_NOISE_START_CPU_MC and battery <= LOW_NOISE_START_BATTERY_MC:
            return
        sleep(min(LOW_NOISE_COOL_POLL_S, max(0, end - clock())))
    raise AdbError('initial cooling budget exhausted; no performance sweep started')


def run_two_hour(client: AdbClient, binary: Path, directory: Path,
                 confirmed_recovery: bool = False, confirmed_writes: bool = False,
                 confirmed_screen_off: bool = False, minimum_battery_percent: int = 60,
                 options: TwoHourSettings = TwoHourSettings()) -> dict:
    """Live adapter remains blocked by the original recovery gate, before any I/O."""
    if not confirmed_writes or not confirmed_screen_off:
        raise ValueError('two-hour run requires device writes/hotplug and screen-off confirmation')
    require_verified_hotplug_recovery(confirmed_recovery)
    if not 60 <= minimum_battery_percent <= 100:
        raise ValueError('two-hour battery reserve must be 60-100 percent')
    if not binary.is_file():
        raise ValueError('benchmark binary not found: ' + str(binary))
    started = time.monotonic()
    directory.mkdir(parents=True, exist_ok=False)
    lease = ScreenOffLease(client)
    plan = None
    schedule = {'states': {}, 'stop_reason': 'PREPARATION_FAILED'}
    summary = {'status': 'FAILED', 'error': None, 'screen_restored': False, 'snapshot_restored': False}
    report = topology = None
    try:
        report, topology, cpu_paths, battery_path, _ = _validate_device_ready(client, minimum_battery_percent)
        _write_json_exclusive(directory / 'initial-preflight.json', asdict(report))
        initial_processes = client.run_root_script("ps -A -o PID,NAME | grep '[c]pu_mix_bench' || true").strip()
        if initial_processes:
            raise AdbError('existing benchmark process; refusing to start')
        remaining_s = int(options.total_s - (time.monotonic() - started))
        if remaining_s <= options.finish_reserve_s:
            raise AdbError('preparation exhausted measurement budget')
        guard = lease.prepare(remaining_s, confirmed=confirmed_recovery)
        _write_json_exclusive(directory / 'screen-lease.json', {
            'before': asdict(lease.before), 'guard': asdict(guard), 'duration_s': remaining_s,
        })
        wait_for_cool_start(client, lease, cpu_paths, battery_path,
                            started + options.total_s - options.finish_reserve_s)
        # Screen-off policies may differ: freeze the plan from this new snapshot.
        report, topology, cpu_paths, battery_path, _ = _validate_device_ready(client, minimum_battery_percent)
        plan = build_two_hour_plan(topology, report, options)
        _write_json_exclusive(directory / 'plan.json', plan.document())
        if not plan.document()['fits_estimate']:
            raise ValueError('current frequency table exceeds two-hour estimate; plan saved, no sweep started')
        _write_json_exclusive(directory / 'measurement-preflight.json', asdict(report))
        bench = deploy_benchmark(client, binary)

        def execute(c):
            lease.check()
            result = execute_matrix_config(client, c, topology, report, cpu_paths, battery_path, bench,
                                           confirm_independent_recovery=confirmed_recovery,
                                           minimum_battery_percent=minimum_battery_percent,
                                           environment_guard=guard)
            try:
                lease.check()
            except (AdbError, OSError) as exc:
                from .matrix_controller import MatrixConfigError
                raise MatrixConfigError(str(exc), result) from exc
            return result

        with (directory / 'raw.log').open('x', encoding='utf-8', newline='\n') as raw, \
                (directory / 'events.jsonl').open('x', encoding='utf-8', newline='\n') as events:
            # No smoke loop can run past admission cutoff either.
            for smoke in build_stage_a(topology, report):
                if time.monotonic() + config_admission_s(smoke) > started + options.total_s - options.finish_reserve_s:
                    raise AdbError('no budget left for screen-off/recovery smoke')
                try:
                    raw.write(execute(smoke) + '\n')
                    raw.flush()
                except (AdbError, OSError) as exc:
                    raw.write(getattr(exc, 'remote_output', '') + '\n'); raw.flush()
                    raise
            def progress(value):
                print('TWO_HOUR|%s|%s|%s' % (value['event'], value.get('config_id', ''), value.get('status', '')), flush=True)
            schedule = schedule_configs(plan.configs, execute, raw, events, started, options, progress=progress)
        summary['status'] = 'MEASURED' if schedule['stop_reason'] is None else schedule['stop_reason']
    except (AdbError, OSError, ValueError, KeyboardInterrupt) as exc:
        summary['error'] = repr(exc)
    finally:
        if report is not None and topology is not None:
            try:
                verify_device_snapshot(client, topology, report)
                summary['snapshot_restored'] = True
            except (AdbError, OSError) as exc:
                summary['snapshot_error'] = repr(exc)
        try:
            if lease.before is not None:
                summary['screen_after'] = asdict(lease.close())
                summary['screen_restored'] = True
        except (AdbError, OSError) as exc:
            summary['screen_error'] = repr(exc)
        summary.update(schedule)
        if lease.before is not None:
            summary['screen_before'] = asdict(lease.before)
        if lease.guard is not None:
            summary['noise_guard'] = {**asdict(lease.guard), 'directory': lease.guard.directory}
        if plan is not None:
            text = (directory / 'raw.log').read_text(encoding='utf-8') if (directory / 'raw.log').exists() else ''
            try:
                summary.update(write_two_hour_artifacts(directory, plan, text, schedule['states']))
            except (OSError, ValueError) as exc:
                summary['artifact_error'] = repr(exc)
        summary['elapsed_s'] = time.monotonic() - started
        summary['budget_s'] = options.total_s
        summary['deadline_overrun'] = summary['elapsed_s'] > options.total_s
        if not summary['screen_restored'] or not summary['snapshot_restored']:
            summary['status'] = 'FAILED_RECOVERY_VERIFICATION'
        summary['recovery_gate_enabled_by_this_run'] = False
        _write_json_exclusive(directory / 'summary.json', summary)
    return summary
