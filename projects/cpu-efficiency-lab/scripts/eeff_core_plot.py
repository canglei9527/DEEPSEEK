"""
eeff_core_plot.py -- 单核曲线：每颗核心一条独立曲线

用户要的是"单独一个核心的曲线"，v1 只按 policy 聚合成两条（小核群/大核群），
把 6 颗小核的差异和 2 颗大核的差异全抹平了。这里按 core 分线。

三种口径都画，因为它们的结论可能不同：
  raw      = 整机功率 / 吞吐            （v1 的口径，含屏幕+基带+内存）
  cpu      = (满载 - 同核同频空闲) / 吞吐  （隔离出该核做计算的增量）
  above    = (满载 - 全机基线) / 吞吐     （不去该核静态漏电）

如果 raw 和 cpu 得到的"最优频点"不同，说明 v1 的结论被固定开销污染了。
"""

import os
import statistics
import sys
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

W, H = 980, 600
PL, PR, PT, PB = 92, 220, 62, 72
PALETTE = ["#4c9aff", "#36b37e", "#ffab00", "#ff5630", "#6554c0",
           "#00b8d9", "#ff8b00", "#e0559b", "#57d9a3", "#ffc400"]


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def load(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        hdr = f.readline().rstrip("\n").split("\t")
        idx = {k: i for i, k in enumerate(hdr)}
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) < len(hdr):
                continue
            try:
                rows.append(dict(
                    rnd=int(p[idx["rnd"]]), policy=int(p[idx["policy"]]),
                    freq=int(p[idx["freq_khz"]]), core=int(p[idx["core"]]),
                    ops=float(p[idx["ops_per_s"]]),
                    p_load=float(p[idx["p_load_w"]]),
                    p_idle=float(p[idx["p_idle_w"]]),
                    p_sys=float(p[idx["p_sys_w"]]),
                    cpu=max(float(p[idx["cpu_w"]]), 0.0),
                    above=max(float(p[idx["above_sys_w"]]), 0.0)))
            except (ValueError, KeyError):
                continue
    return rows


def agg(rows):
    """按 (core, freq) 聚合：所有量取中位数，抑制单轮离群。"""
    g = defaultdict(list)
    for r in rows:
        g[(r["core"], r["freq"])].append(r)
    out = {}
    for k, v in g.items():
        m = lambda f: statistics.median([x[f] for x in v])
        out[k] = dict(policy=v[0]["policy"], ops=m("ops"), load=m("p_load"),
                      idle=m("p_idle"), sys=m("p_sys"), cpu=m("cpu"),
                      above=m("above"), n=len(v),
                      # 轮间离散度，用于判断该点可不可信
                      ops_cv=(statistics.stdev([x["ops"] for x in v]) /
                              statistics.mean([x["ops"] for x in v]) * 100
                              if len(v) > 1 else 0.0),
                      load_cv=(statistics.stdev([x["p_load"] for x in v]) /
                               statistics.mean([x["p_load"] for x in v]) * 100
                               if len(v) > 1 else 0.0))
    return out


