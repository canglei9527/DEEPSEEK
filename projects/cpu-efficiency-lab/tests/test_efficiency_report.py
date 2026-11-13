import json
from pathlib import Path
import tempfile
import unittest

from cpu_efficiency_lab import efficiency_report


class EfficiencyReportTests(unittest.TestCase):
    def test_real_data_keeps_eight_cores_and_correct_combo_minimum(self):
        report = efficiency_report.analyze(Path('data/eeff.tsv'), Path('data/eeff_combo.tsv'))
        self.assertEqual([r['core'] for r in report['cores']], list(range(8)))
        self.assertEqual([len(r['points']) for r in report['cores']], [11] * 6 + [10] * 2)
        self.assertTrue(all(p['record_count'] == 1 for c in report['cores'] for p in c['points']))
        best = report['best_combo_observed']
        self.assertEqual((best['freq_small_khz'], best['freq_big_khz']), (1996800, 1401600))
        self.assertAlmostEqual(best['battery_nj_per_op'], 2.106941966, places=7)
        self.assertEqual(report['combo_missing_pairs_khz'], [[2745600, 2841600], [2745600, 3072000]])
        self.assertFalse(report['incremental_available'])
        self.assertTrue(best['pareto'])
        fastest = report['highest_throughput_combo']
        lowest_power = report['lowest_power_combo']
        self.assertEqual((fastest['freq_small_khz'], fastest['freq_big_khz']), (2745600, 2438400))
        self.assertEqual((lowest_power['freq_small_khz'], lowest_power['freq_big_khz']), (1152000, 1401600))
        self.assertNotEqual((best['freq_small_khz'], best['freq_big_khz']),
                            (fastest['freq_small_khz'], fastest['freq_big_khz']))

    def test_artifacts_and_source_lines_preserved_without_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'report'
            report = efficiency_report.build_report(Path('data/eeff.tsv'), Path('data/eeff_combo.tsv'), output)
            self.assertEqual(len(list(output.glob('core-*.svg'))), 8)
            self.assertEqual(json.loads((output / 'report.json').read_text(encoding='utf-8')), report)
            page = (output / 'index.html').read_text(encoding='utf-8')
            self.assertIn('底噪', page)
            for core in range(8):
                self.assertIn('core-%d.svg' % core, page)
            self.assertIn('polyline', (output / 'core-0.svg').read_text(encoding='utf-8'))
            self.assertIn('未测', (output / 'combinations.svg').read_text(encoding='utf-8'))
            self.assertGreater(report['cores'][0]['points'][0]['source_lines'][0], 1)
            with self.assertRaises(FileExistsError):
                efficiency_report.build_report(Path('data/eeff.tsv'), Path('data/eeff_combo.tsv'), output)


if __name__ == '__main__':
    unittest.main()
