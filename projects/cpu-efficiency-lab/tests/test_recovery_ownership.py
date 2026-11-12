"""Offline regressions: numeric equality must not manufacture sysfs ownership."""
import shutil
import subprocess
import unittest
from dataclasses import replace
from unittest.mock import MagicMock, patch

from cpu_efficiency_lab.matrix_controller import (
    MatrixConfigError, _safe_policy_restore_shell, execute_matrix_config, verify_device_snapshot,
)
from cpu_efficiency_lab.matrix_plan import MatrixConfig
from tests.test_matrix_controller import fixtures


@unittest.skipUnless(shutil.which('sh'), 'local POSIX shell unavailable')
class RecoveryOwnershipTests(unittest.TestCase):
    def run_restore(self, low, high, target='1200', journal='test 400 2000 1200 LOCKED', tainted=False):
        wrapper = r'''
base=$(mktemp -d) || exit 90
trap 'rm -rf "$base"' EXIT
printf '%s\n' "$1" > "$base/scaling_min_freq"
printf '%s\n' "$2" > "$base/scaling_max_freq"
[ "$4" = NONE ] || printf '%s\n' "$4" > "$base/journal"
[ "$5" = no ] || : > "$base/journal.tainted"
safe_restore_policy test "$base" 400 2000 "$3" "$base/journal"
status=$?
read -r low < "$base/scaling_min_freq"
read -r high < "$base/scaling_max_freq"
printf 'RESULT|%s|%s|%s\n' "$status" "$low" "$high"
[ ! -f "$base/journal" ] || { printf 'JOURNAL|'; cat "$base/journal"; }
'''
        result = subprocess.run(
            ['sh', '-s', '--', str(low), str(high), target, journal, 'yes' if tainted else 'no'],
            input=_safe_policy_restore_shell() + wrapper,
            capture_output=True, text=True, encoding='utf-8', timeout=10, check=True,
        )
        return result.stdout

    def test_taint_is_sticky_even_when_values_equal_original_snapshot(self):
        output = self.run_restore(400, 2000, tainted=True)
        self.assertIn('RESULT|2|400|2000', output)
        self.assertIn('POLICY_RESTORE_RESTRICTED|test|400|2000', output)
        self.assertNotIn('ALREADY_ORIGINAL', output)
        self.assertNotIn('JOURNALED', output)

    def test_bad_journal_cannot_hide_behind_original_values(self):
        output = self.run_restore(400, 2000, journal='test 400 2000 1100 LOCKED')
        self.assertIn('RESULT|2|400|2000', output)
        self.assertIn('POLICY_JOURNAL_INVALID|test', output)
        self.assertNotIn('ALREADY_ORIGINAL', output)

    def test_untouched_policy_is_not_rewritten_even_if_system_widened_it(self):
        output = self.run_restore(200, 3000, target='NA', journal='NONE')
        self.assertIn('RESULT|2|200|3000', output)
        self.assertIn('POLICY_RESTORE_UNOWNED|test|200|3000', output)

    def test_tainted_policy_is_left_untouched_not_clamped_by_another_writer(self):
        output = self.run_restore(200, 3000, tainted=True)
        self.assertIn('RESULT|2|200|3000', output)
        self.assertIn('POLICY_RESTORE_RESTRICTED|test|200|3000', output)

    def test_missing_journal_cannot_claim_a_coincidentally_matching_lock(self):
        output = self.run_restore(1200, 1200, journal='NONE')
        self.assertIn('RESULT|2|1200|1200', output)
        self.assertNotIn('JOURNALED', output)

    def test_journalled_owned_lock_still_restores(self):
        output = self.run_restore(1200, 1200)
        self.assertIn('RESULT|0|400|2000', output)
        self.assertIn('JOURNALED', output)


