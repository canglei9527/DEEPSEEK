"""Entirely offline tests: no adb subprocess or phone is used."""
import io
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
import xml.etree.ElementTree as ET
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import MagicMock, patch

from cpu_efficiency_lab.adb import AdbError
from cpu_efficiency_lab.cli import main
from cpu_efficiency_lab.low_noise_environment import NoiseGuard, ScreenOffLease, build_lease_guardian
from cpu_efficiency_lab.matrix_controller import MatrixConfigError, build_matrix_config_script
from cpu_efficiency_lab.screen_control import ScreenState
from cpu_efficiency_lab.two_hour_plan import (
    TwoHourSettings, build_two_hour_plan, load_offline_preflight, load_two_hour_plan,
)
from cpu_efficiency_lab.two_hour_runner import (
    analyze_two_hour_results, config_admission_s, run_two_hour, schedule_configs, wait_for_cool_start, write_two_hour_artifacts,
)
from tests.test_matrix_controller import fixtures
from tests.test_matrix_analysis import sample


def full_fixture():
    topology, report = fixtures()
    e = '384000 556800 748800 960000 1152000 1363200 1555200 1785600 1996800 2227200 2400000 2745600'
    p = '1017600 1209600 1401600 1689600 1958400 2246400 2438400 2649600 2841600 3072000'
    report = replace(report, policies=(replace(report.policies[0], available_frequencies_khz=e, scaling_max_khz=2745600),
                                      replace(report.policies[1], available_frequencies_khz=p, scaling_max_khz=3072000)))
    return topology, report


