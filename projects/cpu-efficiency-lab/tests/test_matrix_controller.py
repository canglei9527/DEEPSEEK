import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from cpu_efficiency_lab.matrix_controller import (
    _safe_policy_restore_shell,
    _watchdog_shell_script,
    MatrixConfigError,
    build_matrix_config_script,
    execute_matrix_config,
    restore_device_snapshot,
    require_verified_hotplug_recovery,
)
from cpu_efficiency_lab.adb import AdbError
from cpu_efficiency_lab.matrix_plan import MatrixConfig
from cpu_efficiency_lab.models import CpuPolicy, PreflightReport
from cpu_efficiency_lab.topology import CpuCore, CpuTopology


class FakeClient:
    def __init__(self):
        self.scripts = []

    def run_root_script(self, script):
        self.scripts.append(script)
        return "HOST_RESTORE_DONE"


def fixtures():
    topology = CpuTopology(
        tuple(
            CpuCore(cpu, True, cpu != 0, 792 if cpu < 6 else 1024, 0 if cpu < 6 else 1,
                    cpu if cpu < 6 else cpu - 6, "midr")
            for cpu in range(8)
        )
    )
    report = PreflightReport(
        serial="phone", model="model", device="device", soc="SM8750",
        android_release="16", kernel="kernel", root_uid="0", root_context="u:r:su:s0",
        selinux="Enforcing",
        nodes={"current_now": {"readable": True}, "voltage_now": {"readable": True}, "status": {"readable": True, "value": "Discharging"}},
        policies=(
            CpuPolicy("policy0", "0 1 2 3 4 5", "384000 556800 960000 1363200 1996800", 384000, 1996800, 384000, 3532800, "walt"),
            CpuPolicy("policy6", "6 7", "1017600 1209600 1401600 1689600 1958400", 1017600, 1958400, 1017600, 4320000, "walt"),
        ),
    )
    return topology, report


