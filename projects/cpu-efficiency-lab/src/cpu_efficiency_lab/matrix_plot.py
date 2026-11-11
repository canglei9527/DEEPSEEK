"""Compact dependency-free SVG views for topology/frequency matrix results."""

from __future__ import annotations

import html
import math
import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple


def _fmt(value, digits=2):
    if value is None or not math.isfinite(float(value)):
        return "n/a"
    return ("%%.%df" % digits) % float(value)


def _svg_text(x, y, text, size=12, anchor="middle", color="#1f2937"):
    return '<text x="%.1f" y="%.1f" text-anchor="%s" font-family="Arial,sans-serif" font-size="%d" fill="%s">%s</text>' % (
        x, y, anchor, size, color, html.escape(str(text))
    )


def _write_svg(path: Path, parts: Sequence[str]) -> None:
    if path.exists():
        raise FileExistsError("refusing to overwrite %s" % path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(parts) + "\n")


def pareto_rows(rows: Sequence[dict]) -> List[dict]:
    valid = [
        row for row in rows
        if row.get("valid")
        and row.get("load_power_w_median") is not None
        and row.get("operations_per_second_median") is not None
        and row["load_power_w_median"] > 0
        and row["operations_per_second_median"] > 0
    ]
    result = []
    for candidate in valid:
        cp = float(candidate["load_power_w_median"])
        ct = float(candidate["operations_per_second_median"])
        dominated = any(
            float(other["load_power_w_median"]) <= cp
            and float(other["operations_per_second_median"]) >= ct
            and (
                float(other["load_power_w_median"]) < cp
                or float(other["operations_per_second_median"]) > ct
            )
            for other in valid
            if other is not candidate
        )
        if not dominated:
            result.append(candidate)
    return sorted(result, key=lambda row: row["load_power_w_median"])


def render_matrix_pareto_svg(rows: Sequence[dict], path: Path) -> None:
    width, height = 1200, 760
    left, top, plot_w, plot_h = 100.0, 75.0, 980.0, 570.0
    valid = [
        row for row in rows
        if row.get("valid") and row.get("load_power_w_median") is not None
        and row.get("operations_per_second_median") is not None
        and row["load_power_w_median"] > 0 and row["operations_per_second_median"] > 0
    ]
    frontier = pareto_rows(valid)
    x_max = max((float(row["load_power_w_median"]) for row in valid), default=1.0) * 1.12
    y_max = max((float(row["operations_per_second_median"]) for row in valid), default=1.0) * 1.12
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d">' % (width, height, width, height),
        '<rect width="100%" height="100%" fill="#fff"/>',
        _svg_text(width / 2, 34, "Performance vs total battery-side power — topology/frequency matrix", 20, color="#111827"),
        _svg_text(width / 2, height - 25, "Whole-device battery draw (W); screen/background included", 14),
        '<text transform="translate(24 %.1f) rotate(-90)" text-anchor="middle" font-family="Arial,sans-serif" font-size="14">Scalar mix throughput (iterations/s)</text>' % (height / 2),
    ]
    for index in range(6):
        x = left + plot_w * index / 5.0
        y = top + plot_h - plot_h * index / 5.0
        parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#e5e7eb"/>' % (x, top, x, top + plot_h))
        parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#e5e7eb"/>' % (left, y, left + plot_w, y))
        parts.append(_svg_text(x, top + plot_h + 20, _fmt(x_max * index / 5.0), 11))
        parts.append(_svg_text(left - 12, y + 4, _fmt(y_max * index / 5.0), 11, anchor="end"))
    parts.append('<path d="M %.1f %.1f V %.1f H %.1f" fill="none" stroke="#374151" stroke-width="1.5"/>' % (left, top, top + plot_h, left + plot_w))
    for row in valid:
        x = left + float(row["load_power_w_median"]) / x_max * plot_w
        y = top + plot_h - float(row["operations_per_second_median"]) / y_max * plot_h
        color = "#2563eb" if row.get("stage") in ("B", "C") else "#7c3aed"
        parts.append('<circle cx="%.1f" cy="%.1f" r="4" fill="%s" opacity="0.4"/>' % (x, y, color))
    frontier_coords = []
    for row in frontier:
        x = left + float(row["load_power_w_median"]) / x_max * plot_w
        y = top + plot_h - float(row["operations_per_second_median"]) / y_max * plot_h
        frontier_coords.append((x, y))
    if len(frontier_coords) > 1:
        path_data = "M " + " L ".join("%.1f %.1f" % point for point in frontier_coords)
        parts.append('<path d="%s" fill="none" stroke="#dc2626" stroke-width="2.5"/>' % path_data)
    for row, (x, y) in zip(frontier, frontier_coords):
        parts.append('<circle cx="%.1f" cy="%.1f" r="6" fill="#dc2626" stroke="#fff" stroke-width="1.5"/>' % (x, y))
        parts.append(_svg_text(x + 8, y - 8, row.get("config_id", ""), 9, anchor="start", color="#7f1d1d"))
    if not valid:
        parts.append(_svg_text(width / 2, height / 2, "No instrument-valid configurations", 18, color="#b91c1c"))
    parts.append(_svg_text(width / 2, height - 48, "Pareto frontier: no valid point is both slower and higher-power than another measured point.", 11, color="#6b7280"))
    parts.append("</svg>")
    _write_svg(path, parts)


