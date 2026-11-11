import tempfile
import unittest
from pathlib import Path

from cpu_efficiency_lab.collector import (
    build_sampler_script,
    parse_samples,
    sample_to_row,
    value_change_intervals,
    write_csv,
)


SAMPLE_LINE = (
    "S|0|95578.46|165000|95578.47|95578.47|4397000|95578.48|308|Discharging|100|5881000"
)


class CollectorTests(unittest.TestCase):
    def test_generated_script_is_bounded_and_read_only(self):
        script = build_sampler_script(11, 1.0)
        self.assertIn("while [ \"$i\" -lt 11 ]", script)
        self.assertIn("/sys/class/power_supply/battery/current_now", script)
        self.assertIn("/sys/class/power_supply/battery/voltage_now", script)
        self.assertNotIn("echo ", script)
        self.assertNotIn("chmod", script)
        self.assertNotIn("scaling_max_freq", script)
        self.assertIn("then sleep 1; fi", script)

    def test_rejects_too_fast_or_unbounded_sampling(self):
        with self.assertRaises(ValueError):
            build_sampler_script(2, 0.1)
        with self.assertRaises(ValueError):
            build_sampler_script(2, 0.0)
        with self.assertRaises(ValueError):
            build_sampler_script(2, float("nan"))
        with self.assertRaises(ValueError):
            build_sampler_script(100000, 1.0)

    def test_parse_raw_sensor_line_and_calculate_power(self):
        samples = parse_samples("noise\r\n" + SAMPLE_LINE + "\r\n")
        self.assertEqual(len(samples), 1)
        sample = samples[0]
        self.assertEqual(sample.current_ua, 165000)
        self.assertEqual(sample.voltage_uv, 4397000)
        self.assertEqual(sample.temp_deci_c, 308)
        self.assertAlmostEqual(float(sample_to_row(sample)["battery_power_W"]), 0.725505)

    def test_malformed_sample_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_samples("S|0|bad|line")

    def test_observed_transition_interval_uses_device_timestamps(self):
        output = "\n".join(
            [
                "S|0|0|100|0|0|1000000|0|300|Discharging|50|1000",
                "S|1|1|200|1|1|1000000|1|300|Discharging|50|1000",
                "S|2|2|200|2|2|1000000|2|300|Discharging|50|1000",
                "S|3|4|300|4|4|1000000|4|300|Discharging|50|1000",
            ]
        )
        intervals = value_change_intervals(parse_samples(output), "current_ua")
        self.assertEqual(intervals, [3.0])
        with self.assertRaises(ValueError):
            value_change_intervals(parse_samples(output), "status")

    def test_csv_is_exclusive_and_preserves_raw_values(self):
        sample = parse_samples(SAMPLE_LINE)[0]
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "capture.csv"
            self.assertEqual(write_csv(target, [sample]), 1)
            contents = target.read_text(encoding="utf-8")
            self.assertIn("current_now_uA", contents)
            self.assertIn("165000", contents)
            with self.assertRaises(FileExistsError):
                write_csv(target, [sample])


if __name__ == "__main__":
    unittest.main()
