package com.powerlens.app;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * dumpsys batterystats --charged 解析器（纯 Java 实现，替代原 libpowerlens.so）。
 *
 * 为什么自己写：原生库对 "*" 电池行的处理不透明，且实测把「充电回升」也算进耗电，
 * 导致 9 小时显示 2813 mAh，而真实放电只有 526 mAh（虚高 5.35 倍）。
 *
 * 输出一行一个 UID：
 *   uid \t cpuMs \t cpuOffMs \t wlMs \t wlOffMs \t rxBytes \t txBytes \t mobileMah \t fgMs \t bgMs \t fgsMs
 */
final class Bstats {

    static final class UidStat {
        int uid;
        String pkg = "?";
        long cpuMs;       // 总 CPU
        long cpuOffMs;    // 息屏/doze 期间 CPU
        long wlMs;        // 总唤醒锁
        long wlOffMs;     // 息屏期间唤醒锁
        long rxBytes;
        long txBytes;
        long mobileMah;   // mobile_radio 估算 mAh（×100 存整数，避免浮点）
        long fgMs;
        long bgMs;
        long fgsMs;

        double weight() {
            // 后台口径权重：息屏 CPU + 息屏唤醒锁×0.5 + 流量（1 MB ≈ 2 单位）
            double traffic = (rxBytes + txBytes) / 1048576.0d * 2.0d;
            return cpuOffMs / 1000.0d + wlOffMs / 2000.0d + traffic;
        }
    }

    private static final Pattern UID_HEAD =
            Pattern.compile("^\\s*UID\\s+([0-9]+|u0a[0-9]+|[0-9]+a[0-9]+):\\s*(.*)$");
    private static final Pattern UID_INT = Pattern.compile("^\\s*UID\\s+([0-9]+):");

    private static final Pattern P_CPU = Pattern.compile("\\bcpu=([0-9.]+)");
    private static final Pattern P_CPU_FG = Pattern.compile("\\bcpu:fg=([0-9.]+)");
    private static final Pattern P_CPU_BG = Pattern.compile("\\bcpu:bg=([0-9.]+)");
    private static final Pattern P_WL = Pattern.compile("\\bwakelock=([0-9.]+)");
    private static final Pattern P_MOBILE = Pattern.compile("\\bmobile_radio=([0-9.]+)");

    private static final Pattern P_WIFI =
            Pattern.compile("Wi-?Fi network:\\s*([0-9.]+)\\s*([KMG]?B)[^,]*,\\s*([0-9.]+)\\s*([KMG]?B)");

    private static final Pattern P_FG_TIME = Pattern.compile("\\bfg:\\s*[0-9.]+\\s*\\(([^)]+)\\)");
    private static final Pattern P_BG_TIME = Pattern.compile("\\bbg:\\s*[0-9.]+\\s*\\(([^)]+)\\)");
    private static final Pattern P_CACHED_TIME = Pattern.compile("\\bcached:\\s*[0-9.]+\\s*\\(([^)]+)\\)");
    private static final Pattern P_FGS_TIME = Pattern.compile("\\bfgs:\\s*[0-9.]+\\s*\\(([^)]+)\\)");

    private static final Pattern P_DUR = Pattern.compile(
            "(?:([0-9]+)h)?\\s*(?:([0-9]+)m)?\\s*(?:([0-9]+)s)?\\s*(?:([0-9]+)ms)?");

    private Bstats() {
    }

    /** "u0a343" / "10817" → int uid */
    static int parseUid(String s) {
        if (s == null) {
            return -1;
        }
        s = s.trim();
        try {
            if (s.startsWith("u0a")) {
                return 10000 + Integer.parseInt(s.substring(3));
            }
            int a = s.indexOf('a');
            if (a > 0) {
                int user = Integer.parseInt(s.substring(1, a));
                int app = Integer.parseInt(s.substring(a + 1));
                return user * 100000 + 10000 + app;
            }
            return Integer.parseInt(s);
        } catch (Throwable e) {
            return -1;
        }
    }

