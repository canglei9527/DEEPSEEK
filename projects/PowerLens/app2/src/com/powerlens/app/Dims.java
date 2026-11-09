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

        /** 该 UID 在指定维度的【增量秒数】 */
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
            One o = byUid.get(uid);
            if (o == null) return out;
            for (int i = 0; i < M_N; i++) {
                out[i] = o.mahUah[i] / 1000.0d;
            }
            return out;
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
