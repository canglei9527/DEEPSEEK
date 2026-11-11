import tempfile
import unittest
from pathlib import Path

from cpu_efficiency_lab.analysis import aggregate_curve, analyze_runs, parse_sweep_log
from cpu_efficiency_lab.plot import (
    render_efficiency_svg,
    render_performance_power_svg,
    render_total_battery_power_svg,
    render_total_efficiency_svg,
)
from cpu_efficiency_lab.sweep import SweepPoint


def sample(point, rep, phase, t, current_ua, frequency=384000):
    return "P|%s|%d|%s|%.2f|%d|%.2f|%.2f|4000000|%.2f|Discharging|30000|70000|%d|5000000" % (
        point, rep, phase, t - 0.01, current_ua, t + 0.01, t + 0.01, t + 0.02, frequency
    )


class AnalysisTests(unittest.TestCase):
    def fixture_log(self):
        lines = [
            "CONTROL_SMOKE_BEGIN",
            "RUN|policy0-384000|1|0",
            "B|MEASURE_BEGIN|0|1.0",
            "B|MEASURE_END|0|3.0|2.0|2|1.0|1",
            "SWEEP_BEGIN",
            "POINT|policy0-384000|/sys/devices/system/cpu/cpufreq/policy0|0|384000",
        ]
        for t in (0, 1, 2):
            lines.append(sample("policy0-384000", 0, "baseline_pre", t, -100000))
        lines.extend(["RUN|policy0-384000|1|0", "B|MEASURE_BEGIN|0|10.0"])
        for t in range(10, 21):
            lines.append(sample("policy0-384000", 1, "active", t, -200000))
        lines.extend(["B|MEASURE_END|0|20.0|10.0|100|10.0|1"])
        for t in (21, 22, 23):
            lines.append(sample("policy0-384000", 0, "baseline_post", t, -100000))
        return "\n".join(lines) + "\n"

    def test_parser_ignores_control_smoke_and_analyzes_paired_baseline(self):
        readings, runs = parse_sweep_log(self.fixture_log())
        self.assertEqual(len(runs), 1)
        self.assertEqual(len(readings), 17)
        point = SweepPoint("policy0-384000", "policy0", 0, 384000, 384000, 1996800)
        metrics = analyze_runs([point], readings, runs)
        self.assertEqual(len(metrics), 1)
        metric = metrics[0]
        self.assertTrue(metric.valid, metric.invalid_reason)
        self.assertAlmostEqual(metric.load_power_w, 0.8, places=4)
        self.assertAlmostEqual(metric.baseline_power_w, 0.4, places=4)
        self.assertAlmostEqual(metric.incremental_power_w, 0.4, places=4)
        self.assertAlmostEqual(metric.operations_per_joule, 25.0, places=3)

    def test_nonpositive_incremental_power_still_preserves_valid_total_device_metrics(self):
        lines = self.fixture_log().splitlines()
        adjusted = []
        for line in lines:
            fields = line.split("|")
            if len(fields) == 15 and fields[0] == "P" and fields[3] in ("baseline_pre", "baseline_post"):
                fields[5] = "-300000"
                line = "|".join(fields)
            adjusted.append(line)
        readings, runs = parse_sweep_log(chr(10).join(adjusted) + chr(10))
        point = SweepPoint("policy0-384000", "policy0", 0, 384000, 384000, 1996800)
        metric = analyze_runs([point], readings, runs)[0]
        self.assertTrue(metric.measurement_valid)
        self.assertFalse(metric.valid)
        curve = aggregate_curve([metric])[0]
        self.assertTrue(curve["measurement_valid"])
        self.assertFalse(curve["incremental_valid"])
        self.assertAlmostEqual(curve["load_power_w_median"], 0.8, places=4)
        self.assertIsNotNone(curve["operations_per_total_joule_median"])

    def test_aggregate_and_svg_outputs(self):
        readings, runs = parse_sweep_log(self.fixture_log())
        point = SweepPoint("policy0-384000", "policy0", 0, 384000, 384000, 1996800)
        curve = aggregate_curve(analyze_runs([point], readings, runs))
        self.assertEqual(curve[0]["valid_repetitions"], 1)
        with tempfile.TemporaryDirectory() as directory:
            performance = Path(directory) / "performance.svg"
            total_power = Path(directory) / "total-power.svg"
            efficiency = Path(directory) / "efficiency.svg"
            total_efficiency = Path(directory) / "total-efficiency.svg"
            render_performance_power_svg(curve, performance)
            render_total_battery_power_svg(curve, total_power)
            render_efficiency_svg(curve, efficiency)
            render_total_efficiency_svg(curve, total_efficiency)
            self.assertIn('width="100%"', performance.read_text(encoding="utf-8"))
            self.assertNotIn("100%%", performance.read_text(encoding="utf-8"))
            self.assertIn("<circle", efficiency.read_text(encoding="utf-8"))
            self.assertIn("Total whole-device battery draw", total_power.read_text(encoding="utf-8"))
            self.assertIn("whole device baseline", total_efficiency.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
