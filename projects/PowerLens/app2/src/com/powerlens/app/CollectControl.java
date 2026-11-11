package com.powerlens.app;

/**
 * CollectControl —— App 侧「一键关闭采集」与 logd 开关。
 *
 * ===== 为什么要有它 =====
 *   原来采集只有一个来源：模块 service.sh 开机拉起守护，之后就一直跑。
 *   用户不需要看数据时（例如只为验证某个改动、或想测纯待机功耗），
 *   没有任何办法真正停下来。每 60 秒两次 su + procfs 读写在待机测试里
 *   是直接的污染源。
 *
 *   这个类把模块侧的 collect-control.sh / logd-control.sh 做成 UI 开关。
 *
 * ===== 三种模式 =====
 *   on    常驻采集
 *   off   完全停止：杀守护、杀 autopilot、置 collect.lock，
 *         开机也不会自己起来。真·零后台。
 *   auto  熄屏 10 分钟后自动停，亮屏立即恢复
 *
 * ===== 边界 =====
 *   这些都是 root 侧脚本的薄封装。App 自身不常驻，也没有后台服务；
 *   off 之后 App 打开仍然能看历史数据，只是不再产生新数据。
 */
final class CollectControl {

    static final String TMP = "/data/local/tmp/plc";
    static final String CTL = TMP + "/collect-control.sh";
    static final String LOGD_CTL = TMP + "/logd-control.sh";
    static final String CONF = TMP + "/collect.conf";
    static final String STATE = TMP + "/collect.state";

    private CollectControl() {
    }

    /** 采集模式。 */
    static final class Status {
        String mode = "unknown";      // on / off / auto
        boolean running;
        String reason = "";
        String screen = "unknown";
        boolean logdDisabled;
        /** bind = 本模块挂的（App 可恢复）；overlay = 旧模块覆盖的（需重启） */
        String logdMechanism = "none";
        boolean scriptsReady;

        String label() {
            if (!scriptsReady) {
                return "模块未就绪";
            }
            if ("off".equals(mode)) {
                return "已关闭";
            }
            if ("auto".equals(mode)) {
                return running ? "自动·采集中" : "自动·已暂停";
            }
            return running ? "采集中" : "未运行";
        }
    }

    /** 读取当前状态。任何一步失败都降级为 unknown，不抛异常。 */
    /**
     * 读取当前状态。
     *
     * ===== 为什么只跑一次 su（重要）=====
     *   早期实现分三次调用：检测脚本、collect-control status、logd-control status。
     *   每次 `su -c` 在 KernelSU 下都要一次 IPC + 授权校验，单次超时 15~25 秒。
     *   而它是被「模块」按钮同步调用的，于是点一下按钮最坏冻 65 秒。
     *
     *   现在把三条命令拼进同一次 su，用分隔标记切分输出，
     *   最坏只等一次超时，且调用方已经把它放在后台线程。
     */
    static Status status() {
        Status st = new Status();

        final String MARK_CTL = "@@CTL@@";
        final String MARK_LOGD = "@@LOGD@@";

        String out = Shell.run(20_000, "su", "-c",
                "if test -x " + CTL + "; then echo READY; sh " + CTL + " status; "
                        + "else echo MISSING; fi; "
                        + "echo " + MARK_LOGD + "; "
                        + "if test -x " + LOGD_CTL + "; then sh " + LOGD_CTL + " status; fi");
        if (out == null) {
            return st;
        }
        if (!out.contains("READY")) {
            st.scriptsReady = false;
            return st;
        }
        st.scriptsReady = true;

        String ctlPart = out;
        String logdPart = "";
        int li = out.indexOf(MARK_LOGD);
        if (li >= 0) {
            ctlPart = out.substring(0, li);
            logdPart = out.substring(li + MARK_LOGD.length());
        }

        for (String line : ctlPart.split("\n")) {
            line = line.trim();
            if (line.startsWith("mode=")) {
                String v = line.substring(5).trim();
                if (!v.isEmpty() && !"unset".equals(v)) {
                    st.mode = v;
                }
            } else if (line.startsWith("daemon_count=")) {
                st.running = parseInt(line.substring(13).trim(), 0) > 0;
            } else if (line.startsWith("screen=")) {
                st.screen = line.substring(7).trim();
            } else if (line.startsWith("reason=")) {
                st.reason = line.substring(7).trim();
            }
        }

        st.logdDisabled = logdPart.contains("logd_stub=mounted");
        /*
         * 区分是谁在禁用 logd：
         *   bind    本模块 logd-control.sh 挂的，App 能一键恢复
         *   overlay 旧 close_logd 模块覆盖的，App 无法在线恢复，
         *           必须停用那个模块再重启。不区分的话，用户点
         *           "恢复" 没效果，会以为是 bug。
         */
        st.logdMechanism = "none";
        if (logdPart.contains("mechanism=bind")) {
            st.logdMechanism = "bind";
        } else if (logdPart.contains("mechanism=overlay")) {
            st.logdMechanism = "overlay";
        }
        return st;
    }

    /** 切换采集模式：on / off / auto。 */
    static String setMode(String mode) {
        if (!"on".equals(mode) && !"off".equals(mode) && !"auto".equals(mode)) {
            return "非法模式: " + mode;
        }
        String out = Shell.run(60_000, "su", "-c",
                "sh " + CTL + " " + mode + " 2>&1");
        return out == null ? "命令无输出" : out.trim();
    }

    /** 开关 logd 禁用。返回给用户看的说明。 */
    static String setLogdDisabled(boolean disable) {
        String out = Shell.run(90_000, "su", "-c",
                "sh " + LOGD_CTL + " " + (disable ? "on" : "off") + " 2>&1");
        return out == null ? "命令无输出" : out.trim();
    }

    private static int parseInt(String s, int def) {
        try {
            return Integer.parseInt(s.replaceAll("[^0-9-]", ""));
        } catch (Throwable ignored) {
            return def;
        }
    }
}