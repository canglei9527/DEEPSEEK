package com.powerlens.app;

import android.app.Activity;
import android.app.job.JobInfo;
import android.app.job.JobScheduler;
import android.content.ComponentName;
import android.content.Context;
import android.content.SharedPreferences;
import android.graphics.Typeface;
import android.graphics.drawable.Drawable;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.method.ScrollingMovementMethod;
import android.util.Log;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.AdapterView;
import android.widget.ArrayAdapter;
import android.widget.BaseAdapter;
import android.widget.Button;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ListView;
import android.widget.ProgressBar;
import android.widget.Spinner;
import android.widget.TextView;

import java.io.File;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.Date;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * PowerLens 1.1 —— 修复 1.0 的四个问题：
 *
 * 1) 【功耗显示异常·致命】mAh 虚高 5.35 倍
 *    1.0 用「区间首电量计 − 区间末电量计」当耗电，期间充电会被当成消耗。
 *    1.1 改为逐对累加"只算下降、跳过充电段、过滤跳变"，见 Storage.summarize。
 *
 * 2) 【吞吐】1.0 每次 refresh 把整个 snapshots.tsv 全读进内存再 split，
 *    1.5 万行 × 30 秒一次，白白吃 CPU 和电。1.1 用 RandomAccessFile 流式读 + 早期 skip。
 *
 * 3) 【并发】1.0 的 autoRefresh 30 秒一次且不判 busy，长采集时堆叠刷新线程。
 *    1.1 加 refreshInFlight 闸门 + 200 ms 去抖。
 *
 * 4) 【口径】
 *    1.0 的"前台"权重用的是总 CPU，和"全部"没区别。
 *    1.1 明确：全部 / 前台(cpu 全量) / 后台(息屏 cpu + 息屏 wl) / 前台服务(fgs)，并在标题写清。
 */
public class MainActivity extends Activity {

    private static final String TAG = "PowerLens";
    private static final String PREFS = "powerlens";
    private static final String KEY_MCAST = "mcast_on";
    private static final String KEY_HIDE_AGG = "hide_agg";
    /** 口径选择（0=全部 1=前台 2=后台 3=前台服务），持久化 */
    private static final String KEY_MODE = "mode";
    /** 时间窗口（0=近1时 1=近6时 2=近24时 3=全天），持久化 */
    private static final String KEY_RANGE = "range";
    private static final int JOB_ID = 4711;

    private static final Map<String, String> SPECIAL = new HashMap<>();

    static {
        SPECIAL.put("0", "root/内核");
        SPECIAL.put("1000", "Android 系统");
        SPECIAL.put("1001", "电话/Radio");
        SPECIAL.put("1002", "蓝牙");
        SPECIAL.put("1003", "图形合成");
        SPECIAL.put("1004", "核心服务");
        SPECIAL.put("1006", "DRM");
        SPECIAL.put("1007", "语音通话");
        SPECIAL.put("1009", "外部存储");
        SPECIAL.put("1010", "媒体扫描");
        SPECIAL.put("1011", "USIM");
        SPECIAL.put("1012", "DRM 证书");
        SPECIAL.put("1013", "媒体服务");
        SPECIAL.put("1014", "GPS");
        SPECIAL.put("1015", "NFC 存储");
        SPECIAL.put("1016", "字典");
        SPECIAL.put("1017", "Shell");
        SPECIAL.put("1018", "蓝牙");
        SPECIAL.put("1019", "安全元件");
        SPECIAL.put("1020", "删除的文件");
        SPECIAL.put("1021", "NFC");
        SPECIAL.put("1022", "日志");
        SPECIAL.put("1023", "com.android.fmradio");
        SPECIAL.put("1024", "输入法");
        SPECIAL.put("1026", "网络定位");
        SPECIAL.put("1027", "内部存储");
        SPECIAL.put("1028", "电话");
        SPECIAL.put("1029", "网络栈");
        SPECIAL.put("1030", "Wi-Fi 广播");
        SPECIAL.put("1031", "输入设备");
        SPECIAL.put("1032", "蓝牙");
        SPECIAL.put("1033", "导出服务");
        SPECIAL.put("1034", "截图");
        SPECIAL.put("1035", "拨号");
        SPECIAL.put("1036", "external_storage");
        SPECIAL.put("1037", "媒体");
        SPECIAL.put("1038", "无标签");
        SPECIAL.put("1039", "存储");
        SPECIAL.put("1040", "日志缓冲");
        SPECIAL.put("1041", "安全");
        SPECIAL.put("1042", "IPC 服务");
        SPECIAL.put("1043", "文件共享");
        SPECIAL.put("1044", "绘制");
        SPECIAL.put("1045", "剪贴板");
        SPECIAL.put("1046", "媒体会话");
        SPECIAL.put("1047", "Wi-Fi");
        SPECIAL.put("1048", "init 脚本");
        SPECIAL.put("1049", "on-device 智能");
        SPECIAL.put("1050", "网络管理");
        SPECIAL.put("1051", "输入法/剪贴板");
        SPECIAL.put("1052", "嵌入 UID");
        SPECIAL.put("1053", "显示");
        SPECIAL.put("1054", "调度");
        SPECIAL.put("1055", "麦克风权限");
        SPECIAL.put("1056", "音频");
        SPECIAL.put("1057", "摄像头");
        SPECIAL.put("1058", "安装包");
        SPECIAL.put("1059", "健康数据");
        SPECIAL.put("1060", "权限管理");
        SPECIAL.put("1061", "文档");
        SPECIAL.put("1062", "设备状态");
        SPECIAL.put("1063", "运行时资源");
        SPECIAL.put("1064", "电话管理");
        SPECIAL.put("1065", "媒体排序");
        SPECIAL.put("1066", "网络共享");
        SPECIAL.put("1067", "媒体共享");
        SPECIAL.put("1068", "相机");
        SPECIAL.put("1069", "TEE 存储");
        SPECIAL.put("1070", "SIM 卡");
        SPECIAL.put("1071", "Wi-Fi 感知");
        SPECIAL.put("1072", "RTT");
        SPECIAL.put("1073", "音效/振动");
        SPECIAL.put("1074", "WebView");
        SPECIAL.put("1075", "预测");
        SPECIAL.put("1076", "语音交互");
        SPECIAL.put("1077", "上下文中心");
        SPECIAL.put("1078", "自动填充");
        SPECIAL.put("1079", "虚拟摄像头");
        SPECIAL.put("1080", "xr 服务");
        SPECIAL.put("1081", "配置");
        SPECIAL.put("1082", "系统组件 1082");
        SPECIAL.put("1083", "文件共享");
        SPECIAL.put("1084", "mac 地址");
        SPECIAL.put("1085", "电话广播");
        SPECIAL.put("1086", "创作");
        SPECIAL.put("1087", "搜索 UI");
        SPECIAL.put("1088", "壁纸");
        SPECIAL.put("1089", "供应商服务");
        SPECIAL.put("1090", "多设备");
        SPECIAL.put("1091", "轨迹");
        SPECIAL.put("1092", "游戏");
        SPECIAL.put("1093", "辅助功能");
        SPECIAL.put("1094", "设备配置");
        SPECIAL.put("1095", "媒体能耗");
        SPECIAL.put("1096", "硬件状态");
        SPECIAL.put("1097", "时间检测");
        SPECIAL.put("1098", "线程网络");
        SPECIAL.put("1999", "Shell");
        SPECIAL.put("2000", "Shell");
        SPECIAL.put("9999", "未分配");
    }
    private static final Map<String, String> SYS_LABEL = new HashMap<>();

