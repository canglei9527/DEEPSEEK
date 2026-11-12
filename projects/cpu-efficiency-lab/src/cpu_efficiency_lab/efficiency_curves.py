"""Reproducible efficiency-curve plotting from measured TSV data.

Pure standard-library. Reads the single-core (``eeff.tsv``) and multi-core
combination (``eeff_combo.tsv``) measurement tables produced by the lab and
renders dependency-free SVG curves: per-policy nJ/op vs frequency, throughput
vs frequency, and a multi-core efficiency scatter. Each point is the median
over repeated measurements. nJ/op is whole-device battery-side energy per
operation (lower is better); it is NOT CPU-rail power.
"""

from __future__ import annotations

import csv
import statistics
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class SinglePoint:
    policy: int
    freq_khz: int
    nj_per_op: float
    mops: float
    samples: int

    @property
    def ghz(self) -> float:
        return self.freq_khz / 1e6


@dataclass(frozen=True)
class ComboPoint:
    freq_small_khz: int
    freq_big_khz: int
    mops: float
    avg_w: float
    nj_per_op: float


def parse_single_core_tsv(text: str) -> List[SinglePoint]:
    """Aggregate repeated rows into one median point per (policy, freq)."""
    nj: Dict[Tuple[int, int], List[float]] = defaultdict(list)
    ops: Dict[Tuple[int, int], List[float]] = defaultdict(list)
    n: Dict[Tuple[int, int], int] = defaultdict(int)
    reader = csv.DictReader(text.splitlines(), delimiter="\t")
    required = {"policy", "freq_khz", "ops_per_s", "nj_per_op"}
    if reader.fieldnames is None or not required.issubset(set(reader.fieldnames)):
        raise ValueError("single-core TSV missing required columns: %s" % sorted(required))
    for row in reader:
        try:
            pol = int(row["policy"])
            freq = int(row["freq_khz"])
            nj_val = float(row["nj_per_op"])
            ops_val = float(row["ops_per_s"])
        except (TypeError, ValueError):
            continue
        nj[(pol, freq)].append(nj_val)
        ops[(pol, freq)].append(ops_val)
        n[(pol, freq)] += 1
    points: List[SinglePoint] = []
    for key in sorted(nj):
        pol, freq = key
        points.append(
            SinglePoint(
                policy=pol,
                freq_khz=freq,
                nj_per_op=statistics.median(nj[key]),
                mops=statistics.median(ops[key]) / 1e6,
                samples=n[key],
            )
        )
    return points


def parse_combo_tsv(text: str) -> List[ComboPoint]:
    reader = csv.DictReader(text.splitlines(), delimiter="\t")
    required = {"freq_small_khz", "freq_big_khz", "total_ops_per_s", "avg_w", "nj_per_op"}
    if reader.fieldnames is None or not required.issubset(set(reader.fieldnames)):
        raise ValueError("combo TSV missing required columns: %s" % sorted(required))
    points: List[ComboPoint] = []
    for row in reader:
        try:
            points.append(
                ComboPoint(
                    freq_small_khz=int(row["freq_small_khz"]),
                    freq_big_khz=int(row["freq_big_khz"]),
                    mops=float(row["total_ops_per_s"]) / 1e6,
                    avg_w=float(row["avg_w"]),
                    nj_per_op=float(row["nj_per_op"]),
                )
            )
        except (TypeError, ValueError):
            continue
    return points


# ---- SVG helpers -----------------------------------------------------------

_PALETTE = ("#2563eb", "#dc2626", "#059669", "#9333ea", "#d97706", "#0891b2")


def _fmt(v: float, digits: int = 2) -> str:
    return ("%%.%df" % digits) % v


def _text(x, y, s, size=12, anchor="middle", color="#1f2937", weight=None):
    w = ' font-weight="%s"' % weight if weight else ""
    return (
        '<text x="%.1f" y="%.1f" text-anchor="%s" font-family="Arial,sans-serif" '
        'font-size="%d" fill="%s"%s>%s</text>' % (x, y, anchor, size, color, w, s)
    )


def _frame(x, y, w, h):
    return '<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="none" stroke="#9ca3af"/>' % (x, y, w, h)


def _write_svg(path: Path, width: int, height: int, body: Sequence[str]) -> None:
    if path.exists():
        raise FileExistsError("refusing to overwrite %s" % path)
    path.parent.mkdir(parents=True, exist_ok=True)
    head = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d">'
        % (width, height, width, height)
    )
    parts = [head, '<rect width="100%" height="100%" fill="#ffffff"/>'] + list(body) + ["</svg>"]
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(parts) + "\n")