def valid_raw(c):
    name, cpu = c.config_id, c.target_cpus[0]
    mask = ','.join(map(str, c.desired_online_cpus))
    freq0, freq6 = str(c.target_for('policy0') or 'NA'), str(c.target_for('policy6') or 'NA')
    lines = [sample(name, 0, 'baseline_pre', t, 100000, freq0, freq6, mask) for t in range(4)]
    for rep in (1, 2):
        start = 10 + (rep - 1) * 20
        lines += ['RUN|%s|%d|%d|%s' % (name, rep, cpu, c.worker_csv),
                  'B|MEASURE_BEGIN|%s|%.6f|%s' % (c.worker_csv, start, c.worker_csv)]
        lines += [sample(name, rep, 'active', t, 300000 + rep * 10000, freq0, freq6, mask)
                  for t in range(start, start + 13)]
        for worker in c.target_cpus:
            lines += ['B|WORKER|%d|%d|100|12000|12.0|1000|1234' % (worker, worker)]
        lines += ['B|MEASURE_END|%s|%.6f|12.0|%d|%d|1234' %
                  (c.worker_csv, start + 12, 12000 * len(c.target_cpus), 1000 * len(c.target_cpus))]
    lines += [sample(name, 0, 'baseline_post', t, 100000, freq0, freq6, mask) for t in range(45, 49)]
    lines += ['CONFIG_DONE|' + name]
    return '\n'.join(lines)


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.topology, self.report = full_fixture()
        self.plan = build_two_hour_plan(self.topology, self.report)

    def test_counts_budget_all_frequencies_and_full_selected_grid(self):
        d = self.plan.document()
        self.assertEqual(d['counts'], {'B': 92, 'C': 10, 'D': 25})
        self.assertEqual(d['config_count'], 127)
        self.assertEqual(d['scoring_windows'], 254)
        self.assertEqual(d['nominal_measurement_s'], 5080)
        self.assertEqual(d['estimated_with_allowances_s'], 7180)
        self.assertTrue(d['fits_estimate'])
        self.assertEqual(d['launch_cutoff_s'], 6600)
        self.assertEqual(len({c.config_id for c in self.plan.configs}), 127)
        for cpu in range(8):
            cs = [c for c in self.plan.configs if c.stage == 'B' and c.target_cpus == (cpu,)]
            self.assertEqual(len(cs), 12 if cpu < 6 else 10)
            self.assertTrue(all(0 in c.desired_online_cpus for c in cs))
        mixed = [c for c in self.plan.configs if c.stage == 'D']
        self.assertEqual({(c.target_for('policy0'), c.target_for('policy6')) for c in mixed},
                         {(e, p) for e in d['frequencies_khz']['policy0']['grid'] for p in d['frequencies_khz']['policy6']['grid']})

    def test_coverage_round_covers_every_core_and_three_by_three_before_refinement(self):
        cover = self.plan.configs[:len(self.plan.coverage_ids)]
        self.assertEqual({c.config_id for c in cover}, set(self.plan.coverage_ids))
        for cpu in range(8):
            self.assertEqual(sum(c.stage == 'B' and c.target_cpus == (cpu,) for c in cover), 3)
        self.assertEqual(sum(c.stage == 'D' for c in cover), 9)
        self.assertEqual(sum(c.stage == 'C' for c in cover), 6)
        # First 8 single-worker configs cover all eight CPUs, not CPU0 only.
        self.assertEqual({c.target_cpus for c in cover if c.stage == 'B'} , {(i,) for i in range(8)})
        self.assertEqual(build_two_hour_plan(self.topology, self.report), self.plan)

    def test_single_frequency_caps_never_expanded(self):
        report = replace(self.report, policies=tuple(replace(p, scaling_max_khz=p.scaling_min_khz) for p in self.report.policies))
        d = build_two_hour_plan(self.topology, report).document()
        self.assertEqual(d['counts'], {'B': 8, 'C': 2, 'D': 1})
        self.assertEqual(len(d['coverage_ids']), 11)

    def test_bad_options_and_unreviewed_topology_are_refused(self):
        for kwargs in ({'total_s': 7201}, {'repetitions': 1}, {'measure_s': 0}, {'grid_levels': 6}, {'warmup_s': True}):
            with self.assertRaises(ValueError):
                TwoHourSettings(**kwargs)
        cores = list(self.topology.cores)
        cores[1] = replace(cores[1], online=False)
        with self.assertRaises(ValueError):
            build_two_hour_plan(replace(self.topology, cores=tuple(cores)), self.report)

    def test_json_roundtrip_and_offline_topology_assumption(self):
        document = json.loads(json.dumps(self.plan.document()))
        self.assertEqual(load_two_hour_plan(document), self.plan)
        topo, report = load_offline_preflight(json.loads(json.dumps(asdict(self.report))))
        self.assertEqual(build_two_hour_plan(topo, report).document(), self.plan.document())
        document['configs'].append(document['configs'][0])
        with self.assertRaises(ValueError):
            load_two_hour_plan(document)


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        topo, report = full_fixture()
        self.plan = build_two_hour_plan(topo, report)
        self.configs = self.plan.configs[:3]
        self.now = 0
        self.raw, self.events = io.StringIO(), io.StringIO()

    def run_schedule(self, execute):
        return schedule_configs(self.configs, execute, self.raw, self.events, 0,
                                TwoHourSettings(), clock=lambda: self.now)

    def test_fake_backend_runs_without_real_wait_and_checkpoints_each_point(self):
        def execute(c):
            self.now += c.planned_seconds
            return valid_raw(c)
        result = self.run_schedule(execute)
        self.assertIsNone(result['stop_reason'])
        self.assertTrue(all(s['status'] == 'VALID' for s in result['states'].values()))
        self.assertEqual(self.now, 120)
        self.assertEqual(self.raw.getvalue().count('CONFIG_DONE|'), 3)
        self.assertIn('CONFIG_RESULT', self.events.getvalue())

    def test_deadline_admission_uses_timeout_not_just_nominal_work(self):
        self.now = 6600 - self.configs[0].planned_seconds - 1
        execute = MagicMock()
        result = self.run_schedule(execute)
        execute.assert_not_called()
        self.assertEqual(result['stop_reason'], 'DEADLINE')
        self.assertGreater(config_admission_s(self.configs[0]), 40)
        self.assertTrue(all(s['reason'] == 'DEADLINE' for s in result['states'].values()))

    def test_deadline_after_one_point_preserves_valid_completed_point(self):
        def execute(c):
            self.now = 6600
            return valid_raw(c)
        result = self.run_schedule(execute)
        self.assertEqual(result['states'][self.configs[0].config_id]['status'], 'VALID')
        self.assertEqual(result['states'][self.configs[1].config_id]['status'], 'NOT_RUN')
        self.assertEqual(result['stop_reason'], 'DEADLINE')

    def test_invalid_analysis_can_continue_but_controller_exception_stops(self):
        calls = []
        def execute(c):
            calls.append(c.config_id)
            if len(calls) == 1:
                return 'CONFIG_DONE|' + c.config_id  # no native result
            raise MatrixConfigError('restoration not verified', 'REMOTE_EVIDENCE')
        result = self.run_schedule(execute)
        self.assertEqual(len(calls), 2)
        self.assertEqual(result['states'][self.configs[0].config_id]['status'], 'INVALID')
        self.assertEqual(result['states'][self.configs[1].config_id]['status'], 'FAILED')
        self.assertIn('REMOTE_EVIDENCE', self.raw.getvalue())
        self.assertEqual(result['stop_reason'], 'EXECUTION_FAILED')

    def test_interrupt_is_saved_with_pending_states(self):
        def execute(c):
            raise KeyboardInterrupt()
        result = self.run_schedule(execute)
        self.assertEqual(result['stop_reason'], 'INTERRUPTED')
        self.assertIn('SCHEDULE_END', self.events.getvalue())

    def test_missing_and_failed_rows_are_never_plotted_as_valid(self):
        c = self.configs[0]
        metrics, rows = analyze_two_hour_results(self.plan, valid_raw(c), {c.config_id: {'status': 'FAILED', 'reason': 'screen woke'}})
        self.assertEqual(len(metrics), 2)
        self.assertTrue(all(not r['valid'] for r in rows))
        self.assertEqual(len(rows), 127)
        self.assertEqual(rows[0]['status'], 'FAILED')

    def test_offline_artifacts_have_no_missing_point_interpolation_and_ranges(self):
        c = self.configs[0]
        states = {c.config_id: {'status': 'VALID', 'reason': ''}}
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d)
            result = write_two_hour_artifacts(directory, self.plan, valid_raw(c), states)
            self.assertEqual(result['valid_configs'], 1)
            self.assertFalse(result['coverage_complete'])
            self.assertFalse(result['all_planned_configs_valid'])
            for svg in directory.glob('*.svg'):
                ET.parse(svg)
            self.assertIn('repeat-range', (directory / 'single-core.svg').read_text(encoding='utf-8'))
            self.assertIn('n/a', (directory / 'mixed-heatmap.svg').read_text(encoding='utf-8'))
            with self.assertRaises(FileExistsError):
                write_two_hour_artifacts(directory, self.plan, '', {})


    def test_frequency_blip_invalidates_screening_even_if_legacy_threshold_passes(self):
        c = self.configs[0]
        raw = valid_raw(c)
        first_active = next(line for line in raw.splitlines() if '|1|active|' in line)
        changed = first_active.replace('|%s|NA|' % c.target_for('policy0'), '|999999|NA|')
        raw = raw.replace(first_active, changed)
        states = {c.config_id: {'status': 'VALID', 'reason': ''}}
        _, rows = analyze_two_hour_results(self.plan, raw, states)
        self.assertFalse(next(r for r in rows if r['config_id'] == c.config_id)['valid'])

    def test_positive_increment_below_observed_idle_range_is_not_reported(self):
        c = self.configs[0]
        raw = valid_raw(c).replace('|100000|', '|600000|', 1)
        metrics, rows = analyze_two_hour_results(self.plan, raw, {c.config_id: {'status': 'VALID', 'reason': ''}})
        self.assertTrue(all(m.valid for m in metrics))
        self.assertTrue(all(m.incremental_power_w > 0 for m in metrics))
        self.assertTrue(all(not m.incremental_valid for m in metrics))
        self.assertTrue(all(m.operations_per_incremental_joule is None for m in metrics))


