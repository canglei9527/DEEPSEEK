import tempfile
import unittest
from pathlib import Path

from cpu_efficiency_lab.models import CpuPolicy, PreflightReport
from cpu_efficiency_lab.sweep import (
    SweepPoint,
    available_points,
    build_point_script,
    discover_thermal_sensors,
    deploy_benchmark,
    REMOTE_DIR,
)


def report():
    policies = (
        CpuPolicy(
            name="policy0",
            related_cpus="0 1 2 3 4 5",
            available_frequencies_khz="384000 556800 1996800 2227200",
            scaling_min_khz=384000,
            scaling_max_khz=1996800,
            hardware_min_khz=384000,
            hardware_max_khz=3532800,
            governor="walt",
        ),
        CpuPolicy(
            name="policy6",
            related_cpus="6 7",
            available_frequencies_khz="1017600 2438400 2649600",
            scaling_min_khz=1017600,
            scaling_max_khz=2438400,
            hardware_min_khz=1017600,
            hardware_max_khz=4320000,
            governor="walt",
        ),
    )
    return PreflightReport(
        serial="phone",
        model="model",
        device="device",
        soc="soc",
        android_release="16",
        kernel="kernel",
        root_uid="0",
        root_context="u:r:su:s0",
        selinux="Enforcing",
        nodes={
            "current_now": {"readable": True},
            "voltage_now": {"readable": True},
            "status": {"readable": True, "value": "Discharging"},
        },
        policies=policies,
    )


class SweepTests(unittest.TestCase):
    def test_frequency_points_never_exceed_current_caps(self):
        points = available_points(report())
        self.assertEqual(
            [(point.policy, point.frequency_khz) for point in points],
            [
                ("policy0", 384000),
                ("policy0", 556800),
                ("policy0", 1996800),
                ("policy6", 1017600),
                ("policy6", 2438400),
            ],
        )
        for point in points:
            self.assertLessEqual(point.frequency_khz, point.original_max_khz)

    def test_refuses_incomplete_sweep_when_a_policy_is_offline(self):
        base = report()
        offline = CpuPolicy("policy6", "", "", None, None, None, None, "")
        incomplete = PreflightReport(**{**base.__dict__, "policies": (base.policies[0], offline)})
        with self.assertRaisesRegex(ValueError, "offline policy"):
            available_points(incomplete)

    def test_point_script_restores_caps_and_keeps_thermal_guard(self):
        point = SweepPoint("policy0-384000", "policy0", 0, 384000, 384000, 1996800)
        script = build_point_script(
            point,
            ["/sys/class/thermal/thermal_zone1/temp", "/sys/class/thermal/thermal_zone60/temp"],
            "/sys/class/thermal/thermal_zone62/temp",
            warmup_s=1,
            measure_s=2,
            repetitions=1,
            baseline_s=1,
            cooldown_s=1,
        )
        self.assertIn("trap 'restore_policy' EXIT", script)
        self.assertIn("target=384000", script)
        self.assertIn("expected_max=1996800", script)
        self.assertIn("cpu_limit=80000", script)
        self.assertIn("battery_limit=43000", script)
        self.assertIn("CPU_TEMPERATURE_LIMIT", script)
        self.assertIn("BATTERY_TEMPERATURE_LIMIT", script)
        self.assertNotIn("scaling_governor", script)
        self.assertNotIn("thermal_zone.*mode", script)

    def test_discovers_cpu_and_battery_thermal_zones(self):
        class FakeClient:
            def run_root_script(self, script):
                return (
                    "THERM|cpu-0-0|/sys/class/thermal/thermal_zone1/temp|42000" + chr(10)
                    + "THERM|battery|/sys/class/thermal/thermal_zone62/temp|30000" + chr(10)
                )

        paths, battery, values = discover_thermal_sensors(FakeClient())
        self.assertEqual(paths, ["/sys/class/thermal/thermal_zone1/temp"])
        self.assertEqual(battery, "/sys/class/thermal/thermal_zone62/temp")
        self.assertEqual(values["cpu-0-0"], 42000)
        self.assertEqual(values["battery"], 30000)

    def test_deployment_pushes_to_tmp_then_root_moves_without_overwrite(self):
        class FakeClient:
            def __init__(self):
                self.root_scripts = []
                self.commands = []

            def run_root_script(self, script):
                self.root_scripts.append(script)
                return ""

            def run(self, arguments, timeout_s=20.0):
                self.commands.append((arguments, timeout_s))
                return ""

        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "bench"
            binary.write_bytes(b"elf")
            client = FakeClient()
            remote = deploy_benchmark(client, binary)
            self.assertTrue(remote.startswith(REMOTE_DIR + "/cpu_mix_bench_"))
            self.assertEqual(client.commands[0][0][0], "push")
            self.assertTrue(client.commands[0][0][2].startswith("/data/local/tmp/.cpu-efficiency-lab-bench-"))
            self.assertIn("test ! -e", client.root_scripts[0])
            self.assertIn("mv", client.root_scripts[1])
            self.assertIn("chmod 755", client.root_scripts[1])

    def test_rejects_frequency_above_cap_and_bad_thermal_path(self):
        too_high = SweepPoint("policy0-high", "policy0", 0, 2000000, 384000, 1996800)
        with self.assertRaises(ValueError):
            build_point_script(
                too_high,
                ["/sys/class/thermal/thermal_zone1/temp"],
                "/sys/class/thermal/thermal_zone62/temp",
            )
        with self.assertRaises(ValueError):
            build_point_script(
                SweepPoint("policy0-low", "policy0", 0, 384000, 384000, 1996800),
                ["/data/local/tmp/not-a-thermal-node"],
                "/sys/class/thermal/thermal_zone62/temp",
            )


if __name__ == "__main__":
    unittest.main()
