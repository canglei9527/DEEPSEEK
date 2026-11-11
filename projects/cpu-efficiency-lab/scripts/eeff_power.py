"""
eeff_power.py -- 功耗曲线（带实测极差带）

为什么要单独做功率图
--------------------
吞吐曲线几乎是完美直线（相关系数 >0.999），说明计时准确；
而功率曲线是锯齿状的，同一频点多次测量的相对极差中位数 9.1%、
最大 50.4%（policy0 748MHz：0.977 ~ 1.558 W）。噪声全在功率侧。

三个处理：
  1. 画出极差带，让噪声可见 —— 不假装自己测得很准
  2. 同时画中位数与低包络。噪声来源（其它进程、热漂移、负载结束后的
     过渡态）只会**抬高**读数，不会让读数低于真实值，所以 min 是更接近
     真值的估计，中位数是偏高的保守估计。两者给出区间的上下边界。
  3. "功耗曲线"最有用的形态是 P vs 吞吐，而不是 P vs 频率 ——
     前者直接回答"要多少算力、花多少电"，横轴换成吞吐后两群可直接比。
"""

import os
import statistics
import sys
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

W, H = 960, 580
PL, PR, PT, PB = 88, 210, 58, 70
C0, C6 = "#4c9aff", "#36b37e"


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def axes(o, W, H, PL, PR, PT, PB, xmin, xmax, ymin, ymax,
         xlabel, ylabel, fmt_x, fmt_y):
    def px(x):
        return PL + (x - xmin) / ((xmax - xmin) or 1) * (W - PL - PR)

    def py(y):
        return H - PB - (y - ymin) / ((ymax - ymin) or 1) * (H - PT - PB)

    for i in range(6):
        y = ymin + (ymax - ymin) * i / 5
        o.append(f'<line x1="{PL}" y1="{py(y):.1f}" x2="{W-PR}" y2="{py(y):.1f}" stroke="#21262d"/>')
        o.append(f'<text x="{PL-8}" y="{py(y)+4:.1f}" fill="#8b949e" font-size="11" '
                 f'text-anchor="end" font-family="monospace">{fmt_y(y)}</text>')
    for i in range(6):
        x = xmin + (xmax - xmin) * i / 5
        o.append(f'<text x="{px(x):.1f}" y="{H-PB+20}" fill="#8b949e" font-size="11" '
                 f'text-anchor="middle" font-family="monospace">{fmt_x(x)}</text>')
    o.append(f'<text x="{(PL+W-PR)/2:.0f}" y="{H-16}" fill="#8b949e" font-size="13" '
             f'text-anchor="middle" font-family="sans-serif">{esc(xlabel)}</text>')
    yc = (PT + H - PB) / 2
    o.append(f'<text x="24" y="{yc:.0f}" fill="#8b949e" font-size="13" text-anchor="middle" '
             f'font-family="sans-serif" transform="rotate(-90 24 {yc:.0f})">{esc(ylabel)}</text>')
    return px, py


def band_curve(o, pts, color, px, py):
    """pts: [(x, lo, mid, hi)]，画极差带 + 中位数折线 + 低包络虚线"""
    lo_poly = " ".join(f"{px(x):.1f},{py(hi):.1f}" for x, lo, mid, hi in pts)
    hi_poly = " ".join(f"{px(x):.1f},{py(lo):.1f}" for x, lo, mid, hi in reversed(pts))
    o.append(f'<polygon points="{lo_poly} {hi_poly}" fill="{color}" fill-opacity="0.16"/>')
    o.append(f'<polyline points="{" ".join(f"{px(x):.1f},{py(lo):.1f}" for x, lo, _, _ in pts)}" '
             f'fill="none" stroke="{color}" stroke-width="1.4" stroke-dasharray="5 4" '
             f'stroke-opacity="0.85"/>')
    o.append(f'<polyline points="{" ".join(f"{px(x):.1f},{py(mid):.1f}" for x, _, mid, _ in pts)}" '
             f'fill="none" stroke="{color}" stroke-width="2.4"/>')
    for x, lo, mid, hi in pts:
        o.append(f'<line x1="{px(x):.1f}" y1="{py(lo):.1f}" x2="{px(x):.1f}" y2="{py(hi):.1f}" '
                 f'stroke="{color}" stroke-width="1" stroke-opacity="0.5"/>')
        o.append(f'<circle cx="{px(x):.1f}" cy="{py(mid):.1f}" r="3" fill="{color}"/>')


