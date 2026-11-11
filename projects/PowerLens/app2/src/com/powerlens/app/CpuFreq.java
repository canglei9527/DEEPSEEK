package com.powerlens.app;

import java.io.File;
import java.io.RandomAccessFile;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 读取 plcollect 写入的 CPU 频率快照行。
 *
 * ===== 为什么需要它 =====
 * 原来的采集只有「每 UID 的 CPU 毫秒」这一个维度。同样 1 秒 CPU 时间，
 * 跑在 1.0 GHz 和 2.4 GHz 上耗电完全不同；没有频率就无法解释
 * 「某个 UID 的 CPU 时间为什么便宜/贵」，也无法判断当前是否被限频。
 *
 * ===== 数据来源与边界 =====
 * 行格式（plcollect 1.3 起）：
 *   ts  *  cpu  <policy>  <cur_khz>  <min_khz>  <max_khz>  <cpus>  <online>  <cores>
 *
 * 三个重要限制，不能跨过：
 *   1. 这是**只读快照**，min/max 是系统当时的限频，不是本工具设置的。
 *   2. scaling_cur_freq 是采样瞬间的频率，不是区间平均值。
 *      两次采样之间频率怎么变，这个文件里没有。
 *   3. 有频率 != 知道该频率下干了多少活。真正的能效需要固定频率负载，
 *      那属于 CPU Efficiency Lab 的矩阵实验，不是这里的数据能替代的。
 *
 * 因此本类只回答「当时频率/限频大概是什么」，不产出任何"最优频点"结论。
 */
final class CpuFreq {

    private CpuFreq() {
    }

    /**
     * 从文件尾部倒着读多少字节去找最后一批 cpu 行。
     *
     * ===== 为什么必须倒读 =====
     * snapshots.tsv 实测已达 25 MB / 35.8 万行。为了显示"当前频率"
     * 而全文件扫一遍，会让本已缓慢的刷新再多等数秒（真机实测刷新
     * 从数秒涨到 20 秒以上，界面长时间停在"启动中…"）。
     *
     * cpu 行由 plcollect 在每次采样末尾追加，所以最后一批必然在文件尾部；
     * 倒读 64 KB 足以覆盖多次采样（每次采样只有 policy 数×1 行）。
     * 若尾部确实找不到（例如刚升级、尾部全是 UID 行），再退回全量扫描。
     */
    private static final int TAIL_BYTES = 64 * 1024;

    /** 单个 cpufreq policy 在一个时间点上的快照。 */
    static final class Policy {
        String name = "?";
        int curKhz = -1;
        int minKhz = -1;
        int maxKhz = -1;
        String cpus = "-";
        long ts;
    }

    /** 一次采样的完整 CPU 状态。 */
    static final class Snapshot {
        final List<Policy> policies = new ArrayList<>();
        String online = "-";
        int cores = -1;
        long ts;

        boolean isEmpty() {
            return policies.isEmpty() && "-".equals(online) && cores < 0;
        }

        /** 是否存在 min != max 的 policy（即系统有明确的限频区间）。 */
        boolean hasRange() {
            for (Policy p : policies) {
                if (p.minKhz > 0 && p.maxKhz > 0 && p.maxKhz > p.minKhz) {
                    return true;
                }
            }
            return false;
        }
    }

    /**
     * 取区间内最后一条 CPU 快照（即"当前"状态）。
     *
     * 为什么取最后一条而不是平均：snapshots.tsv 是时间序追加文件，
     * 最后一条最接近用户此刻看到界面时的状态；历史平均会让
     * "现在是否被限频"这个问题失去意义。
     */
    static Snapshot latest(File f, long from, long to) {
        if (f == null || !f.isFile()) {
            return new Snapshot();
        }
        // 先试尾部（正常路径，快）；尾部没有 cpu 行才全量扫描（升级后的旧文件）
        Snapshot tail = scanTail(f, from, to);
        if (!tail.isEmpty()) {
            return tail;
        }
        return scanAll(f, from, to);
    }

    /** 只读文件末尾一段，正常情况下一次命中。 */
    private static Snapshot scanTail(File f, long from, long to) {
        Snapshot snap = new Snapshot();
        RandomAccessFile raf = null;
        try {
            long len = f.length();
            long start = Math.max(0L, len - TAIL_BYTES);
            raf = new RandomAccessFile(f, "r");
            raf.seek(start);
            String line = start > 0 ? raf.readLine() : null;  // 丢弃可能被截断的首行
            Accum acc = new Accum();
            while ((line = raf.readLine()) != null) {
                acc.offer(line, from, to);
            }
            acc.fill(snap);
        } catch (Throwable ignored) {
            // 并发写入/截断时读失败不应影响电量主功能
        } finally {
            close(raf);
        }
        return snap;
    }

