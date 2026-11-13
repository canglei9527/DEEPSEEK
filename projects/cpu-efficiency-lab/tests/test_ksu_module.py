import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock
import zipfile

from cpu_efficiency_lab import ksu_module
from tests.test_scheduler_report import SOURCE_PATHS


class KsuModuleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.export = self.make_export(Path(self.temp.name) / 'export')

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def make_export(directory):
        directory.mkdir()
        contents = {
            '/system/etc/cgroups.json': ('system/etc/cgroups.json',
                {'Cgroups': [{'Controller': 'cpu', 'Path': '/dev/cpuctl'}]}),
            '/system/etc/task_profiles.json': ('system/etc/task_profiles.json', {
                'Attributes': [{'Name': 'UClampLatencySensitive', 'Controller': 'cpu',
                                'File': 'cpu.uclamp.latency_sensitive'}],
                'Profiles': [
                    {'Name': 'HighEnergySaving', 'Actions': [{'Name': 'JoinCgroup',
                     'Params': {'Controller': 'cpu', 'Path': 'background'}}]},
                    {'Name': 'CpuPolicyPack', 'Actions': [{'Name': 'SetAttribute',
                     'Params': {'Name': 'UClampLatencySensitive', 'Value': '0'}}]},
                ],
                'AggregateProfiles': [
                    {'Name': name, 'Profiles': ['HighEnergySaving']}
                    for name in ('SCHED_SP_BACKGROUND', 'CPUSET_SP_BACKGROUND')
                ],
            }),
            '/vendor/etc/perf/targetresourceconfigs.xml': ('vendor/etc/perf/targetresourceconfigs.xml',
                '<ResourceConfigs><Config MajorValue="1" MinorValue="1" /></ResourceConfigs>'),
            '/vendor/etc/perf/targetconfig.xml': ('vendor/etc/perf/targetconfig.xml',
                '<TargetConfig><Config1><TargetInfo Target="generic" /></Config1></TargetConfig>'),
        }
        sources = []
        for source in SOURCE_PATHS:
            entry = {'source': source, 'status': 'missing', 'size_bytes': None,
                     'sha256': None, 'saved_as': None, 'error': None}
            if source in contents:
                relative, value = contents[source]
                text = json.dumps(value).encode() if isinstance(value, dict) else value.encode()
                target = directory / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(text)
                entry.update(status='present', size_bytes=len(text),
                             sha256=hashlib.sha256(text).hexdigest(), saved_as=relative)
            sources.append(entry)
        (directory / 'manifest.json').write_text(json.dumps({
            'schema': 'cpu-lab-scheduler-sources-v1',
            'collected_utc': '2026-01-02T03:04:05Z',
            'identity': {
                'ro.product.model': 'model-x', 'ro.product.device': 'device-x',
                'ro.product.first_api_level': '35', 'ro.build.fingerprint': 'generic/build',
            },
            'sources': sources,
        }), encoding='utf-8')
        return directory

    def test_only_two_background_aggregates_change_and_zip_is_repeatable(self):
        with tempfile.TemporaryDirectory() as directory:
            a, b = Path(directory) / 'a', Path(directory) / 'b'
            first = ksu_module.build_module(self.export, Path('data/eeff.tsv'), Path('data/eeff_combo.tsv'), a)
            second = ksu_module.build_module(self.export, Path('data/eeff.tsv'), Path('data/eeff_combo.tsv'), b)
            self.assertEqual(first['sha256'], second['sha256'])
            self.assertFalse(first['installed'])
            original = ksu_module.scheduler_report._parse_json((a / 'evidence/original-task_profiles.json').read_bytes(), 'original')
            modified = json.loads((a / 'system/etc/task_profiles.json').read_text(encoding='utf-8'))
            restored = copy.deepcopy(modified)
            changed = []
            for aggregate in restored['AggregateProfiles']:
                if aggregate['Name'] in ('SCHED_SP_BACKGROUND', 'CPUSET_SP_BACKGROUND'):
                    self.assertEqual(aggregate['Profiles'][-1], 'CpuPolicyPack')
                    aggregate['Profiles'].pop()
                    changed.append(aggregate['Name'])
            self.assertEqual(restored, original)
            self.assertEqual(len(changed), 2)
            manifest = json.loads((a / 'evidence/manifest.json').read_text(encoding='utf-8'))
            self.assertFalse(manifest['power_savings_verified'])
            self.assertFalse(manifest['chip_specific_parameters_verified'])
            with zipfile.ZipFile(a / 'module.zip') as archive:
                self.assertIsNone(archive.testzip())
                self.assertIn('module.prop', archive.namelist())
                self.assertIn('system/etc/task_profiles.json', archive.namelist())
                self.assertFalse(any(name.startswith('/') or '..' in Path(name).parts for name in archive.namelist()))
            shell = shutil.which('sh')
            if shell:
                for script in a.glob('*.sh'):
                    result = subprocess.run([shell, '-n'], input=script.read_bytes(), capture_output=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
            installer = (a / 'customize.sh').read_text(encoding='utf-8')
            for needle in ('ro.build.fingerprint', 'sha256sum', '/data/adb/metamodule', 'abort', 'KSU_LATE_LOAD'):
                self.assertIn(needle, installer)
            with self.assertRaises(FileExistsError):
                ksu_module.build_module(self.export, Path('data/eeff.tsv'), Path('data/eeff_combo.tsv'), a)

    def test_installer_rejects_mismatch_disabled_mount_and_missing_node(self):
        import shlex
        shell = shutil.which('sh')
        if shell is None:
            self.skipTest('POSIX shell unavailable')
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'candidate'
            ksu_module.build_module(self.export, Path('data/eeff.tsv'), Path('data/eeff_combo.tsv'), output)
            manifest = json.loads((output / 'evidence/manifest.json').read_text(encoding='utf-8'))
            installer = (output / 'customize.sh').read_text(encoding='utf-8')
            properties = '\n'.join('%s) printf "%%s\\n" %s ;;' % (shlex.quote(key), shlex.quote(value))
                                   for key, value in manifest['device_constraints'].items())
            setup = '''
KSU=true
BOOTMODE=true
KSU_LATE_LOAD=false
MODPATH=unused-local-test
abort() { printf 'ABORT:%s\\n' "$*"; exit 77; }
ui_print() { printf '%s\\n' "$*"; }
set_perm() { :; }
set_perm_recursive() { :; }
getprop() { case "$1" in
''' + properties + '''
esac; }
sha256sum() { printf '%s  source\\n' ''' + shlex.quote(manifest['source_sha256']) + '''; }
[() {
  if command test "$1" = '!'; then
    shift
    [ "$@"
    case $? in 0) return 1 ;; *) return 0 ;; esac
  fi
  case "$1:$2" in
    -d:/data/adb/metamodule) return 0 ;;
    -e:/data/adb/metamodule/disable) command test "$SCENARIO" = disabled; return ;;
    -e:/data/adb/metamodule/remove) return 1 ;;
    -e:/dev/cpuctl/background/cpu.uclamp.latency_sensitive) command test "$SCENARIO" != missing_node; return ;;
    -e:/vendor/etc/task_profiles.json) command test "$SCENARIO" = overlay; return ;;
    -e:/system/etc/task_profiles/task_profiles_*) return 1 ;;
  esac
  command [ "$@"
}
'''
            for scenario in ('success', 'disabled', 'missing_node', 'overlay', 'mismatch', 'late_load'):
                override = ''
                if scenario == 'mismatch':
                    override = 'getprop() { printf wrong; }\n'
                elif scenario == 'late_load':
                    override = 'KSU_LATE_LOAD=1\n'
                script = setup + 'SCENARIO=' + scenario + '\n' + override + installer
                result = subprocess.run([shell], input=script.encode(), capture_output=True)
                self.assertEqual(result.returncode, 0 if scenario == 'success' else 77,
                                 (scenario, result.stdout, result.stderr))
                if scenario != 'success':
                    self.assertIn(b'ABORT:', result.stdout)

    def test_changed_reviewed_action_rejects_before_output(self):
        report, original, _, _ = ksu_module.candidate(self.export)
        altered = ksu_module.scheduler_report._parse_json(original, 'test')
        for profile in altered['Profiles']:
            if profile['Name'] == 'CpuPolicyPack':
                profile['Actions'][0]['Params']['Value'] = '1'
        with mock.patch.object(ksu_module.scheduler_report, 'analyze_sources', return_value=report), \
                mock.patch.object(ksu_module.scheduler_report, '_parse_json', return_value=altered):
            with self.assertRaisesRegex(ValueError, 'reviewed'):
                ksu_module.candidate(self.export)

    def test_new_overlay_source_is_rejected(self):
        report = ksu_module.scheduler_report.analyze_sources(self.export)
        report['sources'].append({'source': '/vendor/etc/task_profiles.json'})
        with mock.patch.object(ksu_module.scheduler_report, 'analyze_sources', return_value=report):
            with self.assertRaisesRegex(ValueError, 'overlays'):
                ksu_module.candidate(self.export)


if __name__ == '__main__':
    unittest.main()
