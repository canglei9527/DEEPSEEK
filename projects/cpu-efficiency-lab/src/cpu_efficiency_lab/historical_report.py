"""Read historical measurements and create a descriptive offline report."""

from __future__ import annotations

import argparse
import csv
import html
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List


def _number(row: dict, name: str, positive: bool = True) -> float:
    try:
        value = float(row[name])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("invalid or missing %s" % name) from exc
    if not math.isfinite(value) or (positive and value <= 0):
        raise ValueError("invalid %s: %r" % (name, value))
    return value


def _integer(row: dict, name: str, minimum: int = 0) -> int:
    value = _number(row, name, positive=False)
    if value < minimum or value != int(value):
        raise ValueError("invalid %s: %r" % (name, value))
    return int(value)


def _read_rows(path: Path) -> List[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise ValueError("invalid header in %s" % path)
        rows = list(reader)
    if not rows:
        raise ValueError("no measurements in %s" % path)
    if any(None in row for row in rows):
        raise ValueError("extra columns in %s" % path)
    return rows


def load_single(path: Path) -> List[dict]:
    result = []
    for line, row in enumerate(_read_rows(path), 2):
        try:
            ops = _number(row, "ops_per_s")
            power = _number(row, "avg_w")
            result.append({"line": line, "policy": _integer(row, "policy"),
                           "freq_khz": _integer(row, "freq_khz", 1),
                           "core": _integer(row, "core"),
                           "ops_per_s": ops, "avg_w": power,
                           "duration_ms": _number(row, "ms"),
                           "power_samples": _integer(row, "samples", 1),
                           "battery_nj_per_op": power * 1e9 / ops})
        except ValueError as exc:
            raise ValueError("%s:%d: %s" % (path, line, exc)) from exc
    return result


def load_combo(path: Path) -> List[dict]:
    result = []
    for line, row in enumerate(_read_rows(path), 2):
        try:
            ops = _number(row, "total_ops_per_s")
            power = _number(row, "avg_w")
            result.append({"line": line,
                           "freq_small_khz": _integer(row, "freq_small_khz", 1),
                           "freq_big_khz": _integer(row, "freq_big_khz", 1),
                           "total_ops_per_s": ops, "avg_w": power,
                           "duration_ms": _number(row, "ms"),
                           "power_samples": _integer(row, "samples", 1),
                           "battery_nj_per_op": power * 1e9 / ops})
        except ValueError as exc:
            raise ValueError("%s:%d: %s" % (path, line, exc)) from exc
    return result


def _svg_start(width: int, height: int, title: str, subtitle: str) -> List[str]:
    return ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d">' % (width, height, width, height),
            '<rect width="100%" height="100%" fill="#111827"/>',
            '<g font-family="sans-serif" fill="#f3f4f6">',
            '<text x="28" y="32" font-size="20">%s</text>' % html.escape(title),
            '<text x="28" y="57" font-size="13" fill="#cbd5e1">%s</text>' % html.escape(subtitle)]


def single_svg(rows: List[dict]) -> str:
    policies = sorted({row["policy"] for row in rows})
    width, panel_height = 1100, 350
    height = 95 + panel_height * len(policies)
    out = _svg_start(width, height, "历史单核测点：整机电池侧能耗",
                     "每点为一次记录，不连接测点；不同核心不是同核重复。无独立 CPU 电源轨。")
    colors = ["#60a5fa", "#34d399", "#fb923c", "#f472b6", "#c084fc", "#facc15", "#22d3ee", "#f87171"]
    for index, policy in enumerate(policies):
        points = [row for row in rows if row["policy"] == policy]
        top, left, plot_width, plot_height = 90 + index * panel_height, 85, 850, 245
        frequencies = [p["freq_khz"] / 1000 for p in points]
        values = [p["battery_nj_per_op"] for p in points]
        xmin, xmax, ymin, ymax = min(frequencies), max(frequencies), min(values), max(values)
        if xmax == xmin:
            xmin, xmax = xmin - 1, xmax + 1
        if ymax == ymin:
            ymin, ymax = max(0, ymin - 1), ymax + 1
        out.append('<text x="%d" y="%d" font-size="15">policy%d · %d 条记录</text>' % (left, top - 10, policy, len(points)))
        for tick in range(5):
            x = left + plot_width * tick / 4
            y = top + plot_height * tick / 4
            out.append('<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="#334155"/>' % (left, y, left + plot_width, y))
            out.append('<text x="%s" y="%s" text-anchor="end" font-size="11">%.2f</text>' % (left - 8, y + 4, ymax - (ymax - ymin) * tick / 4))
            out.append('<text x="%s" y="%s" text-anchor="middle" font-size="11">%.0f</text>' % (x, top + plot_height + 20, xmin + (xmax - xmin) * tick / 4))
        out.append('<text x="%d" y="%d" font-size="12">横轴 MHz · 纵轴 nJ/op（整机电池侧）</text>' % (left, top + plot_height + 42))
        for point in points:
            x = left + (point["freq_khz"] / 1000 - xmin) / (xmax - xmin) * plot_width
            y = top + (ymax - point["battery_nj_per_op"]) / (ymax - ymin) * plot_height
            tooltip = "core%d %.1f MHz %.3f nJ/op %.3f W %d power samples" % (point["core"], point["freq_khz"] / 1000, point["battery_nj_per_op"], point["avg_w"], point["power_samples"])
            out.append('<circle cx="%.2f" cy="%.2f" r="4" fill="%s"><title>%s</title></circle>' % (x, y, colors[point["core"] % len(colors)], html.escape(tooltip)))
        for i, core in enumerate(sorted({p["core"] for p in points})):
            out.append('<text x="950" y="%d" font-size="12" fill="%s">● CPU%d</text>' % (top + 18 * i + 10, colors[core % len(colors)], core))
    return "\n".join(out + ["</g></svg>"])


