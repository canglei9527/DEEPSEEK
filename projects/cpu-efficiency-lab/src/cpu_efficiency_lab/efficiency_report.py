"""Per-core historical curves and observed frequency-combination candidates."""

from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import html
import json
from pathlib import Path
import statistics
import sys

from .historical_report import load_combo, load_single


COLORS = ('#60a5fa', '#34d399', '#fbbf24', '#f472b6', '#a78bfa', '#22d3ee', '#fb923c', '#f87171')
LIMITATIONS = [
    '功耗来自整机电池端；历史 TSV 无前后配对的原始空闲窗口，不能补算可靠的 CPU 增量功耗。',
    '每个核心单独分组；同频不同核心不当作重复实验。连线只辅助阅读，不表示测得中间频率。',
    '最佳点仅指已测记录中的最低整机 nJ/op；未测组合保持缺口，未经独立复测，不代表全局最优。',
    '历史表未记录设备身份、时间戳、实测频率、亲和性或温度，不能据此确定芯片体质或调度参数。',
]


def _digest(path):
    return {'path': str(path), 'sha256': hashlib.sha256(Path(path).read_bytes()).hexdigest()}


def analyze(single_path, combo_path):
    single, combo = load_single(Path(single_path)), load_combo(Path(combo_path))
    groups = defaultdict(list)
    for row in single:
        row['mops_per_joule'] = row['ops_per_s'] / row['avg_w'] / 1e6
        groups[row['core']].append(row)
    cores = []
    for core in sorted(groups):
        records = groups[core]
        frequencies = sorted({row['freq_khz'] for row in records})
        points = []
        for frequency in frequencies:
            rows = [r for r in records if r['freq_khz'] == frequency]
            policies = {r['policy'] for r in rows}
            if len(policies) != 1:
                raise ValueError('CPU%d has conflicting policies at %d kHz' % (core, frequency))
            points.append({'freq_khz': frequency, 'policy': rows[0]['policy'], 'record_count': len(rows),
                           'battery_nj_per_op': statistics.median(r['battery_nj_per_op'] for r in rows),
                           'avg_w': statistics.median(r['avg_w'] for r in rows),
                           'mops': statistics.median(r['ops_per_s'] / 1e6 for r in rows),
                           'source_lines': [r['line'] for r in rows]})
        cores.append({'core': core, 'record_count': len(records), 'points': points,
                      'best_observed': min(points, key=lambda r: r['battery_nj_per_op'])})
    for row in combo:
        row['mops_per_joule'] = row['total_ops_per_s'] / row['avg_w'] / 1e6
        row['pareto'] = not any(
            other['avg_w'] <= row['avg_w'] and other['total_ops_per_s'] >= row['total_ops_per_s']
            and (other['avg_w'] < row['avg_w'] or other['total_ops_per_s'] > row['total_ops_per_s'])
            for other in combo)
    frequencies0 = sorted({r['freq_small_khz'] for r in combo})
    frequencies6 = sorted({r['freq_big_khz'] for r in combo})
    measured = {(r['freq_small_khz'], r['freq_big_khz']) for r in combo}
    return {'schema': 'cpu-lab-efficiency-report-v1', 'scope': 'whole_device_battery_side',
            'baseline_status': 'historical_paired_raw_baseline_missing', 'incremental_available': False,
            'limitations': LIMITATIONS, 'sources': {'single': _digest(single_path), 'combo': _digest(combo_path)},
            'cores': cores, 'single_records': single, 'combo_records': combo,
            'missing_cores': [core for core in range(8) if core not in groups],
            'combo_missing_pairs_khz': [[a, b] for a in frequencies0 for b in frequencies6 if (a, b) not in measured],
            'combo_grid_size': len(frequencies0) * len(frequencies6),
            'combo_measured_pairs': len(measured),
            'best_combo_observed': min(combo, key=lambda r: r['battery_nj_per_op']),
            'highest_throughput_combo': max(combo, key=lambda r: r['total_ops_per_s']),
            'lowest_power_combo': min(combo, key=lambda r: r['avg_w'])}


