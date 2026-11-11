"""Device fault-injection checks for matrix recovery; never runs a full sweep."""
from __future__ import annotations

import argparse
import json
import re
import secrets
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from cpu_efficiency_lab.adb import AdbClient, AdbError
from cpu_efficiency_lab.matrix_controller import _read_recovery_log, build_matrix_config_script, verify_device_snapshot
from cpu_efficiency_lab.matrix_plan import MatrixConfig, all_frequency_levels
from cpu_efficiency_lab.matrix_runner import _validate_device_ready
from cpu_efficiency_lab.screen_control import DEFAULT_MATRIX_TIMEOUT_MS, keep_screen_awake, read_screen_state, restore_screen_timeout
from cpu_efficiency_lab.sweep import deploy_benchmark


# All entries deliberately kill the controller; this is not a benchmark sweep.
CASES = (
    ("first_offline", "after_first_offline", False, "SIGKILL_AFTER_FIRST_OFFLINE"),
    ("policy0_max", "after_policy_max", False, "SIGKILL_AFTER_POLICY_MAX"),
    ("policy0_min", "after_policy_min", False, "SIGKILL_AFTER_POLICY_MIN"),
    ("policy0_restore", "during_restore_after_min", False, "SIGKILL_DURING_POLICY_RESTORE"),
    ("policy6_max", "after_policy6_max", True, "SIGKILL_AFTER_POLICY_MAX"),
    ("policy6_min", "after_policy6_min", True, "SIGKILL_AFTER_POLICY_MIN"),
    ("policy6_restore", "during_restore_policy6_after_min", True, "SIGKILL_DURING_POLICY_RESTORE"),
    ("child_before_register", "before_registration", False, "SIGKILL_BEFORE_REGISTRATION"),
    ("child_after_register", "after_registration", False, "SIGKILL_AFTER_REGISTRATION"),
    ("child_after_go", "after_go", False, "SIGKILL_AFTER_GO"),
    ("child_running", "during_benchmark", False, "SIGKILL_MAIN_DURING_BENCH"),
)


