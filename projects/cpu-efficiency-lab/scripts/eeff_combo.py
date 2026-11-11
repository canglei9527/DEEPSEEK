"""
eeff_combo.py -- 频率组合的【实测】能效

为什么要单独测这个
------------------
eeff_final.py 里的组合热力图是把单核功率按核数线性外推估出来的，
不是实测。它给出的组合最优（小核 2746）和单核实测最优（2400）互相
矛盾，说明外推在高频端失真 —— 真实 SoC 多核满载时共享供电和热预算，
功率不会线性叠加。

所以这里直接测"两群同时满载"的真实整机功率：
  bench 0,1,2,3,4,5,6,7  一次跑满 8 核，policy0 锁 Fsmall、policy6 锁 Fbig。

组合点选择
----------
全组合是 11 × 10 = 110 点，每点约 22 秒，要 40 分钟。
按单核曲线挑出有代表性的 5 × 5 = 25 点（覆盖低/中/高/最优/最高），
约 10 分钟，够定位真实组合最优。
"""

import re
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, "scripts")

from eeff_measure import (PowerSampler, lock, restore_state, sh,
                          snapshot_state)
from eeff_remeasure import ensure_online, online_cpus

BENCH = "/data/local/tmp/eeff/bench"

SMALL = [1152000, 1555200, 1996800, 2400000, 2745600]
BIG = [1401600, 1958400, 2438400, 2841600, 3072000]

WARMUP, MEASURE = 2.0, 15.0


def bench_all(cores, warmup, measure):
    """8 核同时满载，返回 (总吞吐 ops/s, 实测时长 s)。

    解析用 MEASURE_END 而不是逐条 WORKER：
        B|MEASURE_END|<idx>|<boottime>|<elapsed>|<total_iters>|<total_ops_per_s>|...
    这一行直接给出全机总吞吐，省掉自己累加。
    原先想按 WORKER 行累加，但 WORKER 行的 ops/s 是小数
    （182554114.417），用 (\\d+) 匹配会在这行全部失配，
    结果是 total=0，整轮数据被当成 FAIL 丢掉。
    """
    spec = ",".join(str(c) for c in cores)
    out = sh(f"{BENCH} {spec} {warmup} {measure}",
             timeout=int(warmup + measure + 120))
    m = re.search(
        r"B\|MEASURE_END\|[^|]*\|[^|]*\|([\d.]+)\|(\d+)\|([\d.]+)", out)
    if not m:
        return None, None, out
    elapsed = float(m.group(1))
    total_iters = int(m.group(2))
    ops_s = float(m.group(3))
    if elapsed <= 0 or ops_s <= 0:
        return None, None, out
    return ops_s, elapsed, out


def main():
    orig = snapshot_state()
    print(f"# 原始状态 {orig}", file=sys.stderr)
    all_cores = list(range(8))
    ensure_online(all_cores)

    rows = []
    path = "data/eeff_combo.tsv"
    try:
        for fs in SMALL:
            for fb in BIG:
                on = ensure_online(all_cores)
                if len(on) < 8:
                    print(f"# ABORT 核心不足 {sorted(on)}", file=sys.stderr)
                    break
                if not lock(0, fs):
                    print(f"# SKIP p0 {fs}: 锁频失败", file=sys.stderr)
                    continue
                if not lock(6, fb):
                    print(f"# SKIP p6 {fb}: 锁频失败", file=sys.stderr)
                    continue
                # 锁频后复核，防止被系统改回
                cur = sh("cat /sys/devices/system/cpu/cpufreq/policy0/scaling_cur_freq;"
                         "cat /sys/devices/system/cpu/cpufreq/policy6/scaling_cur_freq")
                nums = [int(x) for x in re.findall(r"\d+", cur)]
                if len(nums) < 2 or nums[0] != fs or nums[1] != fb:
                    print(f"# SKIP {fs}/{fb}: 复核失败 {nums}", file=sys.stderr)
                    continue

                smp = PowerSampler()
                smp.start()
                time.sleep(0.6)
                ops, el, raw = bench_all(all_cores, WARMUP, MEASURE)
                avg_w, med_w = smp.stop()
                if ops is None or avg_w is None:
                    print(f"# FAIL {fs}/{fb}", file=sys.stderr)
                    continue
                eff = ops / avg_w
                nj = 1e9 / eff
                rows.append((fs, fb, ops, el * 1000, avg_w, med_w,
                             len(smp.samples), eff, nj))
                print(f"small {fs/1000:6.0f}M big {fb/1000:6.0f}M "
                      f"-> {ops/1e6:7.1f}Mop/s P={avg_w:5.3f}W "
                      f"eff={eff/1e6:6.1f}M/W {nj:6.2f}nJ/op", file=sys.stderr)
    finally:
        restore_state(orig)
        print(f"# 恢复后在线 {sorted(online_cpus())}", file=sys.stderr)

    with open(path, "w", encoding="utf-8") as f:
        f.write("freq_small_khz\tfreq_big_khz\ttotal_ops_per_s\tms\tavg_w\t"
                "median_w\tsamples\teff_ops_per_w\tnj_per_op\n")
        for r in rows:
            f.write("\t".join(str(x) for x in r) + "\n")
    print(f"# -> {path} ({len(rows)} 行)", file=sys.stderr)

    if rows:
        best = max(rows, key=lambda r: r[7])
        print(f"\n# 实测组合最优: 小核 {best[0]/1000:.0f} MHz + "
              f"大核 {best[1]/1000:.0f} MHz -> {best[7]/1e6:.1f} Mop/s/W "
              f"({best[8]:.2f} nJ/op)", file=sys.stderr)


if __name__ == "__main__":
    main()