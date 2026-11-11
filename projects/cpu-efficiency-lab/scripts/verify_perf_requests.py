"""Short, explicitly authorized perfd request-ownership experiment (NOT a sweep).

Only invokes the fixed-resource native verifier with hard-coded short daemon TTLs.
Requires before/after stable snapshots and never rewrites sysfs or releases handles
owned by another client. Device-specific evidence stays in local data/.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
import shlex
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from cpu_efficiency_lab.adb import AdbClient, AdbError
from cpu_efficiency_lab.preflight import collect_preflight
from cpu_efficiency_lab.matrix_runner import _validate_device_ready


def snapshot(client):
    report, topology, _, _, temps = _validate_device_ready(client, 60)
    policies = {p.name: (p.scaling_min_khz, p.scaling_max_khz, p.governor, p.related_cpus)
                for p in report.policies}
    if set(policies) != {'policy0', 'policy6'}:
        raise AdbError('unexpected policies')
    raw_processes = client.run_root_script(
        "ps -A -o PID,NAME | grep -E '[c]pu_mix_bench|[p]erf_request_pr' || true"
    ).strip()
    if raw_processes:
        raise AdbError('existing benchmark or probe process: ' + raw_processes)
    return {'serial': report.serial, 'soc': report.soc, 'selinux': report.selinux,
            'capacity': report.nodes['capacity']['value'], 'status': report.nodes['status']['value'],
            'cpu_online': list(topology.online_cpus), 'policies': policies, 'temperatures_mc': temps}


def same_policy_snapshot(a, b):
    return a['cpu_online'] == b['cpu_online'] and a['policies'] == b['policies']


def ownership_evidence_valid(output):
    """All six distinct cases and both final policy checks are required."""
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    expected = {f'CASE|{policy}|{case}|PASS' for policy in (0, 6)
                for case in ('RELEASE_LOOSER', 'RELEASE_STRICTER', 'LOCK_AND_RELEASE')}
    cases = Counter(line for line in lines if line.startswith('CASE|'))
    if set(cases) != expected or any(count != 1 for count in cases.values()):
        return False
    if lines.count('VERIFY_END|PASS') != 1:
        return False
    if any(line.startswith(('REFUSED|', 'HANDLE_COLLISION|', 'CRASH_NOT_ARMED', 'UNAVAILABLE|'))
           or '|INCONCLUSIVE' in line for line in lines):
        return False
    return all(f'RESTORED_CHECK|{policy}|PASS' in lines for policy in (0, 6))


def settled(client, initial, timeout_s=8.0):
    deadline = time.monotonic() + timeout_s
    observations = []
    while time.monotonic() < deadline:
        try:
            value = snapshot(client)
            observations.append(value)
            if same_policy_snapshot(initial, value):
                return True, observations
        except (AdbError, OSError, ValueError) as exc:
            observations.append({'error': repr(exc)})
        time.sleep(0.5)
    return False, observations


def main(argv: Optional[list] = None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--serial', required=True)
    parser.add_argument('--binary', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path, help='new local evidence prefix')
    parser.add_argument('--confirm-device-writes', action='store_true')
    args = parser.parse_args(argv)
    if not args.confirm_device_writes:
        raise ValueError('finite perfd requests require --confirm-device-writes')
    if not args.binary.is_file():
        raise ValueError('probe binary missing: ' + str(args.binary))
    raw_path = args.output.with_name(args.output.name + '.raw.log')
    summary_path = args.output.with_name(args.output.name + '.summary.json')
    if raw_path.exists() or summary_path.exists():
        raise FileExistsError('refusing to overwrite evidence')
    client = AdbClient(args.serial)
    initial = snapshot(client)
    time.sleep(1)
    repeat = snapshot(client)
    if not same_policy_snapshot(initial, repeat):
        raise AdbError('system policy bounds changed before experiment; no writes performed')
    token = uuid.uuid4().hex
    staging = '/data/local/tmp/.perf-request-probe-' + token
    remote = '/data/local/tmp/cpu-efficiency-lab/perf_request_probe_' + token
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    result = {'serial': args.serial, 'started_utc': datetime.now(timezone.utc).isoformat(),
              'initial': initial, 'binary_sha256': hashlib.sha256(args.binary.read_bytes()).hexdigest(),
              'probe': 'NOT_RUN', 'ownership': 'NOT_RUN', 'crash_ttl': 'NOT_RUN',
              'postflight_restored': False, 'observations': [], 'error': None,
              'scope': 'perfd-owned frequency requests only, not matrix validation or performance measurement',
              'limitations': ['vendor request queue may not include direct sysfs writers',
                              'finite TTL and SIGKILL test do not prove all kernel/daemon crash paths',
                              'concurrent thermal and system policy constraints remain active',
                              'not an authorization to enable matrix recovery gate']}
    with raw_path.open('x', encoding='utf-8', newline='\n') as raw:
        def log(label, output):
            raw.write(label + '\n' + str(output) + '\n'); raw.flush()
        try:
            client.run(['push', str(args.binary), staging], timeout_s=30)
            client.run_root_script("mkdir -p /data/local/tmp/cpu-efficiency-lab && test ! -e %s && mv %s %s && chmod 755 %s"
                                   % tuple(map(shlex.quote, (remote, staging, remote, remote))))
            probe = client.run_root_script(shlex.quote(remote) + ' --probe', timeout_s=10)
            log('API_PROBE', probe)
            if 'API|perf_lock_acq|perf_lock_rel|RESOLVED' not in probe:
                raise AdbError('perfd API not available to root shell')
            result['probe'] = 'PASS'
            before = snapshot(client)
            if not same_policy_snapshot(initial, before):
                raise AdbError('system limits changed after deploy; no perfd requests made')
            result['ownership'] = 'INCONCLUSIVE'
            try:
                ownership = client.run_root_script(shlex.quote(remote) + ' --verify --confirm-device-writes', timeout_s=35)
            except AdbError as exc:
                # AdbClient includes captured stdout in the exception on nonzero
                # device exit. Persist it before propagating; it contains handle
                # release and exact observation evidence.
                log('OWNERSHIP_ERROR', repr(exc))
                raise
            log('OWNERSHIP', ownership)
            if not ownership_evidence_valid(ownership):
                raise AdbError('perfd ownership evidence incomplete or inconsistent')
            result['ownership'] = 'PASS'
            okay, observations = settled(client, initial)
            result['observations'].extend(observations)
            if not okay:
                raise AdbError('ownership check postflight differs from initial snapshot')
            try:
                crash = client.run_root_script(shlex.quote(remote) + ' --crash-test --confirm-device-writes', timeout_s=12)
            except AdbError as exc:
                log('CRASH_TRANSPORT', repr(exc))
                if 'status 137' not in str(exc) or 'CRASH_ARMED|' not in str(exc):
                    raise AdbError('crash did not prove a request was armed and SIGKILL occurred') from exc
            else:
                log('CRASH_RETURN', crash)
                raise AdbError('crash injection unexpectedly returned zero')
            # The verifier used a 3-second service TTL; do not try to release it
            # using a guessed handle. Observe the real kernel and daemon only.
            time.sleep(4)
            okay, observations = settled(client, initial)
            result['observations'].extend(observations)
            if not okay:
                raise AdbError('SIGKILL/TTL recovery was not confirmed')
            result['crash_ttl'] = 'PASS'
        except (AdbError, OSError, ValueError, KeyboardInterrupt) as exc:
            result['error'] = repr(exc)
            log('ERROR', repr(exc))
        finally:
            # Always wait longer than every possible native request TTL before
            # inspecting the device. Do not force snapshot values back into sysfs.
            time.sleep(9)
            okay, observations = settled(client, initial)
            result['observations'].extend(observations)
            result['postflight_restored'] = okay
            log('POSTFLIGHT_RESTORED', okay)
            result['completed_utc'] = datetime.now(timezone.utc).isoformat()
            with summary_path.open('x', encoding='utf-8', newline='\n') as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
                f.write('\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if (result['probe'] == result['ownership'] == result['crash_ttl'] == 'PASS'
                 and result['postflight_restored'] and result['error'] is None) else 2


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (AdbError, OSError, ValueError) as exc:
        print('error: ' + str(exc), file=sys.stderr)
        raise SystemExit(2)
