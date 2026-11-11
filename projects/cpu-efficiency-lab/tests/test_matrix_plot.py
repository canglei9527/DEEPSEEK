import tempfile
import unittest
from pathlib import Path

from cpu_efficiency_lab.matrix_plot import (
    render_mixed_frequency_heatmap,
    render_single_core_frequency_curves,
)


class MatrixPlotTests(unittest.TestCase):
    def test_single_core_plot_shows_only_complete_valid_frequency_points(self):
        rows = [
            {"stage": "B", "config_id": "B-CPU1-F1-300000", "policy0_frequency_khz": 300000,
             "policy6_frequency_khz": None, "valid": True, "measured_repetitions": 2,
             "operations_per_total_joule_median": 2_000_000},
            {"stage": "B", "config_id": "B-CPU1-F2-600000", "policy0_frequency_khz": 600000,
             "policy6_frequency_khz": None, "valid": False, "measured_repetitions": 1,
             "operations_per_total_joule_median": 9_000_000},
            {"stage": "B", "config_id": "B-CPU1-F3-900000", "policy0_frequency_khz": 900000,
             "policy6_frequency_khz": None, "valid": True, "measured_repetitions": 2,
             "operations_per_total_joule_median": 3_000_000},
            {"stage": "B", "config_id": "B-CPU6-F1-1000000", "policy0_frequency_khz": None,
             "policy6_frequency_khz": 1000000, "valid": True, "measured_repetitions": 2,
             "operations_per_total_joule_median": 1_000_000},
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "curve.svg"
            render_single_core_frequency_curves(rows, path)
            svg = path.read_text(encoding="utf-8")
        self.assertIn("CPU1; 300000 kHz", svg)
        self.assertIn("CPU1; 900000 kHz", svg)
        self.assertNotIn("CPU1; 600000 kHz", svg)
        self.assertIn("CPU0 remains online", svg)
        # The gap at 600 MHz breaks the line rather than implying a measured trend.
        self.assertNotIn('stroke="#2563eb" stroke-width="2"', svg)

    def test_mixed_heatmap_uses_invalid_cells_and_a_global_efficiency_scale(self):
        rows = [
            {"stage": "D", "config_id": "D-E1P1-EF300000-PF1000000", "valid": True,
             "operations_per_total_joule_median": 1_000_000},
            {"stage": "D", "config_id": "D-E1P1-EF600000-PF1000000", "valid": False,
             "operations_per_total_joule_median": 99_000_000},
            {"stage": "D", "config_id": "D-E2P1-EF300000-PF1000000", "valid": True,
             "operations_per_total_joule_median": 3_000_000},
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "heatmap.svg"
            render_mixed_frequency_heatmap(rows, path)
            svg = path.read_text(encoding="utf-8")
        self.assertIn("1.0 to 3.0 million iterations/J", svg)
        self.assertIn("E1 + P1 workers", svg)
        self.assertIn("E2 + P1 workers", svg)
        self.assertIn("n/a", svg)
        self.assertNotIn("99.0", svg)
        self.assertIn("gray cells are missing or invalid", svg)


if __name__ == "__main__":
    unittest.main()
