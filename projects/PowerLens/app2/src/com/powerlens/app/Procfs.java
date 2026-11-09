package com.powerlens.app;

import java.io.File;
import java.io.RandomAccessFile;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * 轻量数据源：procfs（对比 dumpsys 快 13 倍、小 65 倍）
 *
 * ===== 为什么换 =====
 * dumpsys batterystats --charged  实测 666 ms / 280 KB / 16144 行
 *   - CPU 记在 system_server(uid 1000) 头上，不在 PowerLens 名下
 *   - 每 10 分钟一次 = 一天 96 秒 system_server CPU，用户看不见但确实在耗电
 * procfs 两个文件                实测  50 ms /   4 KB /   196 行
 *   - 纯内核读取，零用户态解析，不落 system_server
 *
 * ===== 数据源与局限（必须诚实标注）=====
 * /proc/uid_cputime/show_uid_stat  → 每 UID 用户态+内核态 µs（精确，内核计数器）
 * /proc/uid_io/stats               → 每 UID 读写字节（精确，内核计数器）
 * 这两个文件需要 root（实测非 root 是 Permission denied）。
 *
 * 拿不到的维度：按 UID 的「息屏 CPU」「唤醒锁时长」「前台/后台时长」。
 * 这些只在 batterystats 里有。所以：
 *   - 默认走 procfs 主口径（总 CPU + IO），足够回答"谁在耗电"
 *   - 需要息屏维度时，用 --full 模式跑一次 dumpsys（低频，比如手动点/每小时一次）
 */
final class Procfs {

    private Procfs() {
    }

    static final class Uid {
        int uid;
        String pkg = "?";
        long cpuUs;      // utime + stime，微秒
        long rx;         // rchar，字节
        long tx;         // wchar，字节

        long cpuMs() {
            return cpuUs / 1000L;
        }

        double weight() {
            // 主口径：CPU 秒 + 流量（1 MB ≈ 2 单位）
            return cpuUs / 1_000_000.0d + (rx + tx) / 1048576.0d * 2.0d;
        }
    }

    static final class Battery {
        long chargeUah = -1;
        int capacity = -1;
        String status = "?";
    }

    /** 读 /proc/uid_cputime/show_uid_stat： "<uid>: <utime_us> <stime_us>" */
    static void loadCpu(Map<Integer, Uid> map) throws Exception {
        RandomAccessFile f = null;
        try {
            f = new RandomAccessFile("/proc/uid_cputime/show_uid_stat", "r");
            String line;
            while ((line = f.readLine()) != null) {
                int colon = line.indexOf(':');
                if (colon <= 0) {
                    continue;
                }
                int uid;
                try {
                    uid = Integer.parseInt(line.substring(0, colon).trim());
                } catch (Throwable e) {
                    continue;
                }
                String[] p = line.substring(colon + 1).trim().split("\\s+");
                if (p.length < 2) {
                    continue;
                }
                long u, s;
                try {
                    u = Long.parseLong(p[0]);
                    s = Long.parseLong(p[1]);
                } catch (Throwable e) {
                    continue;
                }
                Uid d = map.get(uid);
                if (d == null) {
                    d = new Uid();
                    d.uid = uid;
                    map.put(uid, d);
                }
                d.cpuUs = u + s;
            }
        } finally {
            if (f != null) {
                try {
                    f.close();
                } catch (Throwable ignored) {
                }
            }
        }
    }

    /** 读 /proc/uid_io/stats： "<uid> <rchar> <wchar> ..." */
    static void loadIo(Map<Integer, Uid> map) throws Exception {
        RandomAccessFile f = null;
        try {
            f = new RandomAccessFile("/proc/uid_io/stats", "r");
            String line;
            while ((line = f.readLine()) != null) {
                String[] p = line.trim().split("\\s+");
                if (p.length < 3) {
                    continue;
                }
                int uid;
                long r, w;
                try {
                    uid = Integer.parseInt(p[0]);
                    r = Long.parseLong(p[1]);
                    w = Long.parseLong(p[2]);
                } catch (Throwable e) {
                    continue;
                }
                Uid d = map.get(uid);
                if (d == null) {
                    d = new Uid();
                    d.uid = uid;
                    map.put(uid, d);
                }
                d.rx = r;
                d.tx = w;
            }
        } finally {
            if (f != null) {
                try {
                    f.close();
                } catch (Throwable ignored) {
                }
            }
        }
    }

