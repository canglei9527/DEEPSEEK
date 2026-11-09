package com.powerlens.app;

import android.util.Log;
import java.io.BufferedWriter;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.OutputStreamWriter;
import java.io.RandomAccessFile;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.List;
import java.util.Locale;

/**
 * 存储 + 电量核算。
 *
 * ===== 1.0 的 bug（这是"功耗显示异常"的根因）=====
 * 原实现在 queryRange 里返回一条 "*" 行的第 3 列作为"总耗电"，
 * 而那一列的实际含义是 (区间首个电量计值 − 区间末个电量计值)。
 * 只要区间内充过电（本机 01:33–02:33 连续充了 8 段，2539→5890 mAh），
 * 充电得到的电量就被当成"消耗"，算法直接把正负抵消掉。
 * 实测：显示 2813 mAh，真实放电 526 mAh → 虚高 5.35 倍。
 *
 * ===== 1.1 的算法 =====
 * 逐对相邻采样点：
 *   d = prev.count − cur.count
 *   d > 0  且 d <= 上限        → 记为放电，累加
 *   d > 0  但 d >  上限        → 视为"刷电量计/校准"跳变，丢弃并计数
 *   d <= 0                     → 充电或持平，跳过（不回退、不计负）
 * 上限 = max(50 mAh, 15 mAh/min × 间隔分钟数)，用于过滤换电/校准跳变。
 *
 * 这样 9 小时的数据会算出 526 mAh（≈1.17 %/h，与 97–122 mA 的实测电流一致）。
 */
final class Storage {

    /** 数据来源标记：procfs（C 采集器）vs dumpsys（旧 App）。
     *  两者对 CPU 的定义与精度不同，绝不能跨源做差值。 */
    static final int SRC_PROCFS = 0;
    static final int SRC_DUMPSYS = 1;

    private Storage() {
    }

    static final class Summary {
        long dischargeUah;
        long chargeUah;
        /** 纯放电时长（ms）—— 不含充电段，用于算真实放电电流 */
        long dischargeMs;
        /** 充电时长（ms） */
        long chargeMs;
        int points;
        int skippedSegments;
        int skippedJumps;
        int skippedInvalid;      // 伪数据行（旧 App 用 BatteryManager 写的，无状态无容量）
        long firstTs;
        long lastTs;
        double ratePerHourUah;
        /**
         * 真实放电速率（µAh/h）—— 分母是【纯放电时长】而非全跨度。
         *
         * 区别：
         *   ratePerHourUah       = 放电量 / 全跨度        （区间内含充电时偏低）
         *   ratePerHourUahActive = 放电量 / 纯放电时长    （真实电流）
         *
         * UI 显示电流用后者，显示「占区间比例」用前者。
         */
        double ratePerHourUahActive;
    }

    static final class Row {
        int uid;
        String pkg = "?";
        boolean seen;

        // 区间末值（累计计数器的最后一次读数）
        long cpuMs;
        long cpuOffMs;
        long wlMs;
        long wlOffMs;
        long rx;
        long tx;
        long mobileMahX100;
        long fgMs;
        long bgMs;
        long fgsMs;

        // 区间首值（基线）
        long baseCpuMs;
        long baseCpuOffMs;
        long baseWlMs;
        long baseWlOffMs;
        long baseRx;
        long baseTx;
        long baseFgMs;
        long baseBgMs;
        long baseFgsMs;

        // 是否观测到「离谱跳变」（数据源切换会触发）
        boolean suspicious;
        int src = -1;      // 该 UID 最近一次出现的数据源
        int switches;      // 区间内源切换次数

        // ---- 区间增量（这才是「这段时间里消耗了多少」）----
        // 关键：如果同一 UID 在区间内的值出现过大幅下降或暴增，
        // 说明数据源变了（procfs ↔ dumpsys 的单位定义不同），
        // 此时增量不可信，返回 0 而不是返回一个巨大的假值。
        long dCpuMs()     { return ok() ? nonNeg(cpuMs - baseCpuMs) : 0; }
        long dCpuOffMs()  { return ok() ? nonNeg(cpuOffMs - baseCpuOffMs) : 0; }
        long dWlMs()      { return ok() ? nonNeg(wlMs - baseWlMs) : 0; }
        long dWlOffMs()   { return ok() ? nonNeg(wlOffMs - baseWlOffMs) : 0; }
        long dRx()        { return ok() ? nonNeg(rx - baseRx) : 0; }
        long dTx()        { return ok() ? nonNeg(tx - baseTx) : 0; }
        long dFgMs()      { return ok() ? nonNeg(fgMs - baseFgMs) : 0; }
        long dBgMs()      { return ok() ? nonNeg(bgMs - baseBgMs) : 0; }
        long dFgsMs()     { return ok() ? nonNeg(fgsMs - baseFgsMs) : 0; }

