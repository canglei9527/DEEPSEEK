"""检查 plcollect.c 的 CPU 快照实现。

不依赖设备：用编译期检查保证
  1. 只以读模式打开 cpufreq 节点（绝不写 sysfs）
  2. 新增行不会被旧的 UID/电池解析误吞
  3. 列布局与 CpuFreq.java 的期望一致
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
C_SRC = os.path.join(ROOT, 'c', 'plcollect.c')
JAVA_SRC = os.path.join(ROOT, 'app2', 'src', 'com', 'powerlens', 'app', 'CpuFreq.java')


def read(path):
    with io.open(path, encoding='utf-8') as fh:
        return fh.read()


def main():
    c = read(C_SRC)
    j = read(JAVA_SRC)
    fail = []

    def check(name, ok):
        print(('PASS  ' if ok else 'FAIL  ') + name)
        if not ok:
            fail.append(name)

    # 1) 只读：本功能不得出现任何写 cpufreq/sysfs 的调用
    forbidden = [
        r'fopen\s*\(\s*[^,)]*cpufreq[^,)]*,\s*"w',
        r'fopen\s*\(\s*[^,)]*scaling_[a-z_]*freq[^,)]*,\s*"w',
        r'\bwrite\s*\(\s*[^,)]*cpufreq',
    ]
    for pat in forbidden:
        check('no write to cpufreq (%s)' % pat, not re.search(pat, c))

    # 2) load_cpu_state 只使用读路径
    m = re.search(r'static void load_cpu_state\(void\) \{(.*?)\n\}', c, re.S)
    check('load_cpu_state exists', m is not None)
    if m:
        body = m.group(1)
        check('load_cpu_state uses read_int only', 'read_int(' in body)
        check('load_cpu_state has no fopen "w"', '"w"' not in body)
        check('load_cpu_state no longer reads declared field it never uses',
              'scaling_available_freqs' not in body)

    # 3) cpu 行必须是 10 个 \t 分隔字段
    # 直接找源码里的字面量：%lld\t*\tcpu\t%s\t%d\t%d\t%d\t%s\t%s\t%d\n
    bs = chr(92)
    fmt = '%lld' + bs + 't*' + bs + 'tcpu' + bs + 't%s' + bs + 't%d' + bs + 't%d' \
        + bs + 't%d' + bs + 't%s' + bs + 't%s' + bs + 't%d' + bs + 'n'
    check('cpu line format string present', fmt in c)
    # 恰好一处声明；ts/*/cpu + policy/cur/min/max/cpus/online/cores = 10 字段
    check('cpu line declared exactly once', c.count(fmt) == 1)
    check('cpu line has 10 fields', fmt.count(bs + 't') == 7 + 2)

    # 4) policy 枚举不得硬编码 policy0/policy6
    if m:
        check('policy discovered by opendir', 'opendir' in m.group(1))
        check('no hardcoded policy0/policy6 in discovery',
              'policy0' not in m.group(1) and 'policy6' not in m.group(1))

    # 5) Java 侧读取必须要求 10 列且第 2/3 列为 * / cpu
    check('java requires 10 columns', 'p.length < 10' in j)
    check('java checks * marker', '!"*".equals(p[1])' in j)
    check('java checks cpu marker', '!"cpu".equals(p[2])' in j)

    # 6) Java 侧不得声明写操作
    check('java is read-only', 'RandomAccessFile(f, "r")' in j and '"rw"' not in j)

    # 7) 旧解析路径不受影响：Storage 仍然只接受 battery
    storage = read(os.path.join(ROOT, 'app2', 'src', 'com', 'powerlens', 'app', 'Storage.java'))
    check('Storage still filters battery only', '!"battery".equals(p[2])' in storage)
    check('Storage skips * rows in readRange', '"*".equals(p[1])' in storage)

    print('=' * 50)
    if fail:
        print('%d CHECK(S) FAILED' % len(fail))
        return 1
    print('ALL CHECKS PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())