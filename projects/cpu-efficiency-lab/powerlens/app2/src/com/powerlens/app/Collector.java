package com.powerlens.app;

import android.content.Context;
import android.util.Log;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileReader;
import java.io.FileWriter;
import java.io.IOException;

/**
 * 采集调度（1.2 起彻底重构）
 *
 * ===== 为什么 App 侧不再自己采集 =====
 * 实测本机权限矩阵：
 *
 *   /sys/class/power_supply/battery/charge_counter   root only
 *   /sys/class/power_supply/battery/capacity         root only
 *   /proc/uid_cputime/show_uid_stat                  -r--r--r-- root root，App 读 = EACCES
 *   /proc/uid_io/stats                               同上
 *   su -c ...                                         KernelSU 拒绝 App 的 su 请求
 *
 * 所以 App 侧无论怎么写都拿不到真数据。之前 App 用 BatteryManager 近似值
 * 写进快照，产生 4477 这种「mAh 当 µAh」+ 状态列丢失的伪行，
 * 污染了整个核算（旧版虚高 4.5 倍的直接原因之一）。
 *
 * ===== 现在的分工 =====
 *   root 侧：/data/local/tmp/plc/plc-daemon.sh → plcollect → 写 snapshots.tsv
 *   App 侧：只读 snapshots.tsv，绝不写入
 *
 * ===== 唯一的 App 侧写入 =====
 * 只有当 root 守护不存在时（首次安装 / 守护没起来），
 * 才用 dumpsys 采集一次，并明确标记 src=dumpsys，
 * 且电池行写 root 下读到的真实 charge_counter（通过 su）。
 * su 不可用就干脆不写电池行 —— 宁缺毋滥。
 */
final class Collector {

    private static final String TAG = "PowerLens";
    static final String STORE_APP = "snapshots.tsv";
    static final String STORE_TMP = "/data/local/tmp/powerlens/snapshots.tsv";
    static final long MIN_INTERVAL_MS = 120_000L;

    private static final Object LOCK = new Object();

    private Collector() {
    }

    static File storeFile(Context ctx) {
        File a = new File(ctx.getFilesDir(), STORE_APP);
        File b = new File(STORE_TMP);
        int ca = countLines(a);
        int cb = countLines(b);
        if (ca <= 0 && cb <= 0) {
            return a;
        }
        return ca >= cb ? a : b;
    }

    static int countLines(File f) {
        if (f == null || !f.exists() || !f.canRead()) {
            return 0;
        }
        int n = 0;
        BufferedReader r = null;
        try {
            r = new BufferedReader(new FileReader(f), 65536);
            while (r.readLine() != null) {
                n++;
            }
        } catch (Throwable ignored) {
        } finally {
            closeQuiet(r);
        }
        return n;
    }

    static long lastTs(File f) {
        if (f == null || !f.exists()) {
            return 0;
        }
        String last = null;
        BufferedReader r = null;
        try {
            r = new BufferedReader(new FileReader(f), 65536);
            String line;
            while ((line = r.readLine()) != null) {
                if (!line.isEmpty()) {
                    last = line;
                }
            }
        } catch (Throwable ignored) {
        } finally {
            closeQuiet(r);
        }
        if (last == null) {
            return 0;
        }
        int t = last.indexOf('\t');
        if (t <= 0) {
            return 0;
        }
        try {
            return Long.parseLong(last.substring(0, t));
        } catch (Throwable e) {
            return 0;
        }
    }

    /** root 守护是否在跑 */
    static boolean rootDaemonAlive() {
        String r = Shell.su("ps -A -o ARGS 2>/dev/null | grep plc-daemon.sh | grep -v grep | head -n1");
        return r != null && !r.trim().isEmpty() && !r.startsWith("ERROR");
    }

    /** root 下读电量计（µAh）；读不到返回 -1 */
    static long rootChargeUah() {
        String r = Shell.su("cat /sys/class/power_supply/battery/charge_counter 2>/dev/null");
        if (r == null || r.startsWith("ERROR")) {
            return -1;
        }
        r = r.trim();
        if (r.isEmpty()) {
            return -1;
        }
        try {
            long v = Long.parseLong(r);
            return v > 0 ? v : -1;
        } catch (Throwable e) {
            return -1;
        }
    }