    static {
        SYS_LABEL.put("sys:kernel", "root/内核");
        SYS_LABEL.put("sys:system", "Android 系统");
        SYS_LABEL.put("sys:radio", "电话/Radio");
        SYS_LABEL.put("sys:bluetooth", "蓝牙");
        SYS_LABEL.put("sys:gfx", "图形合成");
        SYS_LABEL.put("sys:coresvc", "核心服务");
        SYS_LABEL.put("sys:drm", "DRM");
        SYS_LABEL.put("sys:voicecall", "语音通话");
        SYS_LABEL.put("sys:media_ext", "外部存储");
        SYS_LABEL.put("sys:mediascan", "媒体扫描");
        SYS_LABEL.put("sys:usim", "USIM");
        SYS_LABEL.put("sys:drm_cert", "DRM 证书");
        SYS_LABEL.put("sys:media", "媒体服务");
        SYS_LABEL.put("sys:gps", "GPS");
        SYS_LABEL.put("sys:nfc_storage", "NFC 存储");
        SYS_LABEL.put("sys:dictionary", "字典");
        SYS_LABEL.put("sys:shell", "Shell");
        SYS_LABEL.put("sys:bluetooth_share", "蓝牙共享");
        SYS_LABEL.put("sys:secure_element", "安全元件");
        SYS_LABEL.put("sys:deleted", "已删文件");
        SYS_LABEL.put("sys:nfc", "NFC");
        SYS_LABEL.put("sys:log", "日志");
        SYS_LABEL.put("sys:fm", "FM 收音");
        SYS_LABEL.put("sys:ime", "输入法");
        SYS_LABEL.put("sys:netlocation", "网络定位");
        SYS_LABEL.put("sys:internal_storage", "内部存储");
        SYS_LABEL.put("sys:phone", "电话");
        SYS_LABEL.put("sys:netstack", "网络栈");
        SYS_LABEL.put("sys:wifi_broadcast", "Wi-Fi 广播");
        SYS_LABEL.put("sys:inputdev", "输入设备");
        SYS_LABEL.put("sys:bluetooth2", "蓝牙");
        SYS_LABEL.put("sys:export", "导出服务");
        SYS_LABEL.put("sys:screenshot", "截图");
        SYS_LABEL.put("sys:dialer", "拨号");
        SYS_LABEL.put("sys:logd", "logd");
        SYS_LABEL.put("sys:media2", "媒体");
        SYS_LABEL.put("sys:nolabel", "无标签");
        SYS_LABEL.put("sys:storage", "存储");
        SYS_LABEL.put("sys:logbuffer", "日志缓冲");
        SYS_LABEL.put("sys:security", "安全");
        SYS_LABEL.put("sys:ipc", "IPC 服务");
        SYS_LABEL.put("sys:fileshare", "文件共享");
        SYS_LABEL.put("sys:draw", "绘制");
        SYS_LABEL.put("sys:clipboard", "剪贴板");
        SYS_LABEL.put("sys:mediasession", "媒体会话");
        SYS_LABEL.put("sys:wifi", "Wi-Fi");
        SYS_LABEL.put("sys:initscript", "init 脚本");
        SYS_LABEL.put("sys:devicesmart", "设备智能");
        SYS_LABEL.put("sys:netmgmt", "网络管理");
        SYS_LABEL.put("sys:ime_clipboard", "输入法/剪贴板");
        SYS_LABEL.put("sys:embedded", "嵌入 UID");
        SYS_LABEL.put("sys:display", "显示");
        SYS_LABEL.put("sys:sched", "调度");
        SYS_LABEL.put("sys:micperm", "麦克风权限");
        SYS_LABEL.put("sys:audio", "音频");
        SYS_LABEL.put("sys:camera_perm", "摄像头权限");
        SYS_LABEL.put("sys:installd", "安装包");
        SYS_LABEL.put("sys:health", "健康数据");
        SYS_LABEL.put("sys:permmgmt", "权限管理");
        SYS_LABEL.put("sys:documents", "文档");
        SYS_LABEL.put("sys:devicestate", "设备状态");
        SYS_LABEL.put("sys:runtime", "运行时资源");
        SYS_LABEL.put("sys:telephonymgmt", "电话管理");
        SYS_LABEL.put("sys:mediasort", "媒体排序");
        SYS_LABEL.put("sys:netshare", "网络共享");
        SYS_LABEL.put("sys:mediashare", "媒体共享");
        SYS_LABEL.put("sys:camera", "相机");
        SYS_LABEL.put("sys:teestorage", "TEE 存储");
        SYS_LABEL.put("sys:sim", "SIM 卡");
        SYS_LABEL.put("sys:wifiaware", "Wi-Fi 感知");
        SYS_LABEL.put("sys:rtt", "RTT");
        SYS_LABEL.put("sys:sound_vib", "音效/振动");
        SYS_LABEL.put("sys:webview", "WebView");
        SYS_LABEL.put("sys:prediction", "预测");
        SYS_LABEL.put("sys:voiceinteract", "语音交互");
        SYS_LABEL.put("sys:context", "上下文中心");
        SYS_LABEL.put("sys:autofill", "自动填充");
        SYS_LABEL.put("sys:virtcamera", "虚拟摄像头");
        SYS_LABEL.put("sys:xr", "XR 服务");
        SYS_LABEL.put("sys:config", "配置");
        SYS_LABEL.put("sys:comp1082", "系统组件 1082");
        SYS_LABEL.put("sys:fileshare2", "文件共享");
        SYS_LABEL.put("sys:macaddr", "MAC 地址");
        SYS_LABEL.put("sys:phonebroadcast", "电话广播");
        SYS_LABEL.put("sys:creation", "创作");
        SYS_LABEL.put("sys:searchui", "搜索 UI");
        SYS_LABEL.put("sys:wallpaper", "壁纸");
        SYS_LABEL.put("sys:vendor", "供应商服务");
        SYS_LABEL.put("sys:multidevice", "多设备");
        SYS_LABEL.put("sys:trace", "轨迹");
        SYS_LABEL.put("sys:game", "游戏");
        SYS_LABEL.put("sys:a11y", "辅助功能");
        SYS_LABEL.put("sys:devconfig", "设备配置");
        SYS_LABEL.put("sys:mediapower", "媒体能耗");
        SYS_LABEL.put("sys:hwstate", "硬件状态");
        SYS_LABEL.put("sys:timedetect", "时间检测");
        SYS_LABEL.put("sys:threadnet", "线程网络");
        SYS_LABEL.put("sys:shell2", "Shell");
            SYS_LABEL.put("sys:adb_shell", "Shell 调试通道");
        SYS_LABEL.put("sys:unassigned", "未分配");
    }