def combo_svg(rows: List[dict]) -> str:
    small = sorted({r["freq_small_khz"] for r in rows})
    big = sorted({r["freq_big_khz"] for r in rows}, reverse=True)
    grouped: Dict[tuple, list] = defaultdict(list)
    for row in rows:
        grouped[(row["freq_small_khz"], row["freq_big_khz"])].append(row)
    cell_w, cell_h, left, top = 125, 70, 110, 105
    width, height = left + len(small) * cell_w + 25, top + len(big) * cell_h + 70
    out = _svg_start(width, height, "历史八核组合测点覆盖",
                     "整机电池侧 nJ/op；空格表示未测，不插值。数字为每次记录，不代表最优配置。")
    for i, freq in enumerate(small):
        out.append('<text x="%d" y="90" text-anchor="middle" font-size="12">%.1f</text>' % (left + i * cell_w + cell_w / 2, freq / 1000))
    for j, freq in enumerate(big):
        out.append('<text x="%d" y="%d" text-anchor="end" font-size="12">%.1f</text>' % (left - 8, top + j * cell_h + 25, freq / 1000))
        for i, sf in enumerate(small):
            x, y = left + i * cell_w, top + j * cell_h
            cell = grouped.get((sf, freq), [])
            out.append('<rect x="%d" y="%d" width="%d" height="%d" fill="%s" stroke="#334155"/>' % (x, y, cell_w - 3, cell_h - 3, "#164e63" if cell else "#1f2937"))
            if not cell:
                out.append('<text x="%d" y="%d" text-anchor="middle" font-size="12">未测</text>' % (x + cell_w / 2, y + 37))
            else:
                for k, point in enumerate(cell):
                    text = "%.3f" % point["battery_nj_per_op"]
                    tooltip = "%.3f W; %.2f Mop/s; %d samples" % (point["avg_w"], point["total_ops_per_s"] / 1e6, point["power_samples"])
                    out.append('<text x="%d" y="%.2f" text-anchor="middle" font-size="%d"><title>%s</title>%s</text>' % (x + cell_w / 2, y + (k + 1) * (cell_h - 6) / (len(cell) + 1) + 4, max(5, min(13, int((cell_h - 6) / len(cell)))), html.escape(tooltip), text))
    out.append('<text x="%d" y="%d" font-size="12">横轴 policy0 MHz · 纵轴 policy6 MHz</text>' % (left, height - 20))
    return "\n".join(out + ["</g></svg>"])


def build_report(single_path: Path, combo_path: Path, output: Path) -> None:
    single, combo = load_single(single_path), load_combo(combo_path)
    grouped = Counter((r["policy"], r["freq_khz"]) for r in single)
    cores = defaultdict(set)
    for row in single:
        cores[(row["policy"], row["freq_khz"])].add(row["core"])
    small = sorted({r["freq_small_khz"] for r in combo})
    big = sorted({r["freq_big_khz"] for r in combo})
    measured = {(r["freq_small_khz"], r["freq_big_khz"]) for r in combo}
    report = {"measurement_scope": "whole_device_battery_power",
              "limitations": ["historical data; sensor and execution validity not independently established",
                              "different cores are not repeated trials of the same core",
                              "no independent CPU power rail; no optimal-frequency inference"],
              "sources": {"single": str(single_path), "combo": str(combo_path)},
              "single_measurement_count": len(single), "combo_measurement_count": len(combo),
              "single_coverage": [{"policy": key[0], "freq_khz": key[1], "record_count": grouped[key],
                                   "cores": sorted(cores[key])} for key in sorted(grouped)],
              "combo_missing_pairs_khz": [[sf, bf] for sf in small for bf in big if (sf, bf) not in measured],
              "single_measurements": single, "combo_measurements": combo}
    contents = {"report.json": json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                "single.svg": single_svg(single), "combo.svg": combo_svg(combo)}
    output.mkdir(parents=True, exist_ok=False)
    for name, content in contents.items():
        with (output / name).open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(content)


def main() -> None:
    parser = argparse.ArgumentParser(description="Create an offline historical measurement report")
    parser.add_argument("--single", required=True, type=Path)
    parser.add_argument("--combo", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path, help="new output directory")
    args = parser.parse_args()
    build_report(args.single, args.combo, args.output)
    print("Report created in %s" % args.output)


if __name__ == "__main__":
    main()
