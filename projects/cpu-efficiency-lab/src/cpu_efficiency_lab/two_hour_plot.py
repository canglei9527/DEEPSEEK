"""Selected-grid slice and pure-cluster views; no smoothing or missing-cell fill."""
from __future__ import annotations

import html
import math
from pathlib import Path
from typing import Sequence

from .matrix_plot import _svg_text, _write_svg, efficiency_plot_rows


def render_frequency_slices(rows: Sequence[dict], path: Path, mixed: bool = True,
                            incremental: bool = False) -> None:
    rows = efficiency_plot_rows(rows, incremental)
    groups = {}
    for row in rows:
        e, p = row.get('policy0_frequency_khz'), row.get('policy6_frequency_khz')
        if mixed and row.get('stage') == 'D' and row.get('worker_count') == 8 and e is not None and p is not None:
            key, x = 'P %.3f GHz' % (p / 1e6), e
        elif not mixed and row.get('stage') == 'C':
            key, x = ('E6', e) if e is not None else ('P2', p)
        else:
            continue
        if x is not None:
            groups.setdefault(key, []).append((x, row))
    series = [(key, sorted(points, key=lambda item: item[0])) for key, points in sorted(groups.items())]
    xs = [x for _, points in series for x, _ in points]
    ys = [row.get('operations_per_total_joule_max') or row.get('operations_per_total_joule_median')
          for _, points in series for _, row in points if row.get('valid')]
    ys = [float(v) / 1e6 for v in ys if v is not None and math.isfinite(float(v))]
    low, high = min(xs, default=0), max(xs, default=1)
    ymax = max(ys, default=1) * 1.12 or 1
    def px(x):
        return 95 + 760 * (x - low) / (high - low) if high != low else 475
    def py(y):
        return 500 - 390 * y / 1e6 / ymax
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="1100" height="600" viewBox="0 0 1100 600">',
             '<rect width="100%" height="100%" fill="white"/>',
             _svg_text(550, 30, 'E6+P2 frequency slices' if mixed else 'Pure E6 / P2 frequency controls', 20),
             _svg_text(550, 55, ('Incremental load-minus-idle battery-side' if incremental else 'Total whole-device battery-side') + '; million iterations/J; bars = observed repeat range', 12),
             _svg_text(475, 550, 'E locked frequency (GHz)' if mixed else 'Worker policy locked frequency (GHz)', 13),
             _svg_text(550, 580, 'Invalid/unmeasured points break lines; selected grid, not a global optimum.', 12)]
    for i in range(6):
        y = 500 - 390 * i / 5
        parts.append('<line x1="95" y1="%.1f" x2="855" y2="%.1f" stroke="#e5e7eb"/>' % (y, y))
        parts.append(_svg_text(85, y + 4, '%.1f' % (ymax * i / 5), 10, anchor='end'))
    for x in sorted(set(xs)):
        parts.append(_svg_text(px(x), 520, '%.3f' % (x / 1e6), 10))
    colors = ('#2563eb', '#059669', '#dc2626', '#9333ea', '#d97706')
    for i, (name, points) in enumerate(series):
        color, previous = colors[i % len(colors)], None
        parts.append(_svg_text(880, 110 + i * 25, name, 12, anchor='start', color=color))
        for freq, row in points:
            value = row.get('operations_per_total_joule_median')
            if not row.get('valid') or value is None or not math.isfinite(float(value)):
                previous = None
                continue
            x, y = px(freq), py(float(value))
            if previous is not None:
                parts.append('<line class="slice-segment" x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" stroke-width="2"/>' % (*previous, x, y, color))
            lo, hi = row.get('operations_per_total_joule_min'), row.get('operations_per_total_joule_max')
            if lo is not None and hi is not None and math.isfinite(float(lo)) and math.isfinite(float(hi)):
                parts.append('<line class="repeat-range" x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s"/>' % (x, py(lo), x, py(hi), color))
            parts.append('<circle cx="%.1f" cy="%.1f" r="4" fill="%s"><title>%s</title></circle>' % (x, y, color, html.escape(str(row['config_id']))))
            previous = x, y
    if not ys:
        parts.append(_svg_text(475, 300, 'No valid measurements', 18))
    parts.append('</svg>')
    _write_svg(path, parts)
