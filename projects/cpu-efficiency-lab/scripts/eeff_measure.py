"""
eeff_measure.py -- 8 核能效逐点测量

===== 测量方法的推导过程（为什么最终是这么测的）=====

踩过的坑，按顺序：

(1) 瞬时电流法 —— 失败
    读 current_now × voltage_now 单点值。
    实测空闲 0.659/0.897/0.880/0.880/1.021 W，噪声 0.36 W，
    比单核负载本身的增量还大，信噪比 < 1，改不出结论。

(2) 短时负载对比 —— 失败得更彻底
    单核跑负载时电流 178 mA，反而低于空闲 208 mA。
    原因：单核忙时其余核进入深休眠，整机功率是下降的。
    所以"负载功率 - 空闲功率"会是负数，这个差值根本不是
    "该核心的功耗"，不能用来算能效。

(3) charge_counter 库仑计 —— 精度不够
    5 秒内读数完全不变（步进 1000 ≈ 0.001 Ah），
    秒级测量落在量化误差里。

(4) 最终方案：长时积分 + 同核对比
    * 每个测点持续 T=45s，用 100ms 间隔高频采样 current_now，
      对整段时间积分得到平均功率。噪声被 ~450 个样本平均掉。
    * 不跨频率比较"绝对功率"，而是比较【同一核心】在
      不同频率下的 (平均功率 / 吞吐) —— 频率变了，但屏幕、
      基带、其他核的休眠策略在同一次会话内基本一致，
      比值可以把这些共模项消掉。
    * 每个频点都配一次"同频率空闲"积分，用于报告净增量，
      但能效裁决以 ops/W 为主，避免被负增量误导。

===== 结论口径 =====
    throughput = ops/s（bench 输出，定时间测量）
    power_avg  = 积分平均功率
    eff        = throughput / power_avg   [ops/s per W]  越大越省电
    nj_per_op  = 1e9 / eff                [nJ/op]       越小越省电

用法:
    python eeff_measure.py <warmup_s> <measure_s> <out.tsv>
"""

import re
import statistics
import subprocess
import sys
import threading
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ADB = r"D:\CODEX\tools\platform-tools\adb.exe"
SERIAL = "192.168.137.99:35383"
BENCH = "/data/local/tmp/eeff/bench"
BATT = "/sys/class/power_supply/battery"


def sh(cmd, timeout=180):
    p = subprocess.run(
        [ADB, "-s", SERIAL, "shell", f"su -c '{cmd}'"],
        capture_output=True, text=True, timeout=timeout,
        encoding="utf-8", errors="replace",
    )
    return (p.stdout or "") + (p.stderr or "")


def read_power_once():
    """单次读功率（W）。读不到返回 None。"""
    out = sh(f"cat {BATT}/current_now; cat {BATT}/voltage_now")
    nums = [int(x) for x in re.findall(r"-?\d+", out)]
    if len(nums) < 2 or nums[1] <= 0:
        return None
    return abs(nums[0]) * nums[1] / 1e12


class PowerSampler:
    """后台线程高频采样功率，返回积分平均。"""

    def __init__(self, interval=0.1):
        self.interval = interval
        self.samples = []
        self._stop = threading.Event()
        self._t = None

    def _loop(self):
        while not self._stop.is_set():
            w = read_power_once()
            if w is not None:
                self.samples.append(w)
            self._stop.wait(self.interval)

    def start(self):
        self._stop.clear()
        self.samples = []
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()

    def stop(self):
        self._stop.set()
        if self._t:
            self._t.join(timeout=5)
        if not self.samples:
            return None, None
        return statistics.mean(self.samples), statistics.median(self.samples)


def lock(policy, freq):
    sh(
        f"echo performance > /sys/devices/system/cpu/cpufreq/policy{policy}/scaling_governor; "
        f"echo {freq} > /sys/devices/system/cpu/cpufreq/policy{policy}/scaling_max_freq; "
        f"echo {freq} > /sys/devices/system/cpu/cpufreq/policy{policy}/scaling_min_freq"
    )
    time.sleep(0.8)
    # 连查两次，避开系统临时改写的窗口
    for _ in range(2):
        out = sh(f"cat /sys/devices/system/cpu/cpufreq/policy{policy}/scaling_cur_freq")
        m = re.search(r"\d+", out)
        if not m or int(m.group(0)) != int(freq):
            return False
        time.sleep(0.3)
    return True


def snapshot_state():
    st = {}
    for p in (0, 6):
        base = f"/sys/devices/system/cpu/cpufreq/policy{p}"
        out = sh(
            f"cat {base}/scaling_governor; cat {base}/scaling_min_freq; "
            f"cat {base}/scaling_max_freq"
        )
        lines = [x.strip() for x in out.splitlines() if x.strip()]
        nums = [int(x) for x in lines[1:3]] if len(lines) >= 3 else [556800, 2745600]
        st[p] = (lines[0] if lines else "walt", nums[0], nums[1])
    return st