    static int rootCapacity() {
        String r = Shell.su("cat /sys/class/power_supply/battery/capacity 2>/dev/null");
        if (r == null || r.startsWith("ERROR")) {
            return -1;
        }
        r = r.trim();
        if (r.isEmpty()) {
            return -1;
        }
        try {
            return Integer.parseInt(r);
        } catch (Throwable e) {
            return -1;
        }
    }

    static String rootStatus() {
        String r = Shell.su("cat /sys/class/power_supply/battery/status 2>/dev/null");
        if (r == null || r.startsWith("ERROR")) {
            return "?";
        }
        r = r.trim();
        return r.isEmpty() ? "?" : r;
    }

    /**
     * 手动采集（1.2 定稿：完全不再尝试 root）
     *
     * ===== 架构定稿 =====
     * 采集 100% 由 root 侧的 plc-daemon.sh + plcollect 完成。
     * App 侧任何 su 调用在 KernelSU 下都会被拒（EACCES），
     * 而且一旦 App 申请过 root，用户重装 APK 时授权就会丢失
     * —— 这是用户反复遇到的"root 没了"的根源。
     *
     * 所以这里彻底移除 su / dumpsys 路径，只做一件事：
     * 告诉用户当前状态，引导去看文件。
     */
    static String collectOnce(Context ctx) {
        synchronized (LOCK) {
            File store = storeFile(ctx);
            long last = lastTs(store);
            int lines = countLines(store);

            StringBuilder sb = new StringBuilder();
            if (last > 0) {
                long ago = (System.currentTimeMillis() - last) / 1000;
                sb.append("采集由 root 守护自动完成（每 10 分钟）。\n")
                        .append("最后一条：").append(Storage.fmtTime(last))
                        .append("（").append(ago < 60 ? ago + " 秒前" : ago / 60 + " 分钟前").append("）\n")
                        .append("数据量：").append(lines).append(" 行\n\n")
                        .append("如需立即采集，在电脑上执行：\n")
                        .append("  su -c \"/data/local/tmp/plc/plcollect --once -f ")
                        .append(store.getAbsolutePath()).append("\"");
            } else {
                sb.append("还没有数据。\n\n")
                        .append("检查 root 守护是否运行：\n")
                        .append("  ps -A | grep plc-daemon\n\n")
                        .append("如果没有，启动它：\n")
                        .append("  su -c \"setsid sh /data/local/tmp/plc/plc-daemon.sh 600 &\"");
            }
            return sb.toString();
        }
    }