    private TextView status;
    private ListView listView;
    private Spinner modeSpinner;
    private Spinner rangeSpinner;
    private Button btnCollect;
    private Button btnDiag;
    private Button btnMcast;

    private AppAdapter adapter;
    private volatile boolean refreshInFlight;
    private boolean hideAggregate;
    private final Handler ui = new Handler(Looper.getMainLooper());
    private final SimpleDateFormat fmt = new SimpleDateFormat("HH:mm:ss", Locale.CHINA);
    private final Map<String, Drawable> iconCache = new HashMap<>();
    private final Map<String, String> labelCache = new HashMap<>();
    private final Map<Integer, String> uidPkgCache = new HashMap<>();

    private final Runnable autoRefresh = new Runnable() {
        @Override
        public void run() {
            refresh();
            ui.postDelayed(this, 30_000L);
        }
    };

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        try {
            setContentView(buildUi());
            hideAggregate = getSharedPreferences(PREFS, 0).getBoolean(KEY_HIDE_AGG, false);
            updateCollectLabel();
            updateMcastLabel();
            try {
                scheduleJob();
            } catch (Throwable e) {
                Log.w(TAG, "job", e);
            }
            refresh();
            if (getIntent() != null && getIntent().getBooleanExtra("autocollect", false)) {
                ui.postDelayed(new Runnable() {
                    @Override
                    public void run() {
                        doCollect();
                    }
                }, 400L);
            }
        } catch (Throwable e) {
            Log.e(TAG, "ui build failed", e);
            TextView tv = new TextView(this);
            tv.setText("UI 构建失败: " + e);
            tv.setTextColor(0xFFFF4444);
            setContentView(tv);
        }
    }

    @Override
    protected void onResume() {
        super.onResume();
        ui.removeCallbacks(autoRefresh);
        ui.postDelayed(autoRefresh, 30_000L);
    }

    @Override
    protected void onPause() {
        super.onPause();
        ui.removeCallbacks(autoRefresh);
    }

    private int dp(float v) {
        return (int) (v * getResources().getDisplayMetrics().density + 0.5f);
    }

    private View buildUi() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(0xFF0F1116);
        int p = dp(12);
        root.setPadding(p, p, p, 0);
        root.setFitsSystemWindows(true);

        status = new TextView(this);
        status.setTextSize(11f);
        status.setTextColor(0xFF9AA7B8);
        status.setText("PowerLens 1.1 启动中…");
        status.setMovementMethod(new ScrollingMovementMethod());
        status.setPadding(dp(4), dp(6), dp(4), dp(6));
        root.addView(status, new LinearLayout.LayoutParams(-1, -2));

        LinearLayout bar = new LinearLayout(this);
        bar.setOrientation(LinearLayout.HORIZONTAL);
        bar.setGravity(Gravity.CENTER_VERTICAL);
        bar.setPadding(0, dp(6), 0, dp(6));

        modeSpinner = new Spinner(this);
        ArrayAdapter<String> ma = new ArrayAdapter<>(this, android.R.layout.simple_spinner_dropdown_item,
                new String[]{"全部", "前台", "后台", "前台服务"});
        modeSpinner.setAdapter(ma);
        // 恢复上次选择（口径 + 时间窗口都持久化）
        android.content.SharedPreferences sp0 = getSharedPreferences(PREFS, 0);
        int savedMode = sp0.getInt(KEY_MODE, 0);
        if (savedMode >= 0 && savedMode <= 3) {
            // animate=false 表示不播放动画；仍会触发 onItemSelected
            modeSpinner.setSelection(savedMode);
        }
        modeSpinner.setLayoutParams(new LinearLayout.LayoutParams(0, dp(40), 1.1f));
        modeSpinner.setOnItemSelectedListener(new AdapterView.OnItemSelectedListener() {
            @Override
            public void onItemSelected(AdapterView<?> parent, View view, int pos, long id) {
                getSharedPreferences(PREFS, 0).edit().putInt(KEY_MODE, pos).apply();
                refresh();
            }

            @Override
            public void onNothingSelected(AdapterView<?> parent) {
            }
        });
        bar.addView(modeSpinner);

        // 时间窗口：解决"6 小时窗口切进充电段导致电流虚高"的问题
        rangeSpinner = new Spinner(this);
        ArrayAdapter<String> ra = new ArrayAdapter<>(this, android.R.layout.simple_spinner_dropdown_item,
                new String[]{"近1时", "近6时", "近24时", "全天"});
        rangeSpinner.setAdapter(ra);
        int savedRange = sp0.getInt(KEY_RANGE, 1);
        rangeSpinner.setSelection((savedRange >= 0 && savedRange <= 3) ? savedRange : 1);
        rangeSpinner.setLayoutParams(new LinearLayout.LayoutParams(0, dp(40), 1.1f));
        rangeSpinner.setOnItemSelectedListener(new AdapterView.OnItemSelectedListener() {
            @Override
            public void onItemSelected(AdapterView<?> parent, View view, int pos, long id) {
                refresh();
            }

            @Override
            public void onNothingSelected(AdapterView<?> parent) {
            }
        });
        bar.addView(rangeSpinner);

        btnCollect = new Button(this);
        btnCollect.setText("采集");
        btnCollect.setAllCaps(false);
        btnCollect.setTextSize(12f);
        btnCollect.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                doCollect();
            }
        });
        // 长按切换「是否显示系统聚合 UID」。
        // root/内核(0)、Android 系统(1000)、系统服务(10000-10499) 是聚合容器，
        // 它们的 cpu 是所有子项之和，和单个应用并列排名会永久霸榜。
        btnCollect.setOnLongClickListener(new View.OnLongClickListener() {
            @Override
            public boolean onLongClick(View v) {
                hideAggregate = !hideAggregate;
                getSharedPreferences(PREFS, 0).edit().putBoolean(KEY_HIDE_AGG, hideAggregate).apply();
                updateCollectLabel();
                refresh();
                return true;
            }
        });
        bar.addView(btnCollect, new LinearLayout.LayoutParams(0, dp(40), 0.85f));

        btnDiag = new Button(this);
        btnDiag.setText("诊断");
        btnDiag.setAllCaps(false);
        btnDiag.setTextSize(12f);
        btnDiag.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                doDiag();
            }
        });
        bar.addView(btnDiag, new LinearLayout.LayoutParams(0, dp(40), 0.85f));

        btnMcast = new Button(this);
        btnMcast.setAllCaps(false);
        btnMcast.setTextSize(12f);
        btnMcast.setText("组播");
        btnMcast.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                toggleMcast();
            }
        });
        bar.addView(btnMcast, new LinearLayout.LayoutParams(0, dp(40), 1f));

        root.addView(bar);

        listView = new ListView(this);
        listView.setDivider(null);
        listView.setDividerHeight(0);
        root.addView(listView, new LinearLayout.LayoutParams(-1, 0, 1f));
        return root;
    }

    /**
     * 这些 UID 是「聚合容器」，不代表任何单一应用，不能和具体应用并列排名。
     * 本机实测：「root/内核」聚合了所有内核线程，「Android 系统」聚合了
     * 几十个系统服务，它们的 cpu/流量是所有子项之和，
     * 与「微信」这种单一应用放一起比是不公平的。
     */
    private static boolean isAggregate(int uid) {
        return uid == 0                       // root/内核
                || uid == 1000                // system_server
                || uid == 1001                // radio
                || uid == 9999                // 未分配
                || (uid >= 10000 && uid < 10500);   // 系统服务 UID 段
    }

    private void scheduleJob() {
        // HyperOS 上 setPersisted(true) 会被 JobSchedulerService.enforceValidJobRequest 拒
        // （需要 RECEIVE_BOOT_COMPLETED + 系统白名单），所以这里不做常驻调度。
        // 采集改由 root 常驻的 C 守护 /data/local/tmp/plc/plc-daemon.sh 负责，
        // App 只读它的输出，App 自己零后台开销。
        Log.i(TAG, "job 调度已交由 root C 守护（plc-daemon.sh），App 侧不注册 JobScheduler");
    }

    private void doCollect() {
        if (refreshInFlight) {
            return;
        }
        status.setText("采集中…（dumpsys 需要 5–20 s）");
        new Thread(new Runnable() {
            @Override
            public void run() {
                final String msg = Collector.collectOnce(MainActivity.this);
                ui.post(new Runnable() {
                    @Override
                    public void run() {
                        status.setText(msg);
                        refresh();
                    }
                });
            }
        }, "pl-collect-ui").start();
    }

    private void doDiag() {
        status.setText("诊断中…");
        new Thread(new Runnable() {
            @Override
            public void run() {
                final String s = Collector.diagnose(MainActivity.this);
                ui.post(new Runnable() {
                    @Override
                    public void run() {
                        status.setText(s);
                    }
                });
            }
        }, "pl-diag").start();
    }

    private void toggleMcast() {
        SharedPreferences sp = getSharedPreferences(PREFS, 0);
        final boolean next = !sp.getBoolean(KEY_MCAST, false);
        new Thread(new Runnable() {
            @Override
            public void run() {
                final String r = Collector.setMulticastBlock(next);
                getSharedPreferences(PREFS, 0).edit().putBoolean(KEY_MCAST, next).apply();
                ui.post(new Runnable() {
                    @Override
                    public void run() {
                        status.setText(r);
                        updateMcastLabel();
                    }
                });
            }
        }, "pl-mcast").start();
    }

    private void updateMcastLabel() {
        boolean on = getSharedPreferences(PREFS, 0).getBoolean(KEY_MCAST, false);
        if (btnMcast != null) {
            btnMcast.setText(on ? "组播:开" : "组播:关");
        }
    }

    /**
     * 「采集」按钮文案随 hideAggregate 变化。
     * 长按切换：显示全部 ↔ 只显示应用。
     */
    private void updateCollectLabel() {
        if (btnCollect != null) {
            btnCollect.setText(hideAggregate ? "采集·应用" : "采集");
        }
    }

    /**
     * 时间范围。
     *
     * 默认「最近 6 小时」而不是「今天 0 点」：
     *   本机采样间隔 10 分钟，一天修完 144 个点，
     *   但夜间充电、白天充电的大段区间会把「实测放电」算成全天总量，
     *   再除全天时长得到 320 mA 这种无意义数字。
     *   6 小时窗口能覆盖大部分连续待机段，且不会把日夜混在一起。
     */
    /** 时间窗口选项；索引对应 rangeSpinner 的位置 */
    private static final long[] RANGE_MS = {
            1 * 3600 * 1000L,
            6 * 3600 * 1000L,
            24 * 3600 * 1000L,
            -1L                       // -1 = 全天（今天 0 点起）
    };

    private long rangeStart() {
        int i = 1;
        if (rangeSpinner != null) {
            i = rangeSpinner.getSelectedItemPosition();
            if (i < 0 || i >= RANGE_MS.length) {
                i = 1;
            }
        }
        if (RANGE_MS[i] < 0) {
            java.util.Calendar c = java.util.Calendar.getInstance();
            c.set(java.util.Calendar.HOUR_OF_DAY, 0);
            c.set(java.util.Calendar.MINUTE, 0);
            c.set(java.util.Calendar.SECOND, 0);
            c.set(java.util.Calendar.MILLISECOND, 0);
            return c.getTimeInMillis();
        }
        return System.currentTimeMillis() - RANGE_MS[i];
    }

    private void refresh() {
        if (refreshInFlight) {
            return;
        }
        refreshInFlight = true;
        final long from = rangeStart();
        final long to = System.currentTimeMillis();

        new Thread(new Runnable() {
            @Override
            public void run() {
                final File store = Collector.storeFile(MainActivity.this);
                final List<Storage.Row> rows = Storage.readRange(store, from, to);
                final Storage.Summary sum = Storage.summarize(store, from, to);

                // 维度数据（pldims 低频产出）
                final Dims.Snapshot dims = Dims.load(MainActivity.this);
                final long dimsAge = Dims.ageSeconds(MainActivity.this);

                final int mode = modeSpinner == null ? 0 : modeSpinner.getSelectedItemPosition();

                // 先把区间 CPU 增量汇总，用于按维度比例分配
                long totDcpu = 0;
                for (Storage.Row r : rows) {
                    totDcpu += r.dCpuMs();
                }

                final List<Item> items = new ArrayList<>();
                double totalW = 0;
                for (Storage.Row r : rows) {
                    long dcpu = r.dCpuMs();
                    double w;

                    if (mode == 1 || mode == 2 || mode == 3) {
                        // 前台 / 后台 / 前台服务：dims 增量时长 × CPU 活跃度
                        //
                        // ===== 为什么不能只用 dims 时长 =====
                        //   实测发现 dumpsys 给很多系统服务报了完全相同的 fg 值
                        //   （8 个 UID 全是 Δfg=170669ms），按它排序就是并列 12.5%。
                        //   fg 字段对这些服务没有区分度。
                        //
                        //   CPU 又是另一面：CPU 高说明真在干活，但没有"前台/后台"语义。
                        //   两者相乘可以同时体现「在前台」和「真在跑」：
                        //     weight = √(fg秒 × CPU秒)
                        //   开平方是为了避免任一项极小时把权重压没
                        //   （例如 fg=170s、cpu=0.08s，直接相乘只有 13.6）。
                        double sec = dims.secondsOf(r.uid, mode);
                        if (sec <= 0.001d) {
                            continue;   // 该 UID 在这个维度上是 0 → 不显示
                        }
                        double cpuSec = dcpu / 1000.0d;
                        double wDim;
                        if (cpuSec > 0.01d) {
                            wDim = Math.sqrt(sec * cpuSec);
                        } else {
                            // 没有 CPU 增量（纯挂后台没动）：用时长本身，但降权
                            wDim = sec * 0.05d;
                        }
                        // 真网络流量补充（procfs 源的 IO 不计）
                        double trafficPart = r.hasRealTraffic()
                                ? (r.dRx() + r.dTx()) / 1048576.0d * 0.44d
                                : 0;
                        w = wDim + trafficPart;
                    } else {
                        w = r.wAll();
                    }

                    if (w <= 0.001d) {
                        continue;
                    }
                    if (hideAggregate && isAggregate(r.uid)) {
                        continue;
                    }
                    Item it = new Item();
                    it.uid = r.uid;
                    it.pkg = r.pkg;
                    it.weight = w;
                    // 必须用区间增量，不能用累计绝对值
                    // （dumpsys 的 CPU 是开机以来累加，直接显示会得到「17 小时」这种荒谬值）
                    it.cpuMs = r.dCpuMs();
                    it.cpuOffMs = r.dCpuOffMs();
                    it.wlMs = r.dWlMs();
                    it.wlOffMs = r.dWlOffMs();
                    it.rx = r.dRx();
                    it.tx = r.dTx();
                    // 只有 dumpsys 来源的 rx/tx 才是真网络流量；
                    // procfs 来源的是文件读写字节数，单独标记以免显示成"流量"
                    it.trafficIsReal = r.hasRealTraffic();
                    it.fgMs = r.dFgMs();
                    it.bgMs = r.dBgMs();
                    it.fgsMs = r.dFgsMs();
                    totalW += w;
                    items.add(it);
                }

                final double dischargeMah = sum.dischargeUah / 1000.0d;
                for (Item it : items) {
                    it.share = totalW > 0 ? it.weight / totalW : 0;
                    it.mah = totalW > 0 && dischargeMah > 0 ? dischargeMah * it.share : 0;
                }
                Collections.sort(items, new Comparator<Item>() {
                    @Override
                    public int compare(Item a, Item b) {
                        return Double.compare(b.weight, a.weight);
                    }
                });

                ui.post(new Runnable() {
                    @Override
                    public void run() {
                        try {
                            adapter = new AppAdapter(items);
                            listView.setAdapter(adapter);
                            status.setText(buildStatus(store, sum, items.size(), mode, from, to, dims, dimsAge));
                        } finally {
                            refreshInFlight = false;
                        }
                    }
                });
            }
        }, "pl-refresh").start();
    }

    private String buildStatus(File store, Storage.Summary sum, int n, int mode, long from, long to,
                                Dims.Snapshot dims, long dimsAge) {
        StringBuilder sb = new StringBuilder();
        sb.append("区间 ")
                .append(new SimpleDateFormat("MM-dd HH:mm", Locale.CHINA).format(new Date(from)))
                .append(" → ")
                .append(fmt.format(new Date(to)))
                .append("  ·  ")
                .append(n)
                .append(" 个进程");

        double mah = sum.dischargeUah / 1000.0d;
        // 用「电池采样点的真实跨度」，不是查询窗口跨度。
        // 两者可能差很多：查询窗口 6 小时，但有效电池点只覆盖 2.8 小时，
        // 用 6 小时去除会得到只有一半的电流值。
        double hours = (sum.lastTs - sum.firstTs) / 3600000.0d;
        if (mah > 0.05d) {
            sb.append("\n实测放电 ").append(String.format(Locale.CHINA, "%.1f", mah)).append(" mAh");
            if (hours > 0.01d) {
                double ma = sum.ratePerHourUah / 1000.0d;
                double pctPerHour = (mah / 6100.0d) * 100.0d / hours;
                sb.append("  ·  ").append(String.format(Locale.CHINA, "%.1f", ma)).append(" mA");
                sb.append("  ·  ").append(String.format(Locale.CHINA, "%.2f", pctPerHour)).append(" %/h");
                sb.append("\n有效采样跨度 ").append(String.format(Locale.CHINA, "%.1f", hours))
                        .append(" 小时（").append(sum.points).append(" 个电池点）");
            }
        } else if (sum.points < 2) {
            sb.append("\n采样点不足（").append(sum.points).append(" 个），至少 2 次才能算差值");
        } else {
            sb.append("\n本区间无有效放电（充电中或电量计未变化）");
        }

        if (sum.skippedSegments > 0 || sum.skippedJumps > 0 || sum.skippedInvalid > 0) {
            sb.append("\n已排除：充电/持平段 ").append(sum.skippedSegments)
                    .append("  · 跳变 ").append(sum.skippedJumps);
            if (sum.skippedInvalid > 0) {
                sb.append("  · 伪数据 ").append(sum.skippedInvalid);
            }
        }

        String modeTip;
        if (mode == 1) {
            modeTip = "前台口径：√(前台秒 × CPU秒)，反映前台且真在跑";
        } else if (mode == 2) {
            modeTip = "后台口径：√(后台秒 × CPU秒)";
        } else if (mode == 3) {
            modeTip = "前台服务口径：√(前台服务秒 × CPU秒)";
        } else {
            modeTip = "全部口径：总 CPU + 息屏 CPU + 唤醒锁 + 流量";
        }
        sb.append("\n").append(modeTip);

        // 维度数据状态（前台/后台/前台服务模式依赖它）
        if (mode >= 1 && mode <= 3) {
            if (dimsAge < 0) {
                sb.append("\n[!] 没有维度数据，已退化到「全部」口径");
                sb.append("\n    修复：su -c \"/data/local/tmp/plc/pldims\"");
            } else if (dimsAge > 3600) {
                sb.append("\n[!] 维度数据已 ").append(dimsAge / 60).append(" 分钟未更新");
            } else {
                sb.append("\n维度数据 ").append(dimsAge < 60 ? dimsAge + " 秒前" : dimsAge / 60 + " 分钟前")
                        .append("（").append(dims.count).append(" 个 UID）");
            }
        }

        long last = Collector.lastTs(store);
        if (last > 0) {
            long ago = (System.currentTimeMillis() - last) / 1000;
            sb.append("\n最后采集 ").append(ago < 60 ? ago + " 秒前" : ago / 60 + " 分钟前")
                    .append("（共 ").append(Collector.countLines(store)).append(" 条）");
        } else {
            sb.append("\n还没有数据，点【采集】");
        }
        sb.append("\n每行 mAh 按权重分摊，合计 = 实测放电（已剔除充电段）");
        return sb.toString();
    }

    // ---------------- Item / Adapter ----------------

    static final class Item {
        int uid;
        String pkg;
        double weight;
        double share;
        double mah;
        long cpuMs;
        long cpuOffMs;
        long wlMs;
        long wlOffMs;
        long rx;
        long tx;
        long fgMs;
        long bgMs;
        long fgsMs;
        /** true = rx/tx 是真网络流量；false = 文件读写字节数（procfs 来源） */
        boolean trafficIsReal;
    }

    private static String dur(long ms) {
        if (ms <= 0) {
            return "0";
        }
        if (ms < 1000) {
            return ms + "ms";
        }
        if (ms < 60_000) {
            // 不到 1 分钟要显示秒，否则全是 "0m" 看不出差异
            return String.format(Locale.CHINA, "%.1fs", ms / 1000.0d);
        }
        long m = ms / 60000;
        if (m < 60) {
            return m + "m";
        }
        return (m / 60) + "h" + (m % 60) + "m";
    }

    private static String bytes(long b) {
        if (b < 1024) {
            return b + "B";
        }
        if (b < 1048576) {
            return String.format(Locale.CHINA, "%.0fK", b / 1024.0);
        }
        if (b < 1073741824L) {
            return String.format(Locale.CHINA, "%.1fM", b / 1048576.0);
        }
        return String.format(Locale.CHINA, "%.2fG", b / 1073741824.0);
    }

    private final class AppAdapter extends BaseAdapter {
        private final List<Item> data;

        AppAdapter(List<Item> d) {
            data = d;
        }

        @Override
        public int getCount() {
            return data.size();
        }

        @Override
        public Object getItem(int i) {
            return data.get(i);
        }

        @Override
        public long getItemId(int i) {
            return i;
        }

        /**
         * 缓存键必须包含 uid。
         * 之前用 pkg 单独作键，导致所有 pkg="?" 的 UID 共用键 "?"，
         * 于是列表里出现一堆重复的 "uid 1082" —— 全是第一个解析结果。
         */
        private String cacheKey(Item it) {
            return it.uid + "|" + (it.pkg == null ? "?" : it.pkg);
        }

        /**
         * UID 名称解析。
         *
         * ===== 教训：不要猜系统 UID 的语义 =====
         * 我一开始维护了一张 uid→名称 的硬编码表（1000=Android系统、1077=上下文中心…），
         * 但不同 Android 版本、不同 OEM 的 UID 分配并不一致。
         * 实测 uid 1077 在这台机器上是 external_storage，而我表里写的是"上下文中心"，
         * 于是 UI 上出现了"CPU 0m 却占 60%"的 external_storage。
         *
         * 现在的优先级：
         *   1. C 采集器已经写好的 pkg（它读 packages.list + 内置 UID 表，最准）
         *   2. PackageManager 能查到 → 应用名
         *   3. 都没有 → "(uid N)"，诚实显示，不猜
         */
        /**
         * C 采集器写的系统 UID 标识（ASCII，形如 "sys:kernel"）→ 中文名。
         *
         * 为什么不在 C 里直接写中文：
         *   C 程序写入的 UTF-8 中文在不同 locale 的读取环境下会变成 "è Ç" 这种乱码。
         *   所以 C 只写 ASCII 标识，由 Java（UTF-8 处理稳定）负责翻译。
         */
        private String label(Item it) {
            String key = cacheKey(it);
            String s = labelCache.get(key);
            if (s != null) {
                return s;
            }

            s = null;
            boolean hasPkg = it.pkg != null && !"?".equals(it.pkg)
                    && !it.pkg.isEmpty() && !it.pkg.startsWith("(uid");

            // 0) 特殊标签：hasPkg 且以 "sys:" 开头时后面会重新赋值
            if (hasPkg && it.pkg.startsWith("sys:")) {
                String zh = SYS_LABEL.get(it.pkg);
                s = (zh != null) ? zh : it.pkg.substring(4);
                labelCache.put(key, s);
                return s;
            }

            // 1) 采集器已给名字（含系统 UID 的语义名）
            if (hasPkg) {
                // 如果看起来是包名，尝试取应用名
                if (it.pkg.indexOf('.') > 0) {
                    try {
                        android.content.pm.PackageManager pm = getPackageManager();
                        s = pm.getApplicationLabel(pm.getApplicationInfo(it.pkg, 0)).toString();
                    } catch (Throwable ignored) {
                    }
                }
                // 取不到应用名就直接用采集器给的名字（可能是"蓝牙""Wi-Fi"这类语义名）
                if (s == null || s.isEmpty()) {
                    s = it.pkg;
                }
            }

            // 2) 采集器给的是 "?"，尝试自己查
            if (s == null) {
                s = pkgNameFromUid(it.uid);
                if (s != null && s.indexOf('.') > 0) {
                    try {
                        android.content.pm.PackageManager pm = getPackageManager();
                        s = pm.getApplicationLabel(pm.getApplicationInfo(s, 0)).toString();
                    } catch (Throwable ignored) {
                    }
                }
            }

            // 3) 诚实兜底
            if (s == null || s.isEmpty()) {
                s = "(uid " + it.uid + ")";
            }

            labelCache.put(key, s);
            return s;
        }

        /** 从 /data/system/packages.list 反查 uid 对应的包名（root 可读，App 读不到时返回 null） */
        private String pkgNameFromUid(int uid) {
            String cached = uidPkgCache.get(uid);
            if (cached != null) {
                return cached.isEmpty() ? null : cached;
            }
            String found = null;
            java.io.BufferedReader r = null;
            try {
                r = new java.io.BufferedReader(new java.io.FileReader("/data/system/packages.list"));
                String line;
                while ((line = r.readLine()) != null) {
                    String[] f = line.split("\\s+");
                    if (f.length >= 2) {
                        try {
                            if (Integer.parseInt(f[1]) == uid) {
                                found = f[0];
                                break;
                            }
                        } catch (Throwable ignored) {
                        }
                    }
                }
            } catch (Throwable ignored) {
                // App 通常读不到，退化到 SPECIAL 表
            } finally {
                if (r != null) {
                    try {
                        r.close();
                    } catch (Throwable ignored) {
                    }
                }
            }
            uidPkgCache.put(uid, found == null ? "" : found);
            return found;
        }

        private Drawable icon(Item it) {
            String key = cacheKey(it);
            Drawable d = iconCache.get(key);
            if (d != null) {
                return d;
            }
            Drawable out = null;
            if (it.pkg != null && !"?".equals(it.pkg)) {
                try {
                    out = getPackageManager().getApplicationIcon(it.pkg);
                } catch (Throwable ignored) {
                }
            }
            if (out == null) {
                try {
                    out = getPackageManager().getDefaultActivityIcon();
                } catch (Throwable ignored) {
                }
            }
            iconCache.put(key, out);
            return out;
        }

        @Override
        public View getView(int pos, View cv, ViewGroup parent) {
            LinearLayout row;
            ViewHolder h;
            if (cv == null) {
                row = new LinearLayout(MainActivity.this);
                row.setOrientation(LinearLayout.HORIZONTAL);
                row.setPadding(dp(4), dp(8), dp(4), dp(8));
                row.setBackgroundColor(0xFF141821);

                ImageView iv = new ImageView(MainActivity.this);
                iv.setLayoutParams(new LinearLayout.LayoutParams(dp(36), dp(36)));
                row.addView(iv);

                LinearLayout mid = new LinearLayout(MainActivity.this);
                mid.setOrientation(LinearLayout.VERTICAL);
                LinearLayout.LayoutParams mlp = new LinearLayout.LayoutParams(0, -2, 1f);
                mlp.leftMargin = dp(8);
                row.addView(mid, mlp);

                TextView t1 = new TextView(MainActivity.this);
                t1.setTextSize(13f);
                t1.setTextColor(0xFFE6EDF3);
                t1.setSingleLine(true);
                t1.setEllipsize(android.text.TextUtils.TruncateAt.END);
                mid.addView(t1);

                TextView t2 = new TextView(MainActivity.this);
                t2.setTextSize(10f);
                t2.setTextColor(0xFF6E7B8B);
                t2.setSingleLine(true);
                mid.addView(t2);

                ProgressBar pb = new ProgressBar(MainActivity.this, null, android.R.attr.progressBarStyleHorizontal);
                pb.setMax(1000);
                LinearLayout.LayoutParams plp = new LinearLayout.LayoutParams(-1, dp(4));
                plp.topMargin = dp(5);
                mid.addView(pb, plp);

                LinearLayout right = new LinearLayout(MainActivity.this);
                right.setOrientation(LinearLayout.VERTICAL);
                right.setGravity(Gravity.END);
                LinearLayout.LayoutParams rlp = new LinearLayout.LayoutParams(dp(84), -2);
                rlp.leftMargin = dp(6);
                row.addView(right, rlp);

                TextView t3 = new TextView(MainActivity.this);
                t3.setTextSize(13f);
                t3.setTypeface(Typeface.MONOSPACE, Typeface.BOLD);
                t3.setTextColor(0xFF4FA3FF);
                t3.setGravity(Gravity.END);
                right.addView(t3);

                TextView t4 = new TextView(MainActivity.this);
                t4.setTextSize(10f);
                t4.setTextColor(0xFF8A97A8);
                t4.setGravity(Gravity.END);
                right.addView(t4);

                h = new ViewHolder();
                h.icon = iv;
                h.name = t1;
                h.sub = t2;
                h.bar = pb;
                h.mah = t3;
                h.pct = t4;
                row.setTag(h);
                cv = row;
            } else {
                row = (LinearLayout) cv;
                h = (ViewHolder) row.getTag();
            }

            Item it = data.get(pos);
            h.icon.setImageDrawable(icon(it));
            h.name.setText(label(it));
            h.sub.setText("CPU " + dur(it.cpuMs) + " · 息屏 " + dur(it.cpuOffMs)
                    + " · 锁 " + dur(it.wlMs) + " · " + (it.trafficIsReal ? bytes(it.rx + it.tx) : "IO " + bytes(it.rx + it.tx)));
            h.bar.setProgress((int) Math.max(0, Math.min(1000, it.share * 1000.0d)));
            h.mah.setText(it.mah > 0.05d ? String.format(Locale.CHINA, "%.1f", it.mah) : "—");
            h.pct.setText(String.format(Locale.CHINA, "%.1f%%", it.share * 100.0d));
            return row;
        }
    }

    private static final class ViewHolder {
        ImageView icon;
        TextView name;
        TextView sub;
        ProgressBar bar;
        TextView mah;
        TextView pct;
    }
}