def chart(title, subtitle, xlabel, ylabel, series, out,
          lower_better=True, mark_best=True):
    allp = [(x, y) for _, pts, _ in series for x, y in pts if y == y]
    if not allp:
        return
    xs = [x for x, _ in allp]
    ys = [y for _, y in allp]
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    pad = (ymax - ymin) * 0.10 or 1
    ymin -= pad
    ymax += pad

    def px(x):
        return PL + (x - xmin) / ((xmax - xmin) or 1) * (W - PL - PR)

    def py(y):
        return H - PB - (y - ymin) / ((ymax - ymin) or 1) * (H - PT - PB)

    o = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
         f'viewBox="0 0 {W} {H}"><rect width="{W}" height="{H}" fill="#0d1117"/>',
         f'<text x="{PL}" y="28" fill="#e6edf3" font-size="17" '
         f'font-family="sans-serif">{esc(title)}</text>']
    if subtitle:
        o.append(f'<text x="{PL}" y="47" fill="#8b949e" font-size="11" '
                 f'font-family="sans-serif">{esc(subtitle)}</text>')
    for i in range(6):
        y = ymin + (ymax - ymin) * i / 5
        o.append(f'<line x1="{PL}" y1="{py(y):.1f}" x2="{W-PR}" y2="{py(y):.1f}" '
                 f'stroke="#21262d"/>')
        o.append(f'<text x="{PL-8}" y="{py(y)+4:.1f}" fill="#8b949e" font-size="11" '
                 f'text-anchor="end" font-family="monospace">{y:.3g}</text>')
        x = xmin + (xmax - xmin) * i / 5
        o.append(f'<text x="{px(x):.1f}" y="{H-PB+20}" fill="#8b949e" font-size="11" '
                 f'text-anchor="middle" font-family="monospace">{x/1000:.0f}</text>')
    o.append(f'<text x="{(PL+W-PR)/2:.0f}" y="{H-18}" fill="#8b949e" font-size="13" '
             f'text-anchor="middle" font-family="sans-serif">{esc(xlabel)}</text>')
    yc = (PT + H - PB) / 2
    o.append(f'<text x="26" y="{yc:.0f}" fill="#8b949e" font-size="13" '
             f'text-anchor="middle" font-family="sans-serif" '
             f'transform="rotate(-90 26 {yc:.0f})">{esc(ylabel)}</text>')

    for idx, (name, pts, color) in enumerate(series):
        pts = sorted((x, y) for x, y in pts if y == y)
        if not pts:
            continue
        o.append(f'<polyline points="{" ".join(f"{px(x):.1f},{py(y):.1f}" for x, y in pts)}" '
                 f'fill="none" stroke="{color}" stroke-width="1.9" stroke-opacity="0.9"/>')
        for x, y in pts:
            o.append(f'<circle cx="{px(x):.1f}" cy="{py(y):.1f}" r="2.6" fill="{color}"/>')
        if mark_best and len(pts) > 1:
            bx, by = (min(pts, key=lambda t: t[1]) if lower_better
                      else max(pts, key=lambda t: t[1]))
            o.append(f'<circle cx="{px(bx):.1f}" cy="{py(by):.1f}" r="7" fill="none" '
                     f'stroke="#ffd33d" stroke-width="1.8"/>')
        ly = PT + 2 + idx * 18
        o.append(f'<line x1="{W-PR+12}" y1="{ly}" x2="{W-PR+32}" y2="{ly}" '
                 f'stroke="{color}" stroke-width="2.4"/>')
        o.append(f'<text x="{W-PR+38}" y="{ly+4}" fill="#c9d1d9" font-size="11.5" '
                 f'font-family="sans-serif">{esc(name)}</text>')
    o.append("</svg>")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(o))
    print(f"  {out}")


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "data/eeff_core.tsv"
    outdir = sys.argv[2] if len(sys.argv) > 2 else "data/plots"
    rows = load(src)
    if not rows:
        print("无数据", file=sys.stderr)
        return
    a = agg(rows)
    cores = sorted({c for c, _ in a})
    cmap = {c: PALETTE[i % len(PALETTE)] for i, c in enumerate(cores)}
    print(f"读入 {len(rows)} 行，{len(cores)} 颗核心，"
          f"{len({f for _, f in a})} 个频点")

    def series(metric, unit_scale=1.0, only=None):
        out = []
        for c in cores:
            if only is not None and c not in only:
                continue
            pts = []
            for (cc, f), v in sorted(a.items()):
                if cc != c or v["ops"] <= 0:
                    continue
                if metric == "nj_raw":
                    y = 1e9 / (v["ops"] / v["load"]) if v["load"] > 0 else float("nan")
                elif metric == "nj_cpu":
                    y = 1e9 / (v["ops"] / v["cpu"]) if v["cpu"] > 0.02 else float("nan")
                elif metric == "nj_above":
                    y = 1e9 / (v["ops"] / v["above"]) if v["above"] > 0.02 else float("nan")
                elif metric == "w_load":
                    y = v["load"]
                elif metric == "w_cpu":
                    y = v["cpu"]
                elif metric == "ops":
                    y = v["ops"] / 1e6
                else:
                    y = float("nan")
                pts.append((f, y * unit_scale))
            out.append((f"core{c}", pts, cmap[c]))
        return out

    chart("单核能效 —— 每颗核心一条曲线（raw 口径）",
          "raw = 整机功率 / 吞吐，未扣除屏幕与基带等固定开销（v1 口径）",
          "锁定频率 (MHz)", "能耗 (nJ/op)", series("nj_raw"),
          f"{outdir}/10_core_nj_raw.svg")

    chart("单核能效 —— 每颗核心一条曲线（cpu 口径）",
          "cpu = (该核满载 - 同核同频空闲) / 吞吐，已隔离固定开销",
          "锁定频率 (MHz)", "能耗 (nJ/op)", series("nj_cpu"),
          f"{outdir}/11_core_nj_cpu.svg")

    chart("单核功耗贡献（cpu 口径）",
          "该核做计算相对空转多消耗的功率，已扣除屏幕/基带/内存",
          "锁定频率 (MHz)", "增量功率 (W)", series("w_cpu"),
          f"{outdir}/12_core_cpu_watt.svg", lower_better=False, mark_best=False)

    chart("整机功率（含全部固定开销）",
          "用于对照：这条曲线里绝大部分不是这颗核",
          "锁定频率 (MHz)", "整机功率 (W)", series("w_load"),
          f"{outdir}/13_core_total_watt.svg", lower_better=False, mark_best=False)

    # ---------- 控制台对比 ----------
    print("\n=== 口径对比：最优频点会不会因是否扣除基线而改变 ===")
    print(f"{'core':>5} {'raw最优':>18} {'cpu最优':>20} {'是否一致':>8}")
    flip = 0
    for c in cores:
        r = [(f, v) for (cc, f), v in a.items() if cc == c and v["ops"] > 0]
        raw = min(((f, 1e9 / (v["ops"] / v["load"])) for f, v in r if v["load"] > 0),
                  key=lambda t: t[1])
        cpu = min(((f, 1e9 / (v["ops"] / v["cpu"])) for f, v in r if v["cpu"] > 0.02),
                  key=lambda t: t[1])
        same = raw[0] == cpu[0]
        flip += 0 if same else 1
        print(f"{c:>5} {raw[0]/1000:7.0f}MHz {raw[1]:7.2f}  "
              f"{cpu[0]/1000:7.0f}MHz {cpu[1]:7.2f}  {'一致' if same else '不同':>8}")
    print(f"\n{flip}/{len(cores)} 颗核心的最优频点在两种口径下不同"
          f"{' -> v1 结论被固定开销污染' if flip else ' -> 结论稳健'}")

    print("\n=== 每颗核心自身的最低能耗（cpu 口径）===")
    for c in cores:
        r = [((f, v["cpu"]), v) for (cc, f), v in a.items() if cc == c and v["cpu"] > 0.02]
        if not r:
            continue
        (f, w), v = min(r, key=lambda t: 1e9 / (t[1]["ops"] / t[0][1]))
        print(f"  core{c}: {f/1000:6.0f} MHz  cpu贡献 {w:.3f} W  "
              f"{v['ops']/1e6:6.1f} Mop/s  {1e9/(v['ops']/w):5.2f} nJ/op")

    print("\n=== 轮间离散度（越大越不可信）===")
    for metric, label in (("load_cv", "整机功率"), ("ops_cv", "吞吐")):
        vals = [v[metric] for v in a.values() if v["n"] > 1]
        if vals:
            print(f"  {label}: 中位 {statistics.median(vals):5.1f}%  最大 {max(vals):5.1f}%")

    # 汇总表
    with open(f"{outdir}/core_summary.tsv", "w", encoding="utf-8") as f:
        f.write("core\tpolicy\tfreq_mhz\tmops\tload_w\tidle_w\tsys_w\tcpu_w\tabove_w\t"
                "nj_raw\tnj_cpu\tn\n")
        for (c, fq), v in sorted(a.items()):
            if v["ops"] <= 0:
                continue
            nj_raw = 1e9 / (v["ops"] / v["load"]) if v["load"] > 0 else float("nan")
            nj_cpu = 1e9 / (v["ops"] / v["cpu"]) if v["cpu"] > 0.02 else float("nan")
            f.write(f"{c}\t{v['policy']}\t{fq/1000:.0f}\t{v['ops']/1e6:.1f}\t"
                    f"{v['load']:.4f}\t{v['idle']:.4f}\t{v['sys']:.4f}\t"
                    f"{v['cpu']:.4f}\t{v['above']:.4f}\t"
                    f"{nj_raw:.2f}\t{nj_cpu:.2f}\t{v['n']}\n")
    print(f"  {outdir}/core_summary.tsv")


if __name__ == "__main__":
    main()