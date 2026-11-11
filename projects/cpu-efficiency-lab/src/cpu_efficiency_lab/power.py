"""Battery-side power and energy calculations."""

from __future__ import annotations

from typing import Iterable, Optional, Tuple

from .models import BatterySample


DISCHARGING_STATUS = "discharging"


def battery_power_w(current_ua: int, voltage_uv: int, status: str) -> Optional[float]:
    """Return battery-side discharge power in watts, or None for other states.

    Android power_supply current/voltage values are conventionally in µA/µV.
    The sign of current is preserved in raw data; this derived draw metric uses
    magnitude only when the status explicitly says the battery is discharging.
    """
    if status.strip().lower() != DISCHARGING_STATUS:
        return None
    return abs(current_ua * voltage_uv) / 1_000_000_000_000.0


def average_discharge_voltage_uv(
    samples: Iterable[BatterySample], max_gap_s: float = 5.0
) -> Optional[float]:
    """Return time-weighted battery voltage over contiguous discharge samples."""
    voltage_uv_s = 0.0
    covered_s = 0.0
    previous = None
    for sample in samples:
        if sample.status.strip().lower() != DISCHARGING_STATUS:
            previous = None
            continue
        point = (sample.midpoint_s, float(sample.voltage_uv))
        if previous is not None:
            dt = point[0] - previous[0]
            if 0.0 < dt <= max_gap_s:
                voltage_uv_s += (previous[1] + point[1]) * 0.5 * dt
                covered_s += dt
        previous = point
    return voltage_uv_s / covered_s if covered_s else None


def charge_counter_energy_j(
    delta_uah: int, average_voltage_uv: float, status: str
) -> Optional[float]:
    """Estimate discharge energy from a decreasing charge counter and voltage.

    This is a cross-check only: charge_counter and current_now may share the
    same fuel-gauge hardware and are not an independent calibration.
    """
    if status.strip().lower() != DISCHARGING_STATUS or delta_uah >= 0:
        return None
    return abs(delta_uah) * average_voltage_uv * 3.6e-9


def charge_counter_average_current_ma(delta_uah: int, duration_s: float) -> Optional[float]:
    """Convert a decreasing µAh counter over time to average discharge mA."""
    if delta_uah >= 0 or duration_s <= 0.0:
        return None
    return abs(delta_uah) * 3.6 / duration_s


def integrate_energy_j(
    samples: Iterable[BatterySample], max_gap_s: float = 5.0
) -> Tuple[float, float]:
    """Trapezoid-integrate valid discharge samples; return (joules, seconds).

    Invalid/charging samples break continuity. Gaps larger than max_gap_s are
    skipped rather than bridged, so a missing interval cannot invent energy.
    """
    energy_j = 0.0
    covered_s = 0.0
    previous = None
    for sample in samples:
        power = battery_power_w(sample.current_ua, sample.voltage_uv, sample.status)
        if power is None:
            previous = None
            continue

        point = (sample.midpoint_s, power)
        if previous is not None:
            dt = point[0] - previous[0]
            if 0.0 < dt <= max_gap_s:
                energy_j += (previous[1] + point[1]) * 0.5 * dt
                covered_s += dt
        previous = point
    return energy_j, covered_s
