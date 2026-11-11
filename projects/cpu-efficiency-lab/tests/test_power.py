import unittest

from cpu_efficiency_lab.models import BatterySample
from cpu_efficiency_lab.power import (
    average_discharge_voltage_uv,
    battery_power_w,
    charge_counter_average_current_ma,
    charge_counter_energy_j,
    integrate_energy_j,
)


def sample(sequence, at_s, current_ua, voltage_uv, status="Discharging"):
    return BatterySample(
        sequence=sequence,
        current_before_s=at_s - 0.01,
        current_ua=current_ua,
        current_after_s=at_s,
        voltage_before_s=at_s,
        voltage_uv=voltage_uv,
        voltage_after_s=at_s + 0.01,
        temp_deci_c=310,
        status=status,
        capacity_percent=80,
        charge_counter_uah=4000000,
    )


class PowerTests(unittest.TestCase):
    def test_micro_units_convert_to_watts(self):
        self.assertAlmostEqual(
            battery_power_w(165000, 4397000, "Discharging"), 0.725505, places=6
        )

    def test_non_discharge_state_is_not_reported_as_draw(self):
        self.assertIsNone(battery_power_w(165000, 4397000, "Charging"))
        self.assertIsNone(battery_power_w(165000, 4397000, "Unknown"))

    def test_discharge_uses_magnitude_but_raw_current_remains_available(self):
        self.assertAlmostEqual(
            battery_power_w(-165000, 4397000, "Discharging"), 0.725505, places=6
        )

    def test_trapezoidal_energy_and_duration(self):
        values = [
            sample(0, 1.0, 1000000, 1000000),
            sample(1, 2.0, 3000000, 1000000),
        ]
        energy, duration = integrate_energy_j(values)
        self.assertAlmostEqual(energy, 2.0)
        self.assertAlmostEqual(duration, 1.0)

    def test_gap_is_not_bridged(self):
        values = [
            sample(0, 1.0, 1000000, 1000000),
            sample(1, 20.0, 1000000, 1000000),
        ]
        energy, duration = integrate_energy_j(values, max_gap_s=5.0)
        self.assertEqual(energy, 0.0)
        self.assertEqual(duration, 0.0)

    def test_charge_counter_cross_check_conversions(self):
        values = [
            sample(0, 1.0, 100000, 4380000),
            sample(1, 2.0, 100000, 4380000),
        ]
        voltage = average_discharge_voltage_uv(values)
        self.assertAlmostEqual(voltage, 4380000.0)
        self.assertAlmostEqual(charge_counter_energy_j(-2000, voltage, "Discharging"), 31.536)
        self.assertAlmostEqual(charge_counter_average_current_ma(-2000, 3600.0), 2.0)
        self.assertIsNone(charge_counter_energy_j(0, voltage, "Discharging"))
        self.assertIsNone(charge_counter_energy_j(-2000, voltage, "Charging"))

    def test_invalid_state_breaks_energy_integration(self):
        values = [
            sample(0, 1.0, 1000000, 1000000),
            sample(1, 2.0, 1000000, 1000000, status="Charging"),
            sample(2, 3.0, 1000000, 1000000),
        ]
        energy, duration = integrate_energy_j(values)
        self.assertEqual(energy, 0.0)
        self.assertEqual(duration, 0.0)


if __name__ == "__main__":
    unittest.main()
