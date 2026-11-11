package com.powerlens.app;

import android.util.Log;

import java.io.File;
import java.io.RandomAccessFile;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * 维度 + 真实 mAh 数据（来自 pldims，低频 dumpsys）。
 *
 * ===== 为什么需要它 =====
 *   主采集器 plcollect 走 procfs，97 ms 出 150 个 UID，
 *   但 /proc/uid_cputime + /proc/uid_io 没有以下维度：
 *     fg（前台时长）/ bg（后台时长）/ fgs（前台服务时长）
 *     cpuOff（息屏 CPU）/ wl（唤醒锁）/ wlOff（息屏唤醒锁）
 *   这些只有 dumpsys batterystats 有。
 *
 * ===== v3 新增：每 UID 的真实 mAh 分项 =====
 *   dumpsys 的 Estimated power use 里，每个 UID 都给了系统自己算好的电量：
 *     UID u0a341: ...
 *         screen=71.6 cpu=135 audio=22.0 video=38.2 mobile_radio=558 ...
 *   这是【按硬件功耗模型算出来的 mAh】，比任何手工权重都可靠。
 *
 *   ===== 为什么必须换成它 =====
 *   旧版（v2）只采时长，电量由 App 侧用
 *   "CPU 秒 + 唤醒锁秒×0.6 + 流量MB×0.44" 猜出来的权重分摊。
 *   实测后果（4.5 小时区间，实测放电 1207 mAh）：
 *     sys:logd  CPU 只有 1 分钟 → 分到 463.5 mAh（38.4%）
 *   荒谬：总放电里【屏幕 560 mAh + 基带 1005 mAh + 待机底噪】这些
 *   "平台级"功耗被强行摊到少数几个有 CPU 时间的进程头上。
 *
 *   现在：份额用 dumpsys 的模型 mAh，总量用真实电池计数器，
 *   两者相乘 = 既符合物理总量，又符合系统自己的归因模型。
 *
 * ===== 文件格式（pldims v3，制表符分隔，22 列）=====
 *   0 ts(ms)  1 uid
 *   2 ΔfgMs   3 ΔbgMs   4 ΔfgsMs
 *   5 ΔcpuOffMs 6 ΔwlMs 7 ΔwlOffMs
 *   8 intervalMs
 *   9 ΔcpuµAh  10 ΔscreenµAh 11 ΔradioµAh 12 ΔwlµAh   13 ΔwifiµAh  14 ΔaudioµAh
 *   15 ΔvideoµAh 16 ΔsensorsµAh 17 ΔcameraµAh 18 ΔgnssµAh 19 ΔdisplayµAh 20 ΔotherµAh
 *   21 ΣΔµAh
 *
 *   dims.global（一行 15 列）：ts interval + 12 个全机累计 µAh + Σ
 *
 * ===== 向后兼容 =====
 *   v2 文件只有 9 列（无 mAh）。检测到列数不足时 mAh 全 0，
 *   hasMah() 返回 false，App 侧自动退回旧的权重分摊。
 */
final class Dims {

    private static final String TAG = "PowerLens";
    static final String FILE_TMP = "/data/local/tmp/powerlens/dims.tsv";
    static final String GLOBAL_TMP = "/data/local/tmp/powerlens/dims.global";

    /**
     * 跨轮次逐 UID 累计表（pldims 每次采样后更新）。
     *
     * ===== 为什么必须有它 =====
     *   dims.tsv 每 5 分钟被【整体覆盖】，只剩最后一帧。
     *   而查询窗口是 1/6/24 小时 —— 用 5 分钟的一帧代表 6 小时，
     *   前台/后台比例必然失真。
     *
     *   实测：红果短剧在 6 小时窗口里 fg 累计 2073s、bg 8319s，
     *   但最后那一帧恰好 fg=0/bg=301s，于是显示成"纯后台"。
     */
    static final String TOTAL_TMP = "/data/local/tmp/powerlens/dims.total.tsv";

