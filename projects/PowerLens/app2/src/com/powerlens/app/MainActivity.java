package com.powerlens.app;

import android.app.Activity;
import android.app.AlertDialog;
import android.app.ProgressDialog;
import android.app.job.JobInfo;
import android.app.job.JobScheduler;
import android.content.ComponentName;
import android.content.Context;
import android.content.DialogInterface;
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
import android.widget.ScrollView;
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
    /**
     * 系统标识 → 显示名。
     *
     * ===== 命名规则（采集器侧约定）=====
     *   sys:kernel      → 内核（绝对确定）
     *   sys:system      → Android 系统（绝对确定）
     *   sys:adb_shell   → Shell 调试通道（绝对确定）
     *   proc:<name>     → 从 /proc/<pid>/status 反查的真实进程名
     *   sys:uid<N>      → 查不到，诚实显示 uid
     *
     * ===== 为什么不再用一张大表硬编码 =====
     *   曾按 AOSP 写了 100 多个 uid→中文名，实战连翻两次车：
     *     · uid 1041 在 AOSP 是 security，这台小米实际是 audioserver
     *       → UI 上出现莫名其妙的「安全」条目，用户以为安全服务偷电
     *     · uid 1077 在 AOSP 是 context_hub，真机是别的服务
     *   根因：OEM 会重新分配 UID，1000-1100 区间几乎认不全。
     *   现在只硬编码 3 个绝对确定的，其余全从 /proc 反查，
     *   查不到就显示 uid 数字 —— 宁可难看，也不要显示错的名字。
     */
    private static final Map<String, String> SYS_LABEL = new HashMap<>();

    static {
        SYS_LABEL.put("sys:kernel", "内核");
        SYS_LABEL.put("sys:system", "Android 系统");
        SYS_LABEL.put("sys:adb_shell", "Shell 调试通道");
    }

    /** proc:<name> / sys:uid<N> 的人类可读化 */
    private static String humanizeProc(String tag) {
        if (tag == null) return null;
        if (tag.startsWith("proc:")) {
            String name = tag.substring(5);
            String zh = procZh(name);
            return zh != null ? (zh + "（" + name + "）") : name;
        }
        if (tag.startsWith("sys:uid")) {
            return "uid " + tag.substring(7);
        }
        return null;
    }

    /** 高频系统进程的中文名（锦上添花，查不到就显示原名） */
    private static String procZh(String name) {
        if (name == null || name.isEmpty()) return null;
        if (name.startsWith("audioserver"))        return "音频服务";
        if (name.startsWith("audiohalservice"))    return "音频 HAL";
        if (name.startsWith("android.hardwar"))    return "安卓 HAL";
        if (name.startsWith("camera.provider"))    return "相机服务";
        if (name.startsWith("droid.bluetooth"))    return "蓝牙服务";
        if (name.startsWith("webview_zygote"))     return "WebView";
        if (name.equals("logd"))                   return "日志服务";
        if (name.startsWith("tftp_server"))        return "TFTP 服务";
        if (name.startsWith("prng_seeder"))        return "随机数种子";
        if (name.startsWith("loc_launcher"))       return "定位服务";
        if (name.startsWith("vendor.qti.hard"))    return "高通 HAL";
        if (name.startsWith("vendor.qti.bluetooth")) return "高通蓝牙";
        return null;
    }


    private TextView status;
    private ListView listView;
    private Spinner modeSpinner;
    private Spinner rangeSpinner;
    private Button btnCollect;
    private Button btnDiag;
    private Button btnMcast;
    private Button btnModule;

    /** 顶部三张指标卡的数值控件（电流 / 功率 / 放电） */
    private TextView metricMaBox;
    private TextView metricWBox;
    private TextView metricMahBox;

    /**
     * 完整状态文本。
     *
     * 这段原来直接塞在顶部 status 里，占 8 行，把界面上半部分全吃掉了。
     * 现在只缓存在这里，用户点顶部那行摘要才弹出来看。
     */
    private String lastStatusDetail = "";

    /** 顶部那一行摘要的缓存值（诊断/采集后用来还原，不必再 refresh） */
    private String lastShortStatus = "";

    private AppAdapter adapter;
    private volatile boolean refreshInFlight;
    private boolean hideAggregate;

    /*
     * ===== 统一配色 =====
     *
     * 旧版把色值硬编码散在十几处：0xFF0F1116 / 0xFF141821 / 0xFF9AA7B8 …
     * 改一个颜色要全文搜索，而且行/卡片/文字三层灰度用得没有层次，
     * 导致「一块深色板上一堆同亮度的字」，视觉上没有主次。
     *
     * 这里按 Material 的 elevation 思路重排：
     *   BG      最底层的窗口背景
     *   CARD    卡片/列表行  (比 BG 亮一档)
     *   CARD_HI 卡片内的次级块（进度条槽、徽章底）
     *   STROKE  1px 描边，用来在纯色块之间划边界
     *   TEXT_1  主文字（名称、数值）
     *   TEXT_2  次要文字（副标题）
     *   TEXT_3  弱化文字（单位、提示）
     *   ACCENT  强调色（进度、关键数字）
     */
    private static final int C_BG      = 0xFF0B0D12;
    private static final int C_CARD    = 0xFF161A22;
    private static final int C_CARD_HI = 0xFF1F2530;
    private static final int C_STROKE  = 0xFF262D3A;
    private static final int C_TEXT_1  = 0xFFEDF2F7;
    private static final int C_TEXT_2  = 0xFF9BA8B8;
    private static final int C_TEXT_3  = 0xFF6B7787;
    private static final int C_ACCENT  = 0xFF4FA3FF;
    private static final int C_WARN    = 0xFFFFB020;
    private final Handler ui = new Handler(Looper.getMainLooper());
    private final SimpleDateFormat fmt = new SimpleDateFormat("HH:mm:ss", Locale.CHINA);
    /*
     * ===== 缓存必须线程安全 =====
     *   uidPkgCache / labelCache / iconCache 会被两条线同时碰：
     *     - "pl-refresh" 后台线程（补 UID 时调 uidToPkg）
     *     - UI 线程（AppAdapter.getView 调 label/icon）
     *   HashMap 在并发写时可能形成链表环，get 会【死循环卡死】。
     *   这里统一换 ConcurrentHashMap。
     */
    private final Map<String, Drawable> iconCache = new java.util.concurrent.ConcurrentHashMap<>();
    private final Map<String, String> labelCache = new java.util.concurrent.ConcurrentHashMap<>();
    private final Map<Integer, String> uidPkgCache = new java.util.concurrent.ConcurrentHashMap<>();

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

    /**
     * ===== 圆角背景 =====
     * 用 GradientDrawable 而不是 drawable 资源文件：
     * 这样颜色常量全部集中在上面的 C_* 里，改配色不用改资源 XML。
     */
    private android.graphics.drawable.GradientDrawable roundBg(int fill, int stroke, float radiusDp) {
        android.graphics.drawable.GradientDrawable g = new android.graphics.drawable.GradientDrawable();
        g.setShape(android.graphics.drawable.GradientDrawable.RECTANGLE);
        g.setColor(fill);
        g.setCornerRadius(dp(radiusDp));
        if (stroke != 0) {
            g.setStroke(Math.max(1, dp(0.6f)), stroke);
        }
        return g;
    }

    /**
     * ===== 主界面 =====
     *
     * 旧布局的问题（四行挤在一起的 7 个控件）：
     *   ① 4 个 Button + 2 个 Spinner 全塞进一个水平 LinearLayout，
     *      在 1080p 上按钮字会被压成两行甚至截断；
     *   ② Button 用系统默认样式，在这台设备的 HyperOS 上是浅色胶囊，
     *      和 0xFF0F1116 的深色背景撞在一起非常突兀；
     *   ③ 顶部那块 status 是 8 行纯文本，把「8.7 mA / 2.11 W」这种
     *      最该一眼看到的数字埋在第二行；
     *   ④ 列表行用 dp(84) 固定宽度放数值，中文大字或三位数 mAh 会溢出；
     *   ⑤ 行与行之间只有 4px padding + 无分隔，密集恐惧。
     *
     * 现在分成三层卡片：
     *   ┌ 顶部栏：标题 + 刷新状态 ─────────────┐
     *   ├ 指标卡：电流(mA) / 功率(W) / 放电(mAh) ─┤  ← 三个大字，主题一眼可见
     *   ├ 控制卡：口径 / 时间窗 / 操作 ──────────┤
     *   └ 列表：圆角卡片行 ────────────────────┘
     */
    private View buildUi() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(C_BG);
        root.setFitsSystemWindows(true);

        // ---------- 顶部栏 ----------
        LinearLayout topBar = new LinearLayout(this);
        topBar.setOrientation(LinearLayout.HORIZONTAL);
        topBar.setGravity(Gravity.CENTER_VERTICAL);
        topBar.setPadding(dp(16), dp(12), dp(16), dp(8));

        LinearLayout titleBox = new LinearLayout(this);
        titleBox.setOrientation(LinearLayout.VERTICAL);
        topBar.addView(titleBox, new LinearLayout.LayoutParams(0, -2, 1f));


        /*
         * 标题。
         *
         * 主题已改成 NoActionBar（styles.xml），系统不再画 ActionBar，
         * 所以这里必须有自己的一份，否则顶部只剩一行小字，没有视觉锚点。
         */
        TextView title = new TextView(this);
        title.setText("PowerLens");
        title.setTextSize(20f);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        title.setTextColor(C_TEXT_1);
        title.setLetterSpacing(0.01f);
        titleBox.addView(title);

        status = new TextView(this);
        status.setTextSize(12f);
        status.setTextColor(C_TEXT_2);
        status.setText("启动中…");
        status.setPadding(0, dp(2), 0, 0);
        // 点击展开完整归因报告（原来这 8 行全挤在这里）
        status.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                showStatusDetail();
            }
        });
        titleBox.addView(status);

        // 三张指标卡（数值由 applyHeader() 填）
        LinearLayout metricRow = new LinearLayout(this);
        metricRow.setOrientation(LinearLayout.HORIZONTAL);
        metricRow.setPadding(dp(12), 0, dp(12), 0);

        metricMaBox = metricCard(metricRow, "电流", "mA", C_ACCENT);
        metricWBox = metricCard(metricRow, "功率", "W", C_ACCENT);
        metricMahBox = metricCard(metricRow, "放电", "mAh", C_WARN);

        root.addView(topBar);
        root.addView(metricRow, new LinearLayout.LayoutParams(-1, -2));

        // ---------- 控制卡 ----------
        LinearLayout ctrl = new LinearLayout(this);
        ctrl.setOrientation(LinearLayout.VERTICAL);
        ctrl.setBackground(roundBg(C_CARD, C_STROKE, 14f));
        LinearLayout.LayoutParams clp = new LinearLayout.LayoutParams(-1, -2);
        clp.leftMargin = dp(12);
        clp.rightMargin = dp(12);
        clp.topMargin = dp(10);
        clp.bottomMargin = dp(4);
        root.addView(ctrl, clp);

        // 第一行：两个下拉
        LinearLayout row1 = new LinearLayout(this);
        row1.setOrientation(LinearLayout.HORIZONTAL);
        row1.setGravity(Gravity.CENTER_VERTICAL);
        row1.setPadding(dp(10), dp(8), dp(10), 0);
        ctrl.addView(row1);

        // "后台" 改叫 "息屏"：这才是它真正回答的问题——
        // 熄屏期间谁在偷偷跑。原名"后台"太含糊，用户找不到入口。
        modeSpinner = makeSpinner(new String[]{"全部", "前台", "息屏", "前台服务"});
        LinearLayout.LayoutParams s1 = new LinearLayout.LayoutParams(0, dp(38), 1f);
        s1.rightMargin = dp(8);
        row1.addView(modeSpinner, s1);

        rangeSpinner = makeSpinner(new String[]{"近1时", "近6时", "近24时", "全天"});
        row1.addView(rangeSpinner, new LinearLayout.LayoutParams(0, dp(38), 1f));

        /*
         * ===== Spinner 必须自己触发刷新 =====
         *
         * 旧代码给 modeSpinner / rangeSpinner 都只塞了 adapter，
         * 【没有 setOnItemSelectedListener】。
         * 结果是：切换「近1时 / 近6时 / 后台 / 前台」完全没反应，
         * 界面还显示上一次的数据 —— 只有点「采集」或等自动刷新才变。
         *
         * 这个 bug 让窗口聚合看起来像没生效：
         * 实际已经按 1 小时切了数据，但屏幕上是旧的 6 小时结果。
         */
        modeSpinner.setOnItemSelectedListener(new AdapterView.OnItemSelectedListener() {
            @Override
            public void onItemSelected(AdapterView<?> parent, View view, int pos, long id) {
                refresh();
            }

            @Override
            public void onNothingSelected(AdapterView<?> parent) {
            }
        });
        rangeSpinner.setOnItemSelectedListener(new AdapterView.OnItemSelectedListener() {
            @Override
            public void onItemSelected(AdapterView<?> parent, View view, int pos, long id) {
                refresh();
            }

            @Override
            public void onNothingSelected(AdapterView<?> parent) {
            }
        });

        // 第二行：四个动作按钮（等宽、平铺，不再挤压）
        LinearLayout row2 = new LinearLayout(this);
        row2.setOrientation(LinearLayout.HORIZONTAL);
        row2.setPadding(dp(10), dp(6), dp(10), dp(10));
        ctrl.addView(row2);

        btnCollect = makeAction("采集", C_ACCENT);
        btnCollect.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                doCollect();
            }
        });
        // 长按切换「是否显示系统聚合 UID」
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
        row2.addView(btnCollect, actionLp(0));

        btnDiag = makeAction("诊断", C_CARD_HI);
        btnDiag.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                doDiag();
            }
        });
        row2.addView(btnDiag, actionLp(1));

        btnMcast = makeAction("组播", C_CARD_HI);
        btnMcast.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                toggleMcast();
            }
        });
        row2.addView(btnMcast, actionLp(2));

        btnModule = makeAction("模块", C_CARD_HI);
        btnModule.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                showModuleDialog();
            }
        });
        row2.addView(btnModule, actionLp(3));

        // ---------- 列表 ----------
        listView = new ListView(this);
        listView.setDivider(null);
        listView.setDividerHeight(dp(6));
        listView.setPadding(dp(12), dp(6), dp(12), dp(12));
        listView.setClipToPadding(false);
        listView.setSelector(new android.graphics.drawable.ColorDrawable(0x00000000));
        listView.setCacheColorHint(0x00000000);
        /*
         * 惯性滚动时 ListView 默认把背景设成黑色，
         * 在我们的深灰列表上会出现一条突兀的黑带。
         * 明确关掉。
         */
        listView.setBackgroundColor(C_BG);
        root.addView(listView, new LinearLayout.LayoutParams(-1, 0, 1f));

        // 恢复上次的选择
        android.content.SharedPreferences sp0 = getSharedPreferences(PREFS, 0);
        int savedMode = sp0.getInt(KEY_MODE, 0);
        if (savedMode >= 0 && savedMode <= 3) {
            modeSpinner.setSelection(savedMode);
        }
        int savedRange = sp0.getInt(KEY_RANGE, 1);
        rangeSpinner.setSelection((savedRange >= 0 && savedRange <= 3) ? savedRange : 1);

        return root;
    }

    /** 指标卡：上面小标签，下面大字数值 + 单位。返回装数值的 TextView 供刷新。 */
    /**
     * 指标卡：上面小标签，下面大字数值 + 单位。返回装数值的 TextView 供刷新。
     *
     * ===== 修两个实测出来的问题 =====
     *
     * (1) 单位被挤到第二行
     *     原来是 HORIZONTAL 里放 [数值][单位]，数值 21sp + 单位 10sp。
     *     卡片宽约 100dp，而 MONOSPACE 的 "1776.0" 就要 6 × 12dp ≈ 72dp，
     *     加单位 " mAh" 直接溢出 —— 截图里 "1776.0" 被压成两行、
     *     "mAh" 掉到右下角，很难看。
     *
     * (2) 数值字号过大
     *     21sp 在 3 位整数 + 1 位小数时刚好，4 位就爆。
     *     降到 18sp，并改成自适应：位数多时再缩。
     *
     * 现在把单位放到【标签那一行】的右边，数值独占下面整行，
     * 这样无论数字多长都不会挤到单位。
     */
    private TextView metricCard(LinearLayout parent, String label, String unit, int accent) {
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setBackground(roundBg(C_CARD, C_STROKE, 14f));
        box.setPadding(dp(11), dp(9), dp(11), dp(9));
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0, -2, 1f);
        lp.leftMargin = dp(3);
        lp.rightMargin = dp(3);
        parent.addView(box, lp);

        // 标签行：左边"电流"，右边"mA"
        LinearLayout lblRow = new LinearLayout(this);
        lblRow.setOrientation(LinearLayout.HORIZONTAL);
        lblRow.setGravity(Gravity.CENTER_VERTICAL);
        box.addView(lblRow);

        TextView lbl = new TextView(this);
        lbl.setText(label);
        lbl.setTextSize(10f);
        lbl.setTextColor(C_TEXT_3);
        lblRow.addView(lbl, new LinearLayout.LayoutParams(0, -2, 1f));

        TextView u = new TextView(this);
        u.setText(unit);
        u.setTextSize(9.5f);
        u.setTextColor(C_TEXT_3);
        u.setSingleLine(true);
        lblRow.addView(u);

        // 数值独占一行，单行显示、不换行
        TextView val = new TextView(this);
        val.setText("—");
        val.setTextSize(18f);
        val.setTypeface(Typeface.MONOSPACE, Typeface.BOLD);
        val.setTextColor(accent);
        val.setSingleLine(true);
        val.setEllipsize(android.text.TextUtils.TruncateAt.END);
        val.setPadding(0, dp(1), 0, 0);
        box.addView(val);

        return val;
    }

    /** 统一样式的 Spinner（深底、圆角、浅字）。 */
    /**
     * 统一样式的 Spinner（深底、圆角、浅字）。
     *
     * ===== 为什么需要 override getDropdownView =====
     *   android.R.layout.simple_spinner_dropdown_item 是【系统浅色】布局，
     *   里面的 TextView 文字是深色。
     *
     *   上一版只 override 了 getView()（收起时显示的那一个），
     *   漏了展开后的下拉列表 —— 结果点开下拉，选项是
     *   『深色字 + 浅色底』，在深色主题上几乎不可读，
     *   而且截图上还会留下一层半透明的残影。
     *
     *   这里同时 override getView 和 getDropDownView，两边都强制浅字深底。
     */
    private Spinner makeSpinner(final String[] items) {
        Spinner sp = new Spinner(this);
        ArrayAdapter<String> a = new ArrayAdapter<String>(this,
                android.R.layout.simple_spinner_dropdown_item, items) {
            @Override
            public View getView(int pos, View convert, ViewGroup parent) {
                TextView tv = (TextView) super.getView(pos, convert, parent);
                tv.setTextColor(C_TEXT_1);
                tv.setTextSize(13f);
                tv.setSingleLine(true);
                tv.setEllipsize(android.text.TextUtils.TruncateAt.END);
                tv.setBackgroundColor(0x00000000);
                return tv;
            }

            @Override
            public View getDropDownView(int pos, View convert, ViewGroup parent) {
                TextView tv = (TextView) super.getDropDownView(pos, convert, parent);
                tv.setTextColor(C_TEXT_1);
                tv.setTextSize(13.5f);
                tv.setPadding(dp(14), dp(12), dp(14), dp(12));
                // 展开列表用 C_CARD 底色，和主界面同色系
                tv.setBackgroundColor(C_CARD);
                return tv;
            }
        };
        sp.setAdapter(a);
        sp.setBackground(roundBg(C_CARD_HI, C_STROKE, 10f));
        sp.setPadding(dp(10), 0, dp(6), 0);
        /*
         * 下拉弹窗本体也要设深色底，否则是系统默认的浅灰，
         * 和每一项的 C_CARD 之间会露出一圈亮边。
         */
        try {
            sp.setPopupBackgroundDrawable(roundBg(C_CARD, C_STROKE, 10f));
        } catch (Throwable ignored) {
        }
        return sp;
    }

    /** 动作按钮：扁平、圆角、无默认阴影。 */
    private Button makeAction(String text, int fill) {
        Button b = new Button(this);
        b.setText(text);
        b.setAllCaps(false);
        b.setTextSize(12.5f);
        b.setTextColor(C_TEXT_1);
        b.setBackground(roundBg(fill, fill == C_ACCENT ? 0 : C_STROKE, 10f));
        b.setPadding(0, 0, 0, 0);
        b.setMinHeight(0);
        b.setMinimumHeight(0);
        b.setStateListAnimator(null);
        return b;
    }

    /** 四个按钮等宽分摊 + 间隙。 */
    private LinearLayout.LayoutParams actionLp(int index) {
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0, dp(38), 1f);
        if (index > 0) {
            lp.leftMargin = dp(6);
        }
        return lp;
    }

    /**
     * 把汇总数字填进顶部三张卡。
     * 从 buildStatus() 里拆出来：那串长文本进「诊断」弹框，
     * 主界面只留最关键的三个数。
     */
    /**
     * 按字符数自适应字号，避免 "1776.0" 这种 6 字符把卡片撑破。
     * 18sp 能舒适容纳 5 字符；每多 1 字符降 1.5sp，最低 13sp。
     */
    private void setMetric(TextView tv, String text) {
        if (tv == null) {
            return;
        }
        tv.setText(text);
        int len = text == null ? 0 : text.length();
        float sz = len <= 5 ? 18f : Math.max(13f, 18f - (len - 5) * 1.5f);
        tv.setTextSize(sz);
    }
    private void applyHeader(Storage.Summary sum) {
        double mah = sum.dischargeUah / 1000.0d;
        double disHours = sum.dischargeMs / 3600000.0d;
        double ma = 0, w = 0;
        if (mah > 0.05d && disHours > 0.01d) {
            ma = sum.ratePerHourUahActive / 1000.0d;
            w = ma * 3.85d / 1000.0d;
        }
        setMetric(metricMaBox, ma > 0.05d ? String.format(Locale.CHINA, "%.1f", ma) : "—");
        setMetric(metricWBox, w > 0.005d ? String.format(Locale.CHINA, "%.2f", w) : "—");
        setMetric(metricMahBox, mah > 0.05d ? String.format(Locale.CHINA, "%.1f", mah) : "—");
    }

    /*
     * 「聚合容器」判定 —— 修掉一个把自己最大客户藏了的 BUG。
     *
     * 旧代码：
     *     uid == 0 || uid == 1000 || uid == 1001 || uid == 9999
     *             || (uid >= 10000 && uid < 10500)   // ← "系统服务 UID 段"？
     *
     * 最后那条是错的。Android 的 AID 划分：
     *     <10000          = 系统守护/内核/媒体/音频（AID_SYSTEM=1000、AID_AUDIO=1041…）
     *     10000..19999    = user 0 安装的应用（u0aXXX → 10000+XXX）
     *     20000..29999    = 共享 GID 段，不是进程归属
     *     99000..99999    = isolated 沙箱
     *     10XXXX          = 其他用户/分身的应用（u10a341 → 1010341）
     * "10000-10500 是系统服务"根本不存在——u0a341=10341 就是 B 站。
     * 于是勾选「采集·应用」时，本机实测区间里【电量第一的那一个】被直接隐藏。
     *
     * 现在：只有真正属于"应用 UID 段"的才算具体应用，其余按聚合容器处理。
     */
    private static boolean isAppUid(int uid) {
        if (uid >= 10000 && uid < 20000) {
            return true;                       // user 0 应用
        }
        if (uid >= 100000 && uid < 1000000) {
            int sub = uid % 100000;            // u1..u9 应用
            return sub >= 10000 && sub < 20000;
        }
        if (uid >= 1000000) {
            int sub = uid % 100000;            // u10+ 分身/工作资料
            return sub >= 10000 && sub < 20000;
        }
        return false;
    }

    private static boolean isAggregate(int uid) {
        return !isAppUid(uid);
    }

    private void scheduleJob() {
        // HyperOS 上 setPersisted(true) 会被 JobSchedulerService.enforceValidJobRequest 拒
        // （需要 RECEIVE_BOOT_COMPLETED + 系统白名单），所以这里不做常驻调度。
        // 采集改由 root 常驻的 C 守护 /data/local/tmp/plc/plc-daemon.sh 负责，
        // App 只读它的输出，App 自己零后台开销。
        Log.i(TAG, "job 调度已交由 root C 守护（plc-daemon.sh），App 侧不注册 JobScheduler");
    }

    /**
     * uid → 包名（MainActivity 层，供非 UI 线程使用）。
     *
     * 优先 PackageManager（App 自身权限即可用，不需要 root），
     * 失败再读 /data/system/packages.list（需 root，通常读不到）。
     * 结果带缓存；查不到返回 null。
     */
    private String uidToPkg(int uid) {
        String cached = uidPkgCache.get(uid);
        if (cached != null) {
            return cached.isEmpty() ? null : cached;
        }
        /*
         * ===== 系统 UID 绝对不能查包名 =====
         *   getPackagesForUid(1000) 返回挂在 sharedUserId="android.uid.system"
         *   下的【几十个】包，取 pkgs[0] 是随机的 —— 于是「Android 系统」
         *   在界面上显示成 com.xiaomi.aiasst.service 这类名字。
         *   这正是用户看到的"幽灵条目"的第二个来源（第一个是 C 侧
         *   packages.list 覆盖，已修）。
         *   只有应用 UID 才有"一个 UID = 一个应用"的语义。
         */
        if (!isAppUid(uid)) {
            uidPkgCache.put(uid, "");
            return null;
        }
        String found = null;
        try {
            String[] pkgs = getPackageManager().getPackagesForUid(uid);
            if (pkgs != null && pkgs.length > 0) {
                found = pkgs[0];
            }
        } catch (Throwable ignored) {
        }
        if (found == null) {
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
            } finally {
                if (r != null) {
                    try {
                        r.close();
                    } catch (Throwable ignored) {
                    }
                }
            }
        }
        uidPkgCache.put(uid, found == null ? "" : found);
        return found;
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
                        /*
                         * 采集结果是多行的（耗时/UID 数/电量计）。
                         * 只在顶部放一句"上次采集 XX 秒前"，
                         * 详情进弹框 —— 和诊断保持一致。
                         */
                        showTextDialog("采集完成", msg);
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
                final String r = Collector.diagnose(MainActivity.this);
                ui.post(new Runnable() {
                    @Override
                    public void run() {
                        /*
                         * 诊断结果是十几行文本，绝不能再塞进顶部那个单行 status。
                         * 之前就是这么干的，一跑诊断顶部就被撑成一大块，
                         * 把指标卡和列表全挤下去。改成弹框。
                         */
                        status.setText(shortStatusFromCache());
                        showTextDialog("诊断结果", r);
                    }
                });
            }
        }, "pl-diag").start();
    }

    /*
     * ===== 模块管理对话框 =====
     *
     * 「一键安装」的真实含义：
     *   App 把 assets 里的模块 zip 释放到 cache，再调 ksud 安装。
     *   ksud 以 root 运行，所以这一步能成；但 ksud 是被 App 用 su 拉起的，
     *   首次会弹一次 KernelSU 授权框，用户点「允许」后长期记住。
     *
     *   这不是「绕过 root」，而是「root 只用这一次、只用于安装」。
     *   装完之后采集完全由模块的 service.sh 负责，App 回到零 root 状态。
     */
    /**
     * 模块面板。
     *
     * ===== 为什么必须异步（踩过的坑）=====
     *   早期实现直接在方法里同步调用：
     *       ModuleInstaller.isInstalled() / installedVersion() / detectBackend() /
     *       probeRoot() / CollectControl.status()
     *   这 5 个方法内部各跑一到两次 `su -c ...`，单次超时最长 25 秒。
     *   它们全在【主线程】执行，结果是点「模块」按钮后界面直接冻住，
     *   实测能卡 60 秒以上，用户以为 App 死了 —— 这是纯 bug，不是"慢"。
     *
     *   现在的做法：
     *     1. 立刻弹出对话框，内容先写"检测中…"，主线程一秒都不等
     *     2. 后台线程做一次合并探测（见 inspectState()，只跑 1 次 su）
     *     3. 探测完回主线程，直接 setMessage 刷新同一个对话框
     *   这样无论 su 多慢，UI 都有响应。
     */
    private void showModuleDialog() {
        final AlertDialog dlg = new AlertDialog.Builder(this)
                .setTitle("采集模块")
                .setMessage("正在检测 root / 模块状态…")
                .setPositiveButton("请稍候", null)
                .setNegativeButton("关闭", null)
                .create();
        dlg.show();

        new Thread(new Runnable() {
            @Override
            public void run() {
                final ModuleState st = inspectState();
                ui.post(new Runnable() {
                    @Override
                    public void run() {
                        bindModuleDialog(dlg, st);
                    }
                });
            }
        }, "pl-module-inspect").start();
    }

    /** 模块面板一次性探测到的全部状态。 */
    private static final class ModuleState {
        boolean installed;
        boolean enabled;
        String version;
        String backend = "none";
        String rootState = "unknown";
        CollectControl.Status collect;
    }

    /**
     * 把原先散在 5 个方法里的 su 调用合并成【一次】。
     *
     * 原来 5 次 su 串行，每次都可能等到超时，最坏 65 秒；
     * 合并后只有一次 shell 往返，最坏 15 秒，且都是在后台线程。
     */
    private ModuleState inspectState() {
        ModuleState st = new ModuleState();
        String out = Shell.run(15_000, "su", "-c",
                "test -d /data/adb/modules/powerlens && echo INST=1 || echo INST=0; "
                        + "test -f /data/adb/modules/powerlens/disable && echo EN=0 || echo EN=1; "
                        + "grep -m1 '^version=' /data/adb/modules/powerlens/module.prop 2>/dev/null; "
                        + "command -v ksud >/dev/null 2>&1 && echo BE=ksud; "
                        + "command -v magisk >/dev/null 2>&1 && echo BE=magisk; "
                        + "echo ROOT_DONE");
        if (out == null) {
            st.rootState = "denied";
            st.collect = new CollectControl.Status();
            return st;
        }
        st.rootState = out.contains("ROOT_DONE") ? "granted" : "denied";
        for (String line : out.split("\n")) {
            line = line.trim();
            if (line.startsWith("INST=")) {
                st.installed = "1".equals(line.substring(5));
            } else if (line.startsWith("EN=")) {
                st.enabled = "1".equals(line.substring(3));
            } else if (line.startsWith("version=")) {
                st.version = line.substring(8).trim();
            } else if (line.startsWith("BE=")) {
                st.backend = line.substring(3);
            }
        }
        // 采集状态复用 CollectControl，但内部已做单次合并，不再叠加多次 su
        st.collect = st.installed ? CollectControl.status() : new CollectControl.Status();
        return st;
    }

    /** 探测结果回来后，原地刷新同一个对话框（不再新开一个）。 */
    private void bindModuleDialog(final AlertDialog dlg, final ModuleState st) {
        final String rootText = "granted".equals(st.rootState) ? "已授权"
                : ("denied".equals(st.rootState) ? "未授权（需在 KernelSU 里放行）" : "不可用");

        StringBuilder sb = new StringBuilder();
        sb.append("root: ").append(rootText).append("\n");
        sb.append("安装后端: ").append(st.backend).append("\n");
        if (st.installed) {
            sb.append("状态: 已安装").append(st.version == null ? "" : " (" + st.version + ")").append("\n");
            sb.append("启用: ").append(st.enabled ? "是" : "否（已停用）").append("\n");
        } else {
            sb.append("状态: 未安装\n");
        }

        CollectControl.Status cs = st.collect;
        sb.append("\n== 采集 ==\n");
        sb.append("状态: ").append(cs.label()).append("\n");
        sb.append("模式: ").append(cs.mode);
        if (cs.scriptsReady && cs.reason != null && !cs.reason.isEmpty()) {
            sb.append("（").append(cs.reason).append("）");
        }
        sb.append("\n");
        sb.append("logd: ").append(cs.logdDisabled
                ? ("已禁用" + ("overlay".equals(cs.logdMechanism)
                        ? "（旧模块覆盖，需重启恢复）" : ""))
                : "正常").append("\n");

        sb.append("\n模块会把采集器部署到 /data/local/tmp/plc，\n");
        sb.append("并由 service.sh 在开机时自动拉起守护。\n");
        sb.append("关闭采集会同时停掉守护与自动调度，开机也不再启动。\n");
        sb.append("卸载模块会停止采集，但保留历史数据。\n");

        dlg.setMessage(sb.toString());
        dlg.setButton(DialogInterface.BUTTON_POSITIVE,
                st.installed ? "覆盖升级" : "一键安装",
                new DialogInterface.OnClickListener() {
                    @Override
                    public void onClick(DialogInterface d, int w) {
                        runModuleInstall(st.installed);
                    }
                });
        if (st.installed) {
            dlg.setButton(DialogInterface.BUTTON_NEUTRAL, "采集/开关",
                    new DialogInterface.OnClickListener() {
                        @Override
                        public void onClick(DialogInterface d, int w) {
                            showCollectSheet();
                        }
                    });
            dlg.setButton(DialogInterface.BUTTON_NEGATIVE, "卸载",
                    new DialogInterface.OnClickListener() {
                        @Override
                        public void onClick(DialogInterface d, int w) {
                            confirmModuleUninstall();
                        }
                    });
        } else {
            // 未安装时把"请稍候"换回"取消"，否则加载完了按钮还写着请稍候
            dlg.setButton(DialogInterface.BUTTON_NEGATIVE, "取消",
                    (DialogInterface.OnClickListener) null);
            dlg.setButton(DialogInterface.BUTTON_NEUTRAL, "采集/开关", (DialogInterface.OnClickListener) null);
        }
    }

    /** 执行安装/升级，全程在后台线程，结果回主线程弹框。 */
    private void runModuleInstall(final boolean upgrade) {
        final ProgressDialog pd = new ProgressDialog(this);
        pd.setMessage(upgrade ? "正在升级模块…" : "正在安装模块…");
        pd.setCancelable(false);
        pd.show();

        new Thread(new Runnable() {
            @Override
            public void run() {
                final ModuleInstaller.Result r = ModuleInstaller.install(MainActivity.this, upgrade);
                runOnUiThread(new Runnable() {
                    @Override
                    public void run() {
                        try { pd.dismiss(); } catch (Throwable ignored) {}
                        String msg = r.message;
                        if (!r.ok && r.rawOutput != null && !r.rawOutput.isEmpty()) {
                            String tail = r.rawOutput;
                            if (tail.length() > 600) {
                                tail = tail.substring(tail.length() - 600);
                            }
                            msg = msg + "\n\n--- 原始输出 ---\n" + tail;
                        }
                        new AlertDialog.Builder(MainActivity.this)
                                .setTitle(r.ok ? "完成" : "失败")
                                .setMessage(msg)
                                .setPositiveButton("知道了", null)
                                .show();
                        refresh();
                    }
                });
            }
        }, "pl-module-install").start();
    }

    /**
     * 采集开关面板。
     *
     * ===== 为什么用列表而不是一排按钮 =====
     * AlertDialog 最多三个按钮，而这里要放「开启 / 自动 / 关闭 / logd / 停用模块」
     * 五个动作。用 setItems 列表既放得下，也能给每一项写清楚后果：
     * 「关闭」必须让用户看到"开机也不再启动"这句话，否则他会以为只是暂停。
     */
    private void showCollectSheet() {
        final CollectControl.Status cs = CollectControl.status();
        final boolean moduleEnabled = ModuleInstaller.isEnabled();

        final String[] items = new String[]{
                "开启采集（常驻）",
                "自动（熄屏 10 分钟后暂停，亮屏恢复）",
                "关闭采集（零后台，开机不启动）",
                (cs.logdDisabled ? "恢复 logd 日志服务" : "禁用 logd 日志服务"),
                (moduleEnabled ? "停用整个模块（重启生效）" : "启用整个模块（重启生效）")
        };

        new AlertDialog.Builder(this)
                .setTitle("采集开关")
                .setItems(items, new DialogInterface.OnClickListener() {
                    @Override
                    public void onClick(DialogInterface d, int which) {
                        switch (which) {
                            case 0:
                                applyCollectMode("on");
                                break;
                            case 1:
                                applyCollectMode("auto");
                                break;
                            case 2:
                                confirmCollectOff();
                                break;
                            case 3:
                                applyLogdToggle(!cs.logdDisabled);
                                break;
                            default:
                                runModuleToggle(!moduleEnabled);
                                break;
                        }
                    }
                })
                .setNegativeButton("取消", null)
                .show();
    }

    /** 「关闭采集」需要二次确认：这是唯一会让数据停止增长的操作。 */
    private void confirmCollectOff() {
        new AlertDialog.Builder(this)
                .setTitle("关闭采集？")
                .setMessage("会立即停止守护进程与自动调度。\n"
                        + "重启手机也不会自己启动，直到你在这里重新开启。\n\n"
                        + "历史数据保留，界面仍可查看已有记录。")
                .setPositiveButton("关闭采集", new DialogInterface.OnClickListener() {
                    @Override
                    public void onClick(DialogInterface d, int w) {
                        applyCollectMode("off");
                    }
                })
                .setNegativeButton("取消", null)
                .show();
    }

    private void applyCollectMode(final String mode) {
        status.setText("正在切换采集模式…");
        new Thread(new Runnable() {
            @Override
            public void run() {
                final String out = CollectControl.setMode(mode);
                ui.post(new Runnable() {
                    @Override
                    public void run() {
                        String tip = "on".equals(mode) ? "采集已开启"
                                : ("off".equals(mode) ? "采集已关闭" : "已切换到自动模式");
                        android.widget.Toast.makeText(MainActivity.this,
                                tip + (out == null || out.isEmpty() ? "" : "\n" + out),
                                android.widget.Toast.LENGTH_LONG).show();
                        refresh();
                    }
                });
            }
        }, "pl-collect-mode").start();
    }

    private void applyLogdToggle(final boolean disable) {
        status.setText(disable ? "正在禁用 logd…" : "正在恢复 logd…");
        new Thread(new Runnable() {
            @Override
            public void run() {
                final String out = CollectControl.setLogdDisabled(disable);
                ui.post(new Runnable() {
                    @Override
                    public void run() {
                        android.widget.Toast.makeText(MainActivity.this,
                                (disable ? "logd 已禁用" : "logd 已恢复")
                                        + (out == null || out.isEmpty() ? "" : "\n" + out),
                                android.widget.Toast.LENGTH_LONG).show();
                        refresh();
                    }
                });
            }
        }, "pl-logd-toggle").start();
    }

    private void runModuleToggle(final boolean enable) {
        final ProgressDialog pd = new ProgressDialog(this);
        pd.setMessage(enable ? "正在启用…" : "正在停用…");
        pd.setCancelable(false);
        pd.show();
        new Thread(new Runnable() {
            @Override
            public void run() {
                final ModuleInstaller.Result r = ModuleInstaller.setEnabled(enable);
                runOnUiThread(new Runnable() {
                    @Override
                    public void run() {
                        try { pd.dismiss(); } catch (Throwable ignored) {}
                        new AlertDialog.Builder(MainActivity.this)
                                .setTitle(r.ok ? "完成" : "失败")
                                .setMessage(r.message)
                                .setPositiveButton("知道了", null)
                                .show();
                    }
                });
            }
        }, "pl-module-toggle").start();
    }

    private void confirmModuleUninstall() {
        new AlertDialog.Builder(this)
                .setTitle("卸载采集模块")
                .setMessage("将停止采集并移除采集器。\n\n历史数据会保留在 /data/local/tmp/powerlens，不会被删除。\n\n确定卸载？")
                .setPositiveButton("确定卸载", new DialogInterface.OnClickListener() {
                    @Override
                    public void onClick(DialogInterface d, int w) {
                        final ProgressDialog pd = new ProgressDialog(MainActivity.this);
                        pd.setMessage("正在卸载…");
                        pd.setCancelable(false);
                        pd.show();
                        new Thread(new Runnable() {
                            @Override
                            public void run() {
                                final ModuleInstaller.Result r = ModuleInstaller.uninstall();
                                runOnUiThread(new Runnable() {
                                    @Override
                                    public void run() {
                                        try { pd.dismiss(); } catch (Throwable ignored) {}
                                        new AlertDialog.Builder(MainActivity.this)
                                                .setTitle(r.ok ? "完成" : "失败")
                                                .setMessage(r.message)
                                                .setPositiveButton("知道了", null)
                                                .show();
                                    }
                                });
                            }
                        }, "pl-module-uninstall").start();
                    }
                })
                .setNegativeButton("取消", null)
                .show();
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
                        // 组播开关的结果只有一两句，用 Toast 不占布局
                        android.widget.Toast.makeText(MainActivity.this, r,
                                android.widget.Toast.LENGTH_LONG).show();
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
                /*
                 * 传查询窗口进去 —— 这是"红果被算成后台"的最终修法。
                 *
                 * 之前 Dims.load(ctx) 不带窗口，只能读 dims.tsv 的最后一帧；
                 * 现在它能按 [from, to] 从 dims-history 精确切片，
                 * 选「近1时」就真的只统计最近 1 小时。
                 */
                final Dims.Snapshot dims = Dims.load(MainActivity.this, from, to);
                final long dimsAge = Dims.ageSeconds(MainActivity.this);

                final int mode = modeSpinner == null ? 0 : modeSpinner.getSelectedItemPosition();

                // 先把区间 CPU 增量汇总，用于按维度比例分配
                long totDcpu = 0;
                for (Storage.Row r : rows) {
                    totDcpu += r.dCpuMs();
                }

                /*
                 * ===== 电量归因用 dumpsys 的模型 mAh，不再用猜的权重 =====
                 *
                 *   pldims v3 起，每个 UID 都带 dumpsys 自己算好的分项电量
                 *   （screen/cpu/radio/wifi/audio/…）。
                 *   旧版把总放电按 "CPU秒 + 唤醒锁×0.6 + 流量MB×0.44" 分摊，
                 *   结果 sys:logd 跑 1 分钟 CPU 却分到 463.5 mAh（38.4%）——
                 *   因为屏幕 560 mAh + 基带 1005 mAh 这些平台级功耗被摊到了
                 *   少数几个有 CPU 时间的进程头上。
                 *
                 *   现在：
                 *     mahShare(uid) = 该 UID 模型 mAh / 所有 UID 模型 mAh 之和
                 *     显示 mAh      = 实测放电 × mahShare
                 *   总量仍锚定真实电池计数器，份额交给系统自己的功耗模型。
                 */
                /*
                 * ===== 优先用【累计】mAh，而不是单帧 =====
                 *
                 * 单帧 mAh 只代表最后 5 分钟。实测最后一帧 78 个 UID
                 * 的模型电量加起来只有 14.657 mAh，而同一窗口实际放电
                 * 1754 mAh —— 模型只覆盖 0.8%。
                 *
                 * 分母这么小，任何一个 UID 的份额都会被放大上百倍：
                 *   PowerLens 明细写 "屏幕 7.00 + CPU 1.00 = 8.00 mAh"，
                 *   界面却显示 1136.4 mAh (64.8%) —— 差了 142 倍。
                 *
                 * dims.total.tsv 的累计值（141 UID / 2980 mAh）和实测
                 * 放电同量级，份额才有意义。
                 */
                final boolean useMahTotal = dims.hasMahTotal();
                final boolean useMah = useMahTotal || dims.hasMah();
                double totModelMah = useMahTotal ? dims.totalMahTotal()
                        : (dims.hasMah() ? dims.totalMah() : 0d);
                final java.util.Set<Integer> seenUid = new java.util.HashSet<>();
                /*
                 * uid → 采集器已解析好的标签（proc:audioserver / 真实包名 / sys:xxx）。
                 *
                 * 补"没有 CPU 却有电量"的 UID 时要用它：
                 * dims.tsv 里【没有名字列】，如果只靠 uidToPkg 现查，
                 * 系统服务一律退化成 "uid1041" —— 用户看不出那是什么。
                 * 而 plcollect 已经用 /proc/<pid>/status 反查过真实进程名，
                 * 直接复用那份结果最准，也省一次 binder 调用。
                 */
                final Map<Integer, String> pkgByUid = new HashMap<>();
                for (Storage.Row r : rows) {
                    String p = r.pkg;
                    if (p == null || p.isEmpty() || "?".equals(p) || p.startsWith("(uid")) {
                        continue;
                    }
                    // 同一 uid 多个标签时，保留【第一个能看懂的】
                    if (pkgByUid.containsKey(r.uid)) {
                        continue;
                    }
                    // 旧数据可能把共享 UID 的第三方包名写到系统 UID 上
                    // （实测 uid 1000 被标成 com.xiaomi.aiasst.service），挡掉
                    if (!isAppUid(r.uid) && p.indexOf('.') > 0
                            && !p.startsWith("sys:") && !p.startsWith("proc:")) {
                        continue;
                    }
                    pkgByUid.put(r.uid, p);
                }

                final List<Item> items = new ArrayList<>();
                double totalW = 0;
                for (Storage.Row r : rows) {
                    long dcpu = r.dCpuMs();
                    double w;
                    boolean proxyMode = false;
                    long dimMsLocal = 0;   // 当前口径下的时长（ms），仅 proxy 模式用

                    if (mode == 1 || mode == 2 || mode == 3) {
                        // 前台 / 后台 / 前台服务：dims 增量时长 × CPU 活跃度
                        //
                        // ===== 为什么不能只用 dims 时长 =====
                        //   实测 dumpsys 对「有任务栈的进程」几乎都报全程前台：
                        //     uid 1027/10183/10146/6120/10091/10124 的 Δfg 全都 = 121908ms
                        //     而 interval = 122131ms —— 即 99.8% 的时间都在前台。
                        //   纯按 fg 排序会让几十个 UID 并列第一，没有区分度。
                        //
                        //   CPU 又是另一面：CPU 高说明真在干活，但没有前台/后台语义。
                        //   两者相乘可以同时体现「在前台」和「真在跑」：
                        //     weight = √(fg秒 × CPU秒)
                        //   开平方是为了避免任一项极小时把权重压没。
                        /*
                         * ===== 关键：用【累计】而不是单帧 =====
                         *
                         * 原来是 dims.secondsOf(uid, mode)，只读 dims.tsv 的
                         * 最后一帧（5 分钟增量），却拿它去代表整个查询窗口
                         * （1/6/24 小时）。
                         *
                         * 实测后果：红果短剧 6 小时里前台 2073s、后台 8319s，
                         * 但最后那一帧恰好 fg=0 / bg=301s，
                         * 于是整段被算成"纯后台"，切到「后台」口径就能看到它
                         * 排在前面，而「前台」口径里完全消失。
                         *
                         * secondsTotal() 读 dims.total.tsv（pldims 逐 UID 累加），
                         * 拿到的才是"这段时间里一共前台多久"。
                         * 老 pldims 没有该文件时自动退回单帧，不会崩。
                         */
                        double sec = dims.secondsTotal(r.uid, mode);
                        if (sec <= 0.001d) {
                            continue;   // 该 UID 在这个维度上是 0 → 不显示
                        }
                        dimMsLocal = (long) (sec * 1000L);
                        double cpuSec = dcpu / 1000.0d;
                        double wDim;
                        if (cpuSec > 0.01d) {
                            wDim = Math.sqrt(sec * cpuSec);
                        } else {
                            /*
                             * ===== 兜底：没有 CPU 增量 =====
                             *
                             * 注意这【不是耗电】，只是"该维度上停留的时长"。
                             *
                             *   com.xiaomi.xmsf（小米服务框架）就是典型：
                             *   - dumpsys 报它 14.3 mAh、CPU 3 分钟
                             *   - 但前台时长 7h38m（占 6h 窗口的 97%）
                             *   - 因为它被几十个应用绑定，谁在前台它就算在前台
                             *   - 走这个兜底后 21298s × 0.05 = 1065 权重 = 25.9%
                             *   - 再乘放电 1566 mAh → 显示 465 mAh
                             *
                             *   真实 14.3 mAh，放大 32 倍。
                             *
                             * 所以这里刻意压得更狠：
                             *   0.05 → 0.005（降一个数量级）
                             * 让"纯时长"型系统服务不至于霸占前排，
                             * 但也不至于完全消失（它确实长期占用前台状态）。
                             */
                            wDim = sec * 0.005d;
                        }
                        // 真网络流量补充（procfs 源的 IO 不计）
                        double trafficPart = r.hasRealTraffic()
                                ? (r.dRx() + r.dTx()) / 1048576.0d * 0.44d
                                : 0;
                        w = wDim + trafficPart;
                        /*
                         * 前台/后台口径的权重是"时长 × CPU 活跃度"，
                         * 摊出来的数值【不是真实电量】，只是排序依据。
                         * 渲染时要用不同单位或标注，不能写成 mAh。
                         */
                        proxyMode = true;
                    } else if (useMah) {
                        // 全部口径 + 有模型电量：直接按 mAh 排
                        double m = useMahTotal ? dims.mahTotalOf(r.uid)
                                : dims.mahOf(r.uid);
                        if (m <= 0.0005d) {
                            /*
                             * 模型没算出这个 UID 的电（常见于纯 IO / 内核活动 /
                             * dumpsys 那一行压根没有 cpu= 字段）。
                             *
                             * 这里的兜底必须保持【量纲一致】。
                             * 旧写法 w = r.wAll() * 0.02 把"秒"和"mAh"混进同一个
                             * totalW 里做除法，share 直接失真 —— 而 totalW 本身
                             * 又用来算份额，等于用两种单位互相除。
                             *
                             * 正确做法：用全机【增量 CPU mAh】按 CPU 时间比例分给
                             * 它，得到的仍是 mAh，可以和别的 UID 相加比较。
                             */
                            double cpuMahTot = dims.totMah[Dims.M_CPU] / 1000.0d;
                            m = (totDcpu > 0 && cpuMahTot > 0)
                                    ? cpuMahTot * ((double) dcpu / (double) totDcpu)
                                    : 0d;
                            w = m;
                        } else {
                            w = m;
                        }
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
                    it.mahIsProxy = proxyMode;
                    it.dimMs = dimMsLocal;
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
                    if (useMah && mode == 0) {
                        it.modelMah = useMahTotal ? dims.mahTotalOf(r.uid)
                                : dims.mahOf(r.uid);
                        it.mahParts = dims.mahPartsOf(r.uid);
                    }
                    seenUid.add(r.uid);
                    totalW += w;
                    items.add(it);
                }

                /*
                 * ===== 补上 procfs 区间里没有、但 dumpsys 有电量的 UID =====
                 *
                 *   plcollect 走 /proc/uid_cputime，只统计【有 CPU 时间】的 UID。
                 *   而 dumpsys 会给出纯硬件型消耗（屏幕、基带待机、传感器），
                 *   这些 UID 可能整段区间 CPU = 0，于是从列表里彻底消失 ——
                 *   用户就会看到"加起来远小于实测放电"，或把平台功耗错算到 App 头上。
                 *
                 *   这里按 dims 的 UID 补齐，CPU/IO 留空，只带模型电量。
                 */
                if (useMah && mode == 0) {
                    java.util.Map<Integer, Dims.One> totalMap =
                            useMahTotal ? dims.byUidTotal : dims.byUid;
                    for (Integer uidObj : totalMap.keySet()) {
                        int uid = uidObj;
                        if (seenUid.contains(uid)) continue;
                        double m = useMahTotal ? dims.mahTotalOf(uid)
                                : dims.mahOf(uid);
                        if (m <= 0.0005d) continue;
                        if (hideAggregate && isAggregate(uid)) continue;
                        Item it = new Item();
                        it.uid = uid;
                        /*
                         * dims.tsv 没有名字列。优先复用 plcollect 已解析的标签
                         * （proc:audioserver / 真实包名 / sys:xxx），
                         * 查不到再退 uidToPkg（只对应用 UID 有效）。
                         * 直接写 "uid1041" 会让用户看不出那是什么服务。
                         */
                        String lbl = pkgByUid.get(uid);
                        if (lbl == null) {
                            lbl = uidToPkg(uid);
                        }
                        if (lbl == null || lbl.isEmpty()) {
                            lbl = "uid" + uid;
                        }
                        it.pkg = lbl;
                        it.weight = m;
                        it.mahIsProxy = false;
                        it.modelMah = m;
                        it.mahParts = dims.mahPartsOf(uid);
                        totalW += m;
                        items.add(it);
                    }
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
                            // 三张指标卡放关键数字，状态栏只留一行摘要
                            applyHeader(sum);
                            lastStatusDetail = buildStatus(store, sum, items.size(), mode, from, to, dims, dimsAge,
                                    CpuFreq.latest(store, from, to));
                            lastShortStatus = shortStatus(sum, items.size(), dims, dimsAge);
                            status.setText(lastShortStatus);
                        } finally {
                            refreshInFlight = false;
                        }
                    }
                });
            }
        }, "pl-refresh").start();
    }

    /**
     * 顶部只显示一行：区间 + 进程数 + 采样健康度。
     *
     * 完整归因（放电分解、维度状态、排除计数）在 showStatusDetail() 里，
     * 主界面不再被 8 行文字淹没。
     */
    private String shortStatus(Storage.Summary sum, int n, Dims.Snapshot dims, long dimsAge) {
        StringBuilder sb = new StringBuilder();
        sb.append(n).append(" 个进程");
        double hours = (sum.lastTs - sum.firstTs) / 3600000.0d;
        if (hours > 0.01d) {
            sb.append(" · 采样 ").append(String.format(Locale.CHINA, "%.1f", hours)).append("h");
        }
        if (dimsAge < 0) {
            sb.append(" · 无维度数据");
        } else if (dimsAge > 3600) {
            sb.append(" · 维度 ").append(dimsAge / 60).append("min 未更新");
        }
        return sb.toString();
    }

    /**
     * 通用「长文本」弹框：可滚动、可选中复制。
     *
     * 凡是超过一行的文本（诊断、采集结果、归因详情）都走这里，
     * 保证顶部 status 永远只有一行。
     */
    private void showTextDialog(String title, String body) {
        ScrollView sv = new ScrollView(this);
        TextView tv = new TextView(this);
        tv.setText(body == null ? "" : body);
        tv.setTextSize(12f);
        tv.setTextColor(0xFFDDDDDD);
        tv.setPadding(dp(16), dp(12), dp(16), dp(12));
        tv.setTextIsSelectable(true);
        sv.addView(tv);
        new AlertDialog.Builder(this)
                .setTitle(title)
                .setView(sv)
                .setPositiveButton("关闭", null)
                .show();
    }

    /** 顶部单行摘要：缓存里那句话，避免为了改状态又跑一次 refresh。 */
    private String shortStatusFromCache() {
        return lastShortStatus;
    }

    /** 点顶部状态行 → 弹出完整归因报告。 */
    private void showStatusDetail() {
        if (lastStatusDetail == null || lastStatusDetail.isEmpty()) {
            return;
        }
        ScrollView sv = new ScrollView(this);
        TextView tv = new TextView(this);
        tv.setText(lastStatusDetail);
        tv.setTextSize(12f);
        tv.setTextColor(0xFFDDDDDD);
        tv.setPadding(dp(16), dp(12), dp(16), dp(12));
        tv.setTextIsSelectable(true);
        sv.addView(tv);
        new AlertDialog.Builder(this)
                .setTitle("归因详情")
                .setView(sv)
                .setPositiveButton("关闭", null)
                .show();
    }
    private String buildStatus(File store, Storage.Summary sum, int n, int mode, long from, long to,
                                Dims.Snapshot dims, long dimsAge, CpuFreq.Snapshot cpu) {
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
        // 纯放电时长：区间内没充电的部分。电流必须用它做分母，
        // 否则充电时段会稀释结果（实测偏差可达 30%+）。
        double disHours = sum.dischargeMs / 3600000.0d;
        if (mah > 0.05d) {
            sb.append("\n实测放电 ").append(String.format(Locale.CHINA, "%.1f", mah)).append(" mAh");
            if (hours > 0.01d) {
                // 电流用「纯放电时长」，比例用「全跨度」
                double ma = (disHours > 0.01d)
                        ? sum.ratePerHourUahActive / 1000.0d
                        : sum.ratePerHourUah / 1000.0d;
                double pctPerHour = (mah / 6100.0d) * 100.0d / hours;
                sb.append("  ·  ").append(String.format(Locale.CHINA, "%.1f", ma)).append(" mA");
                sb.append("  ·  ").append(String.format(Locale.CHINA, "%.2f", pctPerHour)).append(" %/h");
                sb.append("  ·  ").append(String.format(Locale.CHINA, "%.2f", ma * 3.85d / 1000.0d)).append(" W");
                sb.append("\n有效采样跨度 ").append(String.format(Locale.CHINA, "%.1f", hours))
                        .append(" 小时（").append(sum.points).append(" 个电池点）");
                if (sum.chargeMs > 60000L) {
                    sb.append("　其中充电 ").append(String.format(Locale.CHINA, "%.1f", sum.chargeMs / 3600000.0d))
                            .append(" 小时（已从电流分母剔除）");
                }
            }
        } else if (sum.points < 2) {
            sb.append("\n采样点不足（").append(sum.points).append(" 个），至少 2 次才能算差值");
        } else {
            sb.append("\n本区间无有效放电（充电中或电量计未变化）");
        }

        /*
         * CPU 频率 / 限频快照。
         *
         * 只展示「采样瞬间的当前频率 + 系统当时的上下限」，
         * 不做"最优频点"或"被恶意限频"的断言：
         *   - scaling_cur_freq 是瞬时值，两次采样之间怎么变这个文件里没有；
         *   - 频率 ≠ 该频率下干了多少活。真正的能效需要固定频率负载，
         *     那属于 CPU Efficiency Lab 的矩阵实验，不是这里的数据能替代的。
         */
        String cpuLine = CpuFreq.describe(cpu);
        if (cpuLine != null) {
            sb.append("\n").append(cpuLine);
            if (cpu.hasRange()) {
                sb.append("\n（限频范围为系统当时值，PowerLens 只读不改）");
            }
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
        } else if (dims.hasMah()) {
            /*
             * 全部口径优先用 dumpsys 的模型电量。
             * 同时把全机分项摊开，让用户一眼看到"平台级"消耗占多少：
             *   屏幕 / 基带 / CPU 是三大头，加起来通常 > 90%
             * 不做这件事的话，用户只会看到"某 App 38%"，
             * 然后去杀那个 App —— 而真正该调的是屏幕和基带。
             */
            modeTip = "全部口径：按 dumpsys 系统功耗模型（每 UID 真实 mAh）分摊";
        } else {
            modeTip = "全部口径：总 CPU + 息屏 CPU + 唤醒锁 + 流量（旧权重，无模型 mAh）";
        }
        sb.append("\n").append(modeTip);

        if (mode == 0 && dims.hasMah() && dims.totalMah() > 1.0d) {
            double[] parts = new double[Dims.M_N];
            for (int i = 0; i < Dims.M_N; i++) {
                parts[i] = dims.totMah[i] / 1000.0d;
            }
            // 只列非零且较大的分项，按大小排序
            Integer[] idx = new Integer[Dims.M_N];
            for (int i = 0; i < Dims.M_N; i++) idx[i] = i;
            final double[] fp = parts;
            java.util.Arrays.sort(idx, new Comparator<Integer>() {
                @Override
                public int compare(Integer a, Integer b) {
                    return Double.compare(fp[b], fp[a]);
                }
            });
            double tot = dims.totalMah();
            sb.append("\n区间模型电量 ").append(String.format(Locale.CHINA, "%.1f", tot)).append(" mAh：");
            int shown = 0;
            for (int k = 0; k < Dims.M_N && shown < 4; k++) {
                int i = idx[k];
                if (parts[i] < tot * 0.02d) continue;
                sb.append("  ").append(Dims.MAH_NAME[i]).append(' ')
                        .append(String.format(Locale.CHINA, "%.0f", parts[i]))
                        .append("(").append(String.format(Locale.CHINA, "%.0f", parts[i] * 100.0d / tot)).append("%)");
                shown++;
            }
        }

        // 维度数据状态（前台/后台/前台服务模式依赖它；全部口径的 mAh 也依赖它）
        if (mode >= 1 && mode <= 3 || dims.hasMah()) {
            if (dimsAge < 0) {
                if (mode >= 1 && mode <= 3) {
                    sb.append("\n[!] 没有维度数据，已退化到「全部」口径");
                    sb.append("\n    修复：su -c \"/data/local/tmp/plc/pldims\"");
                }
            } else if (dimsAge > 3600) {
                sb.append("\n[!] 维度数据已 ").append(dimsAge / 60).append(" 分钟未更新（pldims 守护可能停了）");
            } else {
                sb.append("\n维度数据 ").append(dimsAge < 60 ? dimsAge + " 秒前" : dimsAge / 60 + " 分钟前")
                        .append("（").append(dims.count).append(" 个 UID");
                if (dims.intervalMs > 0) {
                    sb.append("，采样间隔 ").append(Math.round(dims.intervalMs / 1000.0d)).append(" 秒");
                }
                sb.append('）');
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
        if (dims.hasMah() && mode == 0) {
            sb.append("\n每行 mAh = 实测放电 × 该进程模型电量占比，合计 = 实测放电（已剔除充电段）");
        } else {
            sb.append("\n每行 mAh 按权重分摊，合计 = 实测放电（已剔除充电段）");
        }
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
        /**
         * 这一行的数值是什么口径：
         *
         *   false = dumpsys 模型电量摊份额（可信，单位 mAh）
         *   true  = 前台/后台时长 × 权重摊放电（仅供排序，不是真实电量）
         *
         * 必须区分，否则会把 com.xiaomi.xmsf 这种"被绑定的系统服务"
         * 显示成 465 mAh —— 它真实只有 14.3 mAh，放大 32 倍。
         */
        boolean mahIsProxy;
        /** 当前口径对应的时长（ms）：前台=fgMs 后台=bgMs 前台服务=fgsMs */
        long dimMs;
        /** dumpsys 自己算出的本 UID 模型电量（mAh），0 = 无 */
        double modelMah;
        /** 本 UID 的分项模型电量（mAh），下标见 Dims.M_* */
        double[] mahParts;
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

    /**
     * 单行"模型电量"摘要：只列非零分项，按大小取前 4，单位 mAh。
     *
     * 没有它的话，右侧的 mAh 与左侧的 CPU/锁/流量之间看不到推导关系，
     * 数字仍是黑箱；有它就能当场验算，也能看出"这进程的电其实花在基带上"。
     */
    private static String mahPartsBrief(Item it) {
        if (it.mahParts == null || it.modelMah <= 0.0005d) {
            return null;
        }
        Integer[] idx = new Integer[Dims.M_N];
        for (int i = 0; i < Dims.M_N; i++) {
            idx[i] = i;
        }
        final double[] parts = it.mahParts;
        java.util.Arrays.sort(idx, new Comparator<Integer>() {
            @Override
            public int compare(Integer a, Integer b) {
                return Double.compare(parts[b], parts[a]);
            }
        });
        StringBuilder sb = new StringBuilder();
        int shown = 0;
        double shownSum = 0;
        int restCount = 0;
        double restSum = 0;
        for (int k = 0; k < Dims.M_N; k++) {
            int i = idx[k];
            double v = parts[i];
            if (v < 0.05d) {
                continue;
            }
            /*
             * ===== 必须标出被省略的部分 =====
             *
             * 原来只列前 4 项就写 "= 总额"，但总额是全部 12 项之和。
             * 实测微信：
             *   CPU 16.0 移动网络 4.00 屏幕 2.50 视频 0.50 = 644 mAh
             * 前 4 项加起来 23 mAh，却被标成 644 —— 差 28 倍，
             * 用户会以为这行算错了。
             *
             * 真实情况是剩下 8 项合计 621 mAh 被隐藏了。
             * 现在显示 "…+8项 621"，让等式成立。
             */
            if (shown < 4) {
                if (sb.length() > 0) {
                    sb.append(' ');
                }
                sb.append(Dims.MAH_NAME[i]).append(' ').append(fmtMah(v));
                shown++;
                shownSum += v;
            } else {
                restCount++;
                restSum += v;
            }
        }
        if (sb.length() <= 0) {
            return null;
        }
        if (restCount > 0) {
            sb.append(" +").append(restCount).append("项 ").append(fmtMah(restSum));
        }
        sb.append(" = ").append(fmtMah(it.modelMah));
        /*
         * ===== 模型值 ≠ 右侧显示值，必须标出折算 =====
         *
         * 右侧数字是「实测放电 × 本进程模型电量占比」，
         * 而这里的 = 441 是 dumpsys 模型电量本身（分子）。
         *
         * 实测红果：模型 441，右侧 488.8 —— 因为
         *   占比 = 441 / Σ模型 1505 = 29.3%
         *   显示 = 实测放电 1669 × 29.3% = 489
         * 两个数都对，但只写 "= 441" 会让人以为右侧算错了。
         *
         * 所以把折算后的值也标出来，改成 "= 441 · 折算 489"。
         */
        if (it.mah > 0.05d && Math.abs(it.mah - it.modelMah) > 0.5d) {
            sb.append(" · 折算 ").append(fmtMah(it.mah));
        }
        sb.append(" mAh");
        return sb.toString();
    }

    private static String fmtMah(double v) {
        if (v >= 100d) {
            return String.format(Locale.CHINA, "%.0f", v);
        }
        if (v >= 10d) {
            return String.format(Locale.CHINA, "%.1f", v);
        }
        return String.format(Locale.CHINA, "%.2f", v);
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

            /*
             * ===== 历史脏数据防御：系统 UID 上的第三方包名一律不信 =====
             *
             *   实测本机旧快照里出现过：
             *     uid 1000  pkg=com.xiaomi.aiasst.service
             *   来源是早期采集器直接拿 /data/system/packages.list 覆盖 UID 名。
             *   packages.list 里 uid 1000 挂着几十个 sharedUserId 的包，
             *   "取最后一个/第一个"都是随机的 —— 「Android 系统」于是消失，
             *   换成一个莫名其妙的应用名，用户看到的就是幽灵条目。
             *
             *   现在 C 侧已经加了 uid<10000 不覆盖的保护，但【旧数据还在文件里】：
             *   历史区间一查就会把这些错标签读出来。
             *   所以在显示层再挡一次：系统 UID（<10000）若带的是包名样式的标签，
             *   丢弃它，走 sys_uid_name/proc 反查，最后诚实显示 uid N。
             */
            if (hasPkg && !isAppUid(it.uid)
                    && it.pkg.indexOf('.') > 0
                    && !it.pkg.startsWith("sys:") && !it.pkg.startsWith("proc:")) {
                hasPkg = false;
                it.pkg = "?";
            }

            /*
             * 优先级：
             *   0) sys:kernel / sys:system / sys:adb_shell → 固定中文名
             *   1) proc:<name>  → 真实进程名（含可选中文注解）
             *   2) sys:uid<N>   → 显示 "uid N"
             *   3) 真实包名     → PackageManager 取应用名
             *   4) 都没有       → "(uid N)"
             *
             * 注意：不要跳过 proc:/sys:uid 走 PackageManager ——
             * 它们本来就不是包名，查了也查不到，会白跑一次 binder 调用。
             */
            if (hasPkg) {
                if (it.pkg.startsWith("sys:") || it.pkg.startsWith("proc:")) {
                    String zh = SYS_LABEL.get(it.pkg);
                    if (zh == null) {
                        zh = humanizeProc(it.pkg);
                    }
                    if (zh == null) {
                        zh = it.pkg;
                    }
                    labelCache.put(key, zh);
                    return zh;
                }
            }

            // 3) 真实包名 → 应用名
            if (hasPkg) {
                if (it.pkg.indexOf('.') > 0) {
                    try {
                        android.content.pm.PackageManager pm = getPackageManager();
                        s = pm.getApplicationLabel(pm.getApplicationInfo(it.pkg, 0)).toString();
                    } catch (Throwable ignored) {
                    }
                }
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

        /** 从 uid 反查包名：统一走 MainActivity.uidToPkg（PackageManager 优先） */
        private String pkgNameFromUid(int uid) {
            return uidToPkg(uid);
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
                /*
                 * ===== 列表行（重做）=====
                 *
                 * 旧版问题：
                 *   ① 行背景 0xFF141821 和窗口 0xFF0F1116 差得太小，
                 *      加 4px padding 后基本看不出"一行"，糊成一片；
                 *   ② dp(84) 固定宽度装「1234.5」这种 6 字符数字会溢出；
                 *   ③ 进度条只有 4px，且颜色和文字同色，看不出层级；
                 *   ④ 副标题把 CPU/息屏/锁/流量 4 个指标用 · 平铺，
                 *      小屏上挤成一行长条，扫读困难。
                 *
                 * 现在：
                 *   ┌──────────────────────────────────────┐
                 *   │ [icon] 名称                    12.3 │  ← 主数字加粗
                 *   │        CPU 1m · 锁 2s          4.2% │  ← 次要，右对齐
                 *   │        ▓▓▓▓▓▓░░░░░░░░░░░░░░░        │  ← 占比条
                 *   │        模型电量 CPU 135 网络 571     │  ← 明细，小字灰
                 *   └──────────────────────────────────────┘
                 *   卡片圆角 + 描边 + 行间距 6dp，视觉上真正分区。
                 */
                row = new LinearLayout(MainActivity.this);
                row.setOrientation(LinearLayout.HORIZONTAL);
                row.setGravity(Gravity.CENTER_VERTICAL);
                row.setPadding(dp(12), dp(11), dp(12), dp(11));
                row.setBackground(roundBg(C_CARD, C_STROKE, 12f));

                ImageView iv = new ImageView(MainActivity.this);
                int ic = dp(34);
                iv.setLayoutParams(new LinearLayout.LayoutParams(ic, ic));
                row.addView(iv);

                LinearLayout mid = new LinearLayout(MainActivity.this);
                mid.setOrientation(LinearLayout.VERTICAL);
                LinearLayout.LayoutParams mlp = new LinearLayout.LayoutParams(0, -2, 1f);
                mlp.leftMargin = dp(11);
                row.addView(mid, mlp);

                TextView t1 = new TextView(MainActivity.this);
                t1.setTextSize(13.5f);
                t1.setTextColor(C_TEXT_1);
                t1.setSingleLine(true);
                t1.setEllipsize(android.text.TextUtils.TruncateAt.END);
                mid.addView(t1);

                TextView t2 = new TextView(MainActivity.this);
                t2.setTextSize(10.5f);
                t2.setTextColor(C_TEXT_2);
                /*
                 * 允许两行。setSingleLine(true) 会把 "\n" 后面的内容直接裁掉，
                 * 于是"模型电量"那行永远看不见 —— 数字又变回黑箱了。
                 */
                t2.setSingleLine(false);
                t2.setMaxLines(3);
                t2.setEllipsize(android.text.TextUtils.TruncateAt.END);
                t2.setLineSpacing(dp(1), 0.92f);
                t2.setPadding(0, dp(2), 0, 0);
                mid.addView(t2);

                ProgressBar pb = new ProgressBar(MainActivity.this, null,
                        android.R.attr.progressBarStyleHorizontal);
                pb.setMax(1000);
                LinearLayout.LayoutParams plp = new LinearLayout.LayoutParams(-1, dp(3));
                plp.topMargin = dp(7);
                mid.addView(pb, plp);

                LinearLayout right = new LinearLayout(MainActivity.this);
                right.setOrientation(LinearLayout.VERTICAL);
                right.setGravity(Gravity.END);
                /*
                 * 宽度从固定 dp(84) 改成 wrap_content + 最大 96dp：
                 * 两位数（8.7）不会浪费空间，四位数（1234.5）也不会溢出。
                 */
                LinearLayout.LayoutParams rlp = new LinearLayout.LayoutParams(-2, -2);
                rlp.leftMargin = dp(10);
                row.addView(right, rlp);

                TextView t3 = new TextView(MainActivity.this);
                t3.setTextSize(15f);
                t3.setTypeface(Typeface.MONOSPACE, Typeface.BOLD);
                t3.setTextColor(C_ACCENT);
                t3.setGravity(Gravity.END);
                right.addView(t3);

                TextView t4 = new TextView(MainActivity.this);
                t4.setTextSize(10.5f);
                t4.setTextColor(C_TEXT_3);
                t4.setGravity(Gravity.END);
                t4.setPadding(0, dp(1), 0, 0);
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
            /*
             * ===== 副标题：只显示非零项 =====
             *
             * 旧版无条件拼 4 个字段，于是大量行变成：
             *     CPU 0 · 息屏 0 · 锁 0 · IO 0B
             * 信息量为零，还把真正有值的行挤成一行长条。
             *
             * 现在只列出真有数据的项，且按「最可能被关心的程度」排序：
             *   息屏 CPU（后台偷跑）→ 唤醒锁 → CPU → 流量
             */
            StringBuilder subTxt = new StringBuilder();
            java.util.List<String> bits = new java.util.ArrayList<>();
            if (it.cpuOffMs > 500) {
                bits.add("息屏 " + dur(it.cpuOffMs));
            }
            if (it.wlMs > 500) {
                bits.add("锁 " + dur(it.wlMs));
            }
            if (it.cpuMs > 500) {
                bits.add("CPU " + dur(it.cpuMs));
            }
            long tbytes = it.rx + it.tx;
            if (tbytes > 4096) {
                bits.add((it.trafficIsReal ? "流量 " : "IO ") + bytes(tbytes));
            }
            if (bits.isEmpty()) {
                // 一行数据都没有时给个占位，避免标题下空白显得像渲染失败
                bits.add(it.mah > 0.05d ? "纯模型电量" : "无活动");
            }
            for (int i = 0; i < bits.size(); i++) {
                if (i > 0) {
                    subTxt.append(" · ");
                }
                subTxt.append(bits.get(i));
            }
            /*
             * 把这一行的电量来源摊开。
             *
             * ===== 为什么必须显示 =====
             *   归因改成 dumpsys 模型 mAh 之后，用户看到某进程 300 mAh
             *   会问"凭什么"。原来的副标题只有 CPU/锁/流量，
             *   跟 mAh 之间没有可见的推导关系 —— 数字仍然像黑箱。
             *   显示"CPU 135 网络 571 屏幕 72"之后，
             *   这一行的右侧数字与它成正比，一眼就能验算。
             *   同时也暴露了真相：B 站的电大头是【基带和屏幕】，不是 CPU，
             *   杀进程省不了多少，该调的是亮屏时长和后台网络。
             */
            String mb = mahPartsBrief(it);
            if (mb != null) {
                subTxt.append("\n模型电量 ").append(mb);
            }
            h.sub.setText(subTxt.toString());
            h.bar.setProgress((int) Math.max(0, Math.min(1000, it.share * 1000.0d)));
            /*
             * 占比条按大小分色：
             *   >=20% 强调蓝（真正的大头）
             *   >=5%  次级蓝
             *   其余   暗灰（有一点点，扫一眼就跳过）
             * 全用亮蓝的话，一屏 20 行全是亮条，等于没有重点。
             */
            int barColor = it.share >= 0.20d ? C_ACCENT
                    : (it.share >= 0.05d ? 0xFF2F6FA8 : 0xFF39414F);
            if (android.os.Build.VERSION.SDK_INT >= 21) {
                h.bar.setProgressTintList(android.content.res.ColorStateList.valueOf(barColor));
                h.bar.setProgressBackgroundTintList(
                        android.content.res.ColorStateList.valueOf(C_CARD_HI));
            }
            /*
             * ===== 数值列：区分「真实电量」与「排序代理」=====
             *
             * 「全部」口径：weight = dumpsys 模型 mAh 的份额，
             *   乘实测放电后仍是电量，单位 mAh，可以照常显示。
             *
             * 「前台/后台/前台服务」口径：weight = 时长 × CPU 活跃度，
             *   它是【排序依据】，摊出来的数不是真实电量。
             *
             *   实测反例 com.xiaomi.xmsf：
             *     dumpsys 真实 14.3 mAh / CPU 3 分钟
             *     但它被几十个应用绑定，谁在前台它就算在前台
             *     → 前台时长 7h38m（占窗口 97%）
             *     → 按旧逻辑摊出 465 mAh，放大 32 倍
             *
             *   所以这里不显示 mAh，改成显示该维度上的【时长】，
             *   用户看到的就真的是"它在前台待了多久"。
             */
            if (it.mahIsProxy) {
                h.mah.setText(dur(it.dimMs));
            } else {
                h.mah.setText(it.mah > 0.05d
                        ? String.format(Locale.CHINA, "%.1f", it.mah) : "—");
            }
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