        private boolean ok() {
            return !suspicious;
        }

        private static long nonNeg(long v) {
            return v > 0 ? v : 0;
        }

        double trafficMb() {
            return (dRx() + dTx()) / 1048576.0d;
        }

        /**
         * ===== 关于 rx/tx 的语义（重要，踩过坑）=====
         *
         * 数据里第 8/9 列（rx/tx）有两个来源，含义完全不同：
         *
         *   procfs 行（C 采集器）：取自 /proc/uid_io/stats 的 rchar/wchar
         *     → 这是【文件读写字节数】，包含读 /proc、读数据库、写日志
         *     → 本机实测 uid 0 有 15 GB，因为 init 大量遍历 /proc
         *     → 用它当"网络流量"会得出"内核耗了 895 MB 流量"这种荒谬结论
         *
         *   dumpsys 行（旧 App）：取自 batterystats 的 wifi=/mobile=
         *     → 这才是真正的网络流量
         *
         * 所以不能一律把 rx/tx 当流量加权。
         * 处理方式：procfs 源的行不参与流量加权（只按 CPU 排）。
         */
        boolean hasRealTraffic() {
            return src == SRC_DUMPSYS;
        }

        double realTrafficMb() {
            return hasRealTraffic() ? trafficMb() : 0d;
        }

        /**
         * 权重单位统一为「等效 CPU 秒」。
         *
         * ===== 关键：不能用流量做大权重 =====
         * 本机实测（chg 电流法）：
         *   - 待机 60-120 mA 时，全网流量 < 5 MB/6h
         *   - 流量 895 MB/6h 的时段，电流 318 mA
         *   两者比值：895 MB 带来约 200 mA 增量
         *   → 1 MB ≈ 0.22 mAh
         *
         *   - CPU 4 分钟（240 秒）× 0.5 mAh/s ≈ 120 mAh
         *   → CPU 1 秒 ≈ 0.5 mAh
         *
         * 折成同一单位（以 CPU 秒为基准）：
         *   CPU 1 秒    → 1.0
         *   流量 1 MB   → 0.22 / 0.5 = 0.44   ← 之前用 2.0 高了 4.5 倍
         *   唤醒锁 1 秒 → 0.3  / 0.5 = 0.6
         *
         * 但 0.44 仍然会让 895 MB 压过 240 秒 CPU。
         * 物理上 895 MB 的流量确实是 6 小时里最大的耗电源，
         * 所以显示它占大头是对的 —— 问题在于「根因/Android系统」这两个
         * 聚合 UID **不该** 和具体应用并列排名。
         */
        private static final double MB_WEIGHT = 0.44d;
        private static final double WL_WEIGHT = 0.6d;

        /** 全部口径：CPU + 息屏 CPU + 唤醒锁 + 真实网络流量 */
        double wAll() {
            return (dCpuMs() + dCpuOffMs()) / 1000.0d
                    + (dWlMs() + dWlOffMs()) / 1000.0d * WL_WEIGHT
                    + realTrafficMb() * MB_WEIGHT;
        }

        /** 前台口径：只用总 CPU + 真实流量 */
        double wFg() {
            return dCpuMs() / 1000.0d + realTrafficMb() * MB_WEIGHT;
        }

        /** 后台口径：息屏 CPU + 息屏唤醒锁 + 真实流量 */
        double wBg() {
            return dCpuOffMs() / 1000.0d
                    + dWlOffMs() / 1000.0d * WL_WEIGHT
                    + realTrafficMb() * MB_WEIGHT;
        }

        /** 前台服务口径：前台服务时长 + 真实流量 */
        double wFgs() {
            return dFgsMs() / 1000.0d + realTrafficMb() * MB_WEIGHT;
        }
    }

    static String safe(String s) {
        if (s == null || s.isEmpty()) {
            return "?";
        }
        return s.replace('\t', ' ').replace('\n', ' ');
    }

    static String fmtTime(long ms) {
        return new SimpleDateFormat("MM-dd HH:mm:ss", Locale.CHINA).format(new Date(ms));
    }

