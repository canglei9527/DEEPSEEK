"""Compare and plot two pinned, all-online dynamic-frequency measurement rounds."""
from __future__ import annotations

import argparse
import csv
import json
import math
from html import escape as html_escape
from pathlib import Path
from xml.sax.saxutils import escape

from run_pinned_dynamic import CASES


SINGLE_NAMES = ["CPU%d" % n for n in range(8)]
GROUP_NAMES = ["E6", "P2", "E1P1", "E2P1", "E4P1", "E6P1", "E4P2", "E6P2"]
COLOURS = ("#2563eb", "#e11d48", "#111827")


def _number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _load_round(path: Path) -> dict:
    with path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    result = {}
    for row in rows:
        name = row.get("config_id", "")
        if not name or name in result:
            raise ValueError("missing or duplicate configuration in %s" % path)
        result[name] = row
    expected = {name: ",".join(map(str, cpus)) for name, cpus in CASES}
    if set(result) != set(expected):
        raise ValueError("round is incomplete: %s" % path)
    for name, workers in expected.items():
        if result[name].get("worker_cpus") != workers:
            raise ValueError("worker configuration mismatch: " + name)
    return result


def _valid(row: dict) -> bool:
    return row.get("valid", "").strip().lower() == "true"


def _value(row: dict, field: str, scale: float = 1.0):
    if not _valid(row):
        return None
    number = _number(row.get(field))
    return number / scale if number is not None else None


def _mean(values):
    return sum(values) / len(values) if len(values) == 2 and all(v is not None for v in values) else None


def _compare(rounds):
    output = []
    for name, cpus in CASES:
        first, second = (rounds[0][name], rounds[1][name])
        perf = [_value(row, "operations_per_second", 1e6) for row in (first, second)]
        power = [_value(row, "device_battery_load_power_w") for row in (first, second)]
        score = [_value(row, "operations_per_total_joule", 1e6) for row in (first, second)]
        f0 = [_value(row, "policy0_median_khz", 1e6) for row in (first, second)]
        f6 = [_value(row, "policy6_median_khz", 1e6) for row in (first, second)]
        mean_score = _mean(score)
        output.append({
            "config_id": name, "worker_cpus": ",".join(map(str, cpus)),
            "round1_valid": _valid(first), "round2_valid": _valid(second),
            "round1_Miterations_s": perf[0], "round2_Miterations_s": perf[1], "mean_Miterations_s": _mean(perf),
            "round1_battery_W": power[0], "round2_battery_W": power[1], "mean_battery_W": _mean(power),
            "round1_Miterations_J": score[0], "round2_Miterations_J": score[1], "mean_Miterations_J": mean_score,
            "score_relative_spread_percent": (abs(score[0] - score[1]) / mean_score * 100
                                              if mean_score is not None and mean_score > 0 else None),
            "round1_policy0_median_GHz": f0[0], "round2_policy0_median_GHz": f0[1],
            "round1_policy6_median_GHz": f6[0], "round2_policy6_median_GHz": f6[1],
        })
    return output


def _svg_header(width, height, title, subtitle):
    return ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d">' % (width, height, width, height),
            '<rect width="100%" height="100%" fill="#fff"/>',
            '<g font-family="Microsoft YaHei, Noto Sans CJK SC, sans-serif" fill="#111827">',
            '<text x="36" y="34" font-size="23" font-weight="bold">%s</text>' % escape(title),
            '<text x="36" y="59" font-size="13" fill="#475569">%s</text>' % escape(subtitle)]


def _write_svg(path: Path, lines: list) -> None:
    with path.open('x', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(lines) + '\n')


