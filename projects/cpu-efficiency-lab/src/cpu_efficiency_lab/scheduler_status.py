"""Read-only scheduling/recovery observation; never restores or writes device state."""
from __future__ import annotations

import re
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Optional

from .adb import AdbClient, AdbError
from .preflight import collect_preflight
from .screen_control import read_screen_state
from .topology import discover_cpu_topology

_BOOT_RE = re.compile(r'^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$')
_AUX_SCRIPT = r'''set +e
printf 'BOOT|'; cat /proc/sys/kernel/random/boot_id 2>/dev/null
printf 'LOW_POWER|'; settings get global low_power 2>/dev/null
if processes=$(ps -A -o PID,NAME,ARGS 2>/dev/null); then
  header=$(printf '%s\n' "$processes" | head -n 1)
  case "$header" in
    *PID*NAME*ARGS*)
      printf 'PROCESS_SCAN|OK\n'
      printf '%s\n' "$processes" | grep -E '[c]pu-efficiency-lab|[c]pu_mix_bench|[p]erf_request_pr|[m]atrix-watchdog' | awk '{print "LABPROC|" $1 "|" $2}'
      ;;
    *) printf 'PROCESS_SCAN|UNAVAILABLE\n';;
  esac
else printf 'PROCESS_SCAN|UNAVAILABLE\n'; fi
if locks=$(cat /sys/power/wake_lock 2>/dev/null); then
  printf 'WAKE_SCAN|OK\n'
  for token in $locks; do case "$token" in cel_[0-9a-f]*) printf 'LABWAKE|%s\n' "$token";; esac; done
else printf 'WAKE_SCAN|UNAVAILABLE\n'; fi
if perf=$(dumpsys -t 3 vendor.perfservice 2>/dev/null); then
  case "$perf" in
    *'Active Clients'*)
      printf 'PERF_SCAN|OK\n'
      printf '%s\n' "$perf" | grep -E '[p]erf_request_probe_' | awk -F '|' '{print "LABPERF|" $1 "|" $5}'
      ;;
    *) printf 'PERF_SCAN|UNAVAILABLE\n';;
  esac
else printf 'PERF_SCAN|UNAVAILABLE\n'; fi
'''


def parse_aux(output: str) -> dict:
    """Missing/ambiguous scan markers are unknown, not evidence of no activity."""
    result = {'boot_id': None, 'low_power': None,
              'processes': [], 'wake_locks': [], 'perf_requests': [],
              'scans': {'PROCESS_SCAN': 'UNAVAILABLE', 'WAKE_SCAN': 'UNAVAILABLE',
                        'PERF_SCAN': 'UNAVAILABLE'}}
    seen = set()
    malformed_scans = set()
    for line in output.splitlines():
        fields = line.strip().split('|')
        kind = fields[0]
        if kind in ('BOOT', 'LOW_POWER', *result['scans']):
            if kind in seen or len(fields) != 2:
                raise AdbError('duplicate/malformed scheduler status field: ' + kind)
            seen.add(kind)
            value = fields[1].strip()
            if kind == 'BOOT':
                result['boot_id'] = value.lower() if _BOOT_RE.fullmatch(value) else None
            elif kind == 'LOW_POWER':
                result['low_power'] = value if value in ('0', '1') else None
            else:
                result['scans'][kind] = value if value == 'OK' else 'UNAVAILABLE'
        elif kind == 'LABPROC':
            if len(fields) != 3 or not fields[1].isdigit() or not fields[2]:
                malformed_scans.add('PROCESS_SCAN')
            else:
                result['processes'].append({'pid': int(fields[1]), 'name': fields[2]})
        elif kind == 'LABWAKE':
            if len(fields) != 2 or not fields[1].startswith('cel_'):
                malformed_scans.add('WAKE_SCAN')
            else:
                result['wake_locks'].append(fields[1])
        elif kind == 'LABPERF':
            if len(fields) != 3 or not re.fullmatch(r'\d+\(\d+\)', fields[1].strip()) or not fields[2].strip().isdigit():
                malformed_scans.add('PERF_SCAN')
            else:
                result['perf_requests'].append({'pid_tid': fields[1].strip(), 'handle': fields[2].strip()})
    for scan in malformed_scans:
        result['scans'][scan] = 'UNAVAILABLE'
    return result