    static String diagnose(Context ctx) {
        StringBuilder sb = new StringBuilder();

        sb.append("== 权限矩阵 ==\n");
        boolean hasRoot = Shell.hasRoot();
        sb.append("App root: ").append(hasRoot ? "OK" : "无（KernelSU 拒绝）").append('\n');
        sb.append("App uid: ").append(android.os.Process.myUid()).append('\n');
        sb.append("root 守护: ").append(rootDaemonAlive() ? "运行中" : "未运行").append('\n');

        sb.append("\n== 电池（root 视角）==\n");
        long c = rootChargeUah();
        sb.append("charge_counter: ").append(c <= 0 ? "不可读" : c + " µAh = " + c / 1000 + " mAh").append('\n');
        int cap = rootCapacity();
        sb.append("capacity: ").append(cap <= 0 ? "不可读" : cap + " %").append('\n');
        sb.append("status: ").append(rootStatus()).append('\n');

        long bm = -1;
        try {
            android.os.BatteryManager mgr = (android.os.BatteryManager)
                    ctx.getSystemService(Context.BATTERY_SERVICE);
            if (mgr != null) {
                bm = mgr.getLongProperty(android.os.BatteryManager.BATTERY_PROPERTY_CHARGE_COUNTER) / 1000;
            }
        } catch (Throwable ignored) {
        }
        sb.append("BatteryManager(兜底): ").append(bm < 0 ? "读不到" : bm + " mAh").append('\n');

        sb.append("\n== 数据源成本 ==\n");
        long t0 = System.currentTimeMillis();
        int pn = -1;
        String perr = null;
        try {
            java.util.List<Procfs.Uid> l = Procfs.snapshot();
            pn = l == null ? -1 : l.size();
        } catch (Throwable e) {
            perr = e.getClass().getSimpleName() + ": " + e.getMessage();
        }
        long tp = System.currentTimeMillis() - t0;
        sb.append("procfs:  ").append(tp).append(" ms，").append(pn).append(" UID")
                .append(perr == null ? "" : "（" + perr + "）").append('\n');

        t0 = System.currentTimeMillis();
        String d = Shell.su("dumpsys batterystats --charged");
        long td = System.currentTimeMillis() - t0;
        sb.append("dumpsys: ").append(td).append(" ms，")
                .append(d == null ? 0 : d.length()).append(" 字节")
                .append(d != null && d.contains("  UID ") ? " 有效" : " 无效").append('\n');
        if (td > 0 && tp > 0) {
            sb.append("procfs 比 dumpsys 快 ").append(String.format(java.util.Locale.CHINA, "%.0f", td * 1.0 / Math.max(1, tp))).append(" 倍\n");
        }

        sb.append("\n== 存储 ==\n");
        File store = storeFile(ctx);
        sb.append(store.getAbsolutePath()).append('\n');
        sb.append("存在=").append(store.exists())
                .append(" 行数=").append(countLines(store))
                .append(" 字节=").append(store.length()).append('\n');
        long lt = lastTs(store);
        sb.append("最后采集=").append(lt == 0 ? "无" : Storage.fmtTime(lt)).append('\n');

        Storage.Summary sm = Storage.summarize(store, 0, System.currentTimeMillis());
        sb.append("\n== 区间核算（全部数据）==\n");
        sb.append("采样点     = ").append(sm.points).append('\n');
        sb.append("真实放电   = ").append(sm.dischargeUah / 1000).append(" mAh\n");
        sb.append("充入       = ").append(sm.chargeUah / 1000).append(" mAh\n");
        sb.append("跳过充电段 = ").append(sm.skippedSegments).append('\n');
        sb.append("跳过跳变   = ").append(sm.skippedJumps).append('\n');
        sb.append("跳过伪数据 = ").append(sm.skippedInvalid).append('\n');
        if (sm.ratePerHourUah > 0) {
            sb.append("放电速率   = ")
                    .append(String.format(java.util.Locale.CHINA, "%.1f", sm.ratePerHourUah / 1000.0)).append(" mA / ")
                    .append(String.format(java.util.Locale.CHINA, "%.2f", sm.ratePerHourUah / 60000.0)).append(" %/h\n");
        }
        return sb.toString();
    }

    static String setMulticastBlock(boolean on) {
        String[] ins = {
                "iptables  -I INPUT -p udp --dport 5353 -j DROP",
                "ip6tables -I INPUT -p udp --dport 5353 -j DROP",
                "iptables  -I INPUT -p udp --dport 1900 -j DROP",
                "ip6tables -I INPUT -p udp --dport 1900 -j DROP"
        };
        String[] del = {
                "iptables  -D INPUT -p udp --dport 5353 -j DROP",
                "ip6tables -D INPUT -p udp --dport 5353 -j DROP",
                "iptables  -D INPUT -p udp --dport 1900 -j DROP",
                "ip6tables -D INPUT -p udp --dport 1900 -j DROP"
        };
        StringBuilder sb = new StringBuilder();
        for (String s : (on ? ins : del)) {
            sb.append(s).append(" 2>/dev/null; ");
        }
        String out = Shell.su(sb.toString());
        if (out == null || out.startsWith("ERROR")) {
            return "失败（需要 root）：" + out;
        }
        return on ? "已开启组播屏蔽（5353/1900）" : "已关闭组播屏蔽";
    }

    static void note(Context ctx, String s) {
        Log.i(TAG, s);
        FileWriter w = null;
        try {
            w = new FileWriter(new File(ctx.getFilesDir(), "status.txt"), true);
            w.write((System.currentTimeMillis() / 1000) + " " + s + "\n");
        } catch (Throwable ignored) {
        } finally {
            if (w != null) {
                try {
                    w.close();
                } catch (IOException ignored) {
                }
            }
        }
    }

    static void closeQuiet(BufferedReader r) {
        if (r != null) {
            try {
                r.close();
            } catch (Throwable ignored) {
            }
        }
    }
}