def core_svg(core):
    points = core['points']
    left, top, width, height = 76, 75, 660, 230
    x0, x1 = min(p['freq_khz'] for p in points), max(p['freq_khz'] for p in points)
    y0, y1 = min(p['battery_nj_per_op'] for p in points), max(p['battery_nj_per_op'] for p in points)
    margin = max((y1 - y0) * .15, .1)
    y0, y1 = max(0, y0 - margin), y1 + margin
    def xy(p):
        return (left + (p['freq_khz'] - x0) / max(1, x1 - x0) * width,
                top + (y1 - p['battery_nj_per_op']) / (y1 - y0) * height)
    out = ['<svg xmlns="http://www.w3.org/2000/svg" width="800" height="370" viewBox="0 0 800 370">',
           '<rect width="100%" height="100%" rx="12" fill="#111c30"/>',
           '<g fill="#dce7f7" font-family="sans-serif">',
           '<text x="28" y="32" font-size="20">CPU%d · 整机电池侧 nJ/op</text>' % core['core'],
           '<text x="28" y="54" font-size="12" fill="#93a7c4">实测点连线 · 数值越低越好 · 未扣除底噪</text>']
    for tick in range(5):
        x, y = left + tick * width / 4, top + tick * height / 4
        out += ['<path d="M%d %.2f H%d" stroke="#293b56"/>' % (left, y, left + width),
                '<text x="64" y="%.2f" text-anchor="end" font-size="12">%.2f</text>' % (y + 4, y1 - tick * (y1 - y0) / 4),
                '<text x="%.2f" y="331" text-anchor="middle" font-size="12">%.0f</text>' % (x, (x0 + tick * (x1 - x0) / 4) / 1000)]
    coords = ' '.join('%.2f,%.2f' % xy(p) for p in points)
    color = COLORS[core['core'] % len(COLORS)]
    out.append('<polyline points="%s" fill="none" stroke="%s" stroke-width="2.5"/>' % (coords, color))
    for p in points:
        x, y = xy(p)
        out.append('<circle cx="%.2f" cy="%.2f" r="4" fill="%s"><title>%.1f MHz · %.4f nJ/op · %.3f W · %d record(s)</title></circle>' %
                   (x, y, color, p['freq_khz'] / 1000, p['battery_nj_per_op'], p['avg_w'], p['record_count']))
    x, y = xy(core['best_observed'])
    out += ['<circle cx="%.2f" cy="%.2f" r="9" fill="none" stroke="#fff" stroke-width="2"><title>历史观测最低值</title></circle>' % (x, y),
            '<text x="400" y="357" text-anchor="middle" font-size="12">频率 MHz · 白圈为该核心历史观测最低值</text>', '</g></svg>']
    return '\n'.join(out)


