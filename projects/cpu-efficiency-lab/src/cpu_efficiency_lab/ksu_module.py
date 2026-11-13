"""Build a device-bound KernelSU scheduler-overlay experiment, never install it."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import shlex
import sys
import zipfile

from . import scheduler_report
from .efficiency_report import analyze as analyze_efficiency


MODULE_ID = 'cpu_efficiency_lab_background'
SCOPE = 'background_pack_candidate_not_device_validated'


def _sha(content):
    return hashlib.sha256(content).hexdigest()


def candidate(export):
    report = scheduler_report.analyze_sources(Path(export))
    sources = {r['source']: r for r in report['sources']}
    source_path = '/system/etc/task_profiles.json'
    if source_path not in sources:
        raise ValueError('base system task_profiles.json is required for this candidate')
    if any('/task_profiles/' in path or path == '/vendor/etc/task_profiles.json' for path in sources):
        raise ValueError('API/vendor task profile overlays require separate evaluation; no module generated')
    content = (Path(export) / sources[source_path]['saved_as']).read_bytes()
    original = scheduler_report._parse_json(content, source_path)
    profiles = {p['Name']: p for p in original.get('Profiles', [])}
    pack = profiles.get('CpuPolicyPack')
    expected = [{'Name': 'SetAttribute', 'Params': {'Name': 'UClampLatencySensitive', 'Value': '0'}}]
    if pack is None or pack.get('Actions') != expected:
        raise ValueError('CpuPolicyPack does not match the reviewed background-only action')
    attributes = {a['Name']: a for a in original.get('Attributes', [])}
    attr = attributes.get('UClampLatencySensitive', {})
    if attr.get('Controller') != 'cpu' or attr.get('File') != 'cpu.uclamp.latency_sensitive':
        raise ValueError('uclamp latency-sensitive attribute mapping is not supported')
    controllers = [r['configuration'] for r in report['load_groups'] if r['version'] == 'v1']
    if not any(r.get('Controller') == 'cpu' and r.get('Path') == '/dev/cpuctl' for r in controllers):
        raise ValueError('expected cpu controller mapping /dev/cpuctl is missing')
    identity = report['device']
    names = ('ro.product.model', 'ro.product.device', 'ro.build.fingerprint')
    if any(not isinstance(identity.get(k), str) or not identity[k].strip() for k in names):
        raise ValueError('model, device and full system fingerprint are required')
    api = identity.get('ro.product.first_api_level', '')
    if not re.fullmatch(r'[1-9][0-9]{0,2}', api):
        raise ValueError('invalid first API level')
    modified = copy.deepcopy(original)
    changes = []
    for name in ('SCHED_SP_BACKGROUND', 'CPUSET_SP_BACKGROUND'):
        targets = [r for r in modified.get('AggregateProfiles', []) if r['Name'] == name]
        if len(targets) != 1 or 'HighEnergySaving' not in targets[0]['Profiles']:
            raise ValueError('required background aggregate is missing: ' + name)
        members = targets[0]['Profiles']
        if 'CpuPolicyPack' in members:
            raise ValueError('background aggregate already contains CpuPolicyPack: ' + name)
        members.append('CpuPolicyPack')
        changes.append({'aggregate': name, 'before': members[:-1], 'after': list(members),
                        'action': expected[0]})
    tuned = (json.dumps(modified, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    return report, content, tuned, changes


def build_module(export, single, combo, output):
    report, original, tuned, changes = candidate(export)
    measured = analyze_efficiency(single, combo)
    identity = report['device']
    constraints = {key: identity[key] for key in ('ro.product.model', 'ro.product.device', 'ro.build.fingerprint')}
    manifest = {'schema': 'cpu-lab-ksu-candidate-v1', 'module_id': MODULE_ID, 'status': SCOPE,
                'device_constraints': constraints, 'source_sha256': _sha(original), 'tuned_sha256': _sha(tuned),
                'changes': changes, 'measurement_sources': measured['sources'],
                'observed_combo_candidate': measured['best_combo_observed'],
                'device_measurement_identity_verified': False, 'power_savings_verified': False,
                'chip_specific_parameters_verified': False,
                'limitations': [
                    '仅复用导出中现有 CpuPolicyPack，使两个后台聚合请求 latency_sensitive=0；不改前台、频率或核心掩码。',
                    '后台当前值、实际调用和省电效果未验证；原值为0时可能完全无效。',
                    '这不是替换内核调度器；文件挂载、进程配置缓存和加载时序仍需设备重启验证。',
                    '历史能效点缺少身份与有效配对基线，不能推出按体质验证的 cpuset/uclamp/nice 数值。',
                    '需要支持system挂载的KernelSU metamodule；未安装或启用本模块。'],
                'references': ['https://kernelsu.org/guide/module.html', 'https://kernelsu.org/guide/metamodule.html']}
    checks = []
    for key, value in constraints.items():
        if any(c in value for c in '\n\r\x00'):
            raise ValueError('identity contains unsupported control characters')
        checks.append('[ "$(getprop %s)" = %s ] || abort "Device/build mismatch: %s"' %
                      (shlex.quote(key), shlex.quote(value), key))
    api = identity['ro.product.first_api_level']
    installer = '''#!/system/bin/sh
[ "$KSU" = true ] || abort "KernelSU is required"
[ "$BOOTMODE" = true ] || abort "Install from KernelSU Manager in Android"
[ -d /data/adb/metamodule ] || abort "An active system-mount metamodule is required"
[ ! -e /data/adb/metamodule/disable ] || abort "The mounting metamodule is disabled"
[ ! -e /data/adb/metamodule/remove ] || abort "The mounting metamodule is pending removal"
[ "$KSU_LATE_LOAD" != true ] && [ "$KSU_LATE_LOAD" != 1 ] || abort "Late-load mode is not validated for task profiles"
''' + '\n'.join(checks) + '''
CURRENT=$(sha256sum /system/etc/task_profiles.json) || abort "Cannot read current task profiles"
CURRENT=${CURRENT%% *}
[ "$CURRENT" = "''' + _sha(original) + '''" ] || abort "Current task profiles differ from the reviewed source"
for FILE in /vendor/etc/task_profiles.json /system/etc/task_profiles/task_profiles_''' + api + '''.json; do
  [ ! -e "$FILE" ] || abort "Unreviewed task profile overlay: $FILE"
done
[ -e /dev/cpuctl/background/cpu.uclamp.latency_sensitive ] || abort "Required background uclamp node is unavailable"
ui_print "Experimental background-only scheduler overlay"
ui_print "Power savings and runtime profile loading are NOT verified"
ui_print "Reboot to apply; disable/remove and reboot to roll back"
set_perm_recursive "$MODPATH" 0 0 0755 0644
set_perm "$MODPATH/customize.sh" 0 0 0755
set_perm "$MODPATH/post-mount.sh" 0 0 0755
set_perm "$MODPATH/boot-completed.sh" 0 0 0755
set_perm "$MODPATH/uninstall.sh" 0 0 0755
'''
    verify = '''#!/system/bin/sh
MODDIR=${0%/*}
EXPECTED="''' + _sha(tuned) + '''"
CURRENT=$(sha256sum /system/etc/task_profiles.json 2>/dev/null)
CURRENT=${CURRENT%% *}
{
  printf 'expected_sha256=%s\\nobserved_sha256=%s\\n' "$EXPECTED" "$CURRENT"
  if [ "$CURRENT" = "$EXPECTED" ]; then
    printf 'overlay_hash=MATCH\\n'
  else
    printf 'overlay_hash=MISMATCH\\n'
  fi
  printf 'profile_loading=UNVERIFIED\\npower_savings=UNVERIFIED\\n'
  if [ -r /dev/cpuctl/background/cpu.uclamp.latency_sensitive ]; then
    printf 'background_latency_sensitive='
    cat /dev/cpuctl/background/cpu.uclamp.latency_sensitive
  fi
} > "$MODDIR/runtime-status.txt"
'''
    readme = '''# CPU Efficiency Lab · 后台调度候选

此模块是当前导出配置的 systemless 文件覆盖实验。仅向 SCHED_SP_BACKGROUND 和
CPUSET_SP_BACKGROUND 增加现有 CpuPolicyPack。它没有经过省电/延迟/应用稳定性对照，
不代表芯片体质调优已验证。当前值为0时可能没有行为改变。

安装前：备份；核对实验清单；确认 KernelSU 和挂载 metamodule 可用。
由用户自行在 KernelSU Manager 选择 module.zip。安装器核对型号、device、完整指纹、
原配置哈希、覆盖文件和uclamp节点；不匹配则中止。代码不安装模块或调用ADB写入。
重启后：查看模块目录 runtime-status.txt 的文件挂载哈希；MATCH仅证明覆盖文件可见，
不能证明所有进程已经加载，也不能证明更省电。需代表性任务的能耗、性能和延迟对照。
回滚：KernelSU Manager 禁用或卸载模块，然后重启。systemless覆盖不会改写系统分区。
若有其他调度模块，先处理冲突；本包不停止系统服务、不关闭温控/SELinux。

原配置：evidence/original-task_profiles.json
候选配置：system/etc/task_profiles.json
修改明细与来源：evidence/manifest.json
'''
    payload = {'module.prop': ('id=%s\nname=CPU Efficiency Lab - Background Candidate\nversion=0.1-experimental\nversionCode=1\nauthor=CPU Efficiency Lab\ndescription=Unvalidated background-only task-profile overlay; requires reboot and mounting metamodule\n' % MODULE_ID).encode(),
               'customize.sh': installer.encode(), 'post-mount.sh': verify.encode(),
               'boot-completed.sh': verify.encode(),
               'uninstall.sh': b'#!/system/bin/sh\n# KernelSU removes the systemless overlay on reboot.\n',
               'system/etc/task_profiles.json': tuned,
               'evidence/original-task_profiles.json': original,
               'evidence/manifest.json': (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode(),
               'README.md': readme.encode()}
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    for name, content in payload.items():
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as stream:
            stream.write(content)
    archive = output / 'module.zip'
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED) as stream:
        for name, content in sorted(payload.items()):
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.external_attr = (0o100755 if name.endswith('.sh') else 0o100644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            stream.writestr(info, content)
    return {'output': str(output), 'archive': str(archive), 'sha256': _sha(archive.read_bytes()),
            'status': SCOPE, 'installed': False, 'changes': changes}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--export', required=True, type=Path)
    parser.add_argument('--single', required=True, type=Path)
    parser.add_argument('--combo', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = build_module(args.export, args.single, args.combo, args.output)
    except (OSError, ValueError) as exc:
        print('error: %s' % exc, file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