def _lines_chart(path: Path, names: list, comparison: list, title: str):
    width, height = 1280, 900
    by_name = {row["config_id"]: row for row in comparison}
    labels = [by_name[name] for name in names]
    svg = _svg_header(width, height, title, "每项单次12秒；两条线为两次测量，虚线为两次均值。连线仅连接分类，不代表连续频率扫描。")
    svg.append('<text x="850" y="34" font-size="15" fill="%s">● 第1遍</text>' % COLOURS[0])
    svg.append('<text x="965" y="34" font-size="15" fill="%s">● 第2遍</text>' % COLOURS[1])
    svg.append('<text x="1080" y="34" font-size="15" fill="%s">-- 平均</text>' % COLOURS[2])
    panels = [
        ("吞吐（百万次自定义迭代/秒）", "round1_Miterations_s", "round2_Miterations_s", "mean_Miterations_s"),
        ("整机电池侧负载功率（W）", "round1_battery_W", "round2_battery_W", "mean_battery_W"),
        ("整机能效（百万次自定义迭代/焦耳）", "round1_Miterations_J", "round2_Miterations_J", "mean_Miterations_J"),
    ]
    left, right, plot_h = 100, 1240, 176
    for panel, (label, f1, f2, fm) in enumerate(panels):
        top = 100 + panel * 250
        svg.append('<text x="36" y="%d" font-size="17" font-weight="bold">%s</text>' % (top - 12, escape(label)))
        vals = [_number(row.get(field)) for row in labels for field in (f1, f2, fm)]
        ymax = max([v for v in vals if v is not None] or [1]) * 1.15
        for tick in range(5):
            val = ymax * tick / 4
            y = top + plot_h - plot_h * tick / 4
            svg.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#e2e8f0"/>' % (left, y, right, y))
            svg.append('<text x="%d" y="%.1f" text-anchor="end" font-size="12" fill="#64748b">%.2f</text>' % (left - 8, y + 4, val))
        xs = [left + (right-left) * i / max(1, len(labels)-1) for i in range(len(labels))]
        for k, field in enumerate((f1, f2, fm)):
            previous = None
            for i, row in enumerate(labels):
                value = _number(row.get(field))
                if value is None:
                    previous = None
                    continue
                x, y = xs[i], top + plot_h * (1 - value / ymax)
                dash = ' stroke-dasharray="7 4"' if k == 2 else ''
                if previous:
                    svg.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" stroke-width="2"%s/>' % (previous[0], previous[1], x, y, COLOURS[k], dash))
                svg.append('<circle cx="%.1f" cy="%.1f" r="%s" fill="%s"><title>%s: %.3f</title></circle>' % (x, y, '3' if k == 2 else '4', COLOURS[k], escape(row['config_id']), value))
                previous = (x, y)
        for x, row in zip(xs, labels):
            svg.append('<text x="%.1f" y="%d" text-anchor="middle" font-size="13">%s</text>' % (x, top + plot_h + 21, escape(row['config_id'])))
    svg.append('<text x="36" y="875" font-size="13" fill="#64748b">功率为整机电池侧读数；全部核心在线、动态频率，非 CPU 电源轨。</text>')
    svg.append('</g></svg>')
    _write_svg(path, svg)