    /**
     * 按【日】归档的原始维度批次（pldims 每轮把整份 dims.tsv 追加进来）。
     *
     * ===== 为什么这才是权威数据源 =====
     *   每条记录自带 ts，可以【按查询窗口精确切片】：
     *     选「近1时」就只取最近 1 小时的帧，选「近24时」就取 24 小时。
     *
     *   dims.total.tsv 是【无窗口】的全程累加，只能回答"自首次运行以来"，
     *   拿它回答"近1时"会带上 7.9 小时的旧账（份额被稀释）。
     */
    static final String HIST_DIR = "/data/local/tmp/plc/dims-history";

    /** mAh 字段序号（与 pldims.c 的 MAH_* 枚举严格一致） */
    static final int M_CPU = 0, M_SCREEN = 1, M_RADIO = 2, M_WL = 3, M_WIFI = 4,
            M_AUDIO = 5, M_VIDEO = 6, M_SENSORS = 7, M_CAMERA = 8, M_GNSS = 9,
            M_DISPLAY = 10, M_OTHER = 11, M_N = 12;

    static final String[] MAH_NAME = {
            "CPU", "屏幕", "移动网络", "唤醒锁", "Wi-Fi", "音频",
            "视频", "传感器", "相机", "定位", "显示", "其它"
    };

    /** 单个 UID 的维度（毫秒）+ 电量（µAh） */
    static final class One {
        int uid;
        long fgMs;
        long bgMs;
        long fgsMs;
        long cpuOffMs;
        long wlMs;
        long wlOffMs;
        long[] mahUah = new long[M_N];
        long mahSumUah;

        long sum() {
            return fgMs + bgMs + fgsMs + cpuOffMs + wlMs + wlOffMs;
        }

        /** 该 UID 的模型电量（mAh） */
        double mah() {
            return mahSumUah / 1000.0d;
        }
    }

    /** 整份维度快照 + 汇总 */
    static final class Snapshot {
        /** 本次采样的时间戳（ms） */
        long ts;
        /** 距上次采样的间隔（ms）；0 表示未知 */
        long intervalMs;
        int count;
        long totFg, totBg, totFgs, totCpuOff, totWl, totWlOff;
        final long[] totMah = new long[M_N];
        long totMahSum;
        /** 全机累计（dims.global），µAh */
        final long[] globalMah = new long[M_N];
        long globalSum;
        long globalTs;
        Map<Integer, One> byUid = new HashMap<>();

        /**
         * 逐 UID 的【跨轮次累计】（来自 dims.total.tsv）。
         * 键和 byUid 一样是 uid，但语义不同：
         *   byUid      = 最后一次 5 分钟采样的增量（会被覆盖）
         *   byUidTotal = 自 pldims 首次运行以来的累加值
         *
         * 前台/后台口径必须用后者，否则整段时长会被一帧代表。
         */
        Map<Integer, One> byUidTotal = new HashMap<>();

        /** 累计覆盖的真实时长（ms），来自 dims.total.tsv 的 span 列 */
        long totalSpanMs;

        /**
         * 本次聚合覆盖的时间窗口与来源。
         *
         * byUidTotal 可能来自两个源：
         *   ① 按窗口从 dims-history 精确聚合（首选，winMs > 0）
         *   ② dims.total.tsv 的全程累加（退化路径，winMs = 0）
         */
        long winFromMs;
        long winToMs;
        int  winBatches;   // 落在窗口内的采样批次数
        int  winSource;    // 0=无 1=dims.total.tsv 2=dims-history


        /** 有没有可用的累计数据（老版本 pldims 不产出） */
        boolean hasTotal() {
            return totalSpanMs > 0 && !byUidTotal.isEmpty();
        }

        /** 该 UID 的前台占比（0..1） */
        double ratioFg(int uid) {
            One o = byUid.get(uid);
            if (o == null || totFg <= 0) return 0;
            return (double) o.fgMs / totFg;
        }

        double ratioBg(int uid) {
            One o = byUid.get(uid);
            if (o == null || totBg <= 0) return 0;
            return (double) o.bgMs / totBg;
        }

        double ratioFgs(int uid) {
            One o = byUid.get(uid);
            if (o == null || totFgs <= 0) return 0;
            return (double) o.fgsMs / totFgs;
        }

