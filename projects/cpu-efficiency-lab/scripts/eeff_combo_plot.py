"""
eeff_combo_plot.py -- 用【实测】组合数据出图，替换 eeff_final 的外推热力图

两件事要分开说清楚：
  * 能效最优（Mop/s per W 最大）—— 省电优先
  * 吞吐最优（Mop/s 最大）—— 性能优先
实测里这两者不重合，所以报告两个点，而不是假装存在单一"最佳组合"。
"""

import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def load(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        f.readline()
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) < 9:
                continue
            try:
                rows.append(dict(fs=int(p[0]), fb=int(p[1]), ops=float(p[2]),
                                 w=float(p[4]), eff=float(p[7]), nj=float(p[8])))
            except ValueError:
                continue
    return rows


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "data/eeff_combo.tsv"
    outdir = sys.argv[2] if len(sys.argv) > 2 else "data/plots"
    os.makedirs(outdir, exist_ok=True)
    rows = load(src)
    if not rows:
        print("无数据")
        return

    small = sorted({r["fs"] for r in rows})
    big = sorted({r["fb"] for r in rows})
    grid = {(r["fs"], r["fb"]): r for r in rows}

    cw, ch = 92, 40
    left, top = 128, 92
    W = left + cw * len(small) + 190
    H = top + ch * len(big) + 130
    effs = [r["eff"] for r in rows]
    vmin, vmax = min(effs), max(effs)

    o = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
         f'viewBox="0 0 {W} {H}"><rect width="{W}" height="{H}" fill="#0d1117"/>',
         f'<text x="{left}" y="30" fill="#e6edf3" font-size="17" '
         f'font-family="sans-serif">频率组合能效 —— 8 核满载【实测】</text>',
         f'<text x="{left}" y="52" fill="#8b949e" font-size="11" '
         f'font-family="sans-serif">列=policy0 小核群(MHz) 行=policy6 大核群(MHz) '
         f'数值=整机 Mop/s per W；括号内为整机吞吐 Mop/s</text>',
         f'<text x="{left}" y="70" fill="#36b37e" font-size="11" '
         f'font-family="sans-serif">黄圈=能效最优  蓝框=吞吐最优（两者不重合）</text>']

    for i, sf in enumerate(small):
        o.append(f'<text x="{left+cw*i+cw/2:.0f}" y="{top-10}" fill="#8b949e" '
                 f'font-size="11" text-anchor="middle" font-family="monospace">'
                 f'{sf/1000:.0f}</text>')
    for j, bf in enumerate(big):
        o.append(f'<text x="{left-12}" y="{top+ch*j+ch/2+4:.0f}" fill="#8b949e" '
                 f'font-size="11" text-anchor="end" font-family="monospace">'
                 f'{bf/1000:.0f}</text>')
        for i, sf in enumerate(small):
            r = grid.get((sf, bf))
            if r is None:
                o.append(f'<rect x="{left+cw*i}" y="{top+ch*j}" width="{cw-3}" '
                         f'height="{ch-3}" fill="#161b22"/>')
                continue
            t = (r["eff"] - vmin) / (vmax - vmin) if vmax > vmin else .5
            red = int(13 + t * 63)
            grn = int(17 + t * 176)
            blu = int(23 + t * 103)
            o.append(f'<rect x="{left+cw*i}" y="{top+ch*j}" width="{cw-3}" '
                     f'height="{ch-3}" fill="rgb({red},{grn},{blu})"/>')
            o.append(f'<text x="{left+cw*i+cw/2-2:.0f}" y="{top+ch*j+ch/2-2:.0f}" '
                     f'fill="#e6edf3" font-size="12" text-anchor="middle" '
                     f'font-family="monospace">{r["eff"]/1e6:.0f}</text>')
            o.append(f'<text x="{left+cw*i+cw/2-2:.0f}" y="{top+ch*j+ch/2+12:.0f}" '
                     f'fill="#c9d1d9" font-size="9" text-anchor="middle" '
                     f'font-family="monospace">({r["ops"]/1e6:.0f})</text>')

    be = max(rows, key=lambda r: r["eff"])
    bo = max(rows, key=lambda r: r["ops"])
    ei, ej = small.index(be["fs"]), big.index(be["fb"])
    oi, oj = small.index(bo["fs"]), big.index(bo["fb"])
    o.append(f'<rect x="{left+cw*ei-2}" y="{top+ch*ej-2}" width="{cw+1}" height="{ch+1}" '
             f'fill="none" stroke="#ffd33d" stroke-width="3"/>')
    if (ei, ej) != (oi, oj):
        o.append(f'<rect x="{left+cw*oi-2}" y="{top+ch*oj-2}" width="{cw+1}" '
                 f'height="{ch+1}" fill="none" stroke="#4c9aff" stroke-width="3"/>')

    y = top + ch * len(big) + 30
    o.append(f'<text x="{left}" y="{y}" fill="#ffd33d" font-size="13" '
             f'font-family="sans-serif">能效最优：小核 {be["fs"]/1000:.0f} + '
             f'大核 {be["fb"]/1000:.0f} MHz = {be["eff"]/1e6:.0f} Mop/s/W '
             f'（{be["ops"]/1e6:.0f} Mop/s, {be["w"]:.2f} W, {be["nj"]:.2f} nJ/op）</text>')
    o.append(f'<text x="{left}" y="{y+22}" fill="#4c9aff" font-size="13" '
             f'font-family="sans-serif">吞吐最优：小核 {bo["fs"]/1000:.0f} + '
             f'大核 {bo["fb"]/1000:.0f} MHz = {bo["ops"]/1e6:.0f} Mop/s '
             f'（能效 {bo["eff"]/1e6:.0f} Mop/s/W, {bo["w"]:.2f} W）</text>')
    o.append(f'<text x="{left}" y="{y+46}" fill="#ff8b00" font-size="11" '
             f'font-family="sans-serif">注：能效最优与吞吐最优不重合 —— '
             f'追求省电应锁在能效点，追求性能则能效会下降约 '
             f'{(1-bo["eff"]/be["eff"])*100:.0f}%。</text>')
    o.append("</svg>")

    out = f"{outdir}/5_combo_measured.svg"
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(o))
    print(f"  {out}")

    print("\n=== 实测组合结论 ===")
    print(f"  能效最优: 小核 {be['fs']/1000:.0f} + 大核 {be['fb']/1000:.0f} MHz "
          f"-> {be['eff']/1e6:.0f} Mop/s/W ({be['nj']:.2f} nJ/op)")
    print(f"  吞吐最优: 小核 {bo['fs']/1000:.0f} + 大核 {bo['fb']/1000:.0f} MHz "
          f"-> {bo['ops']/1e6:.0f} Mop/s ({bo['eff']/1e6:.0f} Mop/s/W)")
    print(f"  覆盖 {len(rows)} 个组合点")


if __name__ == "__main__":
    main()