def legend(o, W, PR, PT, items):
    for i, (name, color, dash) in enumerate(items):
        y = PT + 4 + i * 20
        o.append(f'<line x1="{W-PR+14}" y1="{y}" x2="{W-PR+40}" y2="{y}" stroke="{color}" '
                 f'stroke-width="2.5" stroke-dasharray="{dash or "0"}"/>')
        o.append(f'<text x="{W-PR+46}" y="{y+4}" fill="#c9d1d9" font-size="12" '
                 f'font-family="sans-serif">{esc(name)}</text>')


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "data/eeff.tsv"
    outdir = sys.argv[2] if len(sys.argv) > 2 else "data/plots"
    os.makedirs(outdir, exist_ok=True)

    g = defaultdict(lambda: {"w": [], "ops": []})
    with open(src, encoding="utf-8") as f:
        f.readline()
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) < 10:
                continue
            try:
                g[(int(p[0]), int(p[1]))]["w"].append(float(p[5]))
                g[(int(p[0]), int(p[1]))]["ops"].append(float(p[3]))
            except ValueError:
                continue

    per = defaultdict(dict)
    for (pol, fq), v in g.items():
        per[pol][fq] = dict(lo=min(v["w"]), med=statistics.median(v["w"]),
                            hi=max(v["w"]), ops=statistics.mean(v["ops"]),
                            n=len(v["w"]))

    def pts(pol, key_x):
        return [(per[pol][f][key_x], per[pol][f]["lo"], per[pol][f]["med"],
                 per[pol][f]["hi"]) for f in sorted(per[pol])]

    # ---------- 图 A：功率 vs 频率 ----------
    a = [(f, per[0][f]["lo"], per[0][f]["med"], per[0][f]["hi"]) for f in sorted(per[0])]
    b = [(f, per[6][f]["lo"], per[6][f]["med"], per[6][f]["hi"]) for f in sorted(per[6])]
    ymin = min(x[1] for x in a + b) - 0.08
    ymax = max(x[3] for x in a + b) + 0.08
    xmin = min(x[0] for x in a + b)
    xmax = max(x[0] for x in a + b)

    o = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">'
         f'<rect width="{W}" height="{H}" fill="#0d1117"/>',
         f'<text x="{PL}" y="30" fill="#e6edf3" font-size="17" font-family="sans-serif">'
         f'整机功率 vs 锁频频率（阴影=实测极差，虚线=低包络）</text>',
         f'<text x="{PL}" y="48" fill="#8b949e" font-size="11" font-family="sans-serif">'
         f'单核满载；功率噪声只会抬高读数，故低包络更接近真值</text>']
    px, py = axes(o, W, H, PL, PR, PT, PB, xmin, xmax, ymin, ymax,
                  "锁定频率 (MHz)", "整机功率 (W)",
                  lambda v: f"{v/1000:.0f}", lambda v: f"{v:.2f}")
    band_curve(o, a, C0, px, py)
    band_curve(o, b, C6, px, py)
    legend(o, W, PR, PT, [("policy0 小核群", C0, None), ("policy6 大核群", C6, None),
                          ("中位数", "#c9d1d9", None), ("低包络", "#c9d1d9", "5 4"),
                          ("极差带", "#c9d1d9", None)])
    o.append("</svg>")
    p1 = f"{outdir}/6_power_band.svg"
    open(p1, "w", encoding="utf-8").write("\n".join(o))
    print(f"  {p1}")

    # ---------- 图 B：功率 vs 吞吐（更有用的形态）----------
    a2 = [(per[0][per[0].__iter__().__next__()] and per[0][f]["ops"],
           per[0][f]["lo"], per[0][f]["med"], per[0][f]["hi"]) for f in sorted(per[0])]
    b2 = [(per[6][f]["ops"], per[6][f]["lo"], per[6][f]["med"], per[6][f]["hi"])
          for f in sorted(per[6])]
    ymin2 = min(x[1] for x in a2 + b2) - 0.08
    ymax2 = max(x[3] for x in a2 + b2) + 0.08
    xmin2 = 0
    xmax2 = max(x[0] for x in a2 + b2) * 1.05

    o2 = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">'
          f'<rect width="{W}" height="{H}" fill="#0d1117"/>',
          f'<text x="{PL}" y="30" fill="#e6edf3" font-size="17" font-family="sans-serif">'
          f'要多少算力，花多少电（单核）</text>',
          f'<text x="{PL}" y="48" fill="#8b949e" font-size="11" font-family="sans-serif">'
          f'同一吞吐下谁更低，谁就更省电；曲线左右覆盖不同，说明各自的算力上限不同</text>']
    px2, py2 = axes(o2, W, H, PL, PR, PT, PB, xmin2, xmax2, ymin2, ymax2,
                    "吞吐 (Mop/s)", "整机功率 (W)",
                    lambda v: f"{v/1e6:.0f}", lambda v: f"{v:.2f}")
    band_curve(o2, a2, C0, px2, py2)
    band_curve(o2, b2, C6, px2, py2)
    legend(o2, W, PR, PT, [("policy0 小核群", C0, None), ("policy6 大核群", C6, None)])

    # 标出"等吞吐"差距最大的代表点
    import bisect
    xs0 = sorted((x, lo) for x, lo, _, _ in a2)
    for x, lo, mid, hi in b2:
        if x < xs0[0][0]:
            continue
        best = min(xs0, key=lambda t: abs(t[0] - x))
        gap = (1 - best[1] / lo) * 100
        if gap > 25:
            o2.append(f'<line x1="{px2(best[0]):.1f}" y1="{py2(best[1]):.1f}" '
                      f'x2="{px2(x):.1f}" y2="{py2(lo):.1f}" stroke="#ffd33d" '
                      f'stroke-width="1.2" stroke-dasharray="3 3"/>')
            o2.append(f'<text x="{(px2(best[0])+px2(x))/2:.1f}" '
                      f'y="{(py2(best[1])+py2(lo))/2-6:.1f}" fill="#ffd33d" font-size="10" '
                      f'text-anchor="middle" font-family="monospace">-{gap:.0f}%</text>')
            break
    o2.append("</svg>")
    p2 = f"{outdir}/7_power_vs_ops.svg"
    open(p2, "w", encoding="utf-8").write("\n".join(o2))
    print(f"  {p2}")

    # ---------- 控制台：等吞吐对比 ----------
    print("\n=== 等吞吐下的大小核功率对比（低包络口径）===")
    xs0 = sorted((x, lo, mid) for x, lo, mid, _ in a2)
    rows = []
    for x, lo, mid, hi in sorted(b2):
        if x < xs0[0][0]:
            continue
        best = min(xs0, key=lambda t: abs(t[0] - x))
        gap = (1 - best[1] / lo) * 100
        rows.append((x, lo, best[0], best[1], gap))
        print(f"  大核 {x/1e6:6.1f} Mop/s @ {lo:.3f}W  ->  小核达同吞吐 "
              f"{best[0]/1e6:6.1f} Mop/s @ {best[1]:.3f}W   省 {gap:5.1f}%")
    if rows:
        print(f"\n  小核在全部 {len(rows)} 个可比点上都更省，"
              f"区间 {min(r[4] for r in rows):.1f}% ~ {max(r[4] for r in rows):.1f}%")
    print(f"  小核单线程算力上限 {max(x[0] for x in a2)/1e6:.1f} Mop/s，"
          f"大核 {max(x[0] for x in b2)/1e6:.1f} Mop/s")

    # 噪声量化
    sp = []
    for (pol, fq), v in g.items():
        if len(v["w"]) >= 2:
            sp.append(max(v["w"]) / min(v["w"]) - 1)
    print(f"\n  功率测点相对极差：中位 {statistics.median(sp)*100:.1f}%，"
          f"最大 {max(sp)*100:.1f}%")


if __name__ == "__main__":
    main()