        double ratioCpuOff(int uid) {
            One o = byUid.get(uid);
            if (o == null || totCpuOff <= 0) return 0;
            return (double) o.cpuOffMs / totCpuOff;
        }

        double ratioWl(int uid) {
            One o = byUid.get(uid);
            if (o == null || (totWl + totWlOff) <= 0) return 0;
            return (double) (o.wlMs + o.wlOffMs) / (totWl + totWlOff);
        }

        /**
         * 该 UID 在指定维度的【累计秒数】—— 用于 1/6/24 小时窗口。
         *
         * 有 total 数据时优先用它；没有（老 pldims）就退回单帧。
         * 退回是有风险的（会重现"红果被算成纯后台"），
         * 所以 App 侧还会用 span 做一次合理性钳制。
         */
        double secondsTotal(int uid, int mode) {
            One o = byUidTotal.get(uid);
            if (o == null) {
                return secondsOf(uid, mode);
            }
            if (mode == 1) return o.fgMs / 1000.0d;
            if (mode == 2) return o.bgMs / 1000.0d;
            if (mode == 3) return o.fgsMs / 1000.0d;
            return 0;
        }

        /** 该 UID 的累计前台 + 后台秒数（用来算前台占比） */
        double foregroundShare(int uid) {
            One o = byUidTotal.get(uid);
            if (o == null) {
                o = byUid.get(uid);
            }
            if (o == null) {
                return 0;
            }
            double tot = o.fgMs + o.bgMs + o.fgsMs;
            return tot <= 0 ? 0 : o.fgMs / tot;
        }

        /** 该 UID 在指定维度的【增量秒数】（单帧，保留给旧代码路径） */
        double secondsOf(int uid, int mode) {
            One o = byUid.get(uid);
            if (o == null) return 0;
            if (mode == 1) return o.fgMs / 1000.0d;
            if (mode == 2) return o.bgMs / 1000.0d;
            if (mode == 3) return o.fgsMs / 1000.0d;
            return 0;
        }

        /**
         * 该 UID 的模型电量（mAh）。
         * 返回 0 表示这一轮没有 mAh 数据（旧格式/首轮基线）。
         */
        double mahOf(int uid) {
            One o = byUid.get(uid);
            return o == null ? 0 : o.mah();
        }

        /** 该 UID 的分项电量（mAh），用于展开明细 */
        double[] mahPartsOf(int uid) {
            double[] out = new double[M_N];
            /*
             * ===== 明细必须和总额同源 =====
             *
             * 原来这里固定读 byUid（单帧），而总额走 byUidTotal（窗口），
             * 于是出现"总额 473.7 mAh、明细加起来 1.05"的错配。
             *
             * 有累计表就用累计表的分项 —— 两者都是同一个窗口聚合出来的。
             */
            One o = byUidTotal.get(uid);
            boolean fromTotal = (o != null);
            if (o == null) {
                o = byUid.get(uid);
            }
            if (o == null) {
                return out;
            }
            /*
             * 累计表可能没有分项（dims.total.tsv 只存总列）。
             * 这种情况退回单帧分项，让明细至少能显示内容，
             * 但调用方要知道它和总额不同源 —— 见返回值的用法。
             */
            if (fromTotal) {
                long sumParts = 0;
                for (int i = 0; i < M_N; i++) sumParts += o.mahUah[i];
                if (sumParts == 0) {
                    One single = byUid.get(uid);
                    if (single != null) o = single;
                }
            }
            for (int i = 0; i < M_N; i++) {
                out[i] = o.mahUah[i] / 1000.0d;
            }
            return out;
        }

        /**
         * ===== 累计版 mAh（跨轮次）=====
         *
         * 单帧 mAh 有两个致命问题：
         *
         *  ① 数值太小，撑不起份额分母。
         *     实测最后一帧 78 个 UID 的模型 mAh 加起来只有 14.657 mAh，
         *     而同一窗口实际放电 1754 mAh —— 模型只覆盖 0.8%。
         *     剩下 99.2% 没人认领，share 就被放大 120 倍：
         *       PowerLens 明细写 8.00 mAh，界面却显示 1136.4 mAh。
         *
         *  ② 只代表 5 分钟。
         *     查询窗口是 1/6/24 小时，两者对不上。
         *
         * 用 dims.total.tsv 的累计 mAh 就没这两个问题：
         *     141 个 UID 累计 2980 mAh，和实测放电同量级。
         */
        double mahTotalOf(int uid) {
            One o = byUidTotal.get(uid);
            if (o == null) {
                return mahOf(uid);   // 没有累计表时退回单帧
            }
            /*
             * 用 mahSumUah（pldims 写的总列，权威值），
             * 不是 mahUah[M_OTHER] —— 后者只是 12 个分项里的"其它"，
             * 用它当总额会把微信 473.7 mAh 显示成几十。
             */
            return o.mahSumUah / 1000.0d;
        }