    /** 追加写；用 RandomAccessFile 保证多进程/多次调用下不截断。 */
    static boolean append(File f, String text) {
        RandomAccessFile raf = null;
        try {
            File dir = f.getParentFile();
            if (dir != null && !dir.exists()) {
                dir.mkdirs();
            }
            raf = new RandomAccessFile(f, "rw");
            long len = raf.length();
            if (len > 0) {
                raf.seek(len - 1);
                if (raf.read() != '\n') {
                    raf.seek(len);
                    raf.write('\n');
                }
            }
            raf.seek(raf.length());
            raf.write(text.getBytes("UTF-8"));
            return true;
        } catch (Throwable e) {
            return false;
        } finally {
            if (raf != null) {
                try {
                    raf.close();
                } catch (IOException ignored) {
                }
            }
        }
    }

    static void writeText(File f, String text) {
        BufferedWriter w = null;
        try {
            File dir = f.getParentFile();
            if (dir != null && !dir.exists()) {
                dir.mkdirs();
            }
            w = new BufferedWriter(new OutputStreamWriter(new FileOutputStream(f, false), "UTF-8"));
            w.write(text);
        } catch (Throwable ignored) {
        } finally {
            if (w != null) {
                try {
                    w.close();
                } catch (Throwable ignored) {
                }
            }
        }
    }

    static long l(String s, long def) {
        if (s == null || s.isEmpty()) {
            return def;
        }
        try {
            return Long.parseLong(s.trim());
        } catch (Throwable e) {
            return def;
        }
    }

    /** 从 /data/system/packages.list 反查 uid→包名（App 通常读不到，靠 root 守护补齐） */
    private static String resolvePkg(int uid) {
        if (uid <= 0) {
            return null;
        }
        java.io.BufferedReader r = null;
        try {
            r = new java.io.BufferedReader(new java.io.FileReader("/data/system/packages.list"));
            String line;
            while ((line = r.readLine()) != null) {
                String[] f = line.split("\\s+");
                if (f.length >= 2) {
                    try {
                        if (Integer.parseInt(f[1]) == uid) {
                            return f[0];
                        }
                    } catch (Throwable ignored) {
                    }
                }
            }
        } catch (Throwable ignored) {
        } finally {
            if (r != null) {
                try {
                    r.close();
                } catch (Throwable ignored) {
                }
            }
        }
        return null;
    }