class RecoveryEvidenceTests(unittest.TestCase):
    def test_snapshot_rejects_changed_governor_or_policy_mapping_without_writes(self):
        topology, report = fixtures()
        client = MagicMock()
        for policy in (replace(report.policies[0], governor='performance'),
                       replace(report.policies[0], related_cpus='0 1 2 3 4')):
            with self.subTest(policy=policy), \
                 patch('cpu_efficiency_lab.matrix_controller.discover_cpu_topology', return_value=topology), \
                 patch('cpu_efficiency_lab.matrix_controller.collect_preflight',
                       return_value=replace(report, policies=(policy, report.policies[1]))):
                from cpu_efficiency_lab.adb import AdbError
                with self.assertRaisesRegex(AdbError, 'policy0'):
                    verify_device_snapshot(client, topology, report)
        self.assertEqual(client.mock_calls, [])

    def test_snapshot_accepts_equivalent_related_cpu_whitespace(self):
        topology, report = fixtures()
        current = replace(report.policies[0], related_cpus='0  1 2 3 4 5')
        with patch('cpu_efficiency_lab.matrix_controller.discover_cpu_topology', return_value=topology), \
             patch('cpu_efficiency_lab.matrix_controller.collect_preflight',
                   return_value=replace(report, policies=(current, report.policies[1]))):
            verify_device_snapshot(MagicMock(), topology, report)

    @patch('cpu_efficiency_lab.matrix_controller.INDEPENDENT_RECOVERY_VERIFIED', True)
    @patch('cpu_efficiency_lab.matrix_controller._wait_for_device_snapshot', return_value=None)
    def test_matching_snapshot_cannot_replace_missing_watchdog_evidence(self, wait):
        topology, report = fixtures()
        config = MatrixConfig('A-CPU-P', 'A', (6,), (('policy6', 1017600),), 1, 2, 1)
        for log in ('RECOVERY_LOG_UNAVAILABLE|remote',
                    'CONFIG_DONE|A-CPU-P\n',
                    'CONFIG_DONE|A-CPU-P\nNOT_WATCHDOG_DONE\n',
                    'CONFIG_DONE|A-CPU-P\nWATCHDOG_RECOVERY_OK|invalid\n'):
            with self.subTest(log=log):
                client = MagicMock()
                client.run_root_script_stdin.return_value = 'CONFIG_DONE|A-CPU-P\n'
                with patch('cpu_efficiency_lab.matrix_controller._read_recovery_log', return_value=log):
                    with self.assertRaisesRegex(MatrixConfigError, 'watchdog completion evidence missing'):
                        execute_matrix_config(client, config, topology, report,
                                              ['/sys/class/thermal/thermal_zone1/temp'],
                                              '/sys/class/thermal/thermal_zone2/temp',
                                              '/data/local/tmp/cpu-efficiency-lab/cpu_mix_bench',
                                              confirm_independent_recovery=True)

    @patch('cpu_efficiency_lab.matrix_controller.INDEPENDENT_RECOVERY_VERIFIED', True)
    @patch('cpu_efficiency_lab.matrix_controller._wait_for_device_snapshot', return_value=None)
    def test_watchdog_failure_in_first_log_is_not_hidden_by_failed_second_read(self, wait):
        topology, report = fixtures()
        config = MatrixConfig('A-CPU-P', 'A', (6,), (('policy6', 1017600),), 1, 2, 1)
        client = MagicMock()
        client.run_root_script_stdin.return_value = ''
        logs = ['CONFIG_DONE|A-CPU-P\nWATCHDOG_DONE\nWATCHDOG_RECOVERY_FAILED\n',
                'RECOVERY_LOG_UNAVAILABLE|remote']
        with patch('cpu_efficiency_lab.matrix_controller._read_recovery_log', side_effect=logs):
            with self.assertRaisesRegex(MatrixConfigError, 'restore failure'):
                execute_matrix_config(client, config, topology, report,
                                      ['/sys/class/thermal/thermal_zone1/temp'],
                                      '/sys/class/thermal/thermal_zone2/temp',
                                      '/data/local/tmp/cpu-efficiency-lab/cpu_mix_bench',
                                      confirm_independent_recovery=True)

    @patch('cpu_efficiency_lab.matrix_controller.INDEPENDENT_RECOVERY_VERIFIED', True)
    @patch('cpu_efficiency_lab.matrix_controller._wait_for_device_snapshot', return_value=None)
    def test_interrupted_transport_with_matching_values_does_not_claim_watchdog_restored(self, wait):
        topology, report = fixtures()
        config = MatrixConfig('A-CPU-P', 'A', (6,), (('policy6', 1017600),), 1, 2, 1)
        client = MagicMock()
        from cpu_efficiency_lab.adb import AdbError
        client.run_root_script_stdin.side_effect = AdbError('device offline')
        with patch('cpu_efficiency_lab.matrix_controller._read_recovery_log', return_value='RECOVERY_LOG_UNAVAILABLE'):
            with self.assertRaises(MatrixConfigError) as raised:
                execute_matrix_config(client, config, topology, report,
                                      ['/sys/class/thermal/thermal_zone1/temp'],
                                      '/sys/class/thermal/thermal_zone2/temp',
                                      '/data/local/tmp/cpu-efficiency-lab/cpu_mix_bench',
                                      confirm_independent_recovery=True)
        self.assertIn('snapshot currently matches', str(raised.exception))
        self.assertNotIn('independent recovery restored', str(raised.exception))

    @patch('cpu_efficiency_lab.matrix_controller.INDEPENDENT_RECOVERY_VERIFIED', True)
    @patch('cpu_efficiency_lab.matrix_controller._wait_for_device_snapshot', return_value=None)
    def test_interference_never_becomes_success_just_because_final_snapshot_matches(self, wait):
        topology, report = fixtures()
        config = MatrixConfig('A-CPU-P', 'A', (6,), (('policy6', 1017600),), 1, 2, 1)
        for evidence in (
            'POLICY_RESTORE_RESTRICTED|policy6|1017600|1958400',
            'POLICY_RESTORE_UNOWNED|policy6|1017600|1958400',
            'POLICY_RESTORE_CHANGED|policy6|1017600|1958400',
            'POLICY_JOURNAL_INVALID|policy6',
            'POLICY_TAINT_WRITE_FAILED|policy6',
            'WATCHDOG_RECOVERY_RESTRICTED|0',
            'RECOVERY_FILE|policy-policy6.state.tainted',
        ):
            with self.subTest(evidence=evidence):
                client = MagicMock()
                client.run_root_script_stdin.return_value = ''
                log = 'CONFIG_DONE|A-CPU-P\nWATCHDOG_RECOVERY_OK|0\n' + evidence
                with patch('cpu_efficiency_lab.matrix_controller._read_recovery_log', return_value=log):
                    with self.assertRaisesRegex(MatrixConfigError, 'ownership/interference') as raised:
                        execute_matrix_config(client, config, topology, report,
                                              ['/sys/class/thermal/thermal_zone1/temp'],
                                              '/sys/class/thermal/thermal_zone2/temp',
                                              '/data/local/tmp/cpu-efficiency-lab/cpu_mix_bench',
                                              confirm_independent_recovery=True)
                self.assertIn(evidence, raised.exception.remote_output)

    @patch('cpu_efficiency_lab.matrix_controller.INDEPENDENT_RECOVERY_VERIFIED', True)
    @patch('cpu_efficiency_lab.matrix_controller._wait_for_device_snapshot', return_value=None)
    def test_concurrent_system_capping_is_named_not_misread_as_adb_failure(self, wait):
        # A non-zero controller exit surfaced through adb (e.g. status 39) must
        # be diagnosed as concurrent system cpufreq limiting when the recovery
        # log shows out-of-range/lock evidence, not as a generic ADB failure.
        from cpu_efficiency_lab.adb import AdbError
        topology, report = fixtures()
        config = MatrixConfig('A-CPU0', 'A', (0,), (('policy0', 384000),), 1, 2, 1)
        for evidence in (
            'POLICY_RESTORE_RESTRICTED|policy0|384000|1785600',
            'CAP_VIOLATION|policy0|384000|556800|2745600',
            'STALE_POLICY_AFTER_HOTPLUG|policy0|384000|1785600|556800|2745600',
            'LOCK_INPUT_CHANGED|policy0|384000|1785600',
        ):
            with self.subTest(evidence=evidence):
                client = MagicMock()
                client.run_root_script_stdin.side_effect = AdbError('ADB exited with status 39: (no output)')
                with patch('cpu_efficiency_lab.matrix_controller._read_recovery_log', return_value=evidence):
                    with self.assertRaises(MatrixConfigError) as raised:
                        execute_matrix_config(client, config, topology, report,
                                              ['/sys/class/thermal/thermal_zone1/temp'],
                                              '/sys/class/thermal/thermal_zone2/temp',
                                              '/data/local/tmp/cpu-efficiency-lab/cpu_mix_bench',
                                              confirm_independent_recovery=True)
                msg = str(raised.exception)
                self.assertIn('concurrent system cpufreq limiting', msg)
                self.assertIn('not an ADB transport failure', msg)

    @patch('cpu_efficiency_lab.matrix_controller.INDEPENDENT_RECOVERY_VERIFIED', True)
    @patch('cpu_efficiency_lab.matrix_controller._wait_for_device_snapshot', return_value=None)
    def test_plain_adb_failure_without_cap_evidence_keeps_transport_wording(self, wait):
        from cpu_efficiency_lab.adb import AdbError
        topology, report = fixtures()
        config = MatrixConfig('A-CPU0', 'A', (0,), (('policy0', 556800),), 1, 2, 1)
        client = MagicMock()
        client.run_root_script_stdin.side_effect = AdbError('ADB exited with status 1: (no output)')
        with patch('cpu_efficiency_lab.matrix_controller._read_recovery_log', return_value='RECOVERY_LOG_UNAVAILABLE'):
            with self.assertRaises(MatrixConfigError) as raised:
                execute_matrix_config(client, config, topology, report,
                                      ['/sys/class/thermal/thermal_zone1/temp'],
                                      '/sys/class/thermal/thermal_zone2/temp',
                                      '/data/local/tmp/cpu-efficiency-lab/cpu_mix_bench',
                                      confirm_independent_recovery=True)
        self.assertNotIn('concurrent system cpufreq limiting', str(raised.exception))


if __name__ == '__main__':
    unittest.main()