        /** 累计表里所有 UID 的模型 mAh 之和（份额分母） */
        double totalMahTotal() {
            if (byUidTotal.isEmpty()) {
                return totalMah();
            }
            double sum = 0;
            for (One o : byUidTotal.values()) {
                sum += o.mahSumUah / 1000.0d;
            }
            return sum;
        }

        /** 是否有可用的【累计】mAh（区别于单帧 hasMah） */
        boolean hasMahTotal() {
            return !byUidTotal.isEmpty() && totalMahTotal() > 0;
        }

        /** 本轮是否有可用的 mAh 数据 */
        boolean hasMah() {
            return totMahSum > 0;
        }

        /** mAh 总量（mAh） */
        double totalMah() {
            return totMahSum / 1000.0d;
        }

        boolean isEmpty() {
            return byUid.isEmpty()
                    || (totFg + totBg + totFgs + totCpuOff + totWl + totWlOff + totMahSum) == 0;
        }
    }

    private Dims() {
    }

    /**
     * 读跨轮次累计表 dims.total.tsv。
     *
     * 格式（pldims 写入，制表符分隔）：
     *   uid  fgMs  bgMs  fgsMs  cpuOffMs  wlMs  wlOffMs  mahUah  spanMs
     *
     * 和 dims.tsv 前 8 列同序，只是没有 ts 列、多一个 span 列。
     * 没有这个文件（老版本 pldims / 刚清零）时返回空 Snapshot 部分，
     * 调用方用 hasTotal() 判断后决定是否退回单帧。
     */
    static void loadTotal(android.content.Context ctx, Snapshot into) {
        File f = new File(TOTAL_TMP);
        if (!f.exists() || !f.canRead()) {
            File alt = new File(ctx.getFilesDir(), "dims.total.tsv");
            if (alt.exists() && alt.canRead()) {
                f = alt;
            } else {
                return;
            }
        }

        long span = 0;
        RandomAccessFile raf = null;
        try {
            raf = new RandomAccessFile(f, "r");
            String line;
            while ((line = raf.readLine()) != null) {
                if (line.isEmpty()) continue;
                String[] p = line.split("\t");
                if (p.length < 9) continue;
                int uid;
                try {
                    uid = Integer.parseInt(p[0].trim());
                } catch (Throwable e) {
                    continue;
                }
                One o = new One();
                o.uid = uid;
                o.fgMs = l(p[1]);
                o.bgMs = l(p[2]);
                o.fgsMs = l(p[3]);
                o.cpuOffMs = l(p[4]);
                o.wlMs = l(p[5]);
                o.wlOffMs = l(p[6]);
                long sum = l(p[7]);
                /*
                 * dims.total.tsv 只有总列（没有 12 分项），
                 * 所以总额写进 mahSumUah，分项留 0。
                 * mahPartsOf 在这种情况下会退回单帧分项 ——
                 * 见 mahPartsOf 里的注释。
                 */
                o.mahSumUah = sum;
                long sp = l(p[8]);
                if (sp > span) span = sp;
                into.byUidTotal.put(uid, o);
            }
        } catch (Throwable e) {
            Log.w(TAG, "dims.total 读取失败", e);
        } finally {
            closeQuiet(raf);
        }
        into.totalSpanMs = span;
    }

