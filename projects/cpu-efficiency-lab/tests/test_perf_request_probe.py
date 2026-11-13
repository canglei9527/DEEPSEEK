"""Local authorization, failure-path, and evidence tests; no actual ADB."""
import contextlib
import io
import itertools
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from cpu_efficiency_lab.adb import AdbError
from verify_perf_requests import main, ownership_evidence_valid, same_policy_snapshot, snapshot


BOOT = '12345678-1234-1234-1234-123456789abc'
OTHER_BOOT = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
STATE = {'serial': 'phone', 'model': 'model', 'device': 'device', 'soc': 'example',
         'boot_id': BOOT, 'selinux': 'Enforcing',
         'capacity': '90', 'status': 'Discharging', 'cpu_online': list(range(8)),
         'policies': {'policy0': (100, 200, 'gov', '0 1 2 3 4 5'),
                      'policy6': (300, 400, 'gov', '6 7')}, 'temperatures_mc': {}}


class FakeClient:
    def __init__(self, serial):
        self.serial = serial
        self.commands = []

    def run(self, args, **kwargs):
        self.commands.append(('run', args))
        return 'pushed'

    def run_root_script(self, script, **kwargs):
        self.commands.append(('root', script))
        if '--probe' in script:
            return 'API|perf_lock_acq|perf_lock_rel|RESOLVED\n'
        if '--verify' in script:
            raise AdbError('ADB exited with status 2: ACQUIRE|0|CAP|123|8000|42\nCLEAN_RELEASE|42|0\nVERIFY_END|INCONCLUSIVE')
        return ''


