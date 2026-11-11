"""Dependency-free SVG plots for CPU performance/power data."""

from __future__ import annotations

import html
import math
from pathlib import Path
from typing import Dict, List, Sequence


COLORS = {"policy0": "#2563eb", "policy6": "#dc2626"}


def _number(value, digits: int = 2) -> str:
    if value is None or not math.isfinite(float(value)):
        return "n/a"
    return ("%%.%df" % digits) % float(value)


def _svg_text(x: float, y: float, value: str, size: int = 13, anchor: str = "middle", color: str = "#1f2937") -> str:
    return '<text x="%.1f" y="%.1f" text-anchor="%s" font-family="Arial,sans-serif" font-size="%d" fill="%s">%s</text>' % (
        x, y, anchor, size, color, html.escape(value)
    )


def _map(value: float, low: float, high: float, start: float, span: float) -> float:
    if high <= low:
        return start + span * 0.5
    return start + (value - low) / (high - low) * span


def _base_svg(title: str, x_label: str, y_label: str, width: int, height: int) -> List[str]:
    return [
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d">' % (width, height, width, height),
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        _svg_text(width / 2, 32, title, 20, color="#111827"),
        _svg_text(width / 2, height - 20, x_label, 14),
        '<text transform="translate(22 %.1f) rotate(-90)" text-anchor="middle" font-family="Arial,sans-serif" font-size="14" fill="#1f2937">%s</text>'
        % (height / 2, html.escape(y_label)),
    ]


def _render_performance_power_svg(
    rows: Sequence[dict], path: Path, power_field: str, power_sd_field: str,
    validity_field: str, title: str, x_label: str, empty_message: str, footnote: str,
) -> None:
    width, height = 1100, 700
    left, top, plot_w, plot_h = 95.0, 70.0, 900.0, 520.0
    data = [
        row for row in rows
        if row.get(validity_field)
        and row.get(power_field) is not None
        and row.get("operations_per_second_median") is not None
        and row[power_field] > 0
        and row["operations_per_second_median"] > 0
    ]
    x_values = [float(row[power_field]) for row in data]
    y_values = [float(row["operations_per_second_median"]) for row in data]
    x_max = max(x_values) * 1.12 if x_values else 1.0
    y_max = max(y_values) * 1.12 if y_values else 1.0
    parts = _base_svg(
        title,
        x_label,
        "Scalar mix throughput (iterations/s)",
        width,
        height,
    )
    for index in range(6):
        x = left + plot_w * index / 5.0
        y = top + plot_h - plot_h * index / 5.0
        parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#e5e7eb"/>' % (x, top, x, top + plot_h))
        parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#e5e7eb"/>' % (left, y, left + plot_w, y))
        parts.append(_svg_text(x, top + plot_h + 20, _number(x_max * index / 5.0), 11))
        parts.append(_svg_text(left - 12, y + 4, _number(y_max * index / 5.0), 11, anchor="end"))
    parts.append('<path d="M %.1f %.1f V %.1f H %.1f" fill="none" stroke="#374151" stroke-width="1.5"/>' % (left, top, top + plot_h, left + plot_w))

    for row in data:
        x_val = float(row[power_field])
        y_val = float(row["operations_per_second_median"])
        x = _map(x_val, 0.0, x_max, left, plot_w)
        y = top + plot_h - _map(y_val, 0.0, y_max, 0.0, plot_h)
        color = COLORS.get(row["policy"], "#7c3aed")
        x_err = row.get(power_sd_field) or 0.0
        y_err = row.get("operations_per_second_sd") or 0.0
        dx = plot_w * float(x_err) / x_max
        dy = plot_h * float(y_err) / y_max
        parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" stroke-width="1.2"/>' % (x - dx, y, x + dx, y, color))
        parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" stroke-width="1.2"/>' % (x, y - dy, x, y + dy, color))
        parts.append('<circle cx="%.1f" cy="%.1f" r="6" fill="%s" stroke="#ffffff" stroke-width="1.5"/>' % (x, y, color))
        parts.append(_svg_text(x, y - 10, "%.2fGHz" % (row["target_frequency_khz"] / 1e6), 10, color=color))

    legend_y = top + 18
    for index, policy in enumerate(sorted({row["policy"] for row in data})):
        color = COLORS.get(policy, "#7c3aed")
        legend_x = left + 12 + index * 150
        parts.append('<circle cx="%.1f" cy="%.1f" r="5" fill="%s"/>' % (legend_x, legend_y, color))
        parts.append(_svg_text(legend_x + 42, legend_y + 4, policy, 12, anchor="start"))
    if not data:
        parts.append(_svg_text(width / 2, height / 2, empty_message, 16, color="#b91c1c"))
    parts.append(_svg_text(width / 2, height - 42, footnote, 11, color="#6b7280"))
    parts.append("</svg>")
    _write_svg(path, parts)


