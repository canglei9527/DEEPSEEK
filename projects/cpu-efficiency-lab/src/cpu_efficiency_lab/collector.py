"""Read-only battery sensor capture and raw CSV output."""

from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Iterable, List, Optional

from .config import BATTERY_NODES, MAX_CAPTURE_DURATION_S, MIN_SAMPLE_INTERVAL_S, PROTOCOL_VERSION
from .models import BatterySample
from .power import battery_power_w


CSV_FIELDS = [
    "protocol_version",
    "sequence",
    "current_before_s",
    "current_now_uA",
    "current_after_s",
    "voltage_before_s",
    "voltage_now_uV",
    "voltage_after_s",
    "read_skew_s",
    "midpoint_s",
    "battery_temp_deci_c",
    "battery_status",
    "capacity_percent",
    "charge_counter_uAh",
    "battery_power_W",
]


def _interval_text(interval_s: float) -> str:
    return ("%.3f" % interval_s).rstrip("0").rstrip(".")


def build_sampler_script(sample_count: int, interval_s: float) -> str:
    if sample_count < 2:
        raise ValueError("sample_count must be at least 2")
    if not math.isfinite(interval_s):
        raise ValueError("interval_s must be finite")
    if interval_s < MIN_SAMPLE_INTERVAL_S:
        raise ValueError(
            "interval_s must be at least %.3f seconds" % MIN_SAMPLE_INTERVAL_S
        )
    if interval_s * (sample_count - 1) > MAX_CAPTURE_DURATION_S:
        raise ValueError("capture duration exceeds the configured maximum")

    interval = _interval_text(interval_s)
    return "\n".join(
        [
            "i=0",
            "while [ \"$i\" -lt %d ]; do" % sample_count,
            "  read -r ci0 _ < /proc/uptime || exit 21",
            "  read -r current < {current_now} || exit 22".format(**BATTERY_NODES),
            "  read -r ci1 _ < /proc/uptime || exit 23",
            "  read -r vi0 _ < /proc/uptime || exit 24",
            "  read -r voltage < {voltage_now} || exit 25".format(**BATTERY_NODES),
            "  read -r vi1 _ < /proc/uptime || exit 26",
            "  read -r temp < {temp} 2>/dev/null || temp=".format(**BATTERY_NODES),
            "  read -r status < {status} 2>/dev/null || status=Unknown".format(**BATTERY_NODES),
            "  read -r capacity < {capacity} 2>/dev/null || capacity=".format(**BATTERY_NODES),
            "  read -r charge < {charge_counter} 2>/dev/null || charge=".format(**BATTERY_NODES),
            "  printf 'S|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s\\n' \"$i\" \"$ci0\" \"$current\" \"$ci1\" \"$vi0\" \"$voltage\" \"$vi1\" \"$temp\" \"$status\" \"$capacity\" \"$charge\"",
            "  i=$((i + 1))",
            "  if [ \"$i\" -lt %d ]; then sleep %s; fi" % (sample_count, interval),
            "done",
        ]
    )


def _int_or_none(value: str) -> Optional[int]:
    value = value.strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError("invalid integer sensor field: %r" % value) from exc


def _float(value: str) -> float:
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError("invalid uptime field: %r" % value) from exc


def parse_samples(output: str) -> List[BatterySample]:
    samples: List[BatterySample] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line.startswith("S|"):
            continue
        fields = line.split("|")
        if len(fields) != 12:
            raise ValueError("malformed sample line with %d fields" % len(fields))
        samples.append(
            BatterySample(
                sequence=int(fields[1]),
                current_before_s=_float(fields[2]),
                current_ua=int(fields[3]),
                current_after_s=_float(fields[4]),
                voltage_before_s=_float(fields[5]),
                voltage_uv=int(fields[6]),
                voltage_after_s=_float(fields[7]),
                temp_deci_c=_int_or_none(fields[8]),
                status=fields[9] or "Unknown",
                capacity_percent=_int_or_none(fields[10]),
                charge_counter_uah=_int_or_none(fields[11]),
            )
        )
    return samples


def value_change_intervals(samples: Iterable[BatterySample], field_name: str) -> List[float]:
    """Return time between observed value transitions; not a sensor-rate guarantee."""
    rows = list(samples)
    if not rows:
        return []
    if field_name not in ("current_ua", "voltage_uv"):
        raise ValueError("unsupported sensor field: %s" % field_name)

    previous_value = getattr(rows[0], field_name)
    last_transition_s = None
    intervals = []
    for sample in rows[1:]:
        value = getattr(sample, field_name)
        if value != previous_value:
            if last_transition_s is not None:
                intervals.append(sample.midpoint_s - last_transition_s)
            last_transition_s = sample.midpoint_s
            previous_value = value
    return intervals


def sample_to_row(sample: BatterySample) -> dict:
    power = battery_power_w(sample.current_ua, sample.voltage_uv, sample.status)
    return {
        "protocol_version": PROTOCOL_VERSION,
        "sequence": sample.sequence,
        "current_before_s": "%.2f" % sample.current_before_s,
        "current_now_uA": sample.current_ua,
        "current_after_s": "%.2f" % sample.current_after_s,
        "voltage_before_s": "%.2f" % sample.voltage_before_s,
        "voltage_now_uV": sample.voltage_uv,
        "voltage_after_s": "%.2f" % sample.voltage_after_s,
        "read_skew_s": "%.3f" % sample.read_skew_s,
        "midpoint_s": "%.3f" % sample.midpoint_s,
        "battery_temp_deci_c": sample.temp_deci_c,
        "battery_status": sample.status,
        "capacity_percent": sample.capacity_percent,
        "charge_counter_uAh": sample.charge_counter_uah,
        "battery_power_W": "" if power is None else "%.9f" % power,
    }


def write_csv(path: Path, samples: Iterable[BatterySample]) -> int:
    rows = list(samples)
    if not rows:
        raise ValueError("refusing to write an empty capture")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for sample in rows:
            writer.writerow(sample_to_row(sample))
    return len(rows)