def _frequency_chart(path: Path, comparison: list):
    width, height = 1280, 630
    rows = [{r['config_id']: r for r in comparison}[name] for name in GROUP_NAMES]
    svg = _svg_header(width, height, '大小核组合：实测频率中位数', '频率由系统动态调整；不是锁频，也不是完整频率扫描。空闲组的频率只是背景观测。')
    svg.append('<text x="950" y="34" font-size="15" fill="%s">● 第1遍</text>' % COLOURS[0])
    svg.append('<text x="1070" y="34" font-size="15" fill="%s">● 第2遍</text>' % COLOURS[1])
    left, right, plot_h = 100, 1240, 178
    for panel, (label, f1, f2) in enumerate([
        ('E policy0 频率（GHz）', 'round1_policy0_median_GHz', 'round2_policy0_median_GHz'),
        ('P policy6 频率（GHz）', 'round1_policy6_median_GHz', 'round2_policy6_median_GHz'),
    ]):
        top = 105 + panel * 260
        svg.append('<text x="36" y="%d" font-size="17" font-weight="bold">%s</text>' % (top - 12, escape(label)))
        nums = [_number(row.get(field)) for row in rows for field in (f1, f2)]
        ymax = max([v for v in nums if v is not None] or [1]) * 1.15
        for tick in range(5):
            y = top + plot_h * (1 - tick / 4)
            svg.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#e2e8f0"/>' % (left, y, right, y))
            svg.append('<text x="%d" y="%.1f" text-anchor="end" font-size="12" fill="#64748b">%.2f</text>' % (left - 8, y + 4, ymax * tick / 4))
        xs = [left + (right-left)*i/(len(rows)-1) for i in range(len(rows))]
        for k, field in enumerate((f1, f2)):
            previous = None
            for i, row in enumerate(rows):
                val = _number(row.get(field))
                if val is None: previous = None; continue
                x, y = xs[i], top + plot_h * (1 - val / ymax)
                if previous:
                    svg.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" stroke-width="2"/>' % (previous[0], previous[1], x, y, COLOURS[k]))
                svg.append('<circle cx="%.1f" cy="%.1f" r="4" fill="%s"><title>%s %.3f GHz</title></circle>' % (x, y, COLOURS[k], escape(row['config_id']), val))
                previous = (x, y)
        for x, row in zip(xs, rows):
            svg.append('<text x="%.1f" y="%d" text-anchor="middle" font-size="13">%s</text>' % (x, top + plot_h + 21, escape(row['config_id'])))
    svg.append('</g></svg>')
    _write_svg(path, svg)


