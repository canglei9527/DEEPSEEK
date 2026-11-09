package com.powerlens.app;

import android.util.Log;

import java.io.File;
import java.io.RandomAccessFile;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * 维度数据（来自 pldims，低频 dumpsys）。
 *
 * ===== 为什么需要它 =====
 *   主采集器 plcollect 走 procfs，97 ms 出 150 个 UID，
 *   但 /proc/uid_cputime + /proc/uid_io 没有以下维度：
 *     fg（前台时长）/ bg（后台时长）/ fgs（前台服务时长）
 *     cpuOff（息屏 CPU）/ wl（唤醒锁）/ wlOff（息屏唤醒锁）
 *   这些只有 dumpsys batterystats 有。
 *
 * ===== 文件格式（pldims v2，增量）=====
 *   <epochMs> <uid> <ΔfgMs> <ΔbgMs> <ΔfgsMs> <ΔcpuOffMs> <ΔwlMs> <ΔwlOffMs> <intervalMs>
 *
 *   v2 起写的是【两次采样之间的增量】，不是开机以来累计值。
 *   为什么必须增量：
 *     v1 用累计值时，Σfg=290 小时、单个 UID 只有 6-7 小时，
 *     排序出来是"开机以来谁在前台待得久"，与"这 1 小时谁在耗电"无关。
 *     改成增量后，dimensions 与 snapshots 的区间口径一致，可以直接排序。
 */
final class Dims {

    private static final String TAG = "PowerLens";
    static final String FILE_TMP = "/data/local/tmp/powerlens/dims.tsv";

    /** 单个 UID 的维度（毫秒） */
    static final class One {
        int uid;
        long fgMs;
        long bgMs;
        long fgsMs;
        long cpuOffMs;
        long wlMs;
        long wlOffMs;

        long sum() {
            return fgMs + bgMs + fgsMs + cpuOffMs + wlMs + wlOffMs;
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

        boolean isEmpty() {
            return byUid.isEmpty() || (totFg + totBg + totFgs + totCpuOff + totWl + totWlOff) == 0;
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
                list.add(o);
                // 第 9 列是采样间隔（pldims v2 起才有）
                if (p.length > 8 && interval == 0) {
                    interval = l(p[8]);
                }
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
        }
        Log.i(TAG, "dims 载入 " + s.count + " 个 UID，ts=" + s.ts
                + " fg=" + s.totFg + " bg=" + s.totBg + " fgs=" + s.totFgs
                + " cpuOff=" + s.totCpuOff + " wl=" + (s.totWl + s.totWlOff));
        return s;
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