def render_single_core_svg(points: Sequence[SinglePoint], path: Path) -> None:
    """nJ/op vs frequency, one line per cpufreq policy."""
    width, height = 1000, 560
    left, top, pw, ph = 80.0, 60.0, 880.0, 420.0
    by_policy: Dict[int, List[SinglePoint]] = defaultdict(list)
    for p in points:
        by_policy[p.policy].append(p)
    if not by_policy:
        raise ValueError("no single-core points to plot")
    for series in by_policy.values():
        series.sort(key=lambda p: p.freq_khz)
    all_ghz = [p.ghz for p in points]
    xmax = max(all_ghz) * 1.04
    ymax = max(p.nj_per_op for p in points) * 1.1
    body = [
        _text(width / 2, 30, "Single-core efficiency: nJ/op vs frequency (lower is better)", 18, weight="500"),
    ]
    for i in range(6):
        gx = left + pw * i / 5.0
        gy = top + ph - ph * i / 5.0
        body.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#e5e7eb"/>' % (gx, top, gx, top + ph))
        body.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#e5e7eb"/>' % (left, gy, left + pw, gy))
        body.append(_text(gx, top + ph + 20, _fmt(xmax * i / 5.0, 2) + "G", 11))
        body.append(_text(left - 12, gy + 4, _fmt(ymax * i / 5.0, 1), 11, anchor="end"))

    def sx(ghz):
        return left + (ghz / xmax) * pw

    def sy(nj):
        return top + ph - (nj / ymax) * ph

    for idx, (policy, series) in enumerate(sorted(by_policy.items())):
        color = _PALETTE[idx % len(_PALETTE)]
        coords = [(sx(p.ghz), sy(p.nj_per_op)) for p in series]
        path_data = "M " + " L ".join("%.1f %.1f" % c for c in coords)
        body.append('<path d="%s" fill="none" stroke="%s" stroke-width="2.2"/>' % (path_data, color))
        for p, (cx, cy) in zip(series, coords):
            body.append(
                '<circle cx="%.1f" cy="%.1f" r="4.5" fill="%s"><title>policy%d %.3f GHz: %.2f nJ/op, %.1f Mop/s (n=%d)</title></circle>'
                % (cx, cy, color, policy, p.ghz, p.nj_per_op, p.mops, p.samples)
            )
        best = min(series, key=lambda p: p.nj_per_op)
        body.append(
            _text(sx(best.ghz), sy(best.nj_per_op) - 12, "best %.2f" % best.nj_per_op, 11, color=color, weight="500")
        )
        body.append(_text(left + 20, top + 20 + idx * 20, "policy%d" % policy, 12, anchor="start", color=color, weight="500"))
    body.append(_frame(left, top, pw, ph))
    body.append(_text((left + left + pw) / 2, height - 20, "Locked frequency (GHz)", 13))
    body.append(
        '<text transform="translate(24 %.1f) rotate(-90)" text-anchor="middle" font-family="Arial,sans-serif" font-size="13">Energy per op (nJ/op)</text>'
        % (top + ph / 2)
    )
    _write_svg(path, width, height, body)


def render_throughput_svg(points: Sequence[SinglePoint], path: Path) -> None:
    width, height = 1000, 520
    left, top, pw, ph = 80.0, 60.0, 880.0, 400.0
    by_policy: Dict[int, List[SinglePoint]] = defaultdict(list)
    for p in points:
        by_policy[p.policy].append(p)
    if not by_policy:
        raise ValueError("no single-core points to plot")
    for series in by_policy.values():
        series.sort(key=lambda p: p.freq_khz)
    xmax = max(p.ghz for p in points) * 1.04
    ymax = max(p.mops for p in points) * 1.1
    body = [_text(width / 2, 30, "Single-core throughput vs frequency", 18, weight="500")]
    for i in range(6):
        gx = left + pw * i / 5.0
        gy = top + ph - ph * i / 5.0
        body.append(_text(gx, top + ph + 20, _fmt(xmax * i / 5.0, 2) + "G", 11))
        body.append(_text(left - 12, gy + 4, _fmt(ymax * i / 5.0, 0), 11, anchor="end"))
        body.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#e5e7eb"/>' % (left, gy, left + pw, gy))

    def sx(ghz):
        return left + (ghz / xmax) * pw

    def sy(mops):
        return top + ph - (mops / ymax) * ph

    for idx, (policy, series) in enumerate(sorted(by_policy.items())):
        color = _PALETTE[idx % len(_PALETTE)]
        coords = [(sx(p.ghz), sy(p.mops)) for p in series]
        path_data = "M " + " L ".join("%.1f %.1f" % c for c in coords)
        body.append('<path d="%s" fill="none" stroke="%s" stroke-width="2.2"/>' % (path_data, color))
        for (cx, cy) in coords:
            body.append('<circle cx="%.1f" cy="%.1f" r="3.5" fill="%s"/>' % (cx, cy, color))
        body.append(_text(left + 20, top + 20 + idx * 20, "policy%d" % policy, 12, anchor="start", color=color, weight="500"))
    body.append(_frame(left, top, pw, ph))
    body.append(_text((left + left + pw) / 2, height - 20, "Locked frequency (GHz)", 13))
    body.append(
        '<text transform="translate(24 %.1f) rotate(-90)" text-anchor="middle" font-family="Arial,sans-serif" font-size="13">Throughput (Mop/s)</text>'
        % (top + ph / 2)
    )
    _write_svg(path, width, height, body)