def _frequency_efficiency_chart(path: Path, comparison: list):
    width, height = 1500, 820
    by_name = {r['config_id']: r for r in comparison}
    points = []
    for name in GROUP_NAMES:
        row = by_name[name]
        for n in (1, 2):
            x = _number(row.get('round%d_policy0_median_GHz' % n))
            y = _number(row.get('round%d_Miterations_J' % n))
            p = _number(row.get('round%d_policy6_median_GHz' % n))
            if x is not None and y is not None and p is not None:
                points.append({'config_id': name, 'round': n, 'e_ghz': x, 'p_ghz': p, 'score': y})
    if not points:
        raise ValueError('no valid frequency/efficiency observations')
    peak = max(points, key=lambda p: p['score'])
    xmin, xmax = min(p['e_ghz'] for p in points), max(p['e_ghz'] for p in points)
    pad = max(.1, (xmax-xmin) * .15)
    xmin, xmax = max(0, xmin-pad), xmax+pad
    ymax = max(p['score'] for p in points) * 1.2
    left, right, top, bottom = 110, 1450, 110, 690
    svg = _svg_header(width, height, '实测频率与整机能效：按核心组合标记',
                      '横轴为 E 频率中位数，纵轴为整机能效；标签标明工作线程、轮次及实测 P 频率。并非锁频或全频扫频。')
    svg.append('<text x="1180" y="34" font-size="15" fill="%s">● 第1遍</text>' % COLOURS[0])
    svg.append('<text x="1300" y="34" font-size="15" fill="%s">● 第2遍</text>' % COLOURS[1])
    for n in range(6):
        x = left + (right-left)*n/5
        val = xmin + (xmax-xmin)*n/5
        svg.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" stroke="#e2e8f0"/>' % (x, top, x, bottom))
        svg.append('<text x="%.1f" y="%d" text-anchor="middle" font-size="13">%.2f</text>' % (x, bottom+22, val))
        y = bottom - (bottom-top)*n/5
        svg.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#e2e8f0"/>' % (left, y, right, y))
        svg.append('<text x="%d" y="%.1f" text-anchor="end" font-size="13">%.0f</text>' % (left-10, y+4, ymax*n/5))
    for point in points:
        x = left + (right-left) * (point['e_ghz']-xmin)/(xmax-xmin)
        y = bottom - (bottom-top)*point['score']/ymax
        color = COLOURS[point['round']-1]
        text = '%s/%d P%.2f' % (point['config_id'], point['round'], point['p_ghz'])
        svg.append('<circle cx="%.1f" cy="%.1f" r="5" fill="%s"><title>%s E%.4f P%.4f GHz; %.2f M次/J</title></circle>' %
                   (x, y, color, escape(text), point['e_ghz'], point['p_ghz'], point['score']))
        if point is peak:
            svg.append('<circle cx="%.1f" cy="%.1f" r="11" fill="none" stroke="#dc2626" stroke-width="2"/>' % (x,y))
        xtext = x + 8 if x < right-170 else x-170
        ytext = max(top+12, min(bottom-4, y - 8 if point['round'] == 1 else y+16))
        svg.append('<text x="%.1f" y="%.1f" font-size="12" fill="%s">%s</text>' % (xtext, ytext, color, escape(text)))
    svg.append('<text x="%d" y="%d" text-anchor="middle" font-size="16">实测 E policy0 中位频率（GHz）</text>' % ((left+right)//2, bottom+57))
    svg.append('<text transform="translate(24,%d) rotate(-90)" text-anchor="middle" font-size="16">整机能效（百万次迭代/焦耳）</text>' % ((top+bottom)//2))
    svg.append('<text x="36" y="785" font-size="13" fill="#64748b">红圈仅表示本批观察值最高点；频率动态、每项单次12秒，不代表全局最优。E6/P2 标签中的另一组未参与计算时仍在线。</text>')
    svg.append('</g></svg>')
    _write_svg(path, svg)
    return peak


def _html_report(path: Path, prefix: Path, comparison: list, best: dict, peak: dict) -> None:
    def fmt(value, digits=2):
        return ('%.*f' % (digits, value)) if value is not None else '无效'
    groups = [row for row in comparison if row['config_id'] in GROUP_NAMES]
    groups.sort(key=lambda row: row['mean_Miterations_J'] if row['mean_Miterations_J'] is not None else -1, reverse=True)
    lines = ['<!doctype html><html lang="zh-CN"><meta charset="utf-8">',
             '<title>CPU 动态频率能效：两遍复测</title>',
             '<style>body{font-family:Microsoft YaHei,Arial,sans-serif;max-width:1500px;margin:2em auto;padding:0 1em;color:#172033}img{width:100%;height:auto;border:1px solid #dce3ee}table{border-collapse:collapse;width:100%}th,td{border:1px solid #dce3ee;padding:.5em;text-align:right}th:first-child,td:first-child{text-align:left}th{background:#eef4ff}.note{background:#fff5df;padding:1em;line-height:1.6}h2{margin-top:1.8em}</style>',
             '<h1>CPU 动态频率与整机能效：两遍复测</h1>',
             '<p class="note">E表示小核工作线程，P表示大核工作线程，数字为线程数。全部核心在线；没有锁频或关核。每项每遍计分12秒；频率是采样中位数，不是设置值。功率来自整机电池侧，不是CPU电源轨。两次均值为各次能效的算术均值，不能证明全频最优。</p>']
    if best:
        lines.append('<p><b>本批两遍平均能效最高的工作线程组合：</b>%s，%s 百万次迭代/焦耳；第1遍 E/P %s/%s GHz，第2遍 %s/%s GHz。</p>' %
                     (html_escape(best['config_id']), fmt(best['mean_Miterations_J']),
                      fmt(best['round1_policy0_median_GHz']), fmt(best['round1_policy6_median_GHz']),
                      fmt(best['round2_policy0_median_GHz']), fmt(best['round2_policy6_median_GHz'])))
    lines.append('<p>单次观察最高：%s，第%d遍，E/P %s/%s GHz，%s 百万次迭代/焦耳；不代表稳定或全局最优。</p>' %
                 (html_escape(peak['config_id']), peak['round'], fmt(peak['e_ghz']), fmt(peak['p_ghz']), fmt(peak['score'])))
    lines.append('<h2>组合频率与能效对照</h2><p>没有执行线程的组仍在线，其频率仅为背景读数。</p><table><thead><tr><th>工作线程</th><th>第1遍 E/P GHz</th><th>第1遍百万次/J</th><th>第2遍 E/P GHz</th><th>第2遍百万次/J</th><th>两遍均值百万次/J</th></tr></thead><tbody>')
    for row in groups:
        lines.append('<tr><td>%s（%s）</td><td>%s / %s</td><td>%s</td><td>%s / %s</td><td>%s</td><td>%s</td></tr>' %
                     (html_escape(row['config_id']), html_escape(row['worker_cpus']),
                      fmt(row['round1_policy0_median_GHz']), fmt(row['round1_policy6_median_GHz']), fmt(row['round1_Miterations_J']),
                      fmt(row['round2_policy0_median_GHz']), fmt(row['round2_policy6_median_GHz']), fmt(row['round2_Miterations_J']),
                      fmt(row['mean_Miterations_J'])))
    lines.append('</tbody></table>')
    for heading, suffix in [('频率与能效散点', '.frequency-efficiency.svg'), ('大小核组合曲线', '.groups.svg'),
                            ('实测频率曲线', '.frequency.svg'), ('逐核曲线', '.percore.svg')]:
        lines.append('<h2>%s</h2><img alt="%s" src="%s">' %
                     (html_escape(heading), html_escape(heading), html_escape(prefix.name + suffix, quote=True)))
    lines.append('<p><a href="%s">下载两遍对比 CSV</a></p></html>' % html_escape(prefix.name + '.comparison.csv', quote=True))
    with path.open('x', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(lines) + '\n')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True, type=Path)
    prefix = parser.parse_args().output
    path = lambda suffix: prefix.with_name(prefix.name + suffix)
    outputs = [path(x) for x in ('.comparison.csv', '.report.json', '.percore.svg', '.groups.svg', '.frequency.svg', '.frequency-efficiency.svg', '.html')]
    existing = [str(p) for p in outputs if p.exists()]
    if existing: raise FileExistsError('refusing to overwrite: ' + ', '.join(existing))
    rounds = [_load_round(path('.round%d.summary.csv' % n)) for n in (1,2)]
    comparison = _compare(rounds)
    with outputs[0].open('x', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(comparison[0])); writer.writeheader(); writer.writerows(comparison)
    _lines_chart(outputs[2], SINGLE_NAMES, comparison, '八核逐核绑核：两次快速复测')
    _lines_chart(outputs[3], GROUP_NAMES, comparison, '大小核工作线程组合：两次快速复测')
    _frequency_chart(outputs[4], comparison)
    peak = _frequency_efficiency_chart(outputs[5], comparison)
    eligible = [r for r in comparison if r['config_id'] in GROUP_NAMES and r['mean_Miterations_J'] is not None]
    best_mean = max(eligible, key=lambda r: r['mean_Miterations_J']) if eligible else None
    _html_report(outputs[6], prefix, comparison, best_mean, peak)
    report = {
        'rounds': 2, 'configs_per_round': len(CASES),
        'scope': 'all eight CPUs online; pinned workers; dynamic frequency; whole-device battery-side power',
        'limitations': ['not a controlled full-frequency sweep', 'each point is one 12-second observation per round',
                        'observed frequency is a sampled median, not a locked setting',
                        'power is whole-device battery-side, not CPU-rail power',
                        'custom integer iterations are not directly comparable with Geekbench'],
        'best_group_by_mean_score': best_mean, 'highest_single_group_observation': peak,
        'results': comparison, 'files': [str(p) for p in outputs],
    }
    with outputs[1].open('x', encoding='utf-8') as f: json.dump(report, f, ensure_ascii=False, indent=2)
    print('PLOTS_WRITTEN|' + '|'.join(str(p) for p in outputs), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