class MatrixControllerTests(unittest.TestCase):
    @patch("cpu_efficiency_lab.matrix_controller.INDEPENDENT_RECOVERY_VERIFIED", False)
    def test_hotplug_is_blocked_before_any_device_access_while_recovery_is_unverified(self):
        topology, report = fixtures()
        config = MatrixConfig("A-CPU-P", "A", (6,), (("policy6", 1017600),), 1, 2, 1)
        client = FakeClient()
        with self.assertRaisesRegex(AdbError, "dynamic frequency caps"):
            execute_matrix_config(
                client, config, topology, report,
                ["/sys/class/thermal/thermal_zone1/temp"],
                "/sys/class/thermal/thermal_zone62/temp",
                "/data/local/tmp/cpu-efficiency-lab/cpu_mix_bench_" + "a" * 32,
            )
        self.assertEqual(client.scripts, [])

    @patch("cpu_efficiency_lab.matrix_controller.INDEPENDENT_RECOVERY_VERIFIED", False)
    def test_recovery_gate_refuses_even_explicit_confirmation_until_verified(self):
        with self.assertRaisesRegex(AdbError, "dynamic frequency caps"):
            require_verified_hotplug_recovery()
        with self.assertRaisesRegex(AdbError, "dynamic frequency caps"):
            require_verified_hotplug_recovery(confirmed=True)

    @patch("cpu_efficiency_lab.matrix_controller.INDEPENDENT_RECOVERY_VERIFIED", True)
    def test_verified_gate_still_requires_per_run_confirmation(self):
        with self.assertRaisesRegex(AdbError, "confirm-independent-recovery"):
            require_verified_hotplug_recovery()
        require_verified_hotplug_recovery(confirmed=True)

    @patch("cpu_efficiency_lab.matrix_controller.INDEPENDENT_RECOVERY_VERIFIED", True)
    @patch("cpu_efficiency_lab.matrix_controller._wait_for_device_snapshot", return_value=None)
    @patch("cpu_efficiency_lab.matrix_controller._read_recovery_log", return_value="CONFIG_DONE|A-CPU-P\nWATCHDOG_DONE\n")
    def test_verified_executor_uses_stdin_for_long_script_and_reads_recovery_log(self, _read, _wait):
        topology, report = fixtures()
        config = MatrixConfig("A-CPU-P", "A", (6,), (("policy6", 1017600),), 1, 2, 1)
        client = MagicMock()
        client.run_root_script_stdin.return_value = ""
        output = execute_matrix_config(client, config, topology, report,
                    ["/sys/class/thermal/thermal_zone1/temp"], "/sys/class/thermal/thermal_zone62/temp",
                    "/data/local/tmp/cpu-efficiency-lab/cpu_mix_bench_" + "a" * 32,
                    confirm_independent_recovery=True)
        self.assertIn("CONFIG_DONE|A-CPU-P", output)
        self.assertGreater(len(client.run_root_script_stdin.call_args.args[0]), 32767)
        client.run_root_script.assert_not_called()

    @patch("cpu_efficiency_lab.matrix_controller.INDEPENDENT_RECOVERY_VERIFIED", True)
    @patch("cpu_efficiency_lab.matrix_controller._wait_for_device_snapshot", return_value=None)
    @patch("cpu_efficiency_lab.matrix_controller._read_recovery_log", return_value="CONFIG_DONE|A-CPU-P\nRESTORE_ERROR|A-CPU-P\n")
    def test_executor_does_not_treat_restore_error_as_success(self, _read, _wait):
        topology, report = fixtures()
        config = MatrixConfig("A-CPU-P", "A", (6,), (("policy6", 1017600),), 1, 2, 1)
        client = MagicMock()
        client.run_root_script_stdin.return_value = ""
        with self.assertRaisesRegex(MatrixConfigError, "restore failure") as raised:
            execute_matrix_config(client, config, topology, report,
                ["/sys/class/thermal/thermal_zone1/temp"], "/sys/class/thermal/thermal_zone62/temp",
                "/data/local/tmp/cpu-efficiency-lab/cpu_mix_bench_" + "a" * 32,
                confirm_independent_recovery=True)
        self.assertIn("RESTORE_ERROR|A-CPU-P", raised.exception.remote_output)

    @unittest.skipUnless(shutil.which("sh"), "local POSIX shell unavailable")
    def test_safe_restore_uses_journal_and_preserves_unexpected_limits(self):
        cases = (
            # current min/max, original min/max, target, journal stage, expected min/max/status
            (1200, 1200, 400, 2000, "1200", "NONE", 1200, 1200, 2),
            (1200, 1200, 400, 2000, "1200", "LOCKED", 400, 2000, 0),
            (400, 1200, 400, 2000, "1200", "APPLY_MAX_INTENT", 400, 2000, 0),
            (1200, 1200, 400, 2000, "1200", "APPLY_MIN_INTENT", 400, 2000, 0),
            (400, 1200, 400, 2000, "1200", "RESTORE_MAX_INTENT", 400, 2000, 0),
            (400, 1200, 400, 2000, "1200", "LOCKED", 400, 1200, 2),
            (400, 1200, 400, 2000, "1200", "RESTORED", 400, 1200, 2),
            (400, 800, 400, 2000, "1200", "LOCKED", 400, 800, 2),
            (600, 1600, 400, 2000, "NA", "NONE", 600, 1600, 2),
            (200, 3000, 400, 2000, "NA", "NONE", 200, 3000, 2),
            (2100, 2500, 400, 2000, "NA", "NONE", 2100, 2500, 2),
        )
        for current_min, current_max, original_min, original_max, target, stage, want_min, want_max, want_status in cases:
            with self.subTest(current=(current_min, current_max), target=target, stage=stage):
                script = _safe_policy_restore_shell() + r'''
base=$(mktemp -d) || exit 90
trap 'rm -rf "$base"' EXIT
printf '%s\n' "$1" > "$base/scaling_min_freq"
printf '%s\n' "$2" > "$base/scaling_max_freq"
if [ "$6" != NONE ]; then printf 'test %s %s %s %s\n' "$3" "$4" "$5" "$6" > "$base/journal"; fi
safe_restore_policy test "$base" "$3" "$4" "$5" "$base/journal"
status=$?
read -r result_min < "$base/scaling_min_freq"
read -r result_max < "$base/scaling_max_freq"
printf 'RESULT|%s|%s|%s\n' "$result_min" "$result_max" "$status"
'''
                result = subprocess.run(
                    ["sh", "-s", "--", str(current_min), str(current_max),
                     str(original_min), str(original_max), target, stage],
                    input=script, capture_output=True, text=True, check=True,
                )
                self.assertIn("RESULT|%d|%d|%d" % (want_min, want_max, want_status), result.stdout)

    @unittest.skipUnless(shutil.which("sh"), "local POSIX shell unavailable")
    def test_dynamic_cap_change_during_restore_is_detected_and_preserved(self):
        script = _safe_policy_restore_shell() + r'''
base=$(mktemp -d) || exit 90
trap 'rm -rf "$base"' EXIT
printf '1200\n' > "$base/scaling_min_freq"
printf '1200\n' > "$base/scaling_max_freq"
printf 'test 400 2000 1200 LOCKED\n' > "$base/journal"
inject_once=1
printf() {
  if [ "${inject_once:-0}" = 1 ] && [ "$1" = '%s\n' ] && [ "$2" = 400 ]; then
    inject_once=0
    command printf '%s\n' "$2"
    command printf '800\n' > "$base/scaling_max_freq"
  else
    command printf "$@"
  fi
}
safe_restore_policy test "$base" 400 2000 1200 "$base/journal"
first=$?
safe_restore_policy test "$base" 400 2000 1200 "$base/journal"
second=$?
read -r low < "$base/scaling_min_freq"
read -r high < "$base/scaling_max_freq"
[ -f "$base/journal.tainted" ] && marked=yes || marked=no
printf 'DYNAMIC_CAP|%s|%s|%s|%s|%s|%s\n' "$first" "$second" "$low" "$high" "$marked" "$(cat "$base/journal")"
'''
        result = subprocess.run(["sh"], input=script, capture_output=True, text=True, check=True)
        self.assertIn("DYNAMIC_CAP|2|2|400|800|yes", result.stdout)
        self.assertIn("POLICY_RESTORE_CHANGED|test|400|800", result.stdout)
        self.assertNotIn("POLICY_RESTORE|test|400|2000|JOURNALED", result.stdout)

    @unittest.skipUnless(shutil.which("sh"), "local POSIX shell unavailable")
    def test_detected_policy_drift_stays_tainted_even_if_bounds_return(self):
        script = _safe_policy_restore_shell() + r'''
base=$(mktemp -d) || exit 90
trap 'rm -rf "$base"' EXIT
printf '400\n' > "$base/scaling_min_freq"
printf '1200\n' > "$base/scaling_max_freq"
printf 'test 400 2000 1200 LOCKED\n' > "$base/journal"
safe_restore_policy test "$base" 400 2000 1200 "$base/journal"
first=$?
printf '1200\n' > "$base/scaling_min_freq"
safe_restore_policy test "$base" 400 2000 1200 "$base/journal"
second=$?
read -r low < "$base/scaling_min_freq"
read -r high < "$base/scaling_max_freq"
[ -f "$base/journal.tainted" ] && marked=yes || marked=no
printf 'TAINTED_RESULT|%s|%s|%s|%s|%s\n' "$first" "$second" "$low" "$high" "$marked"
'''
        result = subprocess.run(["sh"], input=script, capture_output=True, text=True, check=True)
        self.assertIn("TAINTED_RESULT|2|2|1200|1200|yes", result.stdout)

    @unittest.skipUnless(shutil.which("sh"), "local POSIX shell unavailable")
    def test_invalid_journal_and_empty_bounds_never_widen_policy(self):
        script = _safe_policy_restore_shell() + r'''
base=$(mktemp -d) || exit 90
trap 'rm -rf "$base"' EXIT
printf '1200\n' > "$base/scaling_min_freq"
printf '1200\n' > "$base/scaling_max_freq"
printf 'test 400 2000 1100 LOCKED\n' > "$base/journal"
safe_restore_policy test "$base" 400 2000 1200 "$base/journal"
status=$?
read -r result_min < "$base/scaling_min_freq"
read -r result_max < "$base/scaling_max_freq"
printf 'INVALID_JOURNAL|%s|%s|%s\n' "$result_min" "$result_max" "$status"
printf '\n' > "$base/scaling_max_freq"
safe_restore_policy test "$base" 400 2000 1200 "$base/journal"
status=$?
printf 'EMPTY_BOUND|%s\n' "$status"
'''
        result = subprocess.run(["sh"], input=script, capture_output=True, text=True, check=True)
        self.assertIn("INVALID_JOURNAL|1200|1200|2", result.stdout)
        self.assertIn("EMPTY_BOUND|1", result.stdout)

    @unittest.skipUnless(shutil.which("sh"), "local POSIX shell unavailable")
    def test_watchdog_reports_restricted_or_failed_restoration_without_success_marker(self):
        watchdog = _watchdog_shell_script().replace(
            'path=/sys/devices/system/cpu/cpu$cpu/online', 'path="$FAKE_SYS/cpu$cpu/online"'
        ).replace(
            'path=/sys/devices/system/cpu/cpufreq/$policy', 'path="$FAKE_SYS/cpufreq/$policy"'
        ).replace('while [ "$attempt" -lt 60 ]', 'while [ "$attempt" -lt 1 ]')
        wrapper = r'''
base=$(mktemp -d) || exit 90
trap 'rm -rf "$base"' EXIT
FAKE_SYS="$base/sys"; export FAKE_SYS
mkdir -p "$FAKE_SYS/cpu1" "$FAKE_SYS/cpufreq/policy0"
printf '0\n' > "$FAKE_SYS/cpu1/online"
[ "$2" = missing ] || printf '400\n' > "$FAKE_SYS/cpufreq/policy0/scaling_min_freq"
printf '800\n' > "$FAKE_SYS/cpufreq/policy0/scaling_max_freq"
sh "$1" 99999999 1 5 "$base" /bench '0:1 1:1' 'policy0:400:2000' \
  "$base/ready" "$base/done" "$base/request" "$base/recovered" "$base/bench.pid" \
  "$base/log" 'policy0:400:2000:1200'
status=$?
read -r cpu < "$FAKE_SYS/cpu1/online"
read -r max < "$FAKE_SYS/cpufreq/policy0/scaling_max_freq"
printf 'RESULT|%s|%s|%s\n' "$status" "$cpu" "$max"
[ ! -f "$base/recovered" ] || { printf 'RECOVERED|'; cat "$base/recovered"; }
cat "$base/log"
'''
        if os.name == "nt" and not shutil.which("cygpath"):
            self.skipTest("converting temporary paths for shell requires cygpath")
        with tempfile.TemporaryDirectory() as directory:
            watchdog_path = Path(directory) / "watchdog.sh"
            watchdog_path.write_bytes(watchdog.encode("utf-8"))
            shell_path = (subprocess.check_output(["cygpath", "-u", str(watchdog_path)], text=True).strip()
                          if os.name == "nt" else str(watchdog_path))
            for mode, expected in (("restricted", "WATCHDOG_RECOVERY_RESTRICTED"), ("missing", "WATCHDOG_RECOVERY_FAILED")):
                with self.subTest(mode=mode):
                    result = subprocess.run(["sh", "-c", wrapper, "outer", shell_path, mode],
                                            capture_output=True, text=True, check=True)
                    self.assertIn("RESULT|1|1|800", result.stdout)
                    self.assertIn(expected, result.stdout)
                    self.assertNotIn("WATCHDOG_RECOVERY_OK", result.stdout)
                    if mode == "restricted":
                        self.assertIn("RECOVERED|RESTRICTED", result.stdout)
                    else:
                        self.assertNotIn("RECOVERED|", result.stdout)

    @unittest.skipUnless(shutil.which("sh") and shutil.which("nohup"), "local POSIX shell unavailable")
    def test_policy_write_and_restore_interruptions_recover_from_journal(self):
        for mode, marker in (("after_policy_max", "SIGKILL_AFTER_POLICY_MAX"),
                             ("after_policy_min", "SIGKILL_AFTER_POLICY_MIN"),
                             ("during_restore_after_min", "SIGKILL_DURING_POLICY_RESTORE")):
            with self.subTest(mode=mode):
                self._assert_journaled_recovery(mode, marker)

    @unittest.skipUnless(shutil.which("sh") and shutil.which("nohup"), "local POSIX shell unavailable")
    def test_mixed_policy_interruptions_restore_both_frequency_ranges(self):
        for mode, marker in (("after_policy6_max", "SIGKILL_AFTER_POLICY_MAX|FAULT-HALF|policy6"),
                             ("after_policy6_min", "SIGKILL_AFTER_POLICY_MIN|FAULT-HALF|policy6"),
                             ("during_restore_policy6_after_min", "SIGKILL_DURING_POLICY_RESTORE|policy6")):
            with self.subTest(mode=mode):
                self._assert_journaled_recovery(mode, marker, mixed=True)

    @unittest.skipUnless(shutil.which("sh") and shutil.which("nohup"), "local POSIX shell unavailable")
    def test_normal_fake_device_run_registers_load_and_restores_limits(self):
        self._assert_journaled_recovery("0", "CONFIG_DONE|FAULT-HALF", normal=True)

    def _assert_journaled_recovery(self, mode, marker, normal=False, mixed=False):
        if os.name == "nt" and not shutil.which("cygpath"):
            self.skipTest("converting temporary paths for shell requires cygpath")
        topology, report = fixtures()
        config = (MatrixConfig("FAULT-HALF", "D", (0, 6), (("policy0", 960000), ("policy6", 1401600)), 1, 2, 1)
                  if mixed else MatrixConfig("FAULT-HALF", "A", (0,), (("policy0", 960000),), 1, 2, 1))
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            shell_base = (subprocess.check_output(["cygpath", "-u", temporary], text=True).strip()
                          if os.name == "nt" else temporary)
            if any(char.isspace() for char in shell_base):
                self.skipTest("shell temporary path contains spaces")
            for cpu in range(1, 8):
                path = base / ("cpu%d" % cpu)
                path.mkdir()
                (path / "online").write_bytes(b"1\n")
            for name, low, high in (("policy0", 384000, 1996800), ("policy6", 1017600, 1958400)):
                path = base / "cpufreq" / name
                path.mkdir(parents=True)
                (path / "scaling_min_freq").write_bytes(("%d\n" % low).encode("ascii"))
                (path / "scaling_max_freq").write_bytes(("%d\n" % high).encode("ascii"))
            if normal:
                battery = base / "battery"
                battery.mkdir()
                for key, value in (("current_now", "300000"), ("voltage_now", "4300000"),
                                   ("status", "Discharging"), ("capacity", "90"), ("charge_counter", "5000000")):
                    (battery / key).write_bytes((value + "\n").encode("ascii"))
                (base / "cpu_temp").write_bytes(b"35000\n")
                (base / "battery_temp").write_bytes(b"27000\n")
                for name, freq in (("policy0", 960000), ("policy6", 1017600)):
                    (base / "cpufreq" / name / "scaling_cur_freq").write_bytes((str(freq) + "\n").encode("ascii"))
                (base / "bench_stub.sh").write_bytes(
                    b"printf 'B|META|test\\nB|MEASURE_BEGIN|0|1.0|0\\n'\nsleep 2\nprintf 'B|WORKER|0|0|1|2000|2|1000|1234\\nB|MEASURE_END|0|3.0|2|2000|1000|1234\\n'\n"
                )
            rid = "f" * 32
            script = build_matrix_config_script(
                config, topology, report, ["/sys/class/thermal/thermal_zone1/temp"],
                "/sys/class/thermal/thermal_zone62/temp",
                "/data/local/tmp/cpu-efficiency-lab/cpu_mix_bench_" + "a" * 32,
                recovery_id=rid, fault_injection=mode,
            ).replace("/sys/devices/system/cpu/cpufreq", shell_base + "/cpufreq").replace(
                "/sys/devices/system/cpu/cpu", shell_base + "/cpu"
            ).replace("/data/local/tmp/cpu-efficiency-lab/matrix_recovery_" + rid,
                      shell_base + "/recovery_" + rid).replace("nohup setsid sh -c", "nohup sh -c").replace(
                          'mkdir -m 700 "$recovery_dir" 2>/dev/null', 'mkdir "$recovery_dir"')
            if normal:
                script = script.replace("/sys/class/power_supply/battery", shell_base + "/battery").replace(
                    "/sys/class/thermal/thermal_zone1/temp", shell_base + "/cpu_temp").replace(
                    "/sys/class/thermal/thermal_zone62/temp", shell_base + "/battery_temp").replace(
                    'exec "$bench" "$worker_csv" "$warmup" "$measure"',
                    'exec sh ' + shell_base + '/bench_stub.sh "$worker_csv" "$warmup" "$measure"')
            result = subprocess.run(["sh"], input=script, capture_output=True, text=True, timeout=30)
            if normal:
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            else:
                self.assertNotEqual(result.returncode, 0)
            recovery = base / ("recovery_" + rid)
            self.assertTrue(recovery.exists(), "controller exited before guardian setup: %r %r" % (result.stdout, result.stderr))
            deadline = time.monotonic() + 20
            evidence = recovery / ("watchdog.log" if normal else "recovered")
            while time.monotonic() < deadline:
                if evidence.exists() and (not normal or "WATCHDOG_DONE" in evidence.read_text(encoding="utf-8")):
                    break
                time.sleep(.1)
            if not evidence.exists():
                diagnostics = {p.name: p.read_text(encoding="utf-8", errors="replace")
                               for p in recovery.glob("*.log")}
                self.fail("guardian did not finish; shell=%r stdout=%r stderr=%r diagnostics=%r" %
                          (result.returncode, result.stdout, result.stderr, diagnostics))
            if normal:
                self.assertIn("WATCHDOG_DONE", evidence.read_text(encoding="utf-8"))
                controller_log = (recovery / "controller.log").read_text(encoding="utf-8")
                self.assertIn("B|MEASURE_END", controller_log)
                self.assertRegex(controller_log, r"M\|FAULT-HALF\|1\|active\|")
            else:
                self.assertEqual(evidence.read_text(encoding="utf-8").strip(), "OK")
                self.assertIn("WATCHDOG_RECOVERY_OK", (recovery / "watchdog.log").read_text(encoding="utf-8"))
            self.assertIn("RESTORED", (recovery / "policy-policy0.state").read_text(encoding="utf-8"))
            if mixed:
                self.assertIn("RESTORED", (recovery / "policy-policy6.state").read_text(encoding="utf-8"))
            self.assertIn(marker, (recovery / "controller.log").read_text(encoding="utf-8"))
            self.assertEqual((base / "cpufreq" / "policy0" / "scaling_min_freq").read_text().strip(), "384000")
            self.assertEqual((base / "cpufreq" / "policy0" / "scaling_max_freq").read_text().strip(), "1996800")
            self.assertEqual((base / "cpufreq" / "policy6" / "scaling_min_freq").read_text().strip(), "1017600")
            self.assertEqual((base / "cpufreq" / "policy6" / "scaling_max_freq").read_text().strip(), "1958400")
            self.assertTrue(all((base / ("cpu%d" % cpu) / "online").read_text().strip() == "1" for cpu in range(1, 8)))

    def test_script_offlines_nonworkers_and_keeps_cpu0_as_keeper(self):
        topology, report = fixtures()
        config = MatrixConfig("A-CPU-P", "A", (6,), (("policy6", 1017600),), 1, 2, 1)
        script = build_matrix_config_script(
            config, topology, report,
            ["/sys/class/thermal/thermal_zone1/temp"],
            "/sys/class/thermal/thermal_zone62/temp",
            "/data/local/tmp/cpu-efficiency-lab/cpu_mix_bench_" + "a" * 32,
        )
        self.assertIn("trap 'restore_device' EXIT", script)
        self.assertIn('watchdog_seconds=65', script)
        self.assertIn('kill -TERM "$main_pid"', script)
        self.assertIn("nohup setsid sh -c", script)
        self.assertIn("WATCHDOG_RECOVERY_OK", script)
        self.assertIn("WATCHDOG_RECOVERY_RESTRICTED", script)
        self.assertIn('safe_restore_policy "$policy" "$path"', script)
        self.assertIn('[ "$recovery_status" = OK ]', script)
        self.assertIn("restore_cores\n    restore_policies", script)
        self.assertIn('desired_online="0 6"', script)
        self.assertIn("CPU0 is deliberately never offlined", script)
        self.assertIn('[ "$cpu" = 0 ] && continue', script)
        self.assertIn("CORE_OFFLINE_FAIL", script)
        self.assertIn("CORE_MASK_MISMATCH", script)
        self.assertIn("sample_online=$sample_cpu", script)
        self.assertIn('if [ "$online_state" = 1 ]; then', script)
        self.assertIn("LOCK_MISMATCH", script)
        self.assertIn("POLICY_BOUNDS_CHANGED", script)
        self.assertIn("BATTERY_STATUS_CHANGED", script)
        self.assertIn('while [ ! -f "$bench_go" ]', script)
        self.assertIn('parent_start=$(awk', script)
        self.assertIn('mv "$bench_pid_file.tmp" "$bench_pid_file"', script)
        self.assertLess(script.index('mv "$bench_pid_file.tmp" "$bench_pid_file"'),
                        script.index(': > "$bench_go"'))
        self.assertIn("SIGKILL_BEFORE_REGISTRATION", script)
        self.assertIn("SIGKILL_AFTER_REGISTRATION", script)
        self.assertIn("SIGKILL_AFTER_GO", script)
        self.assertIn("SIGKILL_AFTER_FIRST_OFFLINE", script)
        self.assertIn("SIGKILL_AFTER_POLICY_MAX", script)
        self.assertLess(script.index('"$target" > "$path/scaling_max_freq"'),
                        script.index("SIGKILL_AFTER_POLICY_MAX"))
        self.assertLess(script.index("SIGKILL_AFTER_POLICY_MAX"),
                        script.index('"$target" > "$path/scaling_min_freq"'))
        self.assertIn("CPU_TEMPERATURE_LIMIT", script)
        self.assertIn("BATTERY_TEMPERATURE_LIMIT", script)
        self.assertNotIn("scaling_governor", script)

    def test_script_targets_each_policy_for_mixed_worker_set(self):
        topology, report = fixtures()
        config = MatrixConfig("D-MIX", "D", (0, 1, 6), (("policy0", 960000), ("policy6", 1401600)), 1, 2, 1)
        script = build_matrix_config_script(
            config, topology, report,
            ["/sys/class/thermal/thermal_zone1/temp"],
            "/sys/class/thermal/thermal_zone62/temp",
            "/data/local/tmp/cpu-efficiency-lab/cpu_mix_bench_" + "b" * 32,
        )
        self.assertIn('workers="0 1 6"', script)
        self.assertIn("policy0:384000:1996800:960000", script)
        self.assertIn("policy6:1017600:1958400:1401600", script)
        self.assertIn('"$bench" "$worker_csv" "$warmup" "$measure"', script)
        self.assertIn("policy0/scaling_cur_freq", script)
        self.assertIn("policy6/scaling_cur_freq", script)

    def test_rejects_unexpected_benchmark_or_sensor_path(self):
        topology, report = fixtures()
        config = MatrixConfig("A-CPU0", "A", (0,), (("policy0", 384000),), 1, 2, 1)
        args = (config, topology, report, ["/sys/class/thermal/thermal_zone1/temp"], "/sys/class/thermal/thermal_zone62/temp")
        with self.assertRaisesRegex(ValueError, "benchmark path"):
            build_matrix_config_script(*args, "/data/local/tmp/other")
        with self.assertRaisesRegex(ValueError, "thermal"):
            build_matrix_config_script(config, topology, report, ["/data/not-thermal"], "/sys/class/thermal/thermal_zone62/temp", "/data/local/tmp/cpu-efficiency-lab/cpu_mix_bench_" + "c" * 32)

    def test_host_restore_only_touches_known_cpu_and_policy_nodes(self):
        topology, report = fixtures()
        client = FakeClient()
        restore_device_snapshot(client, topology, report)
        self.assertEqual(len(client.scripts), 1)
        script = client.scripts[0]
        self.assertIn("policy0:384000:1996800", script)
        self.assertIn("policy6:1017600:1958400", script)
        self.assertIn('states="0:1 1:1 2:1 3:1 4:1 5:1 6:1 7:1"', script)


if __name__ == "__main__":
    unittest.main()
