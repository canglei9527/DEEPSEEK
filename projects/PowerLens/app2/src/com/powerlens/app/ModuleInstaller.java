package com.powerlens.app;

import android.content.Context;
import android.content.res.AssetManager;

import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;

/**
 * ModuleInstaller —— 在 App 内一键安装 PowerLens 的 root 采集模块。
 *
 * ===== 前提：App 必须已在 KernelSU 白名单 =====
 *   实测本机 /.allowlist 里有 com.powerlens.app，且
 *     su 10428 -c 'su -c ...'  => GRANTED
 *   所以 App 自己就能拿到 root，不需要任何外部工具。
 *
 *   如果用户从未授权，首次点「安装」会弹一次 KernelSU 授权框；
 *   点「允许」后永久记住（按 uid+签名），后续完全无感。
 *
 * ===== 为何不依赖 KernelSU App =====
 *   本机根本没装 KernelSU 管理器（pm list 里搜不到），
 *   root 完全靠 ksud CLI + allowlist。所以安装只能走 ksud，
 *   而这正是好事：不依赖任何 GUI。
 *
 * ===== 安装后的行为 =====
 *   模块 zip 里 customize.sh 会把二进制落位并拉守护，
 *   所以【不重启也能立即开始采集】；重启只是让 service.sh 接管开机自启。
 */
final class ModuleInstaller {

    static final String MODULE_ID = "powerlens";
    static final String ZIP_ASSET = "powerlens-module.zip";
    static final String MODULE_DIR = "/data/adb/modules/" + MODULE_ID;

    /** 安装结果。 */
    static final class Result {
        boolean ok;
        boolean needsReboot;
        String backend = "";      // ksud / magisk / none
        String message = "";
        String rawOutput = "";

        @Override
        public String toString() {
            return (ok ? "OK" : "FAIL") + " [" + backend + "] " + message;
        }
    }

    private ModuleInstaller() {
    }

    /** 把打包进 APK assets 的模块 zip 释放到可读路径。 */
    static File extractZip(Context ctx) throws Exception {
        File out = new File(ctx.getCacheDir(), ZIP_ASSET);
        AssetManager am = ctx.getAssets();
        InputStream in = null;
        OutputStream os = null;
        try {
            in = am.open(ZIP_ASSET);
            /*
             * ===== 必须先把旧文件恢复为可写 =====
             *
             * 旧实现末尾写了 setWritable(false, false)（试图模仿 780），
             * 结果 /data/user/0/<pkg>/cache/powerlens-module.zip 变成 -r--r--r--。
             * 在 Linux 上 root 可以无视权限写，但 App 以自身 uid 运行，
             * 这里创建 FileOutputStream 会直接 EACCES。
             *
             * 真机复现：首次安装成功，第二次点「一键安装」（升级）必报
             *     释放模块包失败: .../cache/powerlens-module.zip:
             *     open failed: EACCES (Permission denied)
             *
             * 所以写入前先无条件放开写权限；末尾只保留"对组可读"，
             * 不再关掉写位，保证重复安装/升级路径可用。
             */
            if (out.exists() && !out.canWrite()) {
                //noinspection ResultOfMethodCallIgnored
                out.setWritable(true, true);
            }
            os = new FileOutputStream(out);
            byte[] buf = new byte[16384];
            int n;
            while ((n = in.read(buf)) > 0) {
                os.write(buf, 0, n);
            }
            os.flush();
        } finally {
            if (in != null) try { in.close(); } catch (Throwable ignored) {}
            if (os != null) try { os.close(); } catch (Throwable ignored) {}
        }
        /*
         * ksud 以 root 读这个 zip，不需要提前收紧权限；
         * 只把读权限放给组，便于调试时 shell 也能读。
         * 注意：不要再 setWritable(false)，那正是升级失败的根因。
         */
        //noinspection ResultOfMethodCallIgnored
        out.setReadable(true, false);
        return out;
    }

    /** 检测可用的安装后端。 */
    static String detectBackend() {
        String r = Shell.run(15_000, "su", "-c", "command -v ksud; command -v magisk");
        if (r == null) {
            return "none";
        }
        String s = r.trim();
        if (s.contains("ksud")) {
            return "ksud";
        }
        if (s.contains("magisk")) {
            return "magisk";
        }
        return "none";
    }

