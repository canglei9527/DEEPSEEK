"""Typed records used by collection and analysis."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class BatterySample:
    sequence: int
    current_before_s: float
    current_ua: int
    current_after_s: float
    voltage_before_s: float
    voltage_uv: int
    voltage_after_s: float
    temp_deci_c: Optional[int]
    status: str
    capacity_percent: Optional[int]
    charge_counter_uah: Optional[int]

    @property
    def midpoint_s(self) -> float:
        current_mid = (self.current_before_s + self.current_after_s) / 2.0
        voltage_mid = (self.voltage_before_s + self.voltage_after_s) / 2.0
        return (current_mid + voltage_mid) / 2.0

    @property
    def read_skew_s(self) -> float:
        starts = (self.current_before_s, self.voltage_before_s)
        ends = (self.current_after_s, self.voltage_after_s)
        return max(ends) - min(starts)


@dataclass(frozen=True)
class CpuPolicy:
    name: str
    related_cpus: str
    available_frequencies_khz: str
    scaling_min_khz: Optional[int]
    scaling_max_khz: Optional[int]
    hardware_min_khz: Optional[int]
    hardware_max_khz: Optional[int]
    governor: str


@dataclass(frozen=True)
class PreflightReport:
    serial: str
    model: str
    device: str
    soc: str
    android_release: str
    kernel: str
    root_uid: str
    root_context: str
    selinux: str
    nodes: dict
    policies: tuple

    @property
    def ready_for_capture(self) -> bool:
        return self.root_uid == "0" and all(
            self.nodes.get(name, {}).get("readable", False)
            for name in ("current_now", "voltage_now", "status")
        )