    /** 全量扫描（仅在尾部未命中时使用）。 */
    private static Snapshot scanAll(File f, long from, long to) {
        Snapshot snap = new Snapshot();
        RandomAccessFile raf = null;
        try {
            raf = new RandomAccessFile(f, "r");
            Accum acc = new Accum();
            String line;
            while ((line = raf.readLine()) != null) {
                acc.offer(line, from, to);
            }
            acc.fill(snap);
        } catch (Throwable ignored) {
        } finally {
            close(raf);
        }
        return snap;
    }

    private static void close(RandomAccessFile raf) {
        if (raf != null) {
            try {
                raf.close();
            } catch (Throwable ignored) {
            }
        }
    }

    /** 累积器：只保留时间戳最新的一批。 */
    private static final class Accum {
        private final Map<String, Policy> byPolicy = new LinkedHashMap<>();
        private long latestTs = -1L;
        private String online = "-";
        private int cores = -1;

        void offer(String line, long from, long to) {
            if (line.length() == 0 || line.indexOf('\t') <= 0) {
                return;
            }
            String[] p = line.split("\t");
            // ts * cpu <policy> cur min max cpus online cores  => 10 列
            if (p.length < 10 || !"*".equals(p[1]) || !"cpu".equals(p[2])) {
                return;
            }
            long ts = Storage.l(p[0], -1L);
            if (ts < from || ts > to) {
                return;
            }
            if (ts > latestTs) {
                latestTs = ts;
                byPolicy.clear();
            } else if (ts < latestTs) {
                return;
            }
            Policy pol = new Policy();
            pol.ts = ts;
            pol.name = Storage.safe(p[3]);
            pol.curKhz = (int) Storage.l(p[4], -1L);
            pol.minKhz = (int) Storage.l(p[5], -1L);
            pol.maxKhz = (int) Storage.l(p[6], -1L);
            pol.cpus = Storage.safe(p[7]).replace('_', ' ');
            byPolicy.put(pol.name, pol);
            online = Storage.safe(p[8]).replace('_', ' ');
            cores = (int) Storage.l(p[9], -1L);
        }

        void fill(Snapshot snap) {
            snap.policies.addAll(byPolicy.values());
            snap.online = online;
            snap.cores = cores;
            snap.ts = latestTs;
        }
    }

    /** kHz → "1.55G" / "960M"，便于状态栏一行放下。 */
    static String fmtKhz(int khz) {
        if (khz <= 0) {
            return "?";
        }
        if (khz >= 1_000_000) {
            return String.format(java.util.Locale.US, "%.2fG", khz / 1_000_000.0);
        }
        return String.format(java.util.Locale.US, "%.0fM", khz / 1000.0);
    }

    /**
     * 渲染成一行摘要，例如：
     *   CPU 8核 在线 0-7
     *   policy0 [0 1 2 3 4 5] 当前 557M 范围 557M-2.75G
     *
     * 当某 policy 的 max 明显低于硬件上限时不做"被限频"断言 ——
     * 只如实展示当前上下限，是否属于限频由用户结合场景判断。
     */
    static String describe(Snapshot snap) {
        if (snap == null || snap.isEmpty()) {
            return null;
        }
        StringBuilder sb = new StringBuilder();
        if (snap.cores > 0) {
            sb.append("CPU ").append(snap.cores).append("核");
            if (!"-".equals(snap.online) && !snap.online.isEmpty()) {
                sb.append(" 在线 ").append(snap.online);
            }
        }
        for (Policy p : snap.policies) {
            if (sb.length() > 0) {
                sb.append("\n");
            }
            sb.append(p.name);
            if (p.cpus != null && !"-".equals(p.cpus)) {
                sb.append(" [").append(p.cpus).append("]");
            }
            sb.append(" 当前 ").append(fmtKhz(p.curKhz));
            sb.append(" 范围 ").append(fmtKhz(p.minKhz)).append("-").append(fmtKhz(p.maxKhz));
        }
        return sb.length() == 0 ? null : sb.toString();
    }
}