def combo_svg(report):
    rows = report['combo_records']
    xs = sorted({r['freq_small_khz'] for r in rows})
    ys = sorted({r['freq_big_khz'] for r in rows}, reverse=True)
    cells = defaultdict(list)
    for row in rows:
        cells[row['freq_small_khz'], row['freq_big_khz']].append(row)
    width, height = 150 + len(xs) * 130, 150 + len(ys) * 70
    lo, hi = min(r['battery_nj_per_op'] for r in rows), max(r['battery_nj_per_op'] for r in rows)
    best = report['best_combo_observed']
    best_pair = best['freq_small_khz'], best['freq_big_khz']
    out = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d">' % (width, height, width, height),
           '<rect width="100%" height="100%" fill="#111c30" rx="12"/>', '<g fill="#dce7f7" font-family="sans-serif">',
           '<text x="24" y="30" font-size="20">频率组合 · 整机电池侧 nJ/op</text>',
           '<text x="24" y="54" font-size="12">低值更优 · 绿色边框为历史最低记录 · 缺测不插值</text>']
    for i, freq in enumerate(xs):
        out.append('<text x="%d" y="90" text-anchor="middle" font-size="12">%.1f</text>' % (110 + i * 130 + 62, freq / 1000))
    for j, freq in enumerate(ys):
        out.append('<text x="98" y="%d" text-anchor="end" font-size="12">%.1f</text>' % (132 + j * 70, freq / 1000))
        for i, f0 in enumerate(xs):
            pair = f0, freq
            cell = cells.get(pair, [])
            value = statistics.median(r['battery_nj_per_op'] for r in cell) if cell else None
            ratio = (value - lo) / max(.0001, hi - lo) if cell else 1
            color = '#%02x%02x%02x' % (int(26 + ratio * 15), int(110 - ratio * 65), int(115 - ratio * 45)) if cell else '#202d42'
            x, y = 110 + i * 130, 105 + j * 70
            out.append('<rect x="%d" y="%d" width="123" height="63" rx="7" fill="%s" stroke="%s" stroke-width="2"/>' %
                       (x, y, color, '#54e0ae' if pair == best_pair else '#30425c'))
            out.append('<text x="%d" y="%d" text-anchor="middle" font-size="15">%s</text>' % (x + 62, y + 30, '%.3f' % value if cell else '未测'))
            if cell:
                out.append('<text x="%d" y="%d" text-anchor="middle" font-size="10">%d 条记录%s</text>' % (x + 62, y + 49, len(cell), ' · 最低记录' if pair == best_pair else ''))
    out += ['<text x="%d" y="%d" text-anchor="middle" font-size="12">横轴表内 policy0 MHz · 纵轴表内 policy6 MHz</text>' % (width // 2, height - 15), '</g></svg>']
    return '\n'.join(out)


def render_html(report):
    h = lambda text: html.escape(str(text), quote=True)
    best = report['best_combo_observed']
    cards = [('单核实测记录', len(report['single_records'])), ('核心覆盖', '%d / 8' % len(report['cores'])),
             ('组合网格覆盖', '%d / %d' % (report['combo_measured_pairs'], report['combo_grid_size'])),
             ('最低观测能耗', '%.4f nJ/op' % best['battery_nj_per_op'])]
    out = ['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
           '<title>CPU 能效工作台 · 逐核与组合</title><style>',
           'body{margin:0;background:#0b1220;color:#dce7f7;font:15px/1.7 system-ui,"Microsoft YaHei",sans-serif}main{max-width:1440px;margin:auto;padding:32px}h1{font-size:30px;margin:0}h2{font-size:23px;margin-top:36px}.muted{color:#93a7c4}.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin:26px 0}.stat,article,.notice{background:#111c30;border:1px solid #293b56;border-radius:12px;padding:18px}.stat strong{display:block;font-size:24px;color:#54e0ae}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px}img{width:100%;height:auto}article{padding:12px}a{color:#75b5ff}table{width:100%;border-collapse:collapse;font-size:13px}td,th{text-align:left;padding:10px;border-bottom:1px solid #293b56}.table{overflow:auto}.best{color:#54e0ae}@media(max-width:850px){main{padding:18px}.grid,.stats{grid-template-columns:1fr}}',
           '</style><main><p class="muted">CPU EFFICIENCY LAB / MEASURED DATA</p><h1>八核心能效与频率组合</h1>',
           '<p class="muted">逐核保留测点 · 整机能效与负载增量分开判定 · 数据来源可追溯</p><div class="stats">']
    out += ['<div class="stat"><span>%s</span><strong>%s</strong></div>' % (h(a), h(b)) for a, b in cards]
    out += ['</div><div class="notice"><b>底噪处理状态：历史数据缺少配对原始基线</b><p>本报告显示整机电池侧指标。新的原始实验会使用前后空闲插值、基线漂移和噪声门限；历史表中的 idle_w 不能替代这些证据。</p></div>',
            '<h2>CPU0–CPU7 · 每个核心单独绘制</h2><div class="grid">']
    by_core = {r['core']: r for r in report['cores']}
    for core in range(8):
        item = by_core.get(core)
        if item is None:
            out.append('<article><h3>CPU%d · 缺少数据</h3></article>' % core)
            continue
        p = item['best_observed']
        out.append('<article><img src="core-%d.svg" alt="CPU%d 能效曲线"><p class="muted">历史最低：<span class="best">%.1f MHz / %.4f nJ/op</span> · %.2f Mop/s · %.3f W · %d 个频点</p></article>' %
                   (core, core, p['freq_khz'] / 1000, p['battery_nj_per_op'], p['mops'], p['avg_w'], len(item['points'])))
    out += ['</div><h2>频率组合 · 已测范围内最佳点</h2>',
            '<div class="notice"><b class="best">%.1f + %.1f MHz</b> · %.6f nJ/op · %.3f Mop/J · %.3f Mop/s · %.6f W<p>每对频率仅有的历史记录不能证明稳定最优；优先复测该点及相邻组合。</p></div>' %
            (best['freq_small_khz'] / 1000, best['freq_big_khz'] / 1000, best['battery_nj_per_op'], best['mops_per_joule'], best['total_ops_per_s'] / 1e6, best['avg_w']),
            '<p><img src="combinations.svg" alt="频率组合能效及缺测网格" style="max-width:1100px"></p>',
            '<div class="table"><table><thead><tr><th>policy0 MHz</th><th>policy6 MHz</th><th>nJ/op ↓</th><th>Mop/J ↑</th><th>Mop/s</th><th>整机 W</th><th>Pareto</th></tr></thead><tbody>']
    for row in sorted(report['combo_records'], key=lambda r: r['battery_nj_per_op']):
        out.append('<tr><td>%.1f</td><td>%.1f</td><td>%.6f</td><td>%.3f</td><td>%.3f</td><td>%.6f</td><td>%s</td></tr>' %
                   (row['freq_small_khz'] / 1000, row['freq_big_khz'] / 1000, row['battery_nj_per_op'], row['mops_per_joule'], row['total_ops_per_s'] / 1e6, row['avg_w'], '是' if row['pareto'] else ''))
    out += ['</tbody></table></div><h2>测量边界</h2><ul>'] + ['<li>%s</li>' % h(t) for t in report['limitations']]
    out += ['</ul><p><a href="report.json">结构化结果与原始行号</a> · SVG 可独立保存。</p></main></html>']
    return '\n'.join(out)


def build_report(single_path, combo_path, output):
    report = analyze(single_path, combo_path)
    contents = {'report.json': json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n',
                'index.html': render_html(report), 'combinations.svg': combo_svg(report)}
    contents.update({'core-%d.svg' % core['core']: core_svg(core) for core in report['cores']})
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    for name, text in contents.items():
        with (output / name).open('x', encoding='utf-8', newline='\n') as stream:
            stream.write(text)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--single', required=True, type=Path)
    parser.add_argument('--combo', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        report = build_report(args.single, args.combo, args.output)
    except (ValueError, OSError) as exc:
        print('error: %s' % exc, file=sys.stderr)
        return 2
    print(json.dumps({'output': str(args.output), 'cores': len(report['cores']),
                      'best_combo_observed': report['best_combo_observed']}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
