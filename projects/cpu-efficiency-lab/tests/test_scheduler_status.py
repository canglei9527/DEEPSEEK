"""Read-only scheduling observations and conservative baseline comparisons."""
import io
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

from cpu_efficiency_lab.adb import AdbError
from cpu_efficiency_lab.cli import main
from cpu_efficiency_lab.scheduler_status import _AUX_SCRIPT, collect_scheduler_status, compare_baseline, parse_aux
from cpu_efficiency_lab.screen_control import ScreenState
from tests.test_matrix_controller import fixtures

BOOT = '12345678-1234-1234-1234-123456789abc'
AUX = ('BOOT|' + BOOT + '\nLOW_POWER|0\nPROCESS_SCAN|OK\nWAKE_SCAN|OK\nPERF_SCAN|OK\n')


class SchedulerStatusTests(unittest.TestCase):
    def collect(self, aux=AUX, boot=BOOT, baseline=None, screen=ScreenState(1800000, 'Awake', True)):
        topology, report = fixtures()
        report = replace(report, nodes={**report.nodes, 'capacity': {'value': '89'}, 'temp': {'value': '286'}})
        client = MagicMock()
        client.run_root_script.side_effect = [boot + '\n', aux]
        with patch('cpu_efficiency_lab.scheduler_status.collect_preflight', return_value=report), \
             patch('cpu_efficiency_lab.scheduler_status.discover_cpu_topology', return_value=topology), \
             patch('cpu_efficiency_lab.scheduler_status.read_screen_state', return_value=screen):
            result = collect_scheduler_status(client, baseline)
        self.assertEqual(client.run_root_script.call_count, 2)
        return result

    def test_normal_observation_does_not_claim_factory_defaults(self):
        result = self.collect()
        self.assertEqual(result['assessment'], 'NO_LAB_ACTIVITY_OBSERVED')
        self.assertEqual(result['baseline_comparison']['status'], 'NOT_PROVIDED')
        self.assertEqual(result['boot_id'], BOOT)
        self.assertIn('not proof', ' '.join(result['limitations']))
        self.assertEqual(result['policies']['policy0']['governor'], 'walt')

    def test_observed_lab_residue_is_never_called_clear(self):
        result = self.collect(AUX + 'LABPROC|122|cpu_mix_bench\nLABWAKE|cel_abc\nLABPERF|122(122)|33\n')
        self.assertEqual(result['assessment'], 'LAB_ACTIVITY_OBSERVED')
        self.assertEqual(result['project_processes'][0]['pid'], 122)
        self.assertEqual(result['project_perf_requests'][0]['handle'], '33')

    def test_missing_scan_or_inconsistent_boot_is_unknown(self):
        self.assertEqual(self.collect(AUX.replace('PERF_SCAN|OK\n', ''))['assessment'], 'INCOMPLETE')
        other_boot = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
        result = self.collect(boot=other_boot)
        self.assertEqual(result['assessment'], 'INCOMPLETE')
        self.assertIsNone(result['boot_id'])

    def test_malformed_evidence_does_not_turn_into_no_residue(self):
        result = self.collect(AUX + 'LABPROC|not-a-pid|cpu_mix_bench\n')
        self.assertEqual(result['assessment'], 'INCOMPLETE')
        self.assertEqual(result['scan_status']['PROCESS_SCAN'], 'UNAVAILABLE')
        parsed = parse_aux(AUX + 'LABPERF|bad|?\n')
        self.assertEqual(parsed['scans']['PERF_SCAN'], 'UNAVAILABLE')
        with self.assertRaises(AdbError):
            parse_aux(AUX + 'PROCESS_SCAN|OK\n')

    def test_same_boot_baseline_compares_governor_without_any_restore(self):
        baseline = self.collect()
        self.assertEqual(self.collect(baseline=baseline)['baseline_comparison']['status'], 'MATCH')
        changed = {**baseline, 'policies': {**baseline['policies'],
                    'policy0': {**baseline['policies']['policy0'], 'governor': 'performance'}}}
        comparison = compare_baseline(baseline, changed)
        self.assertEqual(comparison['status'], 'DIFFERENT')
        self.assertEqual(comparison['differences'][0]['field'], 'policies')
        self.assertIn('not proof', comparison['note'])

    def test_reboot_or_endpoint_change_never_claims_a_match(self):
        current = self.collect()
        for field, value in (('boot_id', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'), ('serial', 'other-phone')):
            baseline = {**current, field: value}
            self.assertEqual(compare_baseline(current, baseline)['status'], 'UNCOMPARABLE_IDENTITY_OR_BOOT')
        baseline = {**current, 'screen_timeout_ms': None}
        self.assertEqual(compare_baseline(current, baseline)['status'], 'INCOMPLETE_BASELINE_OR_CURRENT')
        with self.assertRaises(ValueError):
            compare_baseline(current, {'schema': 'old-preflight'})

    def test_aux_script_does_not_contain_state_changing_commands(self):
        for forbidden in ('settings put', 'setprop ', 'reboot', 'input keyevent', 'kill ',
                          'scaling_max_freq" >', 'scaling_min_freq" >', 'wake_unlock'):
            self.assertNotIn(forbidden, _AUX_SCRIPT)

    @patch('cpu_efficiency_lab.cli.AdbClient')
    @patch('cpu_efficiency_lab.scheduler_status.collect_scheduler_status')
    def test_cli_nonmatching_or_uncomparable_baseline_returns_nonzero(self, collect, adb):
        report = self.collect()
        with tempfile.TemporaryDirectory() as d:
            baseline = Path(d) / 'baseline.json'
            baseline.write_text(json.dumps(report), encoding='utf-8')
            for status in ('DIFFERENT', 'UNCOMPARABLE_IDENTITY_OR_BOOT', 'INCOMPLETE_BASELINE_OR_CURRENT'):
                with self.subTest(status=status), patch('sys.stdout', new_callable=io.StringIO):
                    collect.return_value = {**report, 'baseline_comparison': {'status': status}}
                    self.assertEqual(main(['scheduler-status', '--serial', 'phone', '--baseline', str(baseline)]), 2)

    @patch('cpu_efficiency_lab.cli.AdbClient')
    @patch('cpu_efficiency_lab.scheduler_status.collect_scheduler_status')
    def test_cli_saves_exclusively_and_never_invokes_restore(self, collect, adb):
        report = self.collect()
        collect.return_value = report
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'status.json'
            with patch('sys.stdout', new_callable=io.StringIO):
                self.assertEqual(main(['scheduler-status', '--serial', 'phone', '--output', str(path)]), 0)
            self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['assessment'], 'NO_LAB_ACTIVITY_OBSERVED')
            old = path.read_bytes()
            with patch('sys.stderr', new_callable=io.StringIO):
                self.assertEqual(main(['scheduler-status', '--serial', 'phone', '--output', str(path)]), 2)
            self.assertEqual(path.read_bytes(), old)
        collect.assert_called_once()


if __name__ == '__main__':
    unittest.main()
