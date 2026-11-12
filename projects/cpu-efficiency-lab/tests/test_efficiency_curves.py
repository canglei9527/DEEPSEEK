"""Tests for reproducible efficiency-curve plotting."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cpu_efficiency_lab.efficiency_curves import (
    parse_combo_tsv,
    parse_single_core_tsv,
    render_single_core_svg,
    write_efficiency_curves,
)

SINGLE_TSV = (
    "policy\tfreq_khz\tcore\tops_per_s\tms\tavg_w\tidle_w\tnet_w\teff_ops_per_w\tnj_per_op\tsamples\n"
    "0\t556800\t0\t36586366.7\t15000\t1.148\t1.000\t0.148\t31854107.7\t31.39\t70\n"
    "0\t556800\t1\t38311898.8\t15000\t1.080\t1.000\t0.080\t35455702.1\t28.20\t69\n"
    "0\t1555200\t0\t108900000.0\t15000\t1.209\t1.000\t0.209\t90000000.0\t11.10\t70\n"
    "6\t3072000\t6\t218100000.0\t15000\t2.06\t1.000\t1.06\t200000000.0\t8.86\t2\n"
)

COMBO_TSV = (
    "freq_small_khz\tfreq_big_khz\ttotal_ops_per_s\tms\tavg_w\tmedian_w\tsamples\teff_ops_per_w\tnj_per_op\n"
    "1152000\t1401600\t629525677.3\t15000\t1.626\t1.727\t60\t387061343.1\t2.584\n"
    "1152000\t1958400\t706648352.0\t15000\t1.803\t1.887\t58\t391878264.3\t2.552\n"
)


class ParseTests(unittest.TestCase):
    def test_single_core_median_per_policy_freq(self):
        points = parse_single_core_tsv(SINGLE_TSV)
        self.assertEqual(len(points), 3)
        p0_low = next(p for p in points if p.policy == 0 and p.freq_khz == 556800)
        # median of 31.39 and 28.20
        self.assertAlmostEqual(p0_low.nj_per_op, (31.39 + 28.20) / 2.0, places=2)
        self.assertEqual(p0_low.samples, 2)
        p6 = next(p for p in points if p.policy == 6)
        self.assertAlmostEqual(p6.nj_per_op, 8.86, places=2)
        self.assertAlmostEqual(p6.ghz, 3.072, places=3)

    def test_single_core_requires_columns(self):
        with self.assertRaises(ValueError):
            parse_single_core_tsv("policy\tfreq_khz\n0\t1\n")

    def test_combo_parse(self):
        points = parse_combo_tsv(COMBO_TSV)
        self.assertEqual(len(points), 2)
        self.assertEqual(points[0].freq_big_khz, 1401600)
        self.assertAlmostEqual(points[0].nj_per_op, 2.584, places=3)


class RenderTests(unittest.TestCase):
    def test_svg_has_no_overwrite_and_valid_structure(self):
        with tempfile.TemporaryDirectory() as d:
            svg = Path(d) / "single.svg"
            points = parse_single_core_tsv(SINGLE_TSV)
            render_single_core_svg(points, svg)
            text = svg.read_text(encoding="utf-8")
            self.assertIn("<svg", text)
            self.assertIn("nJ/op", text)
            self.assertIn("</svg>", text)
            with self.assertRaises(FileExistsError):
                render_single_core_svg(points, svg)

    def test_write_efficiency_curves_outputs_and_summary(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            single = d / "eeff.tsv"
            single.write_text(SINGLE_TSV, encoding="utf-8")
            combo = d / "eeff_combo.tsv"
            combo.write_text(COMBO_TSV, encoding="utf-8")
            out = d / "curves"
            result = write_efficiency_curves(single, combo, out)
            self.assertTrue((out / "single-core-efficiency.svg").is_file())
            self.assertTrue((out / "single-core-throughput.svg").is_file())
            self.assertTrue((out / "multi-core-efficiency.svg").is_file())
            self.assertEqual(result["single_core_points"], 3)
            self.assertEqual(result["multi_core_points"], 2)
            self.assertIn("policy0", result["best_single_core_by_policy"])
            self.assertIn("policy6", result["best_single_core_by_policy"])
            self.assertAlmostEqual(result["best_multi_core_nj_per_op"], 2.552, places=3)
            # output dir must not be reusable
            with self.assertRaises(FileExistsError):
                write_efficiency_curves(single, combo, out)

    def test_write_without_combo(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            single = d / "eeff.tsv"
            single.write_text(SINGLE_TSV, encoding="utf-8")
            out = d / "curves"
            result = write_efficiency_curves(single, None, out)
            self.assertTrue((out / "single-core-efficiency.svg").is_file())
            self.assertFalse((out / "multi-core-efficiency.svg").exists())
            self.assertIsNone(result["best_multi_core_nj_per_op"])


class CliTests(unittest.TestCase):
    def test_cli_plot_efficiency(self):
        from cpu_efficiency_lab.cli import main
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            single = d / "eeff.tsv"
            single.write_text(SINGLE_TSV, encoding="utf-8")
            combo = d / "eeff_combo.tsv"
            combo.write_text(COMBO_TSV, encoding="utf-8")
            out = d / "out"
            rc = main([
                "plot-efficiency",
                "--single", str(single),
                "--combo", str(combo),
                "--output", str(out),
            ])
            self.assertEqual(rc, 0)
            self.assertTrue((out / "summary.json").is_file())
            self.assertTrue((out / "single-core-efficiency.svg").is_file())


if __name__ == "__main__":
    unittest.main()
