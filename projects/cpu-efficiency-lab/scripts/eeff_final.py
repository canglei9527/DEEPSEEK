"""
eeff_final.py -- 能效最终分析与 SVG 出图

为什么重做一版分析
------------------
原始逐核功率噪声太大：同一频点 core6/core7 吞吐一致（158.8 / 159.4
Mop/s），功率却差 30%（2.06 / 1.58 W）。原因是 bench 跑完后才采样功率，
采到的是"负载刚结束、其他核正在回落"的过渡态，不是稳态。

所以最终口径改成：
  * 吞吐 ops/s —— 来自 bench 自身计时，准确，同一 policy 各核取均值
  * 功率      —— 同一频点所有测点取中位数（抑制离群）
  * 能效      —— ops/s ÷ 功率，这才是可比的量

这样每个 (policy, freq) 只有唯一一条曲线，不再出现"同频两点差 30%"。
"""

import os
import statistics
import sys
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

W, H = 940, 560
PL, PR, PT, PB = 82, 200, 56, 66
COLORS = ["#4c9aff", "#36b37e", "#ffab00", "#ff5630", "#6554c0",
          "#00b8d9", "#ff8b00", "#e0559b"]


def load(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        f.readline()
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) < 10:
                continue
            try:
                rows.append(dict(policy=int(p[0]), freq=int(p[1]), core=int(p[2]),
                                 ops=float(p[3]), avg_w=float(p[5]),
                                 eff=float(p[8]), nj=float(p[9])))
            except ValueError:
                continue
    return rows


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def chart(title, xlabel, ylabel, series, out, best_idx=None, lower_is_better=False):
    xs = [x for _, pts in series for x, _ in pts]
    ys = [y for _, pts in series for _, y in pts]
    if not xs:
        return
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    yr = (ymax - ymin) or 1
    xr = (xmax - xmin) or 1

    def px(x):
        return PL + (x - xmin) / xr * (W - PL - PR)

    def py(y):
        return H - PB - (y - ymin) / yr * (H - PT - PB)

    o = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
         f'viewBox="0 0 {W} {H}"><rect width="{W}" height="{H}" fill="#0d1117"/>',
         f'<text x="{PL}" y="30" fill="#e6edf3" font-size="18" '
         f'font-family="sans-serif">{esc(title)}</text>']
    for i in range(6):
        y = ymin + yr * i / 5
        o.append(f'<line x1="{PL}" y1="{py(y):.1f}" x2="{W-PR}" y2="{py(y):.1f}" '
                 f'stroke="#21262d"/>')
        o.append(f'<text x="{PL-8}" y="{py(y)+4:.1f}" fill="#8b949e" font-size="11" '
                 f'text-anchor="end" font-family="monospace">{y:.4g}</text>')
        x = xmin + xr * i / 5
        o.append(f'<text x="{px(x):.1f}" y="{H-PB+20}" fill="#8b949e" font-size="11" '
                 f'text-anchor="middle" font-family="monospace">{x/1000:.0f}</text>')
    o.append(f'<text x="{(PL+W-PR)/2:.0f}" y="{H-14}" fill="#8b949e" font-size="13" '
             f'text-anchor="middle" font-family="sans-serif">{esc(xlabel)}</text>')
    o.append(f'<text x="22" y="{(PT+H-PB)/2:.0f}" fill="#8b949e" font-size="13" '
             f'text-anchor="middle" font-family="sans-serif" '
             f'transform="rotate(-90 22 {(PT+H-PB)/2:.0f})">{esc(ylabel)}</text>')

    for idx, (name, pts) in enumerate(series):
        if not pts:
            continue
        c = COLORS[idx % len(COLORS)]
        pts = sorted(pts)
        o.append(f'<polyline points="{" ".join(f"{px(x):.1f},{py(y):.1f}" for x, y in pts)}" '
                 f'fill="none" stroke="{c}" stroke-width="2"/>')
        for x, y in pts:
            o.append(f'<circle cx="{px(x):.1f}" cy="{py(y):.1f}" r="2.8" fill="{c}"/>')
        if best_idx is not None:
            # 直接取真正的极值点。原先靠 best_idx=-1 走 else 分支也能算对，
            # 但 `pts[best_idx] if 0 <= best_idx < len(pts)` 这种写法把 -1
            # 当成合法下标，一旦条件顺序改动就会把圈打到末点，
            # 而末点未必是最优点（policy0 实测最优 2400，末点 2745）。
            bx, by = (min(pts, key=lambda t: t[1]) if lower_is_better
                      else max(pts, key=lambda t: t[1]))
            o.append(f'<circle cx="{px(bx):.1f}" cy="{py(by):.1f}" r="8" fill="none" '
                     f'stroke="#ffd33d" stroke-width="2"/>')
            o.append(f'<text x="{px(bx):.1f}" y="{py(by)-13:.1f}" fill="#ffd33d" '
                     f'font-size="11" text-anchor="middle" font-family="monospace">'
                     f'{bx/1000:.0f}MHz</text>')
        ly = PT + 4 + idx * 18
        o.append(f'<line x1="{W-PR+12}" y1="{ly}" x2="{W-PR+34}" y2="{ly}" '
                 f'stroke="{c}" stroke-width="2.5"/>')
        o.append(f'<text x="{W-PR+40}" y="{ly+4}" fill="#c9d1d9" font-size="12" '
                 f'font-family="sans-serif">{esc(name)}</text>')
    o.append("</svg>")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(o))
    print(f"  {out}")


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "data/eeff.tsv"
    outdir = sys.argv[2] if len(sys.argv) > 2 else "data/plots"
    rows = load(src)
    print(f"读入 {len(rows)} 条")

    # ---- 按 (policy, freq) 聚合：吞吐取均值，功率取中位数 ----
    g = defaultdict(lambda: {"ops": [], "w": [], "cores": set()})
    for r in rows:
        k = (r["policy"], r["freq"])
        g[k]["ops"].append(r["ops"])
        g[k]["w"].append(r["avg_w"])
        g[k]["cores"].add(r["core"])

    per_policy = defaultdict(dict)
    for (pol, fq), v in g.items():
        ops = statistics.mean(v["ops"])
        pw = statistics.median(v["w"])
        per_policy[pol][fq] = dict(ops=ops, w=pw, eff=ops / pw if pw > 0 else 0,
                                   nj=1e9 * pw / ops if ops > 0 else 0,
                                   n=len(v["ops"]))

    p0 = sorted(per_policy[0])
    p6 = sorted(per_policy[6])

    # ---- 图1：吞吐 vs 频率 ----
    chart("单核吞吐 vs 锁定时频率", "频率 (MHz)", "吞吐 (Mop/s)",
          [(f"policy0 小核群", [(f, per_policy[0][f]["ops"] / 1e6) for f in p0]),
           (f"policy6 大核群", [(f, per_policy[6][f]["ops"] / 1e6) for f in p6])],
          f"{outdir}/1_throughput.svg")

    # ---- 图2：能效 nJ/op vs 频率（核心图）----
    chart("能效 vs 频率（越低越省电）★=最优频点", "频率 (MHz)", "能耗 (nJ/op)",
          [("policy0 小核群", [(f, per_policy[0][f]["nj"]) for f in p0]),
           ("policy6 大核群", [(f, per_policy[6][f]["nj"]) for f in p6])],
          f"{outdir}/2_energy_per_op.svg", lower_is_better=True, best_idx=-1)

    # ---- 图3：功率 vs 频率 ----
    chart("整机平均功率 vs 频率", "频率 (MHz)", "功率 (W)",
          [("policy0 小核群", [(f, per_policy[0][f]["w"]) for f in p0]),
           ("policy6 大核群", [(f, per_policy[6][f]["w"]) for f in p6])],
          f"{outdir}/3_power.svg")

    # ---- 单核最优点 ----
    print("\n=== 单核最佳能效频点 ===")
    best = {}
    for pol, freqs in ((0, p0), (6, p6)):
        cands = [(f, per_policy[pol][f]) for f in freqs if per_policy[pol][f]["nj"] > 0]
        b = min(cands, key=lambda kv: kv[1]["nj"])
        best[pol] = b
        mx = max(cands, key=lambda kv: kv[1]["ops"])
        print(f"  policy{pol}: 最省电 {b[0]/1000:7.1f} MHz -> {b[1]['nj']:5.2f} nJ/op "
              f"({b[1]['ops']/1e6:6.1f} Mop/s, {b[1]['w']:.2f} W)")
        print(f"           最快   {mx[0]/1000:7.1f} MHz -> {mx[1]['ops']/1e6:6.1f} Mop/s "
              f"({mx[1]['nj']:5.2f} nJ/op)")

    # ---- 组合热力图（模型估算，非实测）----
    #
    # 必须诚实标注：我们只测过【单核满载】的功率，没测过
    # "policy0 六核 + policy6 两核同时满载" 的真实整机功率。
    # 这里把单核功率按核数线性外推、再扣一次公共底噪，是估算。
    #
    # 后果：组合最优给出小核 2746MHz，而单核实测最优是 2400MHz。
    # 两者矛盾说明外推在高频端不可靠。所以结论以单核实测为准，
    # 热力图只用来看趋势，不作为可直接下发的频点建议。
    cores0 = sorted({c for r in rows if r["policy"] == 0 for c in [r["core"]]})
    cores6 = sorted({c for r in rows if r["policy"] == 6 for c in [r["core"]]})

    grid = {}
    for sf in p0:
        for bf in p6:
            a, b = per_policy[0][sf], per_policy[6][bf]
            # 并行口径：两群同时满载，吞吐相加
            # 功率：各自"负载功率"相加，但两次测量都含同一份公共底噪，
            #       所以减掉一次底噪（取两者较小功率作近似底噪下界）
            tp = a["ops"] * len(cores0) + b["ops"] * len(cores6)
            pw = a["w"] + b["w"] - min(a["w"], b["w"]) * 0.55
            if pw > 0:
                grid[(sf, bf)] = tp / pw

    if grid:
        small = p0
        big = p6
        cw, ch = 68, 32
        left, top = 118, 84
        W2 = left + cw * len(small) + 150
        H2 = top + ch * len(big) + 92
        vs = list(grid.values())
        vmin, vmax = min(vs), max(vs)
        o = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W2}" height="{H2}" '
             f'viewBox="0 0 {W2} {H2}"><rect width="{W2}" height="{H2}" fill="#0d1117"/>',
             f'<text x="{left}" y="30" fill="#e6edf3" font-size="17" '
             f'font-family="sans-serif">频率组合能效（Mop/s per W，越亮越好）</text>',
             f'<text x="{left}" y="52" fill="#8b949e" font-size="11" '
             f'font-family="sans-serif">列=policy0 小核群频率(MHz)  '
             f'行=policy6 大核群频率(MHz)</text>']
        for i, sf in enumerate(small):
            o.append(f'<text x="{left+cw*i+cw/2:.0f}" y="{top-8}" fill="#8b949e" '
                     f'font-size="10" text-anchor="middle" font-family="monospace">'
                     f'{sf/1000:.0f}</text>')
        for j, bf in enumerate(big):
            o.append(f'<text x="{left-10}" y="{top+ch*j+ch/2+4:.0f}" fill="#8b949e" '
                     f'font-size="10" text-anchor="end" font-family="monospace">'
                     f'{bf/1000:.0f}</text>')
            for i, sf in enumerate(small):
                v = grid.get((sf, bf))
                if v is None:
                    continue
                t = (v - vmin) / (vmax - vmin) if vmax > vmin else .5
                r = int(13 + t * 63)
                gg = int(17 + t * 176)
                bb = int(23 + t * 103)
                o.append(f'<rect x="{left+cw*i}" y="{top+ch*j}" width="{cw-2}" '
                         f'height="{ch-2}" fill="rgb({r},{gg},{bb})"/>')
                o.append(f'<text x="{left+cw*i+cw/2-1:.0f}" y="{top+ch*j+ch/2+3:.0f}" '
                         f'fill="#e6edf3" font-size="9" text-anchor="middle" '
                         f'font-family="monospace">{v/1e6:.0f}</text>')
        bk = max(grid, key=grid.get)
        bi, bj = small.index(bk[0]), big.index(bk[1])
        o.append(f'<rect x="{left+cw*bi-1}" y="{top+ch*bj-1}" width="{cw}" height="{ch}" '
                 f'fill="none" stroke="#ffd33d" stroke-width="2.5"/>')
        o.append(f'<text x="{left}" y="{top+ch*len(big)+30}" fill="#ffd33d" '
                 f'font-size="13" font-family="sans-serif">'
                 f'模型最优：小核 {bk[0]/1000:.0f} MHz + 大核 {bk[1]/1000:.0f} MHz '
                 f'= {grid[bk]/1e6:.0f} Mop/s/W</text>')
        o.append(f'<text x="{left}" y="{top+ch*len(big)+52}" fill="#ff8b00" '
                 f'font-size="11" font-family="sans-serif">'
                 f'本图为单核功率按核数外推的估算，非实测；'
                 f'与单核实测最优（policy0 2400MHz）不一致，仅用于看趋势。</text>')
        o.append("</svg>")
        os.makedirs(outdir, exist_ok=True)
        with open(f"{outdir}/4_combo_heatmap.svg", "w", encoding="utf-8") as f:
            f.write("\n".join(o))
        print(f"  {outdir}/4_combo_heatmap.svg")

        print("\n=== 频率组合（模型估算，非实测）===")
        print(f"  小核群 {bk[0]/1000:.0f} MHz + 大核群 {bk[1]/1000:.0f} MHz "
              f"-> {grid[bk]/1e6:.0f} Mop/s/W")
        print("  注：单核功率按核数线性外推，未实测满载组合；结论以单核实测为准")
        print("\n  Top 6:")
        for k, v in sorted(grid.items(), key=lambda kv: -kv[1])[:6]:
            print(f"    小 {k[0]/1000:6.0f}M + 大 {k[1]/1000:6.0f}M -> {v/1e6:7.0f} Mop/s/W")

    # ---- 导出汇总表 ----
    with open(f"{outdir}/summary.tsv", "w", encoding="utf-8") as f:
        f.write("policy\tfreq_mhz\tmops\tavg_w\tnj_per_op\tsamples\n")
        for pol, freqs in ((0, p0), (6, p6)):
            for fq in freqs:
                d = per_policy[pol][fq]
                f.write(f"{pol}\t{fq/1000:.1f}\t{d['ops']/1e6:.1f}\t{d['w']:.3f}\t"
                        f"{d['nj']:.2f}\t{d['n']}\n")
    print(f"  {outdir}/summary.tsv")


if __name__ == "__main__":
    main()