    /**
     * ===== 按查询窗口精确聚合历史维度数据 =====
     *
     * 这是修「红果被算成后台」的最终形态。
     *
     * 旧路径的问题链：
     *   dims.tsv 每 5 分钟被覆盖 → 只剩最后一帧
     *   → 拿一帧代表 6 小时 → 红果 2073s 前台消失
     *
     * dims.total.tsv 修了一半（跨轮累计），但它【没有窗口】：
     *   选「近1时」仍返回自首次运行以来的全程累加，份额被旧账稀释。
     *
     * dims-history/*.tsv 才是权威源：每条记录自带 ts，
     * 可按 from..to 精确切片。本方法就是干这个。
     *
     * ===== 性能 =====
     *   实测历史文件 6906 行 / 482 KB，单次聚合 < 20 ms。
     *   用一遍扫描 + 直接 ts 区间判断（不用 HashSet，省掉装箱）。
     *
     * @param from  窗口起点（ms），<=0 表示不限制
     * @param to    窗口终点（ms）
     * @return true 表示成功聚合（into.byUidTotal 已被填充）
     */
    static boolean loadWindow(android.content.Context ctx, long from, long to,
                              Snapshot into) {
        File dir = new File(HIST_DIR);
        if (!dir.isDirectory() || !dir.canRead()) {
            return false;
        }
        File[] fs = dir.listFiles();
        if (fs == null || fs.length == 0) {
            return false;
        }

        /*
         * 只取 dims-history-YYYYMMDD.tsv（排除 dims-runs-*.tsv）。
         * 按文件名倒序，最近的先读；跨天时多文件一起聚合。
         */
        java.util.List<File> files = new java.util.ArrayList<>();
        for (File f : fs) {
            String n = f.getName();
            if (n.startsWith("dims-history-") && n.endsWith(".tsv") && f.canRead()) {
                files.add(f);
            }
        }
        if (files.isEmpty()) {
            return false;
        }
        java.util.Collections.sort(files, new java.util.Comparator<File>() {
            @Override
            public int compare(File a, File b) {
                return b.getName().compareTo(a.getName());
            }
        });

        java.util.Map<Integer, One> acc = new HashMap<>();
        /*
         * 只记 ts，不记整帧 —— 同一帧内所有 UID 共享同一个 interval，
         * 必须【整帧保留或整帧丢弃】，否则某 UID 的 fg 会超过全局 interval。
         * 但这里不预先过滤：一遍扫描同时判 ts 区间并累加，
         * 因为 from..to 是单调区间，不需要先知道有哪些 ts。
         */
        long maxInterval = 0;
        long minTs = Long.MAX_VALUE, maxTs = Long.MIN_VALUE;

        for (File f : files) {
            RandomAccessFile raf = null;
            try {
                raf = new RandomAccessFile(f, "r");
                String line;
                while ((line = raf.readLine()) != null) {
                    String[] p = line.split("\t");
                    if (p.length < 22) continue;
                    long ts;
                    int uid;
                    try {
                        ts = Long.parseLong(p[0]);
                        uid = Integer.parseInt(p[1]);
                    } catch (Throwable e) {
                        continue;
                    }
                    if (ts < from || ts > to) continue;
                    if (ts < minTs) minTs = ts;
                    if (ts > maxTs) maxTs = ts;

                    One o = acc.get(uid);
                    if (o == null) {
                        o = new One();
                        o.uid = uid;
                        acc.put(uid, o);
                    }
                    o.fgMs += l(p[2]);
                    o.bgMs += l(p[3]);
                    o.fgsMs += l(p[4]);
                    o.cpuOffMs += l(p[5]);
                    o.wlMs += l(p[6]);
                    o.wlOffMs += l(p[7]);
                    /*
                     * ===== 必须累加全部 12 个分项，不能只累加总列 =====
                     *
                     * mahPartsOf() 展开明细时读的是分项数组。
                     * 原来这里只累加 mahUah[M_OTHER]（总列），
                     * 于是 total 走窗口聚合、分项却是 0 ——
                     * 实测微信总额 473.7 mAh，展开后分项合计 0，
                     * 用户看到"明细加起来对不上总数"。
                     *
                     * dims-history 的列布局与 dims.tsv 一致：
                     *   9..20 = 12 个分项 µAh，21 = 合计
                     * 所以直接按下标累加即可，最后仍用总列校准。
                     */
                    for (int k = 0; k < M_N; k++) {
                        o.mahUah[k] += l(p[9 + k]);
                    }
                    /*
                     * 总列单独存一份到 M_OTHER 之外的地方？不行 ——
                     * M_OTHER 本来就是 12 项之一（"其它"）。
                     * 这里改为：分项照常累加，总数由 mah() 用分项之和得出。
                     * 但 pldims 写的总列（第 21 列）是权威值，
                     * 可能与分项之和有微小差异，所以额外记录到 mahSumUah。
                     */
                    o.mahSumUah += l(p[21]);
                    long iv = l(p[8]);
                    if (iv > maxInterval) maxInterval = iv;
                }
            } catch (Throwable e) {
                Log.w(TAG, "dims-history 读取失败 " + f.getName(), e);
            } finally {
                closeQuiet(raf);
            }
        }

        if (acc.isEmpty()) {
            Log.w(TAG, "dims-history 窗口内无数据 [" + from + "," + to + "]");
            return false;
        }

        into.byUidTotal = acc;
        into.totalSpanMs = maxInterval;
        into.winFromMs = from;
        into.winToMs = to;
        into.winBatches = (int) Math.max(0, (maxTs - minTs) / Math.max(1, maxInterval)) + 1;
        into.winSource = 2;
        return true;
    }
    /** 找 dims 文件：优先 app 私有目录，其次 /data/local/tmp */
    static File file(android.content.Context ctx) {
        File a = new File(ctx.getFilesDir(), "dims.tsv");
        File b = new File(FILE_TMP);
        long ta = a.exists() ? a.lastModified() : 0;
        long tb = b.exists() ? b.lastModified() : 0;
        if (ta <= 0 && tb <= 0) return b;
        return ta >= tb ? a : b;
    }