def compare_baseline(current: dict, baseline: Optional[dict]) -> dict:
    if baseline is None:
        return {'status': 'NOT_PROVIDED', 'differences': []}
    if not isinstance(baseline, dict) or baseline.get('schema') != 'cpu-lab-scheduler-status-v1':
        raise ValueError('baseline must be a prior scheduler-status JSON')
    same = all(current.get(k) == baseline.get(k) and bool(current.get(k))
               for k in ('serial', 'model', 'device', 'soc', 'boot_id'))
    if not same:
        return {'status': 'UNCOMPARABLE_IDENTITY_OR_BOOT', 'differences': [],
                'note': 'serial/model/device/SoC/boot id do not all match; do not restore an old snapshot'}
    for item in (current, baseline):
        if (not isinstance(item.get('online_cpus'), list) or not item['online_cpus'] or
                not isinstance(item.get('policies'), dict) or not item['policies'] or
                not isinstance(item.get('screen_timeout_ms'), int) or item['screen_timeout_ms'] < 0 or
                item.get('low_power') not in ('0', '1')):
            return {'status': 'INCOMPLETE_BASELINE_OR_CURRENT', 'differences': [],
                    'note': 'missing observation cannot establish a match'}
    differences = []
    for key in ('online_cpus', 'policies', 'screen_timeout_ms', 'low_power'):
        if current.get(key) != baseline.get(key):
            differences.append({'field': key, 'baseline': baseline.get(key), 'current': current.get(key)})
    return {'status': 'DIFFERENT' if differences else 'MATCH', 'differences': differences,
            'note': 'a match is observational only, not proof of factory settings or ownership'}


def collect_scheduler_status(client: AdbClient, baseline: Optional[dict] = None) -> dict:
    first_boot = client.run_root_script('cat /proc/sys/kernel/random/boot_id 2>/dev/null').strip().lower()
    report = collect_preflight(client)
    topology = discover_cpu_topology(client)
    aux = parse_aux(client.run_root_script(_AUX_SCRIPT, timeout_s=15))
    screen_error = None
    try:
        screen = asdict(read_screen_state(client))
    except (AdbError, OSError) as exc:
        screen, screen_error = None, str(exc)
    boot_consistent = bool(_BOOT_RE.fullmatch(first_boot) and first_boot == aux['boot_id'])
    policies = {p.name: {'governor': p.governor or None,
                         'scaling_min_khz': p.scaling_min_khz, 'scaling_max_khz': p.scaling_max_khz,
                         'related_cpus': p.related_cpus} for p in report.policies}
    result = {
        'schema': 'cpu-lab-scheduler-status-v1',
        'observed_utc': datetime.now(timezone.utc).isoformat(),
        'serial': report.serial, 'model': report.model, 'device': report.device, 'soc': report.soc,
        'boot_id': aux['boot_id'] if boot_consistent else None,
        'boot_consistent': boot_consistent, 'selinux': report.selinux,
        'online_cpus': list(topology.online_cpus), 'policies': policies,
        'low_power': aux['low_power'], 'battery_status': report.nodes.get('status', {}).get('value'),
        'battery_percent': report.nodes.get('capacity', {}).get('value'),
        'battery_temp_deci_c': report.nodes.get('temp', {}).get('value'),
        'screen': screen, 'screen_timeout_ms': screen['timeout_ms'] if screen else None,
        'screen_error': screen_error, 'project_processes': aux['processes'],
        'project_wake_locks': aux['wake_locks'], 'project_perf_requests': aux['perf_requests'],
        'scan_status': aux['scans'],
        'limitations': [
            'read-only multi-command snapshot is not atomic',
            'no project residue observed is not proof that all system scheduling settings are factory defaults',
            'limits, governors and online CPUs can change autonomously',
            'sysfs and vendor services may not expose every active request',
        ],
    }
    result['baseline_comparison'] = compare_baseline(result, baseline)
    activity = bool(aux['processes'] or aux['wake_locks'] or aux['perf_requests'])
    missing = (not boot_consistent or screen is None or not policies or report.root_uid != '0' or
               aux['low_power'] is None or report.selinux.strip().lower() != 'enforcing' or
               any(value != 'OK' for value in aux['scans'].values()) or
               0 not in topology.online_cpus or
               any(p['scaling_min_khz'] is None or p['scaling_max_khz'] is None or not p['governor']
                   for p in policies.values()))
    result['assessment'] = 'LAB_ACTIVITY_OBSERVED' if activity else (
        'INCOMPLETE' if missing else 'NO_LAB_ACTIVITY_OBSERVED'
    )
    return result
