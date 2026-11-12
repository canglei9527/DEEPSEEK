"""
eeff_energy_plot.py -- 单核曲线（库仑计口径）

为什么用另一个脚本
------------------
之前 eeff_core_plot.py 读的是 current_now 口径（p_load/p_idle 为 µA 窗口均值）。
那批数据实测有 22% 的行出现 load < idle，算出的 CPU 功耗为负 ——
因为电流瞬时噪声（0.41~1.59 W，极差 1.19 W）远大于单核真实增量（0.1~0.5 W）。

core_energy.sh 改用库仑计 charge_counter：
  Δcharge × V = 该窗口真正消耗的能量（焦耳）
它是对电流的时间积分，天然把高频抖动平均掉。
实测信号：空闲 65s 掉 2000 单位，满载 58s 掉 8000 单位（4 倍差）。

指标（功率归一化口径，2026-11-12 起）
------------------------------------
旧版直接用电荷差：net_cc = Δload - Δidle。这要求两个窗口等长；
2026-11-12 起空闲窗口独立加长（空闲量化需要更长时间），必须先归一化为功率：

  P_load = Δload × V / T_load      满载窗口平均功率 (W)
  P_idle = Δidle × V / T_idle      空闲窗口平均功率 (W)，T_idle 独立
  P_net  = P_load - P_idle         该核净增功率（可为负：其余核休眠更深）
  nj_net = P_net / (ops / T_load) × 1e9
  nj_raw = P_load / (ops / T_load) × 1e9        含整机基线
  nj_above = (P_load - P_sys) / (ops / T_load) × 1e9   去全机基线

三个都画：如果它们的"最优频点"一致，说明结论稳健；
如果不一致，说明基线选择会改变结论，必须明说。

向后兼容：旧数据没有 idle_win 列时按 window_s 处理，与旧结果完全一致。
"""

import os
import statistics
import sys
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

W, H = 980, 610
PL, PR, PT, PB = 96, 226, 66, 76
PALETTE = ["#4c9aff", "#36b37e", "#ffab00", "#ff5630", "#6554c0",
           "#00b8d9", "#ff8b00", "#e0559b", "#57d9a3", "#ffc400"]

# charge_counter 单位是 µAh（实测每分钟变化 1000 = 0.001Ah）
# 1 µAh = 3.6e-3 C
UA_H_TO_C = 3.6e-3


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
                # idle_win 是 2026-11-12 起新增的可选列；旧数据回退为 window_s
                idle_win = (float(p[idx["idle_win"]]) if "idle_win" in idx
                            else float(p[idx["window_s"]]))
                # temp_mC 是可选列；旧数据没有则为 -1（未知）
                temp = (float(p[idx["temp_mC"]]) if "temp_mC" in idx
                        else -1.0)
                rows.append(dict(
                    rnd=int(p[idx["rnd"]]), policy=int(p[idx["policy"]]),
                    freq=int(p[idx["freq"]]), core=int(p[idx["core"]]),
                    win=float(p[idx["window_s"]]),
                    idle_win=idle_win,
                    cc_load=float(p[idx["cc_load"]]),
                    cc_idle=float(p[idx["cc_idle"]]),
                    cc_sys=float(p[idx["cc_sys"]]),
                    ops=float(p[idx["ops_total"]]),
                    volt=float(p[idx["dV_uV"]]) / 1e6,
                    temp=temp))
            except (ValueError, KeyError):
                continue
    return rows


def joule(d_cc, volts):
    """Δcharge(µAh) × V -> 焦耳"""
    return abs(d_cc) * UA_H_TO_C * volts