    /** 用 /proc/<pid>/cmdline 补包名（只补还没名字的） */
    static void loadNames(Map<Integer, Uid> map) {
        File proc = new File("/proc");
        String[] kids = proc.list();
        if (kids == null) {
            return;
        }
        for (String k : kids) {
            if (k.isEmpty() || !Character.isDigit(k.charAt(0))) {
                continue;
            }
            int pid;
            try {
                pid = Integer.parseInt(k);
            } catch (Throwable e) {
                continue;
            }
            int uid = uidOfPid(pid);
            if (uid < 0) {
                continue;
            }
            Uid d = map.get(uid);
            if (d == null || !"?".equals(d.pkg)) {
                continue;
            }
            String name = cmdlineOf(pid);
            if (name != null && !name.isEmpty()) {
                d.pkg = name;
            }
        }
    }

    private static int uidOfPid(int pid) {
        RandomAccessFile f = null;
        try {
            f = new RandomAccessFile("/proc/" + pid + "/status", "r");
            String line;
            while ((line = f.readLine()) != null) {
                if (line.startsWith("Uid:")) {
                    String[] p = line.substring(4).trim().split("\\s+");
                    if (p.length > 0) {
                        return Integer.parseInt(p[0]);
                    }
                }
            }
        } catch (Throwable ignored) {
        } finally {
            if (f != null) {
                try {
                    f.close();
                } catch (Throwable ignored) {
                }
            }
        }
        return -1;
    }

    private static String cmdlineOf(int pid) {
        RandomAccessFile f = null;
        try {
            f = new RandomAccessFile("/proc/" + pid + "/cmdline", "r");
            byte[] b = new byte[256];
            int n = f.read(b);
            if (n <= 0) {
                return null;
            }
            StringBuilder sb = new StringBuilder(n);
            for (int i = 0; i < n; i++) {
                byte c = b[i];
                if (c == 0 || c == ':' || c == '\n') {
                    break;
                }
                sb.append((c >= 0x20 && c < 0x7f) ? (char) c : '_');
            }
            return sb.toString();
        } catch (Throwable e) {
            return null;
        } finally {
            if (f != null) {
                try {
                    f.close();
                } catch (Throwable ignored) {
                }
            }
        }
    }

    static Battery battery() {
        Battery b = new Battery();
        b.chargeUah = readLong("/sys/class/power_supply/battery/charge_counter", -1);
        b.capacity = (int) readLong("/sys/class/power_supply/battery/capacity", -1);
        String s = readText("/sys/class/power_supply/battery/status");
        if (s != null && !s.isEmpty()) {
            b.status = s;
        }
        return b;
    }

    static long readLong(String path, long def) {
        String s = readText(path);
        if (s == null || s.isEmpty()) {
            return def;
        }
        try {
            return Long.parseLong(s.trim());
        } catch (Throwable e) {
            return def;
        }
    }

    static String readText(String path) {
        RandomAccessFile f = null;
        try {
            f = new RandomAccessFile(path, "r");
            return f.readLine();
        } catch (Throwable e) {
            return null;
        } finally {
            if (f != null) {
                try {
                    f.close();
                } catch (Throwable ignored) {
                }
            }
        }
    }

    /** 一次性快照（root 下直接读；非 root 会抛异常，调用方降级到 dumpsys） */
    static List<Uid> snapshot() throws Exception {
        Map<Integer, Uid> map = new HashMap<>();
        loadCpu(map);
        loadIo(map);
        loadNames(map);
        return new ArrayList<>(map.values());
    }
}