def restore_state(st):
    for p, (gov, mn, mx) in st.items():
        sh(
            f"echo {gov} > /sys/devices/system/cpu/cpufreq/policy{p}/scaling_governor; "
            f"echo {mn} > /sys/devices/system/cpu/cpufreq/policy{p}/scaling_min_freq; "
            f"echo {mx} > /sys/devices/system/cpu/cpufreq/policy{p}/scaling_max_freq"
        )


def run_bench(core, warmup, measure):
    out = sh(f"{BENCH} {core} {warmup} {measure}", timeout=int(warmup + measure + 90))
    m = re.search(r"B\|MEASURE_END\|[^|]*\|[^|]*\|([\d.]+)\|(\d+)\|([\d.]+)", out)
    if not m:
        return None, None
    elapsed = float(m.group(1))
    ops = int(m.group(2))
    if elapsed <= 0 or ops <= 0:
        return None, None
    return ops / elapsed, elapsed


def measure_point(core, warmup, measure):
    """在已锁频状态下，测一个核心的吞吐与平均功率。"""
    smp = PowerSampler()
    smp.start()
    time.sleep(0.5)
    ops_s, elapsed = run_bench(core, warmup, measure)
    time.sleep(0.5)
    avg_w, med_w = smp.stop()
    return ops_s, elapsed, avg_w, med_w, len(smp.samples)


def main():
    warmup = float(sys.argv[1]) if len(sys.argv) > 1 else 2.0
    measure = float(sys.argv[2]) if len(sys.argv) > 2 else 20.0
    out_path = sys.argv[3] if len(sys.argv) > 3 else "data/eeff.tsv"

    # 频点：policy0 小核群，policy6 大核群
    plan = [
        (0, [0, 1, 2, 3, 4, 5],
         [556800, 748800, 960000, 1152000, 1363200, 1555200, 1785600,
          1996800, 2227200, 2400000, 2745600]),
        (6, [6, 7],
         [1017600, 1209600, 1401600, 1689600, 1958400, 2246400,
          2438400, 2649600, 2841600, 3072000]),
    ]

    orig = snapshot_state()
    print(f"# 原始状态 {orig}", file=sys.stderr)
    rows = []
    try:
        for policy, cores, freqs in plan:
            for freq in freqs:
                if not lock(policy, freq):
                    print(f"# SKIP p{policy} {freq}", file=sys.stderr)
                    continue
                # 同频率空闲基线
                smp = PowerSampler()
                smp.start()
                time.sleep(3.0)
                idle_w, _ = smp.stop()
                for core in cores:
                    t0 = time.time()
                    ops_s, elapsed, avg_w, med_w, n = measure_point(
                        core, warmup, measure)
                    if ops_s is None or avg_w is None:
                        print(f"# FAIL p{policy} {freq} c{core}", file=sys.stderr)
                        continue
                    net = avg_w - idle_w if idle_w else 0.0
                    eff = ops_s / avg_w if avg_w > 0 else 0.0
                    nj = 1e9 / eff if eff > 0 else 0.0
                    rows.append((policy, freq, core, ops_s, elapsed * 1000,
                                 avg_w, idle_w, net, eff, nj, n))
                    print(f"p{policy} {freq/1000:7.1f}M c{core} "
                          f"{ops_s/1e6:7.1f}Mop/s P={avg_w:5.3f}W "
                          f"idle={idle_w if idle_w else 0:5.3f} "
                          f"eff={eff/1e6:7.2f}Mop/sW {nj:7.2f}nJ/op "
                          f"({time.time()-t0:.0f}s)", file=sys.stderr)
    finally:
        restore_state(orig)
        print("# 已恢复", file=sys.stderr)
        print(sh("cat /sys/devices/system/cpu/cpufreq/policy0/scaling_governor; "
                 "cat /sys/devices/system/cpu/cpufreq/policy0/scaling_min_freq; "
                 "cat /sys/devices/system/cpu/cpufreq/policy0/scaling_max_freq"
                 ).replace("\n", " "), file=sys.stderr)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("policy\tfreq_khz\tcore\tops_per_s\tms\tavg_w\tidle_w\tnet_w\t"
                "eff_ops_per_w\tnj_per_op\tsamples\n")
        for r in rows:
            f.write("\t".join(str(x) for x in r) + "\n")
    print(f"# -> {out_path} ({len(rows)} 行)", file=sys.stderr)


if __name__ == "__main__":
    main()