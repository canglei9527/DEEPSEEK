"""
eeff_remeasure.py -- 定点补测，结果并入已有 eeff.tsv

为什么需要补测
--------------
首轮 policy6 的 2246400 / 2438400 / 2649600 / 2841600 吞吐非单调
（159 -> 125 -> 115 -> 201 Mop/s），物理上不可能。查因发现测量期间
CPU6/7 被系统下线（/sys/devices/system/cpu/online 只剩 0-5），
policy6 节点同时返回 EBUSY。所以上面那批数据必须重测。

守卫
----
每次锁频前后都检查 online 是否覆盖目标核心；不覆盖就中止本轮，
绝不把"核心不在线"的数据当成测量结果。
"""

import re
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, "scripts")

from eeff_measure import (PowerSampler, lock, measure_point, restore_state,
                          sh, snapshot_state)


def online_cpus():
    out = sh("cat /sys/devices/system/cpu/online")
    m = re.search(r"[\d,\-]+", out)
    if not m:
        return set()
    s = m.group(0)
    cpus = set()
    for part in s.split(","):
        if "-" in part:
            a, b = part.split("-")
            cpus.update(range(int(a), int(b) + 1))
        else:
            cpus.add(int(part))
    return cpus


def ensure_online(cores):
    """确保目标核心在线；离线就写 1 唤醒。返回实际在线集合。"""
    for c in cores:
        if c not in online_cpus():
            sh(f"echo 1 > /sys/devices/system/cpu/cpu{c}/online")
    time.sleep(1.5)
    return online_cpus()


def main():
    # (policy, freqs, cores)
    targets = [
        (6, [2246400, 2649600, 2841600, 3072000], [6, 7]),
    ]
    warmup, measure = 2.0, 15.0

    orig = snapshot_state()
    print(f"# 原始状态 {orig}", file=sys.stderr)
    print(f"# 在线核心 {sorted(online_cpus())}", file=sys.stderr)

    results = {}
    try:
        for policy, freqs, cores in targets:
            ensure_online(cores)
            for freq in freqs:
                on = ensure_online(cores)
                if not set(cores) <= on:
                    print(f"# ABORT {freq}: 核心不在线 {sorted(on)}",
                          file=sys.stderr)
                    break
                if not lock(policy, freq):
                    print(f"# SKIP {freq}: 锁频失败", file=sys.stderr)
                    continue
                # 锁频后再次确认核心没被踢掉
                if not set(cores) <= online_cpus():
                    print(f"# SKIP {freq}: 锁频后核心掉线", file=sys.stderr)
                    continue
                # 空闲基线守卫：整机空闲一般 < 1.5W；读到异常高值
                # 说明采样窗口撞上了别的活动（亮屏/后台爆发），重采一次
                for attempt in range(3):
                    smp = PowerSampler()
                    smp.start()
                    time.sleep(3.0)
                    idle_w, _ = smp.stop()
                    if idle_w is not None and idle_w < 2.5:
                        break
                    print(f"  retry idle {freq}: {idle_w}", file=sys.stderr)
                    time.sleep(2.0)
                for core in cores:
                    ops_s, elapsed, avg_w, med_w, n = measure_point(
                        core, warmup, measure)
                    if ops_s is None or avg_w is None or idle_w is None:
                        print(f"# FAIL {freq} c{core}", file=sys.stderr)
                        continue
                    eff = ops_s / avg_w if avg_w > 0 else 0.0
                    nj = 1e9 / eff if eff > 0 else 0.0
                    results[(policy, freq, core)] = (
                        policy, freq, core, ops_s, elapsed * 1000, avg_w,
                        idle_w, avg_w - idle_w, eff, nj, n)
                    print(f"p{policy} {freq/1000:7.1f}M c{core} "
                          f"{ops_s/1e6:7.1f}Mop/s P={avg_w:5.3f}W "
                          f"idle={idle_w:5.3f} eff={eff/1e6:7.2f} "
                          f"{nj:7.2f}nJ/op", file=sys.stderr)
    finally:
        restore_state(orig)
        print(f"# 恢复后在线 {sorted(online_cpus())}", file=sys.stderr)

    if not results:
        print("# 无有效补测数据", file=sys.stderr)
        return

    # 合并进主表：按 (policy,freq,core) 覆盖旧行
    path = "data/eeff.tsv"
    rows = []
    with open(path, encoding="utf-8") as f:
        header = f.readline().rstrip("\n")
        rows.append(header)
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) < 3:
                continue
            key = (int(p[0]), int(p[1]), int(p[2]))
            if key in results:
                results.pop(key)      # 用新数据替换
                continue
            rows.append(line.rstrip("\n"))
    for r in sorted(results.values()):
        rows.append("\t".join(str(x) for x in r))
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(rows) + "\n")
    print(f"# 已并入 {path}", file=sys.stderr)


if __name__ == "__main__":
    main()