class PerfProbeTests(unittest.TestCase):
    def test_evidence_requires_exact_six_unique_cases_not_just_pass_count(self):
        cases = [f'CASE|{p}|{case}|PASS' for p in (0, 6)
                 for case in ('RELEASE_LOOSER', 'RELEASE_STRICTER', 'LOCK_AND_RELEASE')]
        complete = cases + ['RESTORED_CHECK|0|PASS', 'RESTORED_CHECK|6|PASS', 'VERIFY_END|PASS']
        self.assertTrue(ownership_evidence_valid('\n'.join(complete)))
        self.assertFalse(ownership_evidence_valid('\n'.join(complete + [cases[0]])))
        self.assertFalse(ownership_evidence_valid('\n'.join(complete[1:] + ['RESTORED_CHECK|0|PASS'] * 20)))
        self.assertFalse(ownership_evidence_valid('\n'.join(complete + ['RESTORED_CHECK|6|INCONCLUSIVE'])))
        self.assertFalse(ownership_evidence_valid('\n'.join(complete[:-1])))
        self.assertFalse(ownership_evidence_valid('\n'.join(complete + ['REFUSED|STALE_LIMITS'])))

    def test_policy_snapshot_compares_governor_and_bounds_not_battery_drift(self):
        self.assertTrue(same_policy_snapshot(STATE, {**STATE, 'capacity': '89'}))
        other = {**STATE, 'policies': {**STATE['policies'], 'policy0': (100, 150, 'gov', '0 1 2 3 4 5')}}
        self.assertFalse(same_policy_snapshot(STATE, other))

    def test_matching_policy_values_require_same_valid_identity_and_boot(self):
        for key, value in [('boot_id', OTHER_BOOT), ('boot_id', ''), ('boot_id', 'not-a-uuid'),
                           ('serial', 'other-phone'), ('model', ''), ('device', 'other-device'),
                           ('soc', 'other-soc')]:
            with self.subTest(key=key, value=value):
                self.assertFalse(same_policy_snapshot(STATE, {**STATE, key: value}))
        without_boot = {key: value for key, value in STATE.items() if key != 'boot_id'}
        self.assertFalse(same_policy_snapshot(without_boot, without_boot))

    def test_snapshot_rejects_reboot_during_observation(self):
        report = SimpleNamespace(serial='phone', model='model', device='device', soc='example',
                                 selinux='Enforcing', nodes={'capacity': {'value': '90'}, 'status': {'value': 'Discharging'}},
                                 policies=[SimpleNamespace(name='policy0', scaling_min_khz=100, scaling_max_khz=200,
                                                           governor='gov', related_cpus='0 1 2 3 4 5'),
                                           SimpleNamespace(name='policy6', scaling_min_khz=300, scaling_max_khz=400,
                                                           governor='gov', related_cpus='6 7')])
        client = MagicMock(serial='phone')
        client.run_root_script.side_effect = [BOOT, '', OTHER_BOOT]
        with patch('verify_perf_requests._validate_device_ready',
                   return_value=(report, SimpleNamespace(online_cpus=tuple(range(8))), [], '', {})):
            with self.assertRaisesRegex(AdbError, 'rebooted during snapshot'):
                snapshot(client)

    def test_reboot_after_armed_sigkill_never_passes_ttl_or_postflight(self):
        cases = [f'CASE|{p}|{case}|PASS' for p in (0, 6)
                 for case in ('RELEASE_LOOSER', 'RELEASE_STRICTER', 'LOCK_AND_RELEASE')]
        complete = '\n'.join(cases + ['RESTORED_CHECK|0|PASS', 'RESTORED_CHECK|6|PASS', 'VERIFY_END|PASS'])

        class CrashClient(FakeClient):
            crashed = False

            def run_root_script(self, script, **kwargs):
                if '--verify' in script:
                    return complete
                if '--crash-test' in script:
                    self.crashed = True
                    raise AdbError('ADB exited with status 137: CRASH_ARMED|123|42|3000')
                return super().run_root_script(script, **kwargs)

        client = CrashClient('phone')
        rebooted = {**STATE, 'boot_id': OTHER_BOOT}
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            binary = root / 'probe'
            binary.write_bytes(b'fake binary')
            out = root / 'reboot'
            with patch('verify_perf_requests.AdbClient', return_value=client), \
                 patch('verify_perf_requests.snapshot', side_effect=lambda _: rebooted if client.crashed else STATE), \
                 patch('verify_perf_requests.time.sleep'), \
                 patch('verify_perf_requests.time.monotonic', side_effect=itertools.count()), \
                 contextlib.redirect_stdout(io.StringIO()):
                code = main(['--serial', 'phone', '--binary', str(binary), '--output', str(out), '--confirm-device-writes'])
            self.assertEqual(code, 2)
            summary = json.loads(out.with_name(out.name + '.summary.json').read_text(encoding='utf-8'))
            self.assertEqual(summary['ownership'], 'PASS')
            self.assertEqual(summary['crash_ttl'], 'INCONCLUSIVE')
            self.assertFalse(summary['postflight_restored'])
            self.assertIn('SIGKILL/TTL recovery was not confirmed', summary['error'])

    def test_requires_explicit_confirmation_before_client_or_files(self):
        with tempfile.TemporaryDirectory() as d, patch('verify_perf_requests.AdbClient') as adb:
            out = Path(d) / 'out'
            with self.assertRaisesRegex(ValueError, 'confirm-device-writes'):
                main(['--serial', 'phone', '--binary', str(Path(d) / 'missing'), '--output', str(out)])
            adb.assert_not_called()
            self.assertFalse(out.exists())

    def test_failed_owned_request_persists_error_and_never_runs_crash(self):
        client = FakeClient('phone')
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            binary = root / 'probe'
            binary.write_bytes(b'fake binary')
            out = root / 'evidence'
            with patch('verify_perf_requests.AdbClient', return_value=client), \
                 patch('verify_perf_requests.snapshot', return_value=STATE), \
                 patch('verify_perf_requests.settled', return_value=(True, [STATE])), \
                 patch('verify_perf_requests.time.sleep'), contextlib.redirect_stdout(io.StringIO()):
                code = main(['--serial', 'phone', '--binary', str(binary), '--output', str(out), '--confirm-device-writes'])
            self.assertEqual(code, 2)
            raw = out.with_name(out.name + '.raw.log').read_text(encoding='utf-8')
            self.assertIn('OWNERSHIP_ERROR', raw)
            self.assertIn('ACQUIRE|0|CAP|123', raw)
            self.assertIn('CLEAN_RELEASE|42|0', raw)
            self.assertNotIn('CRASH_TRANSPORT', raw)
            summary = json.loads(out.with_name(out.name + '.summary.json').read_text(encoding='utf-8'))
            self.assertEqual(summary['ownership'], 'INCONCLUSIVE')
            self.assertEqual(summary['crash_ttl'], 'NOT_RUN')
            self.assertFalse(any('--crash-test' in str(command) for _, command in client.commands))

    def test_interruption_records_inconclusive_status_and_still_checks_postflight(self):
        class InterruptedClient(FakeClient):
            def run_root_script(self, script, **kwargs):
                if '--verify' in script:
                    raise KeyboardInterrupt()
                return super().run_root_script(script, **kwargs)

        client = InterruptedClient('phone')
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            binary = root / 'probe'; binary.write_bytes(b'fake binary')
            out = root / 'interrupted'
            with patch('verify_perf_requests.AdbClient', return_value=client), \
                 patch('verify_perf_requests.snapshot', return_value=STATE), \
                 patch('verify_perf_requests.settled', return_value=(True, [STATE])) as postflight, \
                 patch('verify_perf_requests.time.sleep'), contextlib.redirect_stdout(io.StringIO()):
                code = main(['--serial', 'phone', '--binary', str(binary), '--output', str(out), '--confirm-device-writes'])
            self.assertEqual(code, 2)
            self.assertTrue(postflight.called)
            summary = json.loads(out.with_name(out.name + '.summary.json').read_text(encoding='utf-8'))
            self.assertEqual(summary['ownership'], 'INCONCLUSIVE')
            self.assertEqual(summary['crash_ttl'], 'NOT_RUN')
            self.assertEqual(summary['error'], 'KeyboardInterrupt()')

    def test_existing_evidence_refused_before_client_access(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            binary = root / 'probe'; binary.write_bytes(b'x')
            out = root / 'evidence'
            out.with_name(out.name + '.raw.log').write_text('keep', encoding='utf-8')
            with patch('verify_perf_requests.AdbClient') as adb:
                with self.assertRaises(FileExistsError):
                    main(['--serial', 'phone', '--binary', str(binary), '--output', str(out), '--confirm-device-writes'])
                adb.assert_not_called()


if __name__ == '__main__':
    unittest.main()