def efficiency_plot_rows(rows: Sequence[dict], incremental: bool = False) -> List[dict]:
    """Select denominator and validity together; never reuse total validity for net power."""
    if not incremental:
        return list(rows)
    result = []
    for row in rows:
        copy = dict(row)
        copy['valid'] = bool(row.get('valid') and row.get('required_repetitions', 0) > 0
                             and row.get('valid_incremental_repetitions') == row.get('required_repetitions'))
        copy['operations_per_total_joule_median'] = row.get('operations_per_incremental_joule_median')
        copy['operations_per_total_joule_min'] = None
        copy['operations_per_total_joule_max'] = None
        result.append(copy)
    return result


def render_single_core_frequency_curves(rows: Sequence[dict], path: Path, incremental: bool = False) -> None:
    """Plot fixed-frequency single-worker efficiency, breaking lines at invalid points."""
    rows = efficiency_plot_rows(rows, incremental)
    groups: Dict[str, Dict[int, Dict[int, dict]]] = {"policy0": {}, "policy6": {}}
    for row in rows:
        if row.get("stage") != "B":
            continue
        match = re.fullmatch(r"B-CPU([0-9]+)-F[0-9]+-[0-9]+", row.get("config_id", ""))
        if not match:
            continue
        cpu = int(match.group(1))
        policy = "policy0" if row.get("policy0_frequency_khz") is not None else "policy6"
        freq = row.get(policy + "_frequency_khz")
        if freq is not None:
            groups[policy].setdefault(cpu, {})[int(freq)] = row
    scores = [float(row["operations_per_total_joule_median"]) / 1e6
              for group in groups.values() for points in group.values() for row in points.values()
              if row.get("valid") and row.get("operations_per_total_joule_median") is not None
              and math.isfinite(float(row["operations_per_total_joule_median"]))]
    ranges = [float(row['operations_per_total_joule_max']) / 1e6
              for group in groups.values() for points in group.values() for row in points.values()
              if row.get('valid') and row.get('operations_per_total_joule_max') is not None
              and math.isfinite(float(row['operations_per_total_joule_max']))]
    ymax = max(scores + ranges, default=1.0) * 1.12
    if ymax <= 0:
        ymax = 1.0
    width, height = 1600, 720
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d">' % (width, height, width, height),
        '<rect width="100%" height="100%" fill="#fff"/>',
        _svg_text(width / 2, 34, ('Incremental' if incremental else 'Total') + " fixed-frequency single-worker efficiency: CPU0–CPU7", 21, color="#111827"),
        _svg_text(width / 2, 58, "Only points with all required valid repetitions are shown; gaps are not connected.", 12, color="#475569"),
        _svg_text(width / 2, height - 20, "Whole-device battery-side power, not CPU rail. CPU0 remains online during CPU1–CPU7 tests.", 12, color="#475569"),
    ]
    colors = ("#2563eb", "#dc2626", "#059669", "#9333ea", "#d97706", "#0891b2", "#be123c", "#4b5563")
    top, plot_h, plot_w = 110.0, 450.0, 650.0
    for panel, policy in enumerate(("policy0", "policy6")):
        left = 100.0 + 760.0 * panel
        group = groups[policy]
        levels = sorted({freq for points in group.values() for freq in points})
        parts.append(_svg_text(left + plot_w / 2, 88, "%s (%s)" % (policy, "E workers" if policy == "policy0" else "P workers"), 16, color="#111827"))
        for tick in range(6):
            y = top + plot_h - plot_h * tick / 5.0
            parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#e5e7eb"/>' % (left, y, left + plot_w, y))
            parts.append(_svg_text(left - 10, y + 4, _fmt(ymax * tick / 5.0, 1), 10, anchor="end"))
        if not levels:
            parts.append(_svg_text(left + plot_w / 2, top + plot_h / 2, "No measurements", 14, color="#b91c1c"))
            continue
        low, high = min(levels), max(levels)
        span = max(1, high - low)
        def xpos(freq: int) -> float:
            return left + plot_w * (freq - low) / span if high != low else left + plot_w / 2
        for freq in levels:
            x = xpos(freq)
            parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#f1f5f9"/>' % (x, top, x, top + plot_h))
            parts.append(_svg_text(x, top + plot_h + 18, _fmt(freq / 1e6, 2), 9))
        for cpu, points in sorted(group.items()):
            previous = None
            color = colors[cpu % len(colors)]
            for freq in levels:
                row = points.get(freq)
                value = row.get("operations_per_total_joule_median") if row else None
                if not row or not row.get("valid") or value is None or not math.isfinite(float(value)):
                    previous = None
                    continue
                x, y = xpos(freq), top + plot_h - float(value) / 1e6 / ymax * plot_h
                if previous:
                    parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" stroke-width="2"/>' % (previous[0], previous[1], x, y, color))
                title = "CPU%d; %d kHz; %.2f M iterations/J; %s valid repetitions" % (cpu, freq, float(value) / 1e6, row.get("measured_repetitions", "?"))
                lo, hi = row.get('operations_per_total_joule_min'), row.get('operations_per_total_joule_max')
                if lo is not None and hi is not None and math.isfinite(float(lo)) and math.isfinite(float(hi)):
                    ylo = top + plot_h - float(lo) / 1e6 / ymax * plot_h
                    yhi = top + plot_h - float(hi) / 1e6 / ymax * plot_h
                    parts.append('<line class="repeat-range" x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" stroke-width="2"/>' % (x, ylo, x, yhi, color))
                parts.append('<circle cx="%.1f" cy="%.1f" r="4.5" fill="%s"><title>%s</title></circle>' % (x, y, color, html.escape(title)))
                previous = (x, y)
        parts.append(_svg_text(left + plot_w / 2, top + plot_h + 45, "Locked frequency (GHz)", 13))
        for index, cpu in enumerate(sorted(group)):
            x = left + 65 + (index % 6) * 100
            y = top + plot_h + 76 + (index // 6) * 22
            parts.append('<circle cx="%.1f" cy="%.1f" r="4.5" fill="%s"/>' % (x, y - 4, colors[cpu % len(colors)]))
            parts.append(_svg_text(x + 11, y, "CPU%d" % cpu, 11, anchor="start"))
    parts.append('<text transform="translate(24 %.1f) rotate(-90)" text-anchor="middle" font-family="Arial,sans-serif" font-size="13">Million iterations per %s battery joule</text>' % (top + plot_h / 2, 'incremental' if incremental else 'total'))
    parts.append("</svg>")
    _write_svg(path, parts)


def render_mixed_frequency_heatmap(rows: Sequence[dict], path: Path,
                                   panels: Optional[Sequence[Tuple[int, int]]] = None) -> None:
    """Render the full E×P grid with one comparable color scale across all panels."""
    groups: Dict[Tuple[int, int], Dict[Tuple[int, int], dict]] = {}
    for row in rows:
        if row.get("stage") != "D":
            continue
        match = re.fullmatch(r"D-E([0-9]+)P([0-9]+)-EF([0-9]+)-PF([0-9]+)", row.get("config_id", ""))
        if not match:
            continue
        e_count, p_count, e_freq, p_freq = (int(item) for item in match.groups())
        groups.setdefault((e_count, p_count), {})[(e_freq, p_freq)] = row
    all_values = [float(row["operations_per_total_joule_median"]) / 1e6
                  for group in groups.values() for row in group.values()
                  if row.get("valid") and row.get("operations_per_total_joule_median") is not None
                  and math.isfinite(float(row["operations_per_total_joule_median"]))]
    global_min = min(all_values) if all_values else 0.0
    global_max = max(all_values) if all_values else 1.0
    expected = tuple(panels) if panels is not None else ((1, 1), (2, 1), (4, 1), (6, 1), (4, 2), (6, 2))
    width, height = 1600, 140 + 430 * max(1, (len(expected) + 1) // 2)
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d">' % (width, height, width, height),
        '<rect width="100%" height="100%" fill="#fff"/>',
        _svg_text(width / 2, 34, "Fixed E/P frequency combinations: whole-device battery efficiency", 21, color="#111827"),
        _svg_text(width / 2, 58, "Values: million iterations/J. Both required repetitions must be valid; gray cells are missing or invalid.", 12, color="#475569"),
    ]
    for panel_index, (e_count, p_count) in enumerate(expected):
        col, panel_row = panel_index % 2, panel_index // 2
        x0, y0 = 25 + col * 785, 78 + panel_row * 430
        entries = groups.get((e_count, p_count), {})
        e_levels = sorted({key[0] for key in entries})
        p_levels = sorted({key[1] for key in entries})
        parts.append(_svg_text(x0 + 380, y0 + 18, "E%d + P%d workers" % (e_count, p_count), 16, color="#111827"))
        if not e_levels or not p_levels:
            parts.append(_svg_text(x0 + 380, y0 + 180, "No measurements", 13, color="#b91c1c"))
            continue
        grid_x, grid_y = x0 + 120.0, y0 + 65.0
        cell_w = min(80.0, 610.0 / len(p_levels))
        cell_h = min(32.0, 310.0 / len(e_levels))
        parts.append(_svg_text(grid_x - 55, grid_y - 16, "E GHz", 10))
        parts.append(_svg_text(grid_x + len(p_levels) * cell_w / 2, grid_y - 34, "P locked frequency (GHz)", 10))
        for x_index, p_freq in enumerate(p_levels):
            parts.append(_svg_text(grid_x + (x_index + .5) * cell_w, grid_y - 9, _fmt(p_freq / 1e6, 3), 9))
        for y_index, e_freq in enumerate(e_levels):
            parts.append(_svg_text(grid_x - 12, grid_y + (y_index + .5) * cell_h + 3, _fmt(e_freq / 1e6, 3), 9, anchor="end"))
            for x_index, p_freq in enumerate(p_levels):
                row = entries.get((e_freq, p_freq))
                value = row.get("operations_per_total_joule_median") if row and row.get("valid") else None
                if value is None or not math.isfinite(float(value)):
                    fill, label, color = "#f1f5f9", "n/a", "#64748b"
                else:
                    score = float(value) / 1e6
                    ratio = .5 if global_max <= global_min else (score - global_min) / (global_max - global_min)
                    fill = "#%02x%02x%02x" % (int(240 - 150 * ratio), int(247 - 110 * ratio), int(255 - 45 * ratio))
                    label, color = _fmt(score, 1), "#111827"
                x, y = grid_x + x_index * cell_w, grid_y + y_index * cell_h
                title = "E%d P%d; E=%d kHz; P=%d kHz; efficiency=%s M iterations/J" % (e_count, p_count, e_freq, p_freq, label)
                parts.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="%s" stroke="#fff" stroke-width="1"><title>%s</title></rect>' % (x, y, cell_w, cell_h, fill, html.escape(title)))
                parts.append(_svg_text(x + cell_w / 2, y + cell_h / 2 + 4, label, 10, color=color))
    parts.append(_svg_text(width / 2, height - 42, "Common color scale across shown panels: %s to %s million iterations/J." % (_fmt(global_min, 1), _fmt(global_max, 1)), 12, color="#475569"))
    parts.append(_svg_text(width / 2, height - 20, "Whole-device battery-side power; CPU0 remains online where it is not a worker.", 12, color="#475569"))
    parts.append("</svg>")
    _write_svg(path, parts)