def _interior_frequency(report, policy_name: str) -> int:
    policy = next((p for p in report.policies if p.name == policy_name), None)
    if policy is None or policy.scaling_min_khz is None or policy.scaling_max_khz is None:
        raise AdbError("policy bounds unavailable: " + policy_name)
    candidates = [f for f in all_frequency_levels(report, policy_name)
                  if policy.scaling_min_khz < f < policy.scaling_max_khz]
    if not candidates:
        raise AdbError("no interior frequency inside current limits for " + policy_name)
    return candidates[len(candidates) // 2]


def _journal_restored(output: str, policy: str) -> bool:
    pattern = r"RECOVERY_FILE\|policy-%s\.state\s*\n[^\n]*\bRESTORED\b" % re.escape(policy)
    return bool(re.search(pattern, output)) and ("RECOVERY_FILE|policy-%s.state.tainted" % policy not in output)


def _external_restriction_policies(output: str, policies) -> set:
    """Policies the recovery path deliberately left tighter than the pre-test range.

    A concurrent system cap change is not a tool defect: the conservative
    restore must not widen a limit it no longer owns, and it marks the policy
    tainted. Such a case is inconclusive rather than failed.
    """
    restricted = set()
    for policy in policies:
        if "POLICY_RESTORE_RESTRICTED|%s|" % policy in output and \
                "RECOVERY_FILE|policy-%s.state.tainted" % policy in output:
            restricted.add(policy)
    return restricted


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--serial", required=True)
    parser.add_argument("--output", required=True, type=Path, help="new prefix for diagnostic evidence")
    parser.add_argument("--confirm-device-writes", action="store_true")
    parser.add_argument("--minimum-battery-percent", type=int, default=60)
    args = parser.parse_args(argv)
    if not args.confirm_device_writes:
        raise ValueError("fault injection requires --confirm-device-writes")
    if not 1 <= args.minimum_battery_percent <= 100:
        raise ValueError("invalid battery reserve")
    raw_path = args.output.with_name(args.output.name + ".raw.log")
    summary_path = args.output.with_name(args.output.name + ".summary.json")
    if raw_path.exists() or summary_path.exists():
        raise FileExistsError("refusing to overwrite recovery evidence")

    client = AdbClient(args.serial)
    report, topology, _, _, _ = _validate_device_ready(client, args.minimum_battery_percent)
    screen_before = read_screen_state(client)
    initial_bench = client.run_root_script("ps -A -o PID,NAME | grep '[c]pu_mix_bench' || true").strip()
    if initial_bench:
        raise AdbError("existing benchmark process found; refusing fault injection")
    # Test the long-script transport read-only before any device writes.
    if client.run_root_script_stdin("id -u").strip() != "0":
        raise AdbError("long-script stdin transport did not return root uid")

    raw_path.parent.mkdir(parents=True, exist_ok=True)
    results = []
    screen_after = None
    screen_error = None
    error = None
    prepared = False
    with raw_path.open("x", encoding="utf-8", newline="\n") as raw:
        def log(line):
            raw.write(str(line) + "\n"); raw.flush()
        log("VERIFICATION_BEGIN|%s|%s" % (report.serial, report.model))
        try:
            keep_screen_awake(client, expected_current_timeout_ms=screen_before.timeout_ms)
            prepared = True
            bench = deploy_benchmark(client, ROOT / "build" / "cpu_mix_bench")
            log("REMOTE_BENCH|" + bench)
            for index, (label, mode, mixed, marker) in enumerate(CASES, 1):
                print("VERIFY|%d/%d|%s" % (index, len(CASES), label), flush=True)
                report, topology, cpu_paths, battery_path, _ = _validate_device_ready(client, args.minimum_battery_percent)
                if not read_screen_state(client).display_on:
                    raise AdbError("screen is no longer on")
                # A policy restricted to a single frequency cannot host a
                # temporary lock; record that instead of discarding the run.
                try:
                    e_freq = _interior_frequency(report, "policy0")
                    targets = (("policy0", e_freq),)
                    cpus = (0,)
                    if mixed:
                        targets += (("policy6", _interior_frequency(report, "policy6")),)
                        cpus = (0, 6)
                except AdbError as exc:
                    skipped = {"case": label, "mode": mode, "verdict": "SKIPPED", "reason": str(exc)}
                    results.append(skipped)
                    log("CASE_RESULT|" + json.dumps(skipped, ensure_ascii=False))
                    print("VERIFY_RESULT|%s|SKIPPED|%s" % (label, exc), flush=True)
                    continue
                config = MatrixConfig("FIJ-" + label, "D" if mixed else "A", cpus, targets, 1, 8, 1)
                recovery_id = secrets.token_hex(16)
                recovery_dir = "/data/local/tmp/cpu-efficiency-lab/matrix_recovery_" + recovery_id
                script = build_matrix_config_script(config, topology, report, cpu_paths, battery_path,
                                                    bench, recovery_id=recovery_id, fault_injection=mode,
                                                    minimum_battery_percent=args.minimum_battery_percent)
                log("CASE_BEGIN|%s|%s|%s" % (label, recovery_id, list(targets)))
                transport = ""
                try:
                    transport = client.run_root_script_stdin(script, timeout_s=45)
                    log("CONTROLLER_RETURN|" + transport)
                except (AdbError, OSError) as exc:
                    log("CONTROLLER_ERROR|" + repr(exc))
                output = ""
                deadline = time.monotonic() + 50
                while time.monotonic() < deadline:
                    output = _read_recovery_log(client, recovery_dir)
                    if any(x in output for x in ("WATCHDOG_RECOVERY_OK", "WATCHDOG_RECOVERY_RESTRICTED", "WATCHDOG_RECOVERY_FAILED")):
                        break
                    time.sleep(1)
                log("REMOTE_BEGIN|" + label); log(output); log("REMOTE_END|" + label)
                snapshot_error = None
                try:
                    verify_device_snapshot(client, topology, report)
                except (AdbError, OSError) as exc:
                    snapshot_error = str(exc)
                process_list = client.run_root_script("ps -A -o PID,NAME | grep '[c]pu_mix_bench' || true").strip()
                marker_present = ("FAULT_INJECTION|" + marker + "|") in output
                expected_policies = [name for name, _ in targets]
                restricted_policies = _external_restriction_policies(output, expected_policies)
                restored = "WATCHDOG_RECOVERY_OK" in output and "WATCHDOG_RECOVERY_RESTRICTED" not in output
                journal_ok = (label == "first_offline" or _journal_restored(output, "policy0"))
                if mixed:
                    journal_ok = journal_ok and _journal_restored(output, "policy6")
                # The watchdog recovering a tighter-than-original range is the
                # documented safe outcome for concurrent system caps, not a
                # restoration failure of this tool.
                restricted_ok = bool(restricted_policies) and "WATCHDOG_RECOVERY_RESTRICTED" in output
                no_early_work = mode not in ("before_registration", "after_registration") or "B|META|" not in output
                running_work_seen = mode != "during_benchmark" or "B|META|" in output
                child_state = None
                child_stopped = True
                if mode in ("before_registration", "after_registration", "after_go", "during_benchmark"):
                    match = re.search(r"FAULT_INJECTION\|%s\|[^|\n]+\|(\d+)" % re.escape(marker), output)
                    if match:
                        pid = int(match.group(1))
                        child_state = client.run_root_script(
                            "if [ -r /proc/%d/stat ]; then awk '{print $3}' /proc/%d/stat; else echo missing; fi" % (pid, pid)
                        ).strip()
                        child_stopped = child_state in ("missing", "Z")
                    else:
                        child_stopped = False
                safety_ok = bool(marker_present and (restored or restricted_ok) and
                                 (journal_ok or restricted_ok) and not process_list and
                                 no_early_work and running_work_seen and child_stopped)
                if safety_ok and restricted_ok:
                    verdict = "RESTRICTED"
                else:
                    verdict = "PASS" if safety_ok and snapshot_error is None else "FAIL"
                result = {"case": label, "mode": mode, "target_khz": dict(targets), "verdict": verdict,
                          "passed": verdict == "PASS", "marker_present": marker_present,
                          "journal_restored": journal_ok, "snapshot_restored": snapshot_error is None,
                          "snapshot_error": snapshot_error,
                          "externally_restricted_policies": sorted(restricted_policies),
                          "benchmark_processes": process_list or None,
                          "child_state": child_state, "recovery_dir": recovery_dir}
                results.append(result); log("CASE_RESULT|" + json.dumps(result, ensure_ascii=False))
                print("VERIFY_RESULT|%s|%s" % (label, verdict), flush=True)
                if verdict == "FAIL":
                    raise AdbError("fault-injection check failed: " + label)
        except (AdbError, OSError, ValueError) as exc:
            error = repr(exc); log("VERIFICATION_ERROR|" + error)
        finally:
            if prepared:
                try:
                    screen_after = restore_screen_timeout(client, screen_before.timeout_ms, DEFAULT_MATRIX_TIMEOUT_MS)
                    log("SCREEN_RESTORED|" + repr(screen_after))
                except (AdbError, OSError) as exc:
                    screen_error = repr(exc); log("SCREEN_RESTORE_ERROR|" + screen_error)
            log("VERIFICATION_END|%d/%d|error=%s" % (len(results), len(CASES), error))
    verdicts = [item.get("verdict") for item in results]
    summary = {"serial": args.serial, "cases_planned": len(CASES), "cases_completed": len(results),
               "cases_passed": verdicts.count("PASS"), "cases_restricted": verdicts.count("RESTRICTED"),
               "cases_skipped": verdicts.count("SKIPPED"),
               "cases_failed": verdicts.count("FAIL"), "results": results, "error": error,
               "screen_before": asdict(screen_before), "screen_after": asdict(screen_after) if screen_after else None,
               "screen_restore_error": screen_error, "raw_log": str(raw_path),
               "verification_scope": "controlled fault injection only; does not enable the matrix gate",
               "remaining_limitations": ["sysfs comparisons and writes are not atomic with system changes",
                                         "concurrent system cap changes make a case inconclusive (RESTRICTED), not restored",
                                         "ADB transport disconnect must be independently revalidated",
                                         "thermal/policy behavior may vary across devices"]}
    with summary_path.open("x", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    complete = not error and not screen_error and len(results) == len(CASES)
    if not complete:
        return 2
    # External restriction or a single-frequency policy is inconclusive, not a pass.
    return 0 if verdicts.count("PASS") == len(CASES) else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AdbError, OSError, ValueError) as exc:
        print("error: %s" % exc, file=sys.stderr, flush=True)
        raise SystemExit(2)
