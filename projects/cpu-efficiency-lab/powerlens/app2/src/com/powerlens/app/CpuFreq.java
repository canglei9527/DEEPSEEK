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
     * 若尾部确实找不到（例如刚升级、尾部全是 UID 行），再从尾往前分块
     * 倒序回扫（scanBack），命中最新采样组即停。
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
        // 先试尾部（正常路径，快）；尾部没有 cpu 行才倒序回扫（升级后的旧文件、
        // 或守护停采后窗口盖不住文件尾的情况）
        Snapshot tail = scanTail(f, from, to);
        if (!tail.isEmpty()) {
            return tail;
        }
        return scanBack(f, from, to);
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

    /** 倒序回扫的字节硬上限：应对时间戳恒不可解析的异常文件，防无限读。 */
    private static final long BACK_SCAN_CAP = 32L * 1024 * 1024;

    /**
     * 从文件尾往前分块回扫，攒到最新一批 cpu 行即停。
     *
     * 旧实现是从头到尾的 scanAll：snapshots.tsv 实测 25MB/35.8万行，
     * 正扫要逐行 readLine 到底。它一旦被主线程碰到就是 ANR
     * （2026-11-12 anr_2026-11-12_08-44，栈停在 scanAll 的 readLine）。
     * 即便现在调用方已挪到后台线程，正扫的代价也没必要：文件按时间序
     * 追加，"最后一条"必然在最靠尾的位置 —— 从尾往前扫，offer 一旦
     * 报"遇到更旧的一行"（返回 2）或"已早于窗口下界"（返回 3）就停，
     * 最坏开销从"整个文件"压回"最近几次采样"。
     */
    private static Snapshot scanBack(File f, long from, long to) {
        Snapshot snap = new Snapshot();
        RandomAccessFile raf = null;
        try {
            raf = new RandomAccessFile(f, "r");
            long pos = f.length();
            Accum acc = new Accum();
            String pendingTail = "";   // 已扫内容中最老一行的后半段，等更早的块补头部
            long scanned = 0L;
            boolean stop = false;
            while (pos > 0 && !stop) {
                long start = Math.max(0L, pos - TAIL_BYTES);
                int n = (int) (pos - start);
                raf.seek(start);
                byte[] buf = new byte[n];
                raf.readFully(buf);
                scanned += n;
                // 按 \n 切块：parts 是块内以换行结束的段（parts[0] 可能缺行首），
                // lastSeg 是最后一个换行之后的段（与更靠尾的内容拼成完整行）
                ArrayList<String> parts = new ArrayList<String>();
                int segStart = 0;
                for (int i = 0; i < n; i++) {
                    if (buf[i] == '\n') {
                        parts.add(new String(buf, segStart, i - segStart, "ISO-8859-1"));
                        segStart = i + 1;
                    }
                }
                if (parts.isEmpty()) {
                    // 整块没有换行：全是某一行中段，并入待拼尾段继续往前找
                    pendingTail = new String(buf, 0, n, "ISO-8859-1") + pendingTail;
                } else {
                    String lastSeg = new String(buf, segStart, n - segStart, "ISO-8859-1");
                    int st = acc.offer(lastSeg + pendingTail, from, to);
                    if (st == 2 || st == 3) {
                        stop = true;
                    } else {
                        for (int i = parts.size() - 1; i >= 1 && !stop; i--) {
                            st = acc.offer(parts.get(i), from, to);
                            if (st == 2 || st == 3) {
                                stop = true;
                            }
                        }
                        if (!stop) {
                            pendingTail = parts.get(0);
                        }
                    }
                }
                pos = start;
                if (scanned >= BACK_SCAN_CAP) {
                    break;
                }
            }
            if (!stop && pendingTail.length() > 0) {
                acc.offer(pendingTail, from, to);   // 块扫到文件头仍未终止的那一行
            }
            acc.fill(snap);
        } catch (Throwable ignored) {
            // 并发写入/截断时读失败不应影响电量主功能
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

        /**
         * 返回值供倒序扫描判断何时停止（正序调用方忽略返回值）：
         *   0 = 与 cpu 行无关 / 在窗口上界之外，继续；
         *   1 = 已收入当前最新采样组（同组还要继续收其余 policy）；
         *   2 = 遇到早于当前组的 cpu 行 —— 最新组已完整，可停；
         *   3 = 遇到能解析且早于 from 的时间 —— 时间序文件里再往前全部
         *       在窗口外，可停。
         */
        int offer(String line, long from, long to) {
            if (line.length() == 0) {
                return 0;
            }
            if (line.charAt(line.length() - 1) == '\r') {
                // 分块读入的行不像 readLine 那样自动剥行尾 \r
                line = line.substring(0, line.length() - 1);
            }
            if (line.indexOf('\t') <= 0) {
                return 0;
            }
            long ts = Storage.l(line.substring(0, line.indexOf('\t')), -1L);
            if (ts >= 0 && ts < from) {
                return 3;
            }
            String[] p = line.split("\t");
            // ts * cpu <policy> cur min max cpus online cores  => 10 列
            if (p.length < 10 || !"*".equals(p[1]) || !"cpu".equals(p[2])) {
                return 0;
            }
            if (ts > to) {
                return 0;
            }
            if (ts > latestTs) {
                latestTs = ts;
                byPolicy.clear();   // 进入更新的一组；当前行仍要继续走下方入库
            } else if (ts < latestTs) {
                return 2;
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
            return 1;
        }

        void fill(Snapshot snap) {
            // 倒序扫描时组内插入顺序是反的（policy6 先入），按名排序保证展示稳定
            java.util.List<Policy> ps = new ArrayList<Policy>(byPolicy.values());
            java.util.Collections.sort(ps, new java.util.Comparator<Policy>() {
                @Override
                public int compare(Policy a, Policy b) {
                    return a.name.compareTo(b.name);
                }
            });
            snap.policies.addAll(ps);
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