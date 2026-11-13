import csv
import json
import tempfile
import unittest
from pathlib import Path

from cpu_efficiency_lab.historical_report import build_report


class HistoricalReportTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.single = self.root / "single.tsv"
        self.combo = self.root / "combo.tsv"
        self.write_tsv(self.single, [
            {"policy": 0, "freq_khz": 1000000, "core": 0, "ops_per_s": 100000000, "avg_w": 2, "ms": 15000, "samples": 50},
            {"policy": 0, "freq_khz": 1000000, "core": 1, "ops_per_s": 200000000, "avg_w": 3, "ms": 15000, "samples": 48},
        ])
        self.write_tsv(self.combo, [
            {"freq_small_khz": 1000000, "freq_big_khz": 1500000, "total_ops_per_s": 400000000, "avg_w": 4, "ms": 15000, "samples": 40},
            {"freq_small_khz": 2000000, "freq_big_khz": 1500000, "total_ops_per_s": 500000000, "avg_w": 5, "ms": 15000, "samples": 41},
            {"freq_small_khz": 1000000, "freq_big_khz": 2500000, "total_ops_per_s": 600000000, "avg_w": 6, "ms": 15000, "samples": 42},
        ])

    @staticmethod
    def write_tsv(path, rows):
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]), delimiter="\t")
            writer.writeheader()
            writer.writerows(rows)

    def test_each_measurement_is_preserved_and_missing_grid_cell_is_explicit(self):
        output = self.root / "report"
        build_report(self.single, self.combo, output)
        report = json.loads((output / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["measurement_scope"], "whole_device_battery_power")
        self.assertEqual(report["single_measurement_count"], 2)
        self.assertEqual(report["single_coverage"][0]["cores"], [0, 1])
        self.assertEqual([r["battery_nj_per_op"] for r in report["single_measurements"]], [20, 15])
        self.assertEqual(report["combo_missing_pairs_khz"], [[2000000, 2500000]])
        single_svg = (output / "single.svg").read_text(encoding="utf-8")
        combo_svg = (output / "combo.svg").read_text(encoding="utf-8")
        self.assertEqual(single_svg.count("<circle "), 2)
        self.assertNotIn("<polyline", single_svg)
        self.assertIn("未测", combo_svg)
        self.assertNotIn("最优频点", single_svg + combo_svg)

    def test_existing_directory_and_invalid_data_never_overwrite(self):
        output = self.root / "exists"
        output.mkdir()
        sentinel = output / "report.json"
        sentinel.write_text("keep", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            build_report(self.single, self.combo, output)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")
        self.single.write_text("policy\tfreq_khz\tcore\tops_per_s\tavg_w\tms\tsamples\n0\t1\t0\tNaN\t1\t1\t1\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            build_report(self.single, self.combo, self.root / "invalid")
        self.assertFalse((self.root / "invalid").exists())


if __name__ == "__main__":
    unittest.main()