class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.guard = NoiseGuard('cel_' + 'a' * 32, ('/sys/class/backlight/panel0/actual_brightness',))
        self.screen = ScreenState(1800000, 'Awake', True)

    def test_unsafe_paths_tokens_and_durations_refused(self):
        with self.assertRaises(ValueError):
            NoiseGuard('x;id', self.guard.brightness_paths)
        with self.assertRaises(ValueError):
            NoiseGuard(self.guard.token, ('/tmp/x;id',))
        with self.assertRaises(ValueError):
            build_lease_guardian(self.guard, self.screen, 7201)

    def test_guardian_has_finite_lock_heartbeat_cleanup_and_no_timeout_setting_write(self):
        script = build_lease_guardian(self.guard, self.screen, 7200)
        self.assertIn('7200000000000', script)
        self.assertIn('heartbeat', script)
        self.assertIn('wake_unlock', script)
        self.assertIn('guardian.pid', script)
        self.assertIn('locks=$(cat /sys/power/wake_lock)', script)
        self.assertIn('input keyevent 224', script)
        self.assertNotIn('settings put', script)
        self.assertNotIn('keyevent 26', script)
        asleep = build_lease_guardian(self.guard, ScreenState(1800000, 'Asleep', False), 300)
        self.assertNotIn('keyevent 224', asleep)

    def test_environment_blocks_before_any_device_call(self):
        client = MagicMock()
        with self.assertRaisesRegex(AdbError, 'dynamic frequency caps'):
            ScreenOffLease(client).prepare(600, confirmed=True)
        client.assert_not_called()
        self.assertEqual(client.mock_calls, [])

    @patch('cpu_efficiency_lab.low_noise_environment.require_verified_hotplug_recovery')
    @patch('cpu_efficiency_lab.low_noise_environment.read_screen_state')
    def test_unsupported_kernel_does_not_launch_any_guardian(self, read, gate):
        read.return_value = self.screen
        client = MagicMock()
        client.run_root_script.side_effect = AdbError('NO_WAKE_LOCK_BACKEND')
        with self.assertRaises(AdbError):
            ScreenOffLease(client).prepare(600, confirmed=True)
        client.run_root_script_stdin.assert_not_called()

    @unittest.skipUnless(shutil.which('sh'), 'local POSIX shell unavailable')
    def test_generated_guardian_and_guarded_controller_shell_syntax(self):
        topology, report = fixtures()
        from cpu_efficiency_lab.matrix_plan import MatrixConfig
        c = MatrixConfig('TEST', 'B', (0,), (('policy0', 384000),), 3, 12, 4, 2, 2)
        script = build_matrix_config_script(c, topology, report, ['/sys/class/thermal/thermal_zone1/temp'],
                                           '/sys/class/thermal/thermal_zone2/temp',
                                           '/data/local/tmp/cpu-efficiency-lab/cpu_mix_bench',
                                           environment_guard=self.guard)
        for text in (script, build_lease_guardian(self.guard, self.screen, 600)):
            result = subprocess.run(['sh', '-n'], input=text, text=True, encoding='utf-8', capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn('__ENVIRONMENT_GUARD__', text)
        self.assertIn('DISPLAY_NOT_DARK', script)
        self.assertIn('WAKE_GUARDIAN_LOST', script)
        self.assertIn('WAKE_LOCK_LOST', script)

    @unittest.skipUnless(shutil.which('sh'), 'local POSIX shell unavailable')
    def test_guard_sysfs_simulation_rejects_display_on_lock_loss_and_closed_lease(self):
        if os.name == 'nt' and not shutil.which('cygpath'):
            self.skipTest('temporary shell paths require cygpath on Windows')
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            shell_root = (subprocess.check_output(['cygpath', '-u', d], text=True, encoding='utf-8').strip()
                          if os.name == 'nt' else d)
            (root / 'ready').touch()
            brightness = root / 'brightness'
            brightness.write_bytes(b'0\n')
            text = self.guard.sample_shell().replace(self.guard.directory, shell_root).replace(
                self.guard.brightness_paths[0], shell_root + '/brightness').replace(
                '/sys/power/wake_lock', shell_root + '/wake_lock')
            quote = chr(34)
            setup = ("pid=$$; started=$(awk '{print $22}' " + quote + "/proc/$pid/stat" + quote + "); "
                     "echo " + quote + "$pid $started" + quote + " > '" + shell_root + "/guardian.pid'; "
                     "now=$(cut -d. -f1 /proc/uptime); echo " + quote + "$now" + quote + " > '" + shell_root + "/heartbeat'; ")
            nl = chr(10)
            script = setup + 'check() {' + nl + text + nl + '}' + nl
            script += "echo '" + self.guard.token + "' > '" + shell_root + "/wake_lock'" + nl
            script += 'check || exit 4' + nl
            script += "echo 100 > '" + shell_root + "/brightness'; check || true" + nl
            script += "echo 0 > '" + shell_root + "/brightness'; "
            script += ": > '" + shell_root + "/wake_lock'; check || true" + nl
            script += "echo '" + self.guard.token + "' > '" + shell_root + "/wake_lock'; "
            script += "echo '999999999 1' > '" + shell_root + "/guardian.pid'; check || true" + nl
            script += ": > '" + shell_root + "/cleaned'; check || true" + nl
            result = subprocess.run(['sh'], input=script, text=True, encoding='utf-8', capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('DISPLAY_NOT_DARK', result.stdout)
            self.assertIn('WAKE_LOCK_LOST', result.stdout)
            self.assertIn('WAKE_GUARDIAN_LOST', result.stdout)
            self.assertIn('NOISE_LEASE_LOST', result.stdout)


    @unittest.skipUnless(shutil.which('sh'), 'local POSIX shell unavailable')
    def test_guardian_fake_sysfs_stop_and_stale_heartbeat_release_owned_lock(self):
        if os.name == 'nt' and not shutil.which('cygpath'):
            self.skipTest('temporary shell paths require cygpath on Windows')
        for mode in ('stop', 'stale'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as d:
                root = Path(d)
                shell_root = (subprocess.check_output(['cygpath', '-u', d], text=True, encoding='utf-8').strip()
                              if os.name == 'nt' else d)
                (root / 'uptime').write_bytes(b'10.00 0\n')
                (root / 'brightness').write_bytes(b'0\n')
                (root / 'wake_lock').touch(); (root / 'wake_unlock').touch()
                script = build_lease_guardian(self.guard, self.screen, 7200)
                script = script.replace(self.guard.directory, shell_root).replace('/proc/uptime', shell_root + '/uptime').replace(
                    self.guard.brightness_paths[0], shell_root + '/brightness').replace('/sys/power/', shell_root + '/').replace('sleep 2', 'sleep 0.01')
                script = ('input() { echo "$*" >> "' + shell_root + '/keys"; }\n'
                          'dumpsys() { echo mWakefulness=Asleep; }\n' + script)
                script_path = root / 'guardian.sh'
                script_path.write_bytes(script.encode('utf-8'))
                process = subprocess.Popen(['sh', shell_root + '/guardian.sh'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                try:
                    deadline = time.monotonic() + 5
                    while not (root / 'ready').exists() and process.poll() is None and time.monotonic() < deadline:
                        time.sleep(0.01)
                    self.assertTrue((root / 'ready').exists())
                    if mode == 'stop':
                        (root / 'stop').touch()
                    else:
                        (root / 'uptime.next').write_bytes(b'201.00 0\n')
                        os.replace(root / 'uptime.next', root / 'uptime')
                    out, err = process.communicate(timeout=5)
                    self.assertEqual((root / 'cleaned').read_text().strip(), '0', err.decode('utf-8'))
                    self.assertEqual((root / 'wake_unlock').read_text().strip(), self.guard.token)
                    self.assertIn('keyevent 223', (root / 'keys').read_text())
                    self.assertIn('keyevent 224', (root / 'keys').read_text())
                finally:
                    if process.poll() is None:
                        process.kill(); process.communicate(timeout=5)


class CoolingTests(unittest.TestCase):
    def test_cooling_uses_finite_budget_and_heartbeats(self):
        client, lease = MagicMock(), MagicMock()
        client.run_root_script.return_value = '50000\n33000\n'
        now = [0]
        def sleep(seconds):
            now[0] += seconds
        with self.assertRaisesRegex(AdbError, 'cooling budget exhausted'):
            wait_for_cool_start(client, lease, ['cpu'], 'battery', 25,
                                clock=lambda: now[0], sleep=sleep)
        self.assertEqual(now[0], 25)
        self.assertEqual(lease.check.call_count, 3)

    def test_cool_or_overheated_sensor_does_not_wait_unnecessarily(self):
        client, lease, sleep = MagicMock(), MagicMock(), MagicMock()
        client.run_root_script.return_value = '40000\n30000\n'
        wait_for_cool_start(client, lease, ['cpu'], 'battery', 25, clock=lambda: 0, sleep=sleep)
        sleep.assert_not_called()
        client.run_root_script.return_value = '80000\n30000\n'
        with self.assertRaisesRegex(AdbError, 'temperature safety guard'):
            wait_for_cool_start(client, lease, ['cpu'], 'battery', 25, clock=lambda: 0, sleep=sleep)
        sleep.assert_not_called()


class LiveAdapterMockTests(unittest.TestCase):
    def run_mocked(self, directory, fail_cleanup=False, fail_prepare=False):
        topology, report = full_fixture()
        guard = NoiseGuard('cel_' + 'b' * 32, ('/sys/class/backlight/panel0/actual_brightness',))
        lease = MagicMock()
        lease.before = ScreenState(1800000, 'Awake', True)
        lease.guard = guard
        lease.prepare.return_value = guard
        lease.close.return_value = lease.before
        if fail_cleanup:
            lease.close.side_effect = AdbError('cleanup failed')
        if fail_prepare:
            lease.prepare.side_effect = AdbError('no supported wake-lock backend')
        client = MagicMock()
        client.run_root_script.return_value = ''
        binary = directory / 'bench'
        binary.write_bytes(b'fake executable used only by mocks')
        with patch('cpu_efficiency_lab.two_hour_runner.require_verified_hotplug_recovery'), \
             patch('cpu_efficiency_lab.two_hour_runner._validate_device_ready', return_value=(report, topology, ['cpu'], 'battery', {})), \
             patch('cpu_efficiency_lab.two_hour_runner.ScreenOffLease', return_value=lease), \
             patch('cpu_efficiency_lab.two_hour_runner.wait_for_cool_start'), \
             patch('cpu_efficiency_lab.two_hour_runner.deploy_benchmark', return_value='remote'), \
             patch('cpu_efficiency_lab.two_hour_runner.verify_device_snapshot'), \
             patch('sys.stdout', new_callable=io.StringIO), \
             patch('cpu_efficiency_lab.two_hour_runner.execute_matrix_config', side_effect=lambda *a, **kw: valid_raw(a[1])) as execute:
            result = run_two_hour(client, binary, directory / 'run', True, True, True)
        return result, lease, execute

    def test_complete_run_with_only_mocks_writes_artifacts_and_restores(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            result, lease, execute = self.run_mocked(root)
            self.assertEqual(result['status'], 'MEASURED', result)
            self.assertTrue(result['all_planned_configs_valid'], result)
            self.assertEqual(result['valid_configs'], 127)
            self.assertEqual(execute.call_count, 131)  # 4 smokes + 127 configurations
            self.assertTrue(result['snapshot_restored'])
            self.assertTrue(result['screen_restored'])
            lease.close.assert_called_once()
            self.assertTrue((root / 'run/summary.json').exists())

    def test_cleanup_failure_is_not_marked_restored(self):
        with tempfile.TemporaryDirectory() as d:
            result, lease, _ = self.run_mocked(Path(d), fail_cleanup=True)
            self.assertFalse(result['screen_restored'])
            self.assertIn('cleanup failed', result['screen_error'])
            lease.close.assert_called_once()

    def test_prepare_failure_starts_no_load_and_still_attempts_cleanup(self):
        with tempfile.TemporaryDirectory() as d:
            result, lease, execute = self.run_mocked(Path(d), fail_prepare=True)
            execute.assert_not_called()
            lease.close.assert_called_once()
            self.assertIn('wake-lock', result['error'])
            self.assertEqual(result['status'], 'FAILED')


class CommandTests(unittest.TestCase):
    @patch('cpu_efficiency_lab.cli.AdbClient')
    def test_run_is_fail_closed_even_with_all_confirmation_flags(self, adb):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / 'must-not-exist'
            rc = main(['two-hour-sweep', '--serial', 'phone', '--output', str(out),
                       '--confirm-device-writes', '--confirm-core-hotplug', '--confirm-screen-off',
                       '--confirm-independent-recovery'])
            self.assertEqual(rc, 2)
            self.assertFalse(out.exists())
        adb.assert_not_called()

    def test_direct_runner_cannot_bypass_gate(self):
        client = MagicMock()
        with self.assertRaisesRegex(AdbError, 'dynamic frequency caps'):
            run_two_hour(client, Path('missing'), Path('unused'), True, True, True)
        self.assertEqual(client.mock_calls, [])

    @patch('cpu_efficiency_lab.cli.AdbClient')
    def test_analyze_command_is_offline_and_accepts_torn_final_event(self, adb):
        topo, report = full_fixture()
        plan = build_two_hour_plan(topo, report)
        c = plan.configs[0]
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source, output = root / 'checkpoints', root / 'analysis'
            source.mkdir()
            (source / 'plan.json').write_text(json.dumps(plan.document()), encoding='utf-8')
            (source / 'raw.log').write_text(valid_raw(c), encoding='utf-8')
            event = {'event': 'CONFIG_RESULT', 'config_id': c.config_id, 'status': 'VALID', 'reason': ''}
            (source / 'events.jsonl').write_text(json.dumps(event) + '\n{"event":', encoding='utf-8')
            with patch('sys.stdout', new_callable=io.StringIO):
                self.assertEqual(main(['two-hour-analyze', '--input', str(source), '--output', str(output)]), 0)
            result = json.loads((output / 'analysis-summary.json').read_text(encoding='utf-8'))
            self.assertEqual(result['valid_configs'], 1)
            self.assertIn('incremental battery joule', (output / 'single-core-incremental.svg').read_text(encoding='utf-8'))
        adb.assert_not_called()

    @patch('cpu_efficiency_lab.cli.AdbClient')
    def test_plan_command_works_offline_and_refuses_overwrite(self, adb):
        _, report = full_fixture()
        with tempfile.TemporaryDirectory() as d:
            source, output = Path(d) / 'preflight.json', Path(d) / 'plan.json'
            from cpu_efficiency_lab.preflight import report_dict
            source.write_text(json.dumps(report_dict(report)), encoding='utf-8')
            args = ['two-hour-plan', '--preflight', str(source), '--output', str(output)]
            with patch('sys.stdout', new_callable=io.StringIO):
                self.assertEqual(main(args), 0)
                self.assertEqual(main(args), 2)
            self.assertEqual(json.loads(output.read_text(encoding='utf-8'))['config_count'], 127)
        adb.assert_not_called()


if __name__ == '__main__':
    unittest.main()
