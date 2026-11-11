"""
eeff_core.py -- 单核能效测量 v2：基线减除 + 屏灭 + 重复取分位数

!!! 已实测证伪，请勿使用 !!!
=============================
本脚本的核心假设是“满载功率 - 空闲功率 = 该核真实功耗”。
在本设备（SM8750）上用 current_now 实现时，这个假设不成立：

  * 同一个静止系统连续 6 个 4 秒窗口的电流读数：
        97mA 105mA 115mA 199mA 225mA 378mA   ->  0.41W ~ 1.59W
    噪声极差 1.19 W，而单核满载相对空闲的真实增量只有 0.1~0.5 W。
    噪声是信号的 2~10 倍。

  * 实际跑出来 22% 的行 load < idle，算出的 cpu 功耗为负值。

结论：单核增量功耗小于该设备的电流测量噪声下限，相减法在物理上不可行。
保留本文件仅作为“已尝试并否证”的记录，真正可用的是：
  * core_energy.sh       库仑计（charge_counter）口径，但受 1000 单位量化限制，
                         需 >=180s 窗口才能把误差压到 5%
  * eeff_energy_plot.py  对应分析
  * eeff_core_plot.py    current_now 口径的单核曲线（只用于看形状，不适合绝对比较）

原始设计说明（历史记录）
------------------------
对 (core, freq) 这一对，测四组配对读数：

    A. 该核满载                    P_load(core, freq)
    B. 该核空闲但绑在该核上        P_idle(core, freq)
    C. 该核在线但没有任何负载      P_sys      （全机基线）

真正的 CPU 贡献 = P_load - P_idle。再减去 P_sys 就隔离出"这一颗核
在这一个频率上、做实际计算"的增量功耗。

用法
----
    python eeff_core.py [rounds] [measure_s] [out.tsv]
"""

import os
import re
import statistics
import sys
import time
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ADB = os.environ.get("ADB", r"D:\CODEX\tools\platform-tools\adb.exe")
SERIAL = os.environ.get("SERIAL", "192.168.137.99:35383")
BENCH = "/data/local/tmp/eeff/bench"
BATT = "/sys/class/power_supply/battery"
CPUFREQ = "/sys/devices/system/cpu/cpufreq"

# 屏幕与输入：全程熄灭，减少一个巨大且波动的功耗项
SCREEN_OFF = ("input keyevent 223; svc power stayon false; "
              "settings put system screen_off_timeout 60000")


def sh(cmd, timeout=240):
    import subprocess
    p = subprocess.run([ADB, "-s", SERIAL, "shell", f"su -c '{cmd}'"],
                       capture_output=True, text=True, timeout=timeout,
                       encoding="utf-8", errors="replace")
    return (p.stdout or "") + (p.stderr or "")


def read_power_once():
    out = sh(f"cat {BATT}/current_now; cat {BATT}/voltage_now")
    nums = [int(x) for x in re.findall(r"-?\d+", out)]
    if len(nums) < 2 or nums[1] <= 0:
        return None
    return abs(nums[0]) * nums[1] / 1e12


class Sampler:
    """后台高频采样，返回 (均值, 中位, 样本数)。"""

    def __init__(self, interval=0.1):
        import threading
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
        import threading
        self._stop.clear()
        self.samples = []
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()

    def stop(self):
        self._stop.set()
        if self._t:
            self._t.join(timeout=5)
        if not self.samples:
            return None, None, 0
        return (statistics.mean(self.samples), statistics.median(self.samples),
                len(self.samples))


def lock(policy, freq):
    sh(f"echo performance > {CPUFREQ}/policy{policy}/scaling_governor; "
       f"echo {freq} > {CPUFREQ}/policy{policy}/scaling_max_freq; "
       f"echo {freq} > {CPUFREQ}/policy{policy}/scaling_min_freq")
    time.sleep(0.7)
    for _ in range(2):
        out = sh(f"cat {CPUFREQ}/policy{policy}/scaling_cur_freq")
        m = re.search(r"\d+", out)
        if not m or int(m.group(0)) != int(freq):
            return False
        time.sleep(0.3)
    return True


def policy_of(core):
    return 6 if core >= 6 else 0


def online_cores():
    out = sh("cat /sys/devices/system/cpu/online")
    m = re.search(r"([\d,\-]+)", out)
    if not m:
        return set()
    s = set()
    for part in m.group(1).split(","):
        if "-" in part:
            a, b = part.split("-")
            s.update(range(int(a), int(b) + 1))
        elif part.strip():
            s.add(int(part))
    return s


def ensure_online(cores):
    for c in cores:
        sh(f"echo 1 > /sys/devices/system/cpu/cpu{c}/online 2>/dev/null")
    time.sleep(0.5)
    return online_cores()


def snapshot_state():
    st = {}
    for p in (0, 6):
        base = f"{CPUFREQ}/policy{p}"
        out = sh(f"cat {base}/scaling_governor; cat {base}/scaling_min_freq; "
                 f"cat {base}/scaling_max_freq")
        lines = [x.strip() for x in out.splitlines() if x.strip()]
        nums = [int(x) for x in lines[1:3]] if len(lines) >= 3 else [556800, 2745600]
        st[p] = (lines[0] if lines else "walt", nums[0], nums[1])
    return st


def restore_state(st):
    for p, (gov, mn, mx) in st.items():
        sh(f"echo {gov} > {CPUFREQ}/policy{p}/scaling_governor; "
           f"echo {mn} > {CPUFREQ}/policy{p}/scaling_min_freq; "
           f"echo {mx} > {CPUFREQ}/policy{p}/scaling_max_freq")