def render_performance_power_svg(rows: Sequence[dict], path: Path) -> None:
    _render_performance_power_svg(
        rows,
        path,
        "incremental_power_w_median",
        "incremental_power_w_sd",
        "incremental_valid",
        "Performance vs incremental battery-side power",
        "Incremental whole-device battery power (W)",
        "No valid positive incremental-power points",
        "Median of instrument-valid repetitions; error bars = 1 SD. Custom workload; whole-device net power, not CPU rail.",
    )


def render_total_battery_power_svg(rows: Sequence[dict], path: Path) -> None:
    _render_performance_power_svg(
        rows,
        path,
        "load_power_w_median",
        "load_power_w_sd",
        "measurement_valid",
        "Performance vs total battery-side power",
        "Total whole-device battery draw (W)",
        "No valid battery-side power measurements",
        "Median of instrument-valid repetitions; error bars = 1 SD. Total device draw includes screen, memory and background activity.",
    )


def _render_efficiency_svg(
    rows: Sequence[dict], path: Path, metric_field: str, validity_field: str,
    title: str, y_label: str, footnote: str,
) -> None:
    width, height = 1100, 700
    left, top, plot_w, plot_h = 95.0, 70.0, 900.0, 520.0
    data = [
        row for row in rows
        if row.get(validity_field)
        and row.get(metric_field) is not None
        and row[metric_field] > 0
    ]
    x_values = [float(row["target_frequency_khz"]) / 1e6 for row in data]
    y_values = [float(row[metric_field]) for row in data]
    x_min = min(x_values) * 0.95 if x_values else 0.0
    x_max = max(x_values) * 1.05 if x_values else 1.0
    y_max = max(y_values) * 1.12 if y_values else 1.0
    parts = _base_svg(
        title,
        "Target policy frequency (GHz)",
        y_label,
        width,
        height,
    )
    for index in range(6):
        x_val = x_min + (x_max - x_min) * index / 5.0
        x = _map(x_val, x_min, x_max, left, plot_w)
        y = top + plot_h - plot_h * index / 5.0
        parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#e5e7eb"/>' % (x, top, x, top + plot_h))
        parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#e5e7eb"/>' % (left, y, left + plot_w, y))
        parts.append(_svg_text(x, top + plot_h + 20, _number(x_val, 2), 11))
        parts.append(_svg_text(left - 12, y + 4, _number(y_max * index / 5.0, 1), 11, anchor="end"))
    parts.append('<path d="M %.1f %.1f V %.1f H %.1f" fill="none" stroke="#374151" stroke-width="1.5"/>' % (left, top, top + plot_h, left + plot_w))
    for policy in sorted({row["policy"] for row in data}):
        cluster = sorted((row for row in data if row["policy"] == policy), key=lambda row: row["target_frequency_khz"])
        color = COLORS.get(policy, "#7c3aed")
        coords = []
        for row in cluster:
            x_val = float(row["target_frequency_khz"]) / 1e6
            y_val = float(row[metric_field])
            x = _map(x_val, x_min, x_max, left, plot_w)
            y = top + plot_h - _map(y_val, 0.0, y_max, 0.0, plot_h)
            coords.append((x, y))
            parts.append('<circle cx="%.1f" cy="%.1f" r="6" fill="%s" stroke="#ffffff" stroke-width="1.5"/>' % (x, y, color))
        if len(coords) > 1:
            path_data = "M " + " L ".join("%.1f %.1f" % coord for coord in coords)
            parts.append('<path d="%s" fill="none" stroke="%s" stroke-width="1.7" opacity="0.75"/>' % (path_data, color))
    legend_y = top + 18
    for index, policy in enumerate(sorted({row["policy"] for row in data})):
        color = COLORS.get(policy, "#7c3aed")
        legend_x = left + 12 + index * 150
        parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" stroke-width="2"/>' % (legend_x - 8, legend_y, legend_x + 8, legend_y, color))
        parts.append(_svg_text(legend_x + 42, legend_y + 4, policy, 12, anchor="start"))
    if not data:
        parts.append(_svg_text(width / 2, height / 2, "No valid efficiency points", 16, color="#b91c1c"))
    parts.append(_svg_text(width / 2, height - 42, footnote, 11, color="#6b7280"))
    parts.append("</svg>")
    _write_svg(path, parts)


def render_efficiency_svg(rows: Sequence[dict], path: Path) -> None:
    _render_efficiency_svg(
        rows,
        path,
        "operations_per_joule_median",
        "incremental_valid",
        "Incremental-work efficiency by CPU frequency",
        "Incremental-work efficiency (iterations/J)",
        "Positive load-minus-idle battery power only; no external calibration; custom workload, not Geekbench-comparable.",
    )


def render_total_efficiency_svg(rows: Sequence[dict], path: Path) -> None:
    _render_efficiency_svg(
        rows,
        path,
        "operations_per_total_joule_median",
        "measurement_valid",
        "Whole-device energy efficiency by CPU frequency",
        "Throughput per total battery energy (iterations/J)",
        "Includes the whole device baseline (screen, memory and background activity); not CPU-only efficiency.",
    )


def _write_svg(path: Path, parts: Sequence[str]) -> None:
    if path.exists():
        raise FileExistsError("refusing to overwrite %s" % path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(parts))
        handle.write("\n")
