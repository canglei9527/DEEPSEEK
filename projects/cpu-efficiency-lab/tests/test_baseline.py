import unittest

from cpu_efficiency_lab.baseline import paired_baseline


class BaselineTests(unittest.TestCase):
    def test_subtracts_idle_interpolated_at_load_time(self):
        result = paired_baseline([(0, .4), (2, .4)], [(18, .5), (20, .5)], .9, 4, 8)
        self.assertTrue(result.valid, result.reason)
        self.assertAlmostEqual(result.estimated_w, .4 + .1 * 5 / 18)
        self.assertAlmostEqual(result.incremental_w, .9 - result.estimated_w)
        self.assertEqual(result.coverage_s, 4)

    def test_requires_both_sides_and_does_not_bridge_invalid_samples(self):
        for after in ([], [(10, .4)], [(10, .4), (11, None), (12, .4)]):
            result = paired_baseline([(0, .4), (1, .4)], after, 1, 3, 5)
            self.assertFalse(result.valid)
            self.assertIsNone(result.incremental_w)
            self.assertEqual(result.reason, 'missing paired idle baseline')

    def test_rejects_drift_and_sub_noise_increment(self):
        drift = paired_baseline([(0, .4), (1, .4)], [(10, .8), (11, .8)], 1.2, 3, 7)
        self.assertFalse(drift.valid)
        self.assertIn('drift', drift.reason)
        noisy = paired_baseline([(0, .2), (1, .6)], [(10, .2), (11, .6)], .6, 3, 7)
        self.assertFalse(noisy.valid)
        self.assertIn('idle range', noisy.reason)
        self.assertGreater(noisy.incremental_w, 0)

    def test_rejects_unbracketed_and_nonpositive_increment(self):
        result = paired_baseline([(0, .4), (1, .4)], [(10, .4), (11, .4)], 1, -1, 3)
        self.assertIn('bracket', result.reason)
        result = paired_baseline([(0, .4), (1, .4)], [(10, .4), (11, .4)], .3, 3, 7)
        self.assertFalse(result.valid)
        self.assertIn('non-positive', result.reason)


if __name__ == '__main__':
    unittest.main()