    /**
     * root 预检：区分「没 root」和「有 root 但未授权本应用」。
     * 两者给用户的建议不同，所以必须分开。
     *
     * ===== 为什么不能用 su -c id 判断 =====
     *   实测踩过三个坑，都不能用：
     *
     *   (1) su 10428 -c "su -c id"  -> uid=0   假阳性
     *       外层 su 已经是 root，内层直接继承，根本没走授权检查。
     *
     *   (2) su 10428 -c "cat /.allowlist"  -> Permission denied
     *       即使以 su 域运行，读 root:root 0600 的文件仍失败，
     *       这反映的是文件权限，不是 su 授权状态。
     *
     *   (3) 以 shell(uid 2000) 跑探针 -> GRANTED
     *       shell 自己就在 allowlist 里(policy=2000)，会误导。
     *
     *   可靠的探针：执行一个【需要真 root 权限】的操作。
     *   这里用 ls /data/adb/modules（该目录 0700 root:root）。
     *
     * ===== 本机的真实授权状态（已实测）=====
     *   用 busybox setuidgid 真正切到各 uid 后测：
     *     uid 10428 (PowerLens)         -> ROOT_OK uid=0
     *     uid 10347 (byyoung.setting)   -> ROOT_OK uid=0      已知可用
     *     uid 10343 (微信)              -> Permission denied  已知未授权
     *   KernelSU allowlist 里 com.powerlens.app 的条目：
     *     flag=2 (allow), policy=10428 (就是这个包的 uid)
     *   所以 App 自己调 su 能拿到 root，一键安装确实可行。
     *
     * @return "granted" | "denied" | "noroot"
     */
    static String probeRoot() {
        // /data/adb/modules 是 0700 root:root。
        // 能 list 就说明拿到了真 root；被拒就是没授权。
        String r = Shell.run(25_000, "su", "-c",
                "ls /data/adb/modules >/dev/null 2>&1 && echo GRANTED || echo DENIED");
        if (r == null) {
            return "noroot";
        }
        String s = r.trim();
        if (s.contains("GRANTED")) {
            return "granted";
        }
        if (s.contains("DENIED")) {
            return "denied";
        }
        // su 二进制都不存在，或 pu 进程启动失败
        if (s.toLowerCase().contains("not found") || s.toLowerCase().contains("no such file")) {
            return "noroot";
        }
        if (s.toLowerCase().contains("permission") || s.toLowerCase().contains("denied")) {
            return "denied";
        }
        return "noroot";
    }

    /** 模块是否已安装。 */
    static boolean isInstalled() {
        String r = Shell.run(15_000, "su", "-c",
                "test -f " + MODULE_DIR + "/module.prop && echo YES || echo NO");
        return r != null && r.contains("YES");
    }

    /** 模块是否处于启用状态（缺 disable 标记）。 */
    static boolean isEnabled() {
        String r = Shell.run(15_000, "su", "-c",
                "test -f " + MODULE_DIR + "/disable && echo DISABLED || echo ENABLED");
        return r != null && r.contains("ENABLED");
    }