    /** 读最新一份维度（只保留最后一个时间戳的批次） */
    /**
     * 带查询窗口的加载：先用 dims-history 精确聚合，失败才退回全程累计。
     *
     * 三档优先级：
     *   ① dims-history 按 [from,to] 切片   ← 精确，窗口对得上
     *   ② dims.total.tsv 全程累加          ← 无窗口，但至少不是单帧
     *   ③ dims.tsv 最后一帧                ← 老 pldims 的退化路径
     */
    static Snapshot load(android.content.Context ctx, long from, long to) {
        Snapshot s = load(ctx);          // 这步已含 ②③
        if (from > 0 && to > from) {
            /* ① 覆盖掉 ② 的结果（load 里先填了 total） */
            Snapshot win = new Snapshot();
            if (loadWindow(ctx, from, to, win)) {
                s.byUidTotal = win.byUidTotal;
                s.totalSpanMs = win.totalSpanMs;
                s.winFromMs = win.winFromMs;
                s.winToMs = win.winToMs;
                s.winBatches = win.winBatches;
                s.winSource = win.winSource;
            }
        }
        return s;
    }
    static Snapshot load(android.content.Context ctx) {
        Snapshot s = new Snapshot();
        File f = file(ctx);
        if (f == null || !f.exists() || !f.canRead()) {
            return s;
        }

        // 先扫一遍找最大 ts（文件是追加写的，可能有历史批次）
        long maxTs = 0;
        RandomAccessFile raf = null;
        try {
            raf = new RandomAccessFile(f, "r");
            String line;
            while ((line = raf.readLine()) != null) {
                int t = line.indexOf('\t');
                if (t <= 0) continue;
                long ts;
                try {
                    ts = Long.parseLong(line.substring(0, t));
                } catch (Throwable e) {
                    continue;
                }
                if (ts > maxTs) maxTs = ts;
            }
        } catch (Throwable e) {
            Log.w(TAG, "dims 扫描失败", e);
        } finally {
            closeQuiet(raf);
        }
        if (maxTs <= 0) return s;

        // 再读该批次
        List<One> list = new ArrayList<>();
        long interval = 0;
        raf = null;
        try {
            raf = new RandomAccessFile(f, "r");
            String line;
            while ((line = raf.readLine()) != null) {
                String[] p = line.split("\t");
                if (p.length < 8) continue;
                long ts;
                int uid;
                try {
                    ts = Long.parseLong(p[0]);
                    uid = Integer.parseInt(p[1]);
                } catch (Throwable e) {
                    continue;
                }
                if (ts != maxTs) continue;
                One o = new One();
                o.uid = uid;
                o.fgMs = l(p[2]);
                o.bgMs = l(p[3]);
                o.fgsMs = l(p[4]);
                o.cpuOffMs = l(p[5]);
                o.wlMs = l(p[6]);
                o.wlOffMs = l(p[7]);
                // 第 9 列是采样间隔（pldims v2 起才有）
                if (p.length > 8 && interval == 0) {
                    interval = l(p[8]);
                }
                // v3：第 9..20 列是 12 个分项 µAh，第 21 列是合计
                if (p.length >= 22) {
                    long sum = 0;
                    for (int i = 0; i < M_N; i++) {
                        long v = l(p[9 + i]);
                        o.mahUah[i] = v;
                        sum += v;
                    }
                    long given = l(p[21]);
                    o.mahSumUah = given > 0 ? given : sum;
                }
                list.add(o);
            }
        } catch (Throwable e) {
            Log.w(TAG, "dims 读取失败", e);
        } finally {
            closeQuiet(raf);
        }

        s.ts = maxTs;
        s.intervalMs = interval;
        s.count = list.size();
        for (One o : list) {
            s.byUid.put(o.uid, o);
            s.totFg     += o.fgMs;
            s.totBg     += o.bgMs;
            s.totFgs    += o.fgsMs;
            s.totCpuOff += o.cpuOffMs;
            s.totWl     += o.wlMs;
            s.totWlOff  += o.wlOffMs;
            s.totMahSum += o.mahSumUah;
            for (int i = 0; i < M_N; i++) {
                s.totMah[i] += o.mahUah[i];
            }
        }
        loadGlobal(ctx, s);
        Log.i(TAG, "dims 载入 " + s.count + " 个 UID，ts=" + s.ts
                + " fg=" + s.totFg + " bg=" + s.totBg + " fgs=" + s.totFgs
                + " cpuOff=" + s.totCpuOff + " wl=" + (s.totWl + s.totWlOff)
                + " mah=" + s.totMahSum / 1000 + "mAh");
        /* 顺带把累计表读进来（不阻断主流程：读不到就当没有） */
        loadTotal(ctx, s);

        return s;
    }

