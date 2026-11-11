import csv
import json
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from run_pinned_dynamic import CASES


class PlotPinnedRepeatsTests(unittest.TestCase):
    def _round(self, path, reverse=False):
        fields = ["config_id", "worker_cpus", "valid", "operations_per_second", "device_battery_load_power_w",
                  "operations_per_total_joule", "policy0_median_khz", "policy6_median_khz"]
        cases = list(reversed(CASES)) if reverse else CASES
        with path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for i, (name, cpus) in enumerate(cases):
                perf = 100_000_000 + len(cpus) * 80_000_000
                power = 1 + len(cpus) / 10
                writer.writerow({"config_id": name, "worker_cpus": ",".join(map(str, cpus)), "valid": True,
                                 "operations_per_second": perf, "device_battery_load_power_w": power,
                                 "operations_per_total_joule": perf / power,
                                 "policy0_median_khz": 1555200 + i * 100,
                                 "policy6_median_khz": 2438400})

    def test_two_rounds_produce_non_overwriting_comparison_and_svg(self):
        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory) / "run"
            self._round(Path(str(prefix) + ".round1.summary.csv"))
            self._round(Path(str(prefix) + ".round2.summary.csv"), reverse=True)
            command = [sys.executable, str(ROOT / "scripts" / "plot_pinned_repeats.py"), "--output", str(prefix)]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            with Path(str(prefix) + ".comparison.csv").open(encoding="utf-8", newline="") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(len(rows), 16)
            self.assertEqual(rows[0]["config_id"], "CPU0")
            self.assertAlmostEqual(float(rows[0]["round1_Miterations_s"]), 180.0)
            self.assertAlmostEqual(float(rows[0]["round2_Miterations_s"]), 180.0)
            report = json.loads(Path(str(prefix) + ".report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["best_group_by_mean_score"]["config_id"], "E6P2")
            for suffix in (".percore.svg", ".groups.svg", ".frequency.svg", ".frequency-efficiency.svg"):
                graph = Path(str(prefix) + suffix)
                ET.parse(graph)
                self.assertNotIn("100%%", graph.read_text(encoding="utf-8"))
            html = Path(str(prefix) + ".html").read_text(encoding="utf-8")
            self.assertIn("run.frequency-efficiency.svg", html)
            self.assertIn("没有锁频或关核", html)
            result2 = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(result2.returncode, 0)


if __name__ == "__main__":
    unittest.main()