def agg(rows):
    g = defaultdict(list)
    for r in rows:
        g[(r["core"], r["freq"])].append(r)
    out = {}
    for k, v in g.items():
        med = lambda f: statistics.median([x[f] for x in v])
        volts = med("volt") or 4.2
        dl = med("cc_load")
        di = med("cc_idle")
        ds = med("cc_sys")
        ops = med("ops")
        win = med("win") or 120.0
        idle_win = med("idle_win") or win
        temp = med("temp")
        e_load = joule(dl, volts)
        # 功率归一化：满载/空闲窗口可以不等长，先各自除以窗口时长。
        # 旧数据 idle_win==win，与旧版 (dl-di)*V/ops 数学等价。
        p_load = e_load / win                      # 满载平均功率 (W)
        p_idle = joule(di, volts) / idle_win       # 空闲平均功率 (W)
        p_sys = joule(ds, volts) / win             # 全机基线功率 (W)
        p_net = p_load - p_idle
        p_above = p_load - p_sys
        rate = ops / win                           # 吞吐 (op/s)

        # 误差带：逐行算 nj_net，取 min/max（极差带）
        nj_nets = []
        for x in v:
            w = x["win"] or 120.0
            iw = x["idle_win"] or w
            r_ = x["ops"] / w if x["ops"] > 0 else 0
            if r_ > 0:
                pn = (joule(x["cc_load"], x["volt"]) / w
                      - joule(x["cc_idle"], x["volt"]) / iw)
                nj_nets.append(pn / r_ * 1e9)
        nj_lo = min(nj_nets) if nj_nets else float("nan")
        nj_hi = max(nj_nets) if nj_nets else float("nan")

        out[k] = dict(
            policy=v[0]["policy"], n=len(v), volts=volts,
            cc_load=dl, cc_idle=di, cc_sys=ds, ops=ops,
            win=win, idle_win=idle_win, temp=temp,
            e_load=e_load, p_load=p_load, p_idle=p_idle, p_sys=p_sys,
            nj_raw=(p_load / rate * 1e9) if rate > 0 else float("nan"),
            nj_net=(p_net / rate * 1e9) if rate > 0 else float("nan"),
            nj_above=(p_above / rate * 1e9) if rate > 0 else float("nan"),
            nj_net_lo=nj_lo, nj_net_hi=nj_hi,
            net_positive=p_net > 0,
            idle_zero=di <= 0,
            hot=temp >= 85000)
    return out