    /**
     * 一键安装。
     *
     * @param ctx     上下文（用于读 assets）
     * @param upgrade true=覆盖升级（不提示重启），false=首次安装
     */
    static Result install(Context ctx, boolean upgrade) {
        Result res = new Result();

        // ---- 1. 释放 zip ----
        File zip;
        try {
            zip = extractZip(ctx);
        } catch (Throwable e) {
            res.message = "释放模块包失败: " + e.getMessage();
            return res;
        }
        if (!zip.exists() || zip.length() == 0) {
            res.message = "模块包为空，APK 可能没有打包 assets/" + ZIP_ASSET;
            return res;
        }

        // ---- 2. root 预检（先分清「没 root」还是「没授权」）----
        String root = probeRoot();
        if ("noroot".equals(root)) {
            res.backend = "none";
            res.message = "无法获取 root。\n\n设备需要 KernelSU / Magisk 且已 root。";
            return res;
        }
        if ("denied".equals(root)) {
            res.backend = "none";
            res.message = "root 可用，但本应用未被授权。\n\n"
                    + "请在 KernelSU 管理器中把 PowerLens 加入白名单（超级用户列表），\n"
                    + "然后重试。\n\n"
                    + "注：如果根本没装 KernelSU 管理器，授权已由 allowlist 预先配置，\n"
                    + "出现本提示说明条目被移除或签名变动。";
            return res;
        }

        // ---- 3. 选后端 ----
        String backend = detectBackend();
        res.backend = backend;
        if ("none".equals(backend)) {
            res.message = "root 已授权，但未找到 ksud 或 magisk —— root 框架可能不完整";
            return res;
        }

        // ---- 4. 执行安装 ----
        // zip 放在 cacheDir，App 自己可读；ksud 以 root 读没问题。
        // 路径可能含空格，统一加引号。
        final String q = "'" + zip.getAbsolutePath() + "'";
        String cmd;
        if ("ksud".equals(backend)) {
            cmd = "ksud module install " + q;
        } else {
            cmd = "magisk --install-module " + q;
        }

        String out = Shell.run(180_000, "su", "-c", cmd);
        res.rawOutput = out == null ? "" : out;

        boolean looksOk = out != null
                && !out.contains("ERROR")
                && !out.contains("失败")
                && !out.toLowerCase().contains("fail");

        // ---- 5. 二次确认：模块目录真的出现了 ----
        boolean dirOk = isInstalled();

        if (!dirOk) {
            res.ok = false;
            res.message = "安装命令已执行，但 /data/adb/modules/" + MODULE_ID + " 不存在。"
                    + (looksOk ? "（命令无报错但未落地，可能是模块 id 冲突）" : "（见下方原始输出）");
            return res;
        }

        res.ok = true;
        res.needsReboot = !upgrade;

        // ---- 6. 立刻让采集生效（不必等重启）----
        // customize.sh 已经把二进制放到 /data/local/tmp/plc，这里直接拉守护。
        String kick = Shell.run(60_000, "su", "-c",
                "if [ -x /data/local/tmp/plc/plcollect ] && [ -f /data/local/tmp/plc/plc-daemon.sh ]; then "
              + "  for P in $(ps -A -o PID,ARGS 2>/dev/null | grep plc-daemon.sh | grep -v grep | awk '{print $1}'); do kill $P 2>/dev/null; done; "
              + "  rm -f /data/local/tmp/plc/daemon.pid; "
              + "  setsid sh /data/local/tmp/plc/plc-daemon.sh 60 1200 >/dev/null 2>&1 & "
              + "  sleep 2; echo STARTED; "
              + "else echo NOBIN; fi");

        boolean started = kick != null && kick.contains("STARTED");
        res.message = upgrade
                ? "模块已升级" + (started ? "，采集守护已重启" : "，但守护未能启动（重启后自动生效）")
                : "模块已安装" + (started ? "，采集已开始，重启后完全生效" : "，请重启手机使其生效");

        // ---- 7. 清理临时 zip ----
        try { zip.delete(); } catch (Throwable ignored) {}

        return res;
    }

    /** 卸载模块。历史数据默认保留（见模块 uninstall.sh）。 */
    static Result uninstall() {
        Result res = new Result();
        String backend = detectBackend();
        res.backend = backend;

        if (!isInstalled()) {
            res.ok = true;
            res.message = "模块未安装，无需卸载";
            return res;
        }

        String cmd;
        if ("ksud".equals(backend)) {
            cmd = "ksud module uninstall " + MODULE_ID;
        } else if ("magisk".equals(backend)) {
            cmd = "touch " + MODULE_DIR + "/remove";
        } else {
            cmd = "rm -rf " + MODULE_DIR;
        }

        String out = Shell.run(120_000, "su", "-c", cmd);
        res.rawOutput = out == null ? "" : out;

        // 目录可能因 remove 标记机制在重启后才消失，这里看 daemon 是否停了
        String still = Shell.run(20_000, "su", "-c",
                "ps -A -o ARGS 2>/dev/null | grep -c plc-daemon.sh");
        int n = 0;
        try { n = Integer.parseInt((still == null ? "0" : still).trim().replaceAll("[^0-9]", "")); } catch (Throwable ignored) {}

        res.ok = true;
        res.needsReboot = true;
        res.message = n > 1
                ? "已标记卸载，守护进程仍在运行，重启后彻底移除"
                : "已卸载，采集已停止（历史数据保留在 /data/local/tmp/powerlens）";
        return res;
    }

    /** 启用/停用模块（不卸载）。 */
    static Result setEnabled(boolean enable) {
        Result res = new Result();
        String backend = detectBackend();
        res.backend = backend;
        if (!isInstalled()) {
            res.message = "模块未安装";
            return res;
        }
        String cmd;
        if ("ksud".equals(backend)) {
            cmd = "ksud module " + (enable ? "enable" : "disable") + " " + MODULE_ID;
        } else {
            cmd = enable ? "rm -f " + MODULE_DIR + "/disable"
                         : "touch " + MODULE_DIR + "/disable";
        }
        String out = Shell.run(60_000, "su", "-c", cmd);
        res.rawOutput = out == null ? "" : out;
        res.ok = true;
        res.needsReboot = true;
        res.message = enable ? "已启用，重启后生效" : "已停用，重启后生效";
        return res;
    }

    /** 读模块版本号，未安装返回 null。 */
    static String installedVersion() {
        String r = Shell.run(15_000, "su", "-c",
                "grep '^version=' " + MODULE_DIR + "/module.prop 2>/dev/null | cut -d= -f2");
        if (r == null) return null;
        String v = r.trim();
        return v.isEmpty() ? null : v;
    }
}