def render_combo_svg(points: Sequence[ComboPoint], path: Path) -> None:
    """Multi-core nJ/op vs total throughput, colored by big-core frequency."""
    if not points:
        raise ValueError("no combo points to plot")
    width, height = 1000, 560
    left, top, pw, ph = 80.0, 60.0, 880.0, 420.0
    xmax = max(p.mops for p in points) * 1.08
    ymax = max(p.nj_per_op for p in points) * 1.1
    fp_lo = min(p.freq_big_khz for p in points) / 1e6
    fp_hi = max(p.freq_big_khz for p in points) / 1e6
    body = [_text(width / 2, 30, "Multi-core (big+little) efficiency: nJ/op vs total throughput", 18, weight="500")]
    for i in range(6):
        gx = left + pw * i / 5.0
        gy = top + ph - ph * i / 5.0
        body.append(_text(gx, top + ph + 20, _fmt(xmax * i / 5.0, 0), 11))
        body.append(_text(left - 12, gy + 4, _fmt(ymax * i / 5.0, 1), 11, anchor="end"))
        body.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#e5e7eb"/>' % (left, gy, left + pw, gy))
        body.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#e5e7eb"/>' % (gx, top, gx, top + ph))

    def sx(mops):
        return left + (mops / xmax) * pw

    def sy(nj):
        return top + ph - (nj / ymax) * ph

    def color_for(fp_ghz):
        if fp_hi <= fp_lo:
            return _PALETTE[0]
        ratio = (fp_ghz - fp_lo) / (fp_hi - fp_lo)
        # dark-blue (low) -> warm (high)
        r = int(37 + ratio * (220 - 37))
        g = int(99 - ratio * 60)
        b = int(235 - ratio * 200)
        return "#%02x%02x%02x" % (r, max(0, g), max(0, b))

    for p in points:
        cx, cy = sx(p.mops), sy(p.nj_per_op)
        col = color_for(p.freq_big_khz / 1e6)
        body.append(
            '<circle cx="%.1f" cy="%.1f" r="6" fill="%s" fill-opacity="0.78" stroke="#ffffff"><title>little %.2fG big %.2fG: %.1f Mop/s, %.3f W, %.3f nJ/op</title></circle>'
            % (cx, cy, col, p.freq_small_khz / 1e6, p.freq_big_khz / 1e6, p.mops, p.avg_w, p.nj_per_op)
        )
    body.append(_frame(left, top, pw, ph))
    body.append(_text((left + left + pw) / 2, height - 20, "Total throughput (Mop/s)", 13))
    body.append(
        '<text transform="translate(24 %.1f) rotate(-90)" text-anchor="middle" font-family="Arial,sans-serif" font-size="13">Energy per op (nJ/op)</text>'
        % (top + ph / 2)
    )
    body.append(_text(left + pw - 6, top + 16, "color = big-core freq (dark=low, warm=high)", 11, anchor="end", color="#6b7280"))
    _write_svg(path, width, height, body)


def write_efficiency_curves(
    single_tsv: Path,
    combo_tsv: Optional[Path],
    output_dir: Path,
) -> Dict[str, object]:
    """Parse measured TSVs and write SVG curves into output_dir.

    output_dir must not already exist (results are never overwritten).
    Returns a summary dict with artifact paths and per-policy best points.
    """
    single_points = parse_single_core_tsv(single_tsv.read_text(encoding="utf-8"))
    if not single_points:
        raise ValueError("no usable single-core measurements in %s" % single_tsv)
    combo_points: List[ComboPoint] = []
    if combo_tsv is not None and combo_tsv.is_file():
        combo_points = parse_combo_tsv(combo_tsv.read_text(encoding="utf-8"))

    output_dir.mkdir(parents=True, exist_ok=False)
    single_svg = output_dir / "single-core-efficiency.svg"
    throughput_svg = output_dir / "single-core-throughput.svg"
    render_single_core_svg(single_points, single_svg)
    render_throughput_svg(single_points, throughput_svg)
    artifacts = {
        "single_core_efficiency_svg": str(single_svg),
        "single_core_throughput_svg": str(throughput_svg),
    }
    if combo_points:
        combo_svg = output_dir / "multi-core-efficiency.svg"
        render_combo_svg(combo_points, combo_svg)
        artifacts["multi_core_efficiency_svg"] = str(combo_svg)

    best_by_policy: Dict[str, dict] = {}
    for p in single_points:
        key = "policy%d" % p.policy
        cur = best_by_policy.get(key)
        if cur is None or p.nj_per_op < cur["nj_per_op"]:
            best_by_policy[key] = {
                "freq_khz": p.freq_khz,
                "ghz": round(p.ghz, 3),
                "nj_per_op": round(p.nj_per_op, 3),
                "mops": round(p.mops, 1),
            }
    return {
        "measurement_scope": "whole-device battery-side energy per op; not CPU-rail power",
        "single_core_points": len(single_points),
        "multi_core_points": len(combo_points),
        "best_single_core_by_policy": best_by_policy,
        "best_multi_core_nj_per_op": round(min((p.nj_per_op for p in combo_points), default=0.0), 3) if combo_points else None,
        "artifacts": artifacts,
    }