    /** "1h 24m 55s 998ms" → ms */
    static long parseDur(String s) {
        if (s == null) {
            return 0;
        }
        Matcher m = P_DUR.matcher(s.trim());
        long ms = 0;
        if (m.find()) {
            if (m.group(1) != null) {
                ms += Long.parseLong(m.group(1)) * 3600000L;
            }
            if (m.group(2) != null) {
                ms += Long.parseLong(m.group(2)) * 60000L;
            }
            if (m.group(3) != null) {
                ms += Long.parseLong(m.group(3)) * 1000L;
            }
            if (m.group(4) != null) {
                ms += Long.parseLong(m.group(4));
            }
        }
        return ms;
    }

    private static long unitToBytes(String num, String unit) {
        double v;
        try {
            v = Double.parseDouble(num);
        } catch (Throwable e) {
            return 0;
        }
        if ("KB".equals(unit)) {
            v *= 1024d;
        } else if ("MB".equals(unit)) {
            v *= 1048576d;
        } else if ("GB".equals(unit)) {
            v *= 1073741824d;
        }
        return (long) v;
    }

    /** 从一行里取 "key=12.3 (dur)" 的括号内时长 */
    private static long parenDur(Pattern key, String line) {
        Matcher m = key.matcher(line);
        if (!m.find()) {
            return 0;
        }
        int i = m.end();
        int lp = line.indexOf('(', i);
        int rp = line.indexOf(')', lp + 1);
        if (lp < 0 || rp < 0) {
            return 0;
        }
        return parseDur(line.substring(lp + 1, rp));
    }

    private static double num(Pattern key, String line) {
        Matcher m = key.matcher(line);
        if (!m.find()) {
            return 0;
        }
        try {
            return Double.parseDouble(m.group(1));
        } catch (Throwable e) {
            return 0;
        }
    }

    /** 解析主入口 */
    static List<UidStat> parse(String dump) {
        List<UidStat> out = new ArrayList<>();
        if (dump == null || dump.length() < 5000) {
            return out;
        }
        Map<Integer, UidStat> map = new HashMap<>();
        UidStat cur = null;
        boolean inOffDoz = false;

        String[] lines = dump.split("\n", -1);
        for (String raw : lines) {
            String line = raw;

            Matcher um = UID_HEAD.matcher(line);
            if (um.find()) {
                int uid = parseUid(um.group(1));
                if (uid < 0) {
                    cur = null;
                    continue;
                }
                cur = map.get(uid);
                if (cur == null) {
                    cur = new UidStat();
                    cur.uid = uid;
                    map.put(uid, cur);
                }
                String tail = um.group(2);
                cur.fgMs += parenDur(P_FG_TIME, tail);
                cur.bgMs += parenDur(P_BG_TIME, tail);
                cur.fgsMs += parenDur(P_FGS_TIME, tail);
                cur.fgMs += parenDur(P_CACHED_TIME, tail); // cached 也算后台时长
                inOffDoz = false;
                continue;
            }

            if (cur == null) {
                continue;
            }

            // 段标记
            if (line.contains("(on battery, screen off/doze)")) {
                inOffDoz = true;
            } else if (line.contains("(on battery, screen on)")) {
                inOffDoz = false;
            } else if (line.contains("(on battery,")) {
                inOffDoz = line.contains("off") || line.contains("doze");
            }

            boolean hasCpu = line.contains("cpu=");
            boolean hasWl = line.contains("wakelock=");
            boolean hasWifi = line.contains("Fi network:");
            boolean hasMob = line.contains("mobile_radio=");

            if (!hasCpu && !hasWl && !hasWifi && !hasMob) {
                continue;
            }

            if (hasCpu) {
                long d = parenDur(P_CPU, line);
                if (inOffDoz) {
                    cur.cpuOffMs += d;
                } else {
                    cur.cpuMs += d;
                }
            }
            if (hasWl) {
                long d = parenDur(P_WL, line);
                if (inOffDoz) {
                    cur.wlOffMs += d;
                } else {
                    cur.wlMs += d;
                }
            }
            if (hasMob) {
                cur.mobileMah += (long) (num(P_MOBILE, line) * 100d);
            }
            if (hasWifi) {
                Matcher w = P_WIFI.matcher(line);
                if (w.find()) {
                    cur.rxBytes += unitToBytes(w.group(1), w.group(2));
                    cur.txBytes += unitToBytes(w.group(3), w.group(4));
                }
            }
        }

        out.addAll(map.values());
        return out;
    }
}