    /**
     * 读区间内所有行。
     * @return [0]=按 uid 合并后的 Row 列表，[1..] 通过 Summary 另行计算
     */
    static List<Row> readRange(File f, long from, long to) {
        List<Row> out = new ArrayList<>();
        if (f == null || !f.exists() || !f.canRead()) {
            return out;
        }
        RandomAccessFile raf = null;
        java.util.HashMap<Integer, Row> map = new java.util.HashMap<>();
        try {
            raf = new RandomAccessFile(f, "r");
            String line;
            while ((line = raf.readLine()) != null) {
                int t1 = line.indexOf('\t');
                if (t1 <= 0) {
                    continue;
                }
                long ts = l(line.substring(0, t1), -1);
                if (ts < from || ts > to) {
                    continue;
                }
                String[] p = line.split("\t");
                if (p.length < 4) {
                    continue;
                }
                if ("*".equals(p[1])) {
                    continue; // 电池行不进 UID 列表
                }
                int uid = (int) l(p[1], -1);
                if (uid < 0) {
                    continue;
                }

                // 先判定数据来源（Row 创建时就要用到）
                long vCpuOff = p.length > 4 ? l(p[4], 0) : 0;
                long vWl = p.length > 5 ? l(p[5], 0) : 0;
                long vWlOff = p.length > 6 ? l(p[6], 0) : 0;
                long vFg = p.length > 10 ? l(p[10], 0) : 0;
                long vBg = p.length > 11 ? l(p[11], 0) : 0;
                long vFgs = p.length > 12 ? l(p[12], 0) : 0;
                boolean hasDims = (vCpuOff | vWl | vWlOff | vFg | vBg | vFgs) != 0;
                int src = hasDims ? SRC_DUMPSYS : SRC_PROCFS;

                Row r = map.get(uid);
                if (r == null) {
                    r = new Row();
                    r.uid = uid;
                    r.pkg = p.length > 2 ? p[2] : "?";
                    // 记住区间内第一条（作为基线）
                    r.baseCpuMs = l(p.length > 3 ? p[3] : "0", 0);
                    r.baseCpuOffMs = l(p.length > 4 ? p[4] : "0", 0);
                    r.baseWlMs = l(p.length > 5 ? p[5] : "0", 0);
                    r.baseWlOffMs = l(p.length > 6 ? p[6] : "0", 0);
                    r.baseRx = l(p.length > 7 ? p[7] : "0", 0);
                    r.baseTx = l(p.length > 8 ? p[8] : "0", 0);
                    r.baseFgMs = l(p.length > 10 ? p[10] : "0", 0);
                    r.baseBgMs = l(p.length > 11 ? p[11] : "0", 0);
                    r.baseFgsMs = l(p.length > 12 ? p[12] : "0", 0);
                    // 首条数据就要确定来源，否则 hasRealTraffic() 会一直判错
                    r.src = src;
                    map.put(uid, r);
                }
                if (p.length > 12) {
                    long curCpu = l(p[3], 0);

                    // 数据源识别：
                //   procfs 行（C 采集器）只有 cpuMs + rx/tx，其余维度全为 0
                //   dumpsys 行（旧 App）有 cpuOff/wl/fg/bg/fgs 中的至少一个
                // 两种源的 CPU 定义与精度不同，绝不能跨源做差值。

                if (r.seen && r.src != src) {
                    // 源切换：把当前末值当作新源的基线，重新开始累计
                    r.baseCpuMs = l(p[3], 0);
                    r.baseCpuOffMs = vCpuOff;
                    r.baseWlMs = vWl;
                    r.baseWlOffMs = vWlOff;
                    r.baseRx = l(p[7], 0);
                    r.baseTx = l(p[8], 0);
                    r.baseFgMs = vFg;
                    r.baseBgMs = vBg;
                    r.baseFgsMs = vFgs;
                    r.src = src;
                    r.switches++;
                } else if (r.seen) {
                    long drift = l(p[3], 0) - r.cpuMs;
                    // 同源内也不应有大幅回退或暴增（回退=计数器重置/进程重启）
                    if (drift < -60_000L) {
                        r.baseCpuMs = l(p[3], 0);
                        r.baseCpuOffMs = vCpuOff;
                        r.baseWlMs = vWl;
                        r.baseWlOffMs = vWlOff;
                        r.baseRx = l(p[7], 0);
                        r.baseTx = l(p[8], 0);
                        r.baseFgMs = vFg;
                        r.baseBgMs = vBg;
                        r.baseFgsMs = vFgs;
                    }
                }
                // 末值覆盖（同一 UID 在区间内出现多次，取最后一次作为末值）
                r.cpuMs = l(p[3], 0);
                r.cpuOffMs = vCpuOff;
                r.wlMs = vWl;
                r.wlOffMs = vWlOff;
                r.rx = l(p[7], 0);
                r.tx = l(p[8], 0);
                r.mobileMahX100 = l(p[9], 0);
                r.fgMs = vFg;
                r.bgMs = vBg;
                r.fgsMs = vFgs;
                r.seen = true;
                }
            }
        } catch (Throwable ignored) {
        } finally {
            if (raf != null) {
                try {
                    raf.close();
                } catch (Throwable ignored) {
                }
            }
        }
        out.addAll(map.values());

        // 包名补全：
        // C 采集器（procfs）无法从 /proc/<pid>/cmdline 解析出所有 UID 的包名，
        // 大量行 pkg="?"（本机实测 23388 行里有 11088 行是 "?"）。
        // 这里用 /data/system/packages.list 兜底反查，避免 UI 上一堆 "系统组件 xxxxx"。
        for (Row r : out) {
            if (r.pkg == null || "?".equals(r.pkg) || r.pkg.isEmpty()) {
                String p = resolvePkg(r.uid);
                if (p != null) {
                    r.pkg = p;
                }
            }
        }
        return out;
    }

