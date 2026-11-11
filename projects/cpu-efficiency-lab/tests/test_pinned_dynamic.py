import importlib.util
import shutil
import subprocess
import unittest
from pathlib import Path


path = Path(__file__).resolve().parents[1] / "scripts" / "run_pinned_dynamic.py"
spec = importlib.util.spec_from_file_location("run_pinned_dynamic", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def sample(phase, t, current):
    return ("M|CPU0|1|%s|%.2f|%d|%.2f|%.2f|4300000|%.2f|Discharging|27000|85|35000|"
            "1555200|1017600|5000000|0,1,2,3,4,5,6,7") % (phase, t, current, t + .01, t + .02, t + .03)


class PinnedDynamicTests(unittest.TestCase):
    def test_plan_covers_each_core_and_mixed_groups_without_duplicates(self):
        self.assertEqual(len(module.CASES), 16)
        self.assertEqual({cpus for _, cpus in module.CASES[:8]}, {(n,) for n in range(8)})
        self.assertEqual(len({name for name, _ in module.CASES}), 16)

    def test_device_script_keeps_thermal_guards_without_sysfs_writes(self):
        script = module._script("CPU0", (0,), "/data/local/tmp/cpu-efficiency-lab/bench",
                                ["/sys/class/thermal/thermal_zone1/temp"],
                                "/sys/class/thermal/thermal_zone62/temp",
                                "/data/local/tmp/cpu-efficiency-lab/test.log")
        self.assertIn('"$cpu_temp" -lt 80000', script)
        self.assertIn('"$battery_temp" -lt 43000', script)
        self.assertIn('"$capacity" -ge 60', script)
        self.assertIn('"$bench" "$cpus" 2 12', script)
        self.assertNotIn('scaling_max_freq" >', script)
        self.assertNotIn('scaling_min_freq" >', script)
        self.assertNotIn('/online" >', script)
        if shutil.which("sh"):
            result = subprocess.run(["sh", "-n"], input=script, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_summary_uses_total_device_power_and_all_online_mask(self):
        raw = "\n".join([
            sample("baseline_pre", 0, 100000), sample("baseline_pre", 1, 100000),
            "RUN|CPU0|1|0|0", "B|MEASURE_BEGIN|0|1.0|0",
            sample("active", 1, 300000), sample("active", 2, 300000), sample("active", 3, 300000),
            "B|WORKER|0|0|100|2000|2.0|1000|1234", "B|MEASURE_END|0|3.0|2.0|2000|1000|1234",
            sample("baseline_post", 3, 100000), sample("baseline_post", 4, 100000),
        ])
        rows = module._summarize(raw)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["valid"])
        self.assertFalse(rows[0]["strict_isolation"])
        self.assertFalse(rows[0]["frequency_locked"])
        self.assertAlmostEqual(rows[0]["device_battery_load_power_w"], 1.29)
        self.assertGreater(rows[0]["operations_per_total_joule"], 0)


if __name__ == "__main__":
    unittest.main()
