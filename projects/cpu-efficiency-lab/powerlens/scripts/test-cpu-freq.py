"""用真实设备格式的样本验证 CpuFreq 解析（不需要设备）。

构造的 TSV 与 plcollect 实际写出的列布局完全一致：
  UID 行   13 列
  battery  13 列（ts * battery ...）
  cpu      10 列（ts * cpu <policy> cur min max cpus online cores）
"""
import io
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JAVA = r'D:\CODEX\tools\jdk17\bin\java.exe'
JAVAC = r'D:\CODEX\tools\jdk17\bin\javac.exe'
PLATFORM = r'D:\CODEX\tools\android-sdk\platforms\android-35\android.jar'
SRC = os.path.join(ROOT, 'app2', 'src')

SAMPLE = """\
1000\t1000\tproc:system\t1200\t0\t0\t0\t10\t20\t0\t0\t0\t0
1000\t1010\tproc:system\t1500\t0\t0\t0\t10\t20\t0\t0\t0\t0
1000\t*\tbattery\t5000000\t84\tDischarging\t0\t0\t0\t0\t0\t0
1000\t*\tcpu\tpolicy0\t556800\t556800\t2227200\t0_1_2_3_4_5\t0-7\t8
1000\t*\tcpu\tpolicy6\t2438400\t1017600\t2438400\t6_7\t0-7\t8
2000\t2000\tproc:demo\t900\t0\t0\t0\t5\t5\t0\t0\t0\t0
2000\t2005\tproc:demo\t1100\t0\t0\t0\t5\t5\t0\t0\t0\t0
2000\t*\tbattery\t4990000\t83\tDischarging\t0\t0\t0\t0\t0\t0
2000\t*\tcpu\tpolicy0\t1152000\t1152000\t1363200\t0_1_2_3_4_5\t0-7\t8
2000\t*\tcpu\tpolicy6\t1017600\t1017600\t1017600\t6_7\t0-7\t8
"""

HARNESS = r"""
package com.powerlens.app;

import java.io.File;
public class Harness {
    public static void main(String[] a) throws Exception {
        File f = new File(a[0]);
        long from = 0, to = 9999;
        com.powerlens.app.CpuFreq.Snapshot s = com.powerlens.app.CpuFreq.latest(f, from, to);
        System.out.println("POLICIES=" + s.policies.size());
        for (com.powerlens.app.CpuFreq.Policy p : s.policies) {
            System.out.println("P|" + p.name + "|" + p.curKhz + "|" + p.minKhz + "|" + p.maxKhz
                    + "|" + p.cpus + "|" + p.ts);
        }
        System.out.println("ONLINE=" + s.online + " CORES=" + s.cores + " TS=" + s.ts);
        System.out.println("DESCRIBE_BEGIN");
        System.out.println(com.powerlens.app.CpuFreq.describe(s));
        System.out.println("DESCRIBE_END");
    }
}
"""


def main():
    tmp = tempfile.mkdtemp(prefix='cpufreq_test_')
    tsv = os.path.join(tmp, 'snapshots.tsv')
    with io.open(tsv, 'w', encoding='utf-8', newline='') as fh:
        fh.write(SAMPLE)

    sources = []
    for name in ('Storage.java', 'CpuFreq.java'):
        sources.append(os.path.join(SRC, 'com', 'powerlens', 'app', name))
    harness_dir = os.path.join(tmp, 'com', 'powerlens', 'app')
    os.makedirs(harness_dir, exist_ok=True)
    harness = os.path.join(harness_dir, 'Harness.java')
    with io.open(harness, 'w', encoding='utf-8') as fh:
        fh.write(HARNESS)

    # 只编译解析相关类，避免拖入 Android UI 依赖
    cmd = [JAVAC, '-encoding', 'UTF-8', '-source', '8', '-target', '8',
           '-bootclasspath', PLATFORM, '-d', tmp] + sources + [harness]
    r = subprocess.run(cmd, capture_output=True, text=True, errors='replace')
    if r.returncode != 0:
        print('JAVAC FAILED')
        print(r.stdout)
        print(r.stderr)
        return 1

    r = subprocess.run([JAVA, '-cp', tmp, 'com.powerlens.app.Harness', tsv], capture_output=True, text=True, errors='replace')
    print(r.stdout)
    if r.returncode != 0:
        print(r.stderr)
        return 1

    out = r.stdout
    checks = []
    # 应只取最新一批（ts=2000）
    checks.append(('latest policy0 1152000', 'P|policy0|1152000|1152000|1363200' in out))
    checks.append(('latest policy6 1017600', 'P|policy6|1017600|1017600|1017600' in out))
    checks.append(('two policies', 'POLICIES=2' in out))
    checks.append(('cores 8', 'CORES=8' in out))
    checks.append(('online expanded', 'ONLINE=0-7' in out))
    checks.append(('desc has 1.15G', '1.15G' in out))
    checks.append(('desc has range', '1.15G-1.36G' in out))

    print('=' * 50)
    ok = True
    for name, passed in checks:
        print(('PASS  ' if passed else 'FAIL  ') + name)
        ok = ok and passed
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())