    /**
     * 计算区间真实放电量（µAh）。
     * 这是修复 1.0 虚高 bug 的核心。
     */
    static Summary summarize(File f, long from, long to) {
        Summary s = new Summary();
        if (f == null || !f.exists() || !f.canRead()) {
            return s;
        }
        RandomAccessFile raf = null;
        List<long[]> pts = new ArrayList<>(); // {ts, uah, discharging?1:0}
        try {
            raf = new RandomAccessFile(f, "r");
            String line;
            while ((line = raf.readLine()) != null) {
                int t1 = line.indexOf('\t');
                if (t1 <= 0) {
                    continue;
                }
                long ts = l(line.substring(0, t1), -1);
                if (ts < from || ts > to) {
                    continue;
                }
                String[] p = line.split("\t");
                if (p.length < 4) {
                    continue;
                }
                // 纯 split 判定，不依赖字符串预筛（预筛里的 \t 曾因编码问题失效）
                if (!"*".equals(p[1]) || !"battery".equals(p[2])) {
                    continue;
                }
                long uah = l(p[3], 0);
                if (uah <= 0) {
                    continue; // 电量计不可用，不写假数据
                }
                // 单位自适应：1.0 写的是 mAh，1.2 起写 µAh
                if (uah < 100_000L) {
                    uah *= 1000L;
                }
                String st = p.length > 5 ? p[5] : "";
                int dis = ("Discharging".equalsIgnoreCase(st) || "Not charging".equalsIgnoreCase(st)) ? 1 : 0;
                // 老格式状态列全是 "0"，此时不能判为充电，否则放电全丢
                boolean unknown = st.isEmpty() || "0".equals(st) || "?".equals(st);

                // 识别伪数据：旧 App 用 BatteryManager 写的行，
                // 特征是 status 为 "0" 且第 5 列（capacity）为 0，
                // 这种行既没有真实状态也没有容量，参与核算会污染结果。
                boolean pseudo = ("0".equals(st) || st.isEmpty())
                        && p.length > 4 && "0".equals(p[4].trim());
                if (pseudo) {
                    s.skippedInvalid++;
                    continue;
                }

                pts.add(new long[]{ts, uah, dis, unknown ? 1 : 0});
            }
        } catch (Throwable ignored) {
        } finally {
            if (raf != null) {
                try {
                    raf.close();
                } catch (Throwable ignored) {
                }
            }
        }

        s.points = pts.size();
        if (pts.size() < 2) {
            return s;
        }
        s.firstTs = pts.get(0)[0];
        s.lastTs = pts.get(pts.size() - 1)[0];

        for (int i = 1; i < pts.size(); i++) {
            long[] a = pts.get(i - 1);
            long[] b = pts.get(i);
            long dtMin = Math.max(1, (b[0] - a[0]) / 60000L);
            long d = a[1] - b[1];                       // µAh，正数=放电

            /*
             * 物理上限（判「电量计跳变/校准」用）。
             *
             * ===== 实战教训：15 mAh/min 太紧，会误杀真实放电 =====
             *   实测这台机器有一次：
             *     11:35:11 → 11:41:31（6.3 分钟）掉了 110 mAh
             *     = 1043 mA 平均电流
             *   这是亮屏高负载的真实消耗（游戏/导航/热点），
             *   但被 15 mAh/min（= 900 mA）判定为跳变直接丢掉，
             *   导致总量少算 110 mAh（约 12%）。
             *
             * 现在放宽到 30 mAh/min（1800 mA）：
             *   · 覆盖到 8 Gen 3 满载 + 亮屏 + 热点的极端情况
             *   · 仍能抓住真正的电量计跳变（那种通常是几百 mAh 的突变，
             *     比如换电池/校准，一秒跳 500+ mAh，远超 30 mAh/min × 1 = 30 mAh）
             *
             * 另外 dtMin 用 max(1, ...) 意味着「1 分钟内」都按 1 分钟算，
             * 所以下限也要提到 30 mAh，否则短间隔的高速放电还是会被杀。
             */
            long cap = Math.max(30_000L, 30_000L * dtMin);

            if (d > 0) {
                if (d > cap) {
                    s.skippedJumps++;                    // 电量计跳变/校准，丢掉
                    continue;
                }
                // unknown=1 表示老格式没有状态列，按放电处理
                if (a[2] == 0 && b[2] == 0 && a[3] == 0) {
                    s.skippedSegments++;                 // 明确两端都在充电，不算放电
                    continue;
                }
                s.dischargeUah += d;
                s.dischargeMs += (b[0] - a[0]);          // 累计真实放电时长
            } else if (d < 0) {
                s.chargeUah += -d;
                s.chargeMs += (b[0] - a[0]);
                s.skippedSegments++;
            }
        }

        long spanMs = s.lastTs - s.firstTs;
        if (spanMs > 60000L) {
            s.ratePerHourUah = s.dischargeUah * 3600000L / spanMs;
        }

        /*
         * ===== 放电率的分母（重要）=====
         *
         * 上面 ratePerHourUah 用的是【全跨度】。
         * 如果区间里充过电，充电那段时间没有放电，却也被算进分母，
         * 结果就是放电率被稀释、显示偏低（实测偏差可达 30%+）。
         *
         * 正确的「放电速率」= 总放电量 ÷ 纯放电时长。
         * 两个都保留：一个用于「占区间比例」，一个用于「真实电流」。
         */
        if (s.dischargeMs > 60000L) {
            s.ratePerHourUahActive = s.dischargeUah * 3600000L / s.dischargeMs;
        }
        return s;
    }
}