    /** 读全机累计（dims.global），用于校验和"平台级功耗"展示 */
    private static void loadGlobal(android.content.Context ctx, Snapshot s) {
        File g = new File(GLOBAL_TMP);
        if (!g.exists() || !g.canRead()) {
            g = new File(ctx.getFilesDir(), "dims.global");
        }
        if (!g.exists() || !g.canRead()) return;
        RandomAccessFile raf = null;
        try {
            raf = new RandomAccessFile(g, "r");
            String line = raf.readLine();
            if (line == null) return;
            String[] p = line.split("\t");
            if (p.length < 14) return;
            s.globalTs = l(p[0]);
            for (int i = 0; i < M_N; i++) {
                s.globalMah[i] = l(p[2 + i]);
            }
            s.globalSum = p.length >= 15 ? l(p[14]) : 0;
            if (s.globalSum <= 0) {
                long sum = 0;
                for (int i = 0; i < M_N; i++) sum += s.globalMah[i];
                s.globalSum = sum;
            }
        } catch (Throwable e) {
            Log.w(TAG, "dims.global 读取失败", e);
        } finally {
            closeQuiet(raf);
        }
    }

    private static long l(String s) {
        if (s == null || s.isEmpty()) return 0;
        try {
            return Long.parseLong(s.trim());
        } catch (Throwable e) {
            return 0;
        }
    }

    private static void closeQuiet(RandomAccessFile r) {
        if (r != null) {
            try {
                r.close();
            } catch (Throwable ignored) {
            }
        }
    }

    /** dims 多久没更新了（秒）；-1 表示没有数据 */
    static long ageSeconds(android.content.Context ctx) {
        File f = file(ctx);
        if (f == null || !f.exists()) return -1;
        return (System.currentTimeMillis() - f.lastModified()) / 1000;
    }
}