def chart(title, subtitle, ylabel, series, out, lower_better=True, band=None):
    """series: [(name, [(x, y), ...], color), ...]
    band: 可选，[(name, [(x, lo, hi), ...], color), ...] 画极差带"""
    allp = [(x, y) for _, pts, _ in series for x, y in pts if y == y]
    if band:
        for _, bpts, _ in band:
            for x, lo, hi in bpts:
                if lo == lo:
                    allp.append((x, lo))
                if hi == hi:
                    allp.append((x, hi))
    if not allp:
        print(f"  (跳过 {out}: 无有效点)", file=sys.stderr)
        return
    xs = [x for x, _ in allp]
    ys = [y for _, y in allp]
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    pad = (ymax - ymin) * 0.10 or (abs(ymax) * 0.1 or 1)
    ymin -= pad
    ymax += pad

    def px(x):
        return PL + (x - xmin) / ((xmax - xmin) or 1) * (W - PL - PR)

    def py(y):
        return H - PB - (y - ymin) / ((ymax - ymin) or 1) * (H - PT - PB)

    o = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
         f'viewBox="0 0 {W} {H}"><rect width="{W}" height="{H}" fill="#0d1117"/>',
         f'<text x="{PL}" y="30" fill="#e6edf3" font-size="17" '
         f'font-family="sans-serif">{esc(title)}</text>']
    if subtitle:
        o.append(f'<text x="{PL}" y="50" fill="#8b949e" font-size="11" '
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
    o.append(f'<text x="{(PL+W-PR)/2:.0f}" y="{H-20}" fill="#8b949e" font-size="13" '
             f'text-anchor="middle" font-family="sans-serif">锁定频率 (MHz)</text>')
    yc = (PT + H - PB) / 2
    o.append(f'<text x="28" y="{yc:.0f}" fill="#8b949e" font-size="13" '
             f'text-anchor="middle" font-family="sans-serif" '
             f'transform="rotate(-90 28 {yc:.0f})">{esc(ylabel)}</text>')

    # 误差带（极差带，画在数据线下面）
    if band:
        for name, bpts, color in band:
            bpts = sorted((x, lo, hi) for x, lo, hi in bpts if lo == lo and hi == hi)
            if len(bpts) < 2:
                continue
            upper = " ".join(f"{px(x):.1f},{py(hi):.1f}" for x, lo, hi in bpts)
            lower = " ".join(f"{px(x):.1f},{py(lo):.1f}" for x, lo, hi in reversed(bpts))
            o.append(f'<polygon points="{upper} {lower}" fill="{color}" opacity="0.13"/>')

    for idx, (name, pts, color) in enumerate(series):
        pts = sorted((x, y) for x, y in pts if y == y)
        if not pts:
            continue
        if len(pts) > 1:
            o.append(f'<polyline points="{" ".join(f"{px(x):.1f},{py(y):.1f}" for x, y in pts)}" '
                     f'fill="none" stroke="{color}" stroke-width="1.9"/>')
        for x, y in pts:
            o.append(f'<circle cx="{px(x):.1f}" cy="{py(y):.1f}" r="3.2" fill="{color}"/>')
        bx, by = (min(pts, key=lambda t: t[1]) if lower_better
                  else max(pts, key=lambda t: t[1]))
        o.append(f'<circle cx="{px(bx):.1f}" cy="{py(by):.1f}" r="8" fill="none" '
                 f'stroke="#ffd33d" stroke-width="1.8"/>')
        ly = PT + 2 + idx * 18
        o.append(f'<line x1="{W-PR+12}" y1="{ly}" x2="{W-PR+32}" y2="{ly}" '
                 f'stroke="{color}" stroke-width="2.4"/>')
        o.append(f'<text x="{W-PR+38}" y="{ly+4}" fill="#c9d1d9" font-size="11.5" '
                 f'font-family="sans-serif">{esc(name)}</text>')
    o.append("</svg>")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    open(out, "w", encoding="utf-8").write("\n".join(o))
    print(f"  {out}")


def chart_throughput_vs_efficiency(a, cores, cmap, out):
    """吞吐-能效双轴图：横轴吞吐(Mop/s)，纵轴能效(nJ/op)。
    这是极客湾式"功耗-性能曲线"的等价形式——
    每个频点一个点，能直接看出"多花多少电买多少性能"。
    与 P-f 图互补：P-f 看频率趋势，本图看性能/功耗权衡。"""
    series = []
    for c in cores:
        pts = []
        for (cc, fq), v in sorted(a.items()):
            if cc != c:
                continue
            tput = v["ops"] / v["win"] / 1e6  # Mop/s
            nj = v["nj_net"]
            if nj == nj and tput > 0:  # not nan
                pts.append((tput, nj, fq))
        if pts:
            series.append((f"core{c}", pts, cmap[c]))

    allp = [(x, y) for _, pts, _ in series for x, y, _ in pts]
    if not allp:
        print(f"  (跳过 {out}: 无有效点)", file=sys.stderr)
        return
    xs = [x for x, _ in allp]
    ys = [y for _, y in allp]
    xmin, xmax = min(xs) * 0.9, max(xs) * 1.1
    ymin, ymax = min(ys), max(ys)
    pad = (ymax - ymin) * 0.12 or 1
    ymin -= pad
    ymax += pad

    def px(x):
        return PL + (x - xmin) / ((xmax - xmin) or 1) * (W - PL - PR)

    def py(y):
        return H - PB - (y - ymin) / ((ymax - ymin) or 1) * (H - PT - PB)

    o = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
         f'viewBox="0 0 {W} {H}"><rect width="{W}" height="{H}" fill="#0d1117"/>',
         f'<text x="{PL}" y="30" fill="#e6edf3" font-size="17" '
         f'font-family="sans-serif">能效曲线 —— 吞吐 vs 能耗（净口径）</text>',
         f'<text x="{PL}" y="50" fill="#8b949e" font-size="11" '
         f'font-family="sans-serif">横轴=吞吐(Mop/s)，纵轴=能耗(nJ/op)；'
         f'每个点标注频率(MHz)；左下角=高能效区</text>']

    for i in range(6):
        y = ymin + (ymax - ymin) * i / 5
        o.append(f'<line x1="{PL}" y1="{py(y):.1f}" x2="{W-PR}" y2="{py(y):.1f}" '
                 f'stroke="#21262d"/>')
        o.append(f'<text x="{PL-8}" y="{py(y)+4:.1f}" fill="#8b949e" font-size="11" '
                 f'text-anchor="end" font-family="monospace">{y:.3g}</text>')
        x = xmin + (xmax - xmin) * i / 5
        o.append(f'<text x="{px(x):.1f}" y="{H-PB+20}" fill="#8b949e" font-size="11" '
                 f'text-anchor="middle" font-family="monospace">{x:.0f}</text>')
    o.append(f'<text x="{(PL+W-PR)/2:.0f}" y="{H-20}" fill="#8b949e" font-size="13" '
             f'text-anchor="middle" font-family="sans-serif">吞吐 (Mop/s)</text>')
    yc = (PT + H - PB) / 2
    o.append(f'<text x="28" y="{yc:.0f}" fill="#8b949e" font-size="13" '
             f'text-anchor="middle" font-family="sans-serif" '
             f'transform="rotate(-90 28 {yc:.0f})">能耗 (nJ/op)</text>')

    for idx, (name, pts, color) in enumerate(series):
        pts = sorted(pts)
        if len(pts) > 1:
            o.append(f'<polyline points="{" ".join(f"{px(x):.1f},{py(y):.1f}" for x, y, _ in pts)}" '
                     f'fill="none" stroke="{color}" stroke-width="1.9"/>')
        for x, y, fq in pts:
            o.append(f'<circle cx="{px(x):.1f}" cy="{py(y):.1f}" r="4" fill="{color}"/>')
            o.append(f'<text x="{px(x):.1f}" y="{py(y)-8:.1f}" fill="{color}" '
                     f'font-size="10" text-anchor="middle" font-family="monospace">'
                     f'{fq/1000:.0f}</text>')
        # 最优点（最低 nJ/op）
        bx, by, bf = min(pts, key=lambda t: t[1])
        o.append(f'<circle cx="{px(bx):.1f}" cy="{py(by):.1f}" r="9" fill="none" '
                 f'stroke="#ffd33d" stroke-width="2"/>')
        ly = PT + 2 + idx * 18
        o.append(f'<line x1="{W-PR+12}" y1="{ly}" x2="{W-PR+32}" y2="{ly}" '
                 f'stroke="{color}" stroke-width="2.4"/>')
        o.append(f'<text x="{W-PR+38}" y="{ly+4}" fill="#c9d1d9" font-size="11.5" '
                 f'font-family="sans-serif">{esc(name)}</text>')

    o.append("</svg>")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    open(out, "w", encoding="utf-8").write("\n".join(o))
    print(f"  {out}")


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "data/eeff_energy.tsv"
    outdir = sys.argv[2] if len(sys.argv) > 2 else "data/plots"
    if not os.path.exists(src):
        print(f"缺少 {src}", file=sys.stderr)
        return
    rows = load(src)
    if not rows:
        print("无数据", file=sys.stderr)
        return
    a = agg(rows)
    cores = sorted({c for c, _ in a})
    cmap = {c: PALETTE[i % len(PALETTE)] for i, c in enumerate(cores)}
    print(f"读入 {len(rows)} 行，{len(cores)} 核，"
          f"{len({f for _, f in a})} 频点")

    neg = sum(1 for v in a.values() if not v["net_positive"])
    print(f"净功率<=0 的测点：{neg}/{len(a)} ({neg/len(a)*100:.0f}%)"
          f"{'  -> 噪声仍压过信号（或他核休眠抵消）' if neg > len(a) * 0.2 else '  -> 信号可用'}")
    iz = sum(1 for v in a.values() if v["idle_zero"])
    if iz:
        print(f"空闲量化不足的测点：{iz}/{len(a)} ({iz/len(a)*100:.0f}%)"
              f"  -> 这些点 P_idle 按下限 0 处理，净功耗为上界（偏保守），"
              f"建议加大 idle_win 重测")

    def series(metric):
        out = []
        for c in cores:
            pts = [(f, v[metric]) for (cc, f), v in sorted(a.items()) if cc == c]
            out.append((f"core{c}", pts, cmap[c]))
        return out

    def band_net():
        out = []
        for c in cores:
            pts = [(f, v["nj_net_lo"], v["nj_net_hi"])
                   for (cc, f), v in sorted(a.items()) if cc == c]
            out.append((f"core{c}", pts, cmap[c]))
        return out

    chart("单核能效 —— 每核一条线（净口径，扣该核空闲基线）",
          "净 = (满载功率 - 同核同频空闲功率) / 吞吐；阴影=多轮极差带；"
          "空闲窗口独立加长；负值=他核休眠抵消",
          "能耗 (nJ/op)", series("nj_net"), f"{outdir}/20_core_nj_net.svg",
          band=band_net())

    chart("单核能效 —— 原始口径（未扣基线）",
          "原始 = 满载功率 / 吞吐；含屏幕等全部固定开销，用于对照",
          "能耗 (nJ/op)", series("nj_raw"), f"{outdir}/21_core_nj_raw.svg")

    chart("单核能效 —— 扣全机基线",
          "above = (满载功率 - 全机基线功率) / 吞吐；不去该核自身静态漏电",
          "能耗 (nJ/op)", series("nj_above"), f"{outdir}/22_core_nj_above.svg")

    # 吞吐-能效双轴图（极客湾式功耗-性能曲线的等价形式）
    chart_throughput_vs_efficiency(a, cores, cmap,
                                   f"{outdir}/23_throughput_vs_efficiency.svg")

    print("\n=== 每核最优频点（三种口径对比）===")
    print(f"{'core':>5} {'净口径':>16} {'原始口径':>16} {'一致':>6}")
    flip = 0
    for c in cores:
        def best(m):
            cand = [(f, v[m]) for (cc, f), v in a.items()
                    if cc == c and v[m] == v[m]]
            return min(cand, key=lambda t: t[1]) if cand else (None, float("nan"))
        bn = best("nj_net")
        br = best("nj_raw")
        same = bn[0] == br[0]
        flip += 0 if same else 1
        print(f"{c:>5} {bn[0]/1000 if bn[0] else 0:7.0f}MHz {bn[1]:7.2f}  "
              f"{br[0]/1000 if br[0] else 0:7.0f}MHz {br[1]:7.2f}  "
              f"{'一致' if same else '不同':>6}")
    print(f"\n{flip}/{len(cores)} 核的最优频点因是否扣基线而改变")

    # 汇总
    out = f"{outdir}/energy_summary.tsv"
    with open(out, "w", encoding="utf-8") as f:
        f.write("core\tpolicy\tfreq_mhz\tmops\tp_load_w\tp_idle_w\tp_sys_w\tvolts\t"
                "nj_raw\tnj_net\tnj_net_lo\tnj_net_hi\tnj_above\tidle_zero\ttemp_c\tn\n")
        for (c, fq), v in sorted(a.items()):
            f.write(f"{c}\t{v['policy']}\t{fq/1000:.0f}\t{v['ops']/v['win']/1e6:.1f}\t"
                    f"{v['p_load']:.4f}\t{v['p_idle']:.4f}\t{v['p_sys']:.4f}\t"
                    f"{v['volts']:.3f}\t{v['nj_raw']:.2f}\t{v['nj_net']:.2f}\t"
                    f"{v['nj_net_lo']:.2f}\t{v['nj_net_hi']:.2f}\t"
                    f"{v['nj_above']:.2f}\t{int(v['idle_zero'])}\t"
                    f"{v['temp']/1000:.1f}\t{v['n']}\n")
    print(f"  {out}")


if __name__ == "__main__":
    main()