def parse_bench(out):
    m = re.search(r"B\|MEASURE_END\|[^|]*\|[^|]*\|([\d.]+)\|(\d+)\|([\d.]+)", out)
    if not m:
        return None, None
    return float(m.group(3)), float(m.group(1))


def measure_load(core, measure_s):
    """该核满载。返回 (ops_per_s, avg_w, n)。"""
    smp = Sampler()
    smp.start()
    time.sleep(0.4)
    ops, _ = parse_bench(sh(f"{BENCH} {core} 1.0 {measure_s}",
                            timeout=int(measure_s + 90)))
    time.sleep(0.3)
    avg, _, n = smp.stop()
    if ops is None or avg is None:
        return None, None, 0
    return ops, avg, n


def measure_idle(core, measure_s):
    """该核空闲，但用 taskset 把它自己钉在该核上，
    保证"其它核心的休眠状态"与满载时一致。返回 (avg_w, n)。"""
    smp = Sampler()
    smp.start()
    time.sleep(0.4)
    sh(f"taskset -c {core} sh -c 'sleep {measure_s}'",
       timeout=int(measure_s + 60))
    time.sleep(0.3)
    avg, _, n = smp.stop()
    return avg, n


def measure_system(measure_s):
    """全机基线：不绑核、不起 bench。返回 (avg_w, n)。"""
    smp = Sampler()
    smp.start()
    time.sleep(measure_s + 0.7)
    avg, _, n = smp.stop()
    return avg, n


def main():
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    measure_s = float(sys.argv[2]) if len(sys.argv) > 2 else 6.0
    out_path = sys.argv[3] if len(sys.argv) > 3 else "data/eeff_core.tsv"

    on = online_cores()
    print(f"# 在线核心 {sorted(on)}", file=sys.stderr)
    if len(on) < 8:
        on = ensure_online(list(range(8)))
        print(f"# 补上线后 {sorted(on)}", file=sys.stderr)

    orig = snapshot_state()
    print(f"# 原始状态 {orig}", file=sys.stderr)
    print(f"# 轮数={rounds} 每窗={measure_s}s", file=sys.stderr)

    sh(SCREEN_OFF)
    time.sleep(4)      # 等屏幕真正熄灭、背光电流归零
    print("# 已熄屏", file=sys.stderr)

    plan = [(0, [556800, 960000, 1363200, 1785600, 2227200, 2400000, 2745600]),
            (6, [1017600, 1401600, 1958400, 2438400, 2841600, 3072000])]

    rows = []
    try:
        for rnd in range(1, rounds + 1):
            # 每轮重新取一次全机基线，跟踪热漂移
            sys_w, sys_n = measure_system(measure_s)
            print(f"\n# === 第 {rnd}/{rounds} 轮  全机基线 {sys_w:.3f} W ===",
                  file=sys.stderr)

            for pol, freqs in plan:
                for freq in freqs:
                    if not lock(pol, freq):
                        print(f"#  SKIP policy{pol} {freq}: 锁频失败", file=sys.stderr)
                        continue
                    cores = [c for c in range(8) if policy_of(c) == pol]
                    for core in cores:
                        if core not in online_cores():
                            print(f"#  SKIP core{core}: 不在线", file=sys.stderr)
                            continue

                        ops, p_load, n1 = measure_load(core, measure_s)
                        p_idle, n2 = measure_idle(core, measure_s)
                        if ops is None or p_load is None or p_idle is None:
                            print(f"#  FAIL core{core} {freq}", file=sys.stderr)
                            continue

                        # 两个口径，含义不同，都要存：
                        # cpu_w = 满载 - 同核同频空闲。
                        #   p_load 和 p_idle 都是在"同一个核被钉住、其余核
                        #   休眠状态相同"下测的，所以屏幕/基带/内存等固定开销
                        #   在相减时抵消。剩下的是该核"做计算"相对"空转"的
                        #   增量功耗 —— 这才是 CPU 自己的贡献。
                        # above_sys = 满载 - 全机基线。
                        #   不去掉该核自身的静态漏电，用于交叉校验。
                        cpu_w = p_load - p_idle
                        above_sys = p_load - sys_w

                        rows.append(dict(rnd=rnd, policy=pol, freq=freq, core=core,
                                         ops=ops, p_load=p_load, p_idle=p_idle,
                                         p_sys=sys_w, cpu_w=cpu_w,
                                         above_sys=above_sys, n1=n1, n2=n2))
                        nj = (1e9 / (ops / cpu_w) / 1e6) if cpu_w > 0.02 else float("nan")
                        print(f"  p{pol} {freq/1000:6.0f}M c{core} "
                              f"{ops/1e6:6.1f}Mop/s "
                              f"load={p_load:.3f} idle={p_idle:.3f} "
                              f"base={sys_w:.3f} cpu={cpu_w:+.3f}W {nj:.2f}nJ/op",
                              file=sys.stderr)
    finally:
        restore_state(orig)
        online = sorted(online_cores())
        print(f"\n# 已恢复 {orig}  在线 {online}", file=sys.stderr)

    hdr = ("rnd\tpolicy\tfreq_khz\tcore\tops_per_s\tp_load_w\tp_idle_w\tp_sys_w\t"
           "cpu_w\tabove_sys_w\tn_load\tn_idle\n")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(hdr)
        for r in rows:
            f.write(f"{r['rnd']}\t{r['policy']}\t{r['freq']}\t{r['core']}\t"
                    f"{r['ops']:.1f}\t{r['p_load']:.4f}\t{r['p_idle']:.4f}\t"
                    f"{r['p_sys']:.4f}\t{r['cpu_w']:.4f}\t{r['above_sys']:.4f}\t"
                    f"{r['n1']}\t{r['n2']}\n")
    print(f"# -> {out_path} ({len(rows)} 行)", file=sys.stderr)


if __name__ == "__main__":
    main()