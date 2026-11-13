package com.powerlens.app;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;

/**
 * 极简 shell 执行器。
 * 修：原版 run() 在读流时没有超时，dumpsys 卡住会把整条采集线程挂死；
 *     这里加超时，并在超时后 destroy 进程。
 */
final class Shell {

    static final int DEFAULT_TIMEOUT_MS = 90_000;

    private Shell() {
    }

    static String run(String... cmd) {
        return run(DEFAULT_TIMEOUT_MS, cmd);
    }

    static String run(int timeoutMs, String... cmd) {
        Process p = null;
        try {
            ProcessBuilder pb = new ProcessBuilder(cmd);
            pb.redirectErrorStream(true);
            p = pb.start();

            final Process proc = p;
            final ByteArrayOutputStream buf = new ByteArrayOutputStream();
            final InputStream in = proc.getInputStream();

            Thread reader = new Thread(new Runnable() {
                @Override
                public void run() {
                    byte[] b = new byte[16384];
                    try {
                        int n;
                        while ((n = in.read(b)) > 0) {
                            buf.write(b, 0, n);
                        }
                    } catch (Throwable ignored) {
                    }
                }
            }, "pl-reader");
            reader.setDaemon(true);
            reader.start();

            boolean done = proc.waitFor(timeoutMs, java.util.concurrent.TimeUnit.MILLISECONDS);
            if (!done) {
                proc.destroy();
                reader.join(1000);
                return "ERROR: timeout after " + timeoutMs + " ms";
            }
            reader.join(2000);
            return new String(buf.toByteArray(), StandardCharsets.UTF_8);
        } catch (Throwable e) {
            if (p != null) {
                try {
                    p.destroy();
                } catch (Throwable ignored) {
                }
            }
            return "ERROR: " + e;
        }
    }

    static String su(String cmd) {
        return run("su", "-c", cmd);
    }

    static boolean hasRoot() {
        String s = su("id");
        return s != null && s.contains("uid=0");
    }

    /** 读一个 sysfs 文件的整数值；读不到返回 Long.MIN_VALUE。 */
    static long readLongFile(String path) {
        try {
            String s = run(4000, "cat", path);
            if (s == null) {
                return Long.MIN_VALUE;
            }
            s = s.trim();
            if (s.isEmpty() || s.startsWith("ERROR")) {
                return Long.MIN_VALUE;
            }
            return Long.parseLong(s);
        } catch (Throwable e) {
            return Long.MIN_VALUE;
        }
    }

    /** 电池电量计：/sys/class/power_supply/battery/charge_counter，单位 µAh。 */
    static long readChargeCounterUah() {
        long v = readLongFile("/sys/class/power_supply/battery/charge_counter");
        if (v == Long.MIN_VALUE || v <= 0) {
            // 部分机型需要 root
            String s = su("cat /sys/class/power_supply/battery/charge_counter");
            if (s != null) {
                try {
                    v = Long.parseLong(s.trim());
                    if (v <= 0) {
                        return Long.MIN_VALUE;
                    }
                    return v;
                } catch (Throwable ignored) {
                }
            }
            return Long.MIN_VALUE;
        }
        return v;
    }

    static String batteryStatus() {
        String s = run(4000, "cat", "/sys/class/power_supply/battery/status");
        return s == null ? "" : s.trim();
    }
}