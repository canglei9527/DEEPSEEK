// pldims.c -- PowerLens 维度 + 真实 mAh 采集器 v3
//
// ============================================================
// v3 的核心变化：不再靠猜权重分摊电量，直接读 dumpsys 的 mAh 分项
// ============================================================
//
// ===== 为什么必须这么改（实测翻车记录）=====
//
//   v2 只采时长（fg/bg/fgs/cpuOff/wl），电量由 App 侧用
//   "CPU 秒 + 唤醒锁秒×0.6 + 流量MB×0.44" 这类猜出来的权重分摊。
//   实测后果（2026-11-09，4.5 小时区间，实测放电 1207 mAh）：
//     sys:logd  CPU 只有 1 分钟  →  分到 463.5 mAh（38.4%）
//     sys:security(uid1041实为audioserver) CPU 1 分钟 → 347.2 mAh
//   荒谬。因为总放电里【屏幕 + 基带 + 待机底噪】这些"平台级"功耗
//   被强行摊到了少数几个有 CPU 时间的进程头上。
//
//   而 dumpsys batterystats 本身就给了每个 UID 的 mAh 分项：
//     Estimated power use (mAh):
//       Capacity: 6100, Computed drain: 2768, actual drain: 2768
//       screen: 560  cpu: 2359  mobile_radio: 1005  audio: 41.0 ...
//     UID u0a341: ...
//         screen=71.6 cpu=135 audio=22.0 video=38.2 mobile_radio=558 ...
//   → 每 UID 的 mAh 是【系统自己按模型算好的】，跨 UID 可加总回 Global。
//   用它做归因，比任何手工权重都准。
//
// ===== 输出格式 v3（dims.tsv，制表符分隔）=====
//   0  ts(ms)
//   1  uid
//   2  ΔfgMs      3 ΔbgMs      4 ΔfgsMs
//   5  ΔcpuOffMs  6 ΔwlMs      7 ΔwlOffMs
//   8  intervalMs
//   9..20  ΔµAh（×1000 存整数）：
//          cpu screen radio wl wifi audio video sensors camera gnss display other
//   21 ΣµAh
//
//   dims.prev（累计值）：
//     ts uid fg bg fgs cpuOff wl wlOff + 上面 12 个 µAh  = 20 列
//
//   dims.global（一行，同字段的全机总量，App 用于校验/归一化）：
//     ts interval cpu screen radio wl wifi audio video sensors camera gnss display other sum
//
// ===== 向后兼容 =====
//   旧 dims.prev 只有 8 列。检测到列数不足时，本轮 mAh 全部写 0，
//   并把 prev 升级成新格式；下一轮起才有真实增量。
//   （否则会把"开机以来累计 mAh"当成增量灌进去，数字爆表。）

#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <time.h>
#include <ctype.h>
#include <errno.h>

#define MAXU     2048
/*
 * ===== 致命陷阱：行缓冲太小会截断长明细行 =====
 *   u0a341 的明细行长这样（一条 1500+ 字符）：
 *     screen=71.6 cpu=135 (44m 10s) cpu:fg=108 (...) cpu:bg=4.55 (...)
 *     audio=22.0 (...) video=38.2 (...) mobile_radio=558 ... wakelock=1.74 (...)
 *   LINEBUF=1024 时 fgets 把它劈成两段，后半段以 "audio:cached=..." 之类开头，
 *   会被当成【另一条数据行】重复解析 → 维度值污染。
 *   必须给足 8192。
 */
#define LINEBUF  8192
#define DEF_DIR  "/data/local/tmp/powerlens"

/* mAh 字段表（顺序即输出列顺序） */
enum {
    MAH_CPU = 0, MAH_SCREEN, MAH_RADIO, MAH_WL, MAH_WIFI, MAH_AUDIO,
    MAH_VIDEO, MAH_SENSORS, MAH_CAMERA, MAH_GNSS, MAH_DISPLAY, MAH_OTHER,
    MAH_N
};
static const char *MAH_KEY[MAH_N] = {
    "cpu", "screen", "mobile_radio", "wakelock", "wifi", "audio",
    "video", "sensors", "camera", "gnss", "display", NULL
};

typedef struct {
    int  uid;
    long fgMs, bgMs, fgsMs, cpuOffMs, wlMs, wlOffMs;
    long long mahUah[MAH_N];      /* µAh（mAh×1000），累计 */
} dimrec;

static dimrec g_cur[MAXU];
static int    g_cn = 0;
static dimrec g_prev[MAXU];
static int    g_pn = 0;
static long long g_prevTs = 0;
static int    g_prevHasMah = 0;   /* prev 是否为 v3 格式 */

static long long g_globalMah[MAH_N];
static long long g_globalSum = 0;
static long long g_prevGlobal[MAH_N];   /* 上一轮的 global 累计，用于钳制 */
static int       g_havePrevGlobal = 0;

/*
 * ===== 为什么必须用 global 钳制单 UID 增量 =====
 *
 *   实测一次 31 秒间隔的采样：
 *     u0a341  Δmobile_radio = 556504 µAh（556 mAh）
 *   而全机 8 小时的 radio 总量才 1010 mAh —— 一个 UID 在 31 秒内
 *   绝不可能消耗 556 mAh。
 *
 *   成因：dims.prev 与本次 dumpsys 之间发生过【统计重置/基线混格式】，
 *   导致 prev 里某些字段是残缺值（例如 audio/video 为 0、cpu 只有 10341），
 *   相减就等于把"开机以来累计"当成一次增量吐出来。
 *
 *   物理约束是可靠的自检：
 *     单 UID 某字段增量 ≤ 全机该字段增量
 *   超出即判定为基线异常，钳到全机增量；全机增量为 0 则该字段本轮全置 0。
 *   这样宁可少报，也绝不报出物理上不可能的数。
 */
/*
 * 跳过 n 个制表符列，返回第 n+1 列的起始指针；越界返回 NULL。
 *
 * ===== 致命陷阱：strchr 会命中自己 =====
 *   错误写法：
 *     for (t = 0; t < 8; t++) col = strchr(col, '\t');
 *     col++;
 *   第一次 strchr 找到 tab1，col 指向 tab1；
 *   第二次 strchr(tab1 起始, '\t') 又找到【同一个 tab1】（它匹配自身），
 *   于是 8 次循环只跳过 1 个 tab。
 *
 *   后果（实测）：load_prev 把 uid 读成 mahUah[0]、fgMs 读成 mahUah[1]…
 *   整个 mAh 数组错位一格，增量计算出来的数字全是垃圾：
 *     Δcpu = 124659 µAh（124 mAh / 5 分钟）—— 物理上不可能，
 *     实际就是 cur_cpu_mAh(135000) - uid(10341)。
 *   必须每次找到 tab 后【立刻 +1 越过它】再找下一个。
 */
static char *skip_cols(char *line, int n) {
    char *p = line;
    for (int i = 0; i < n; i++) {
        p = strchr(p, '\t');
        if (!p) return NULL;
        p++;                       /* 关键：越过这个 tab */
    }
    return p;
}

static void load_prev_global(const char *dir) {
    char path[256];
    snprintf(path, sizeof path, "%s/dims.global", dir);
    FILE *f = fopen(path, "r");
    if (!f) return;
    static char line[1024];
    if (fgets(line, sizeof line, f)) {
        long long ts, iv;
        int r = sscanf(line, "%lld\t%lld", &ts, &iv);
        if (r == 2) {
                char *col = skip_cols(line, 2);      /* 跳过 ts 和 interval */
                if (col) {
                    for (int i = 0; i < MAH_N && col; i++) {
                        g_prevGlobal[i] = strtoll(col, &col, 10);
                        if (*col == '\t') col++; else break;
                    }
                    g_havePrevGlobal = 1;
                }
            }
    }
    fclose(f);
}

static int g_verbose = 0;
static int g_trace_uid = -1;
static const char *g_input_file = NULL;   /* -i FILE：离线回归测试用 */

static void trace(int uid, const char *what, const char *line, long long val) {
    if (!g_verbose) return;
    if (g_trace_uid >= 0 && uid != g_trace_uid) return;
    fprintf(stderr, "TRACE uid=%d %s val=%lld | %.90s\n", uid, what, val, line);
}

static long long now_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_REALTIME, &ts);
    return (long long)ts.tv_sec * 1000LL + ts.tv_nsec / 1000000LL;
}

static int find_cur(int uid) {
    for (int i = 0; i < g_cn; i++) if (g_cur[i].uid == uid) return i;
    return -1;
}

static int add_cur(int uid) {
    int i = find_cur(uid);
    if (i >= 0) return i;
    if (g_cn >= MAXU) return -1;
    memset(&g_cur[g_cn], 0, sizeof g_cur[0]);
    g_cur[g_cn].uid = uid;
    return g_cn++;
}

static int find_prev(int uid) {
    for (int i = 0; i < g_pn; i++) if (g_prev[i].uid == uid) return i;
    return -1;
}

static int parse_uid(const char *s) {
    if (!s || !*s) return -1;
    if (s[0] == 'u') {
        const char *a = strchr(s, 'a');
        if (!a) return -1;
        return atoi(s + 1) * 100000 + 10000 + atoi(a + 1);
    }
    if (isdigit((unsigned char)s[0])) return atoi(s);
    return -1;
}

/*
 * 解析形如 "5h 58m 53s 606ms" 的时长，返回毫秒。
 *
 * ===== 致命陷阱：必须有结束边界 =====
 *   调用方给的是 '(' 之后的 C 字符串。若一路解析到 '\0'，
 *   会把【同一行后面所有字段的时长全累加进来】：
 *     "(43m 42s 223ms) bg: 34.3 (15s 496ms) fgs: 184 (5h 58m 53s 606ms) cached: 0.0195 (98ms)"
 *     → 43m+42s+223ms+15s+496ms+5h+58m+53s+606ms+98ms ≈ 6h37m
 *   实测 dims.prev 里 u0a341 的 fg=23835760ms = 6h37m，正是这个和；
 *   真值只有 43m 42s（2622223ms）—— 虚高 9 倍，整个维度排序全废。
 *   现在用 end 指针限定右括号。
 */
static long parse_dur_bounded(const char *s, const char *end) {
    long ms = 0, v = 0;
    int inNum = 0;
    const char *p = s;
    while (p < end && *p) {
        if (isdigit((unsigned char)*p)) { v = v * 10 + (*p - '0'); inNum = 1; }
        else if (inNum) {
            if (p[0] == 'h')                     { ms += v * 3600000L; p++; }
            else if (p[0] == 'm' && p[1] == 's') { ms += v;            p += 2; }
            else if (p[0] == 'm')                { ms += v * 60000L;   p++; }
            else if (p[0] == 's')                { ms += v * 1000L;    p++; }
            else                                  { p++; }
            v = 0; inNum = 0; continue;
        }
        p++;
    }
    return ms;
}

/*
 * 在 line 里找 key（"fg:" / "cpu=" ...）【紧跟的】括号时长。
 *
 * ===== 陷阱①：前缀冲突（词边界）=====
 *   明细行有 "cpu=94.2 (...) cpu:fg=69.3 (...) cpu:bg=4.56 (...)"
 *   strstr(line,"bg:") 会匹配到 "cpu:bg=" 里的 "bg:"。
 *   → 要求 key 前面是空格/制表符/行首。
 *
 * ===== 陷阱②：strchr 越过字段边界 =====
 *   headline 里某些字段【没有括号时长】：
 *     "UID u0a351: 30.3 fg: 1.47 (5m 44s) bg: 1.46 fgs: 16.9 (7h 51m)"
 *   "bg: 1.46" 后没有 '('，若用 strchr(k,'(') 往后找会找到
 *   【下一个字段 fgs 的括号】→ bg 被灌成 7h51m。
 *   → key 后只允许跨过 数字/小数点/负号/空格，下一个字符必须【直接】是 '('。
 */
static long paren_after(const char *line, const char *key) {
    const char *p = line;
    const char *k = NULL;
    size_t klen = strlen(key);

    while ((p = strstr(p, key)) != NULL) {
        int ok = 1;
        if (p > line) {
            char prev = *(p - 1);
            if (prev != ' ' && prev != '\t') ok = 0;
        }
        if (ok) { k = p; break; }
        p += klen;
    }
    if (!k) return 0;

    const char *q = k + klen;
    while (*q == ' ' || *q == '-' || *q == '.' || isdigit((unsigned char)*q)) q++;
    if (*q != '(') return 0;
    const char *rp = strchr(q, ')');
    if (!rp) return 0;
    return parse_dur_bounded(q + 1, rp);
}

/* 取 key= 后面的数值（支持小数），返回 double；找不到返回 -1
   （保留给后续扩展：per-UID 的 audio/video 校验。当前解析走 parse_mah。） */
__attribute__((unused))
static double num_after(const char *line, const char *key) {
    const char *p = line;
    const char *k = NULL;
    size_t klen = strlen(key);
    while ((p = strstr(p, key)) != NULL) {
        int ok = 1;
        if (p > line) {
            char prev = *(p - 1);
            if (prev != ' ' && prev != '\t') ok = 0;
        }
        if (ok) { k = p; break; }
        p += klen;
    }
    if (!k) return -1;
    const char *q = k + klen;      /* 跳过 "key=" */
    if (*q != '=') return -1;
    q++;
    while (*q == ' ') q++;
    char *endp = NULL;
    double v = strtod(q, &endp);
    if (endp == q) return -1;
    return v;
}

/*
 * 解析 UID 段的【全时段合计明细行】，抽取 mAh 分项。
 *
 *   screen=71.6 cpu=135 (44m 10s) cpu:fg=108 (...) mobile_radio=558 ... wakelock=1.74 (...)
 *
 * 只认【不带冒号】的总项（cpu / screen / mobile_radio / wakelock …），
 * "cpu:fg=" "mobile_radio:bg=" 这类分项跳过 —— 它们是总项的拆分，
 * 再累加会翻倍。未列入字段表的键统一进 MAH_OTHER。
 */
static void parse_mah(const char *line, dimrec *r) {
    const char *q = line;
    while (*q) {
        /* 定位下一个 "key=" 且 key 前是空格/行首 */
        const char *eq = strchr(q, '=');
        if (!eq) break;
        /* 找 key 起点 */
        const char *ks = eq - 1;
        while (ks > line && ks[-1] != ' ' && ks[-1] != '\t') ks--;
        size_t klen = (size_t)(eq - ks);
        if (klen == 0 || klen > 63) { q = eq + 1; continue; }

        char key[64];
        memcpy(key, ks, klen);
        key[klen] = 0;
        /* key 必须是纯小写字母/下划线/数字，否则是句子碎片 */
        int valid = 1;
        for (size_t i = 0; i < klen; i++) {
            char ch = key[i];
            if (!(islower((unsigned char)ch) || ch == '_' || isdigit((unsigned char)ch))) { valid = 0; break; }
        }
        if (!valid) { q = eq + 1; continue; }

        /* 值：可带小数 */
        const char *vs = eq + 1;
        while (*vs == ' ') vs++;
        char *endp = NULL;
        double v = strtod(vs, &endp);
        if (endp == vs) { q = eq + 1; continue; }

        /* 映射到字段 */
        int slot = -1;
        for (int i = 0; i < MAH_N - 1; i++) {
            if (MAH_KEY[i] && strcmp(key, MAH_KEY[i]) == 0) { slot = i; break; }
        }
        if (slot >= 0) {
            /* µAh = mAh × 1000，四舍五入避免累计误差 */
            long long uah = (long long)(v * 1000.0 + 0.5);
            if (uah > r->mahUah[slot]) r->mahUah[slot] = uah;   /* 只取首次/较大值 */
        } else if (strchr(key, ':') == NULL) {
            long long uah = (long long)(v * 1000.0 + 0.5);
            if (uah > 0) r->mahUah[MAH_OTHER] += uah;
        }
        q = endp;
    }
}

/*
 * 解析 Estimated power use 段的 Global 行：
 *     cpu: 2359 apps: 2356 duration: 8h 19m 48s 766ms
 * 注意这里是 "key: 数值"（冒号），不是 "key=数值"。
 */
static void parse_global(const char *line) {
    const char *q = line;
    while (*q) {
        const char *co = strchr(q, ':');
        if (!co) break;
        const char *ks = co - 1;
        while (ks > line && ks[-1] != ' ' && ks[-1] != '\t') ks--;
        size_t klen = (size_t)(co - ks);
        if (klen == 0 || klen > 63) { q = co + 1; continue; }
        char key[64];
        memcpy(key, ks, klen);
        key[klen] = 0;
        int valid = 1;
        for (size_t i = 0; i < klen; i++) {
            char ch = key[i];
            if (!(islower((unsigned char)ch) || ch == '_')) { valid = 0; break; }
        }
        if (!valid) { q = co + 1; continue; }
        const char *vs = co + 1;
        while (*vs == ' ') vs++;
        char *endp = NULL;
        double v = strtod(vs, &endp);
        if (endp == vs) { q = co + 1; continue; }
        /* 值后面必须紧跟 " apps:"，否则是 duration 里的时间碎片 */
        const char *ap = strstr(endp, "apps:");
        if (!ap || (size_t)(ap - endp) > 16) { q = endp; continue; }

        int slot = -1;
        for (int i = 0; i < MAH_N - 1; i++) {
            if (MAH_KEY[i] && strcmp(key, MAH_KEY[i]) == 0) { slot = i; break; }
        }
        long long uah = (long long)(v * 1000.0 + 0.5);
        if (slot >= 0)      g_globalMah[slot] = uah;
        else if (strcmp(key, "capacity") != 0) g_globalMah[MAH_OTHER] += uah;
        q = ap + 5;
    }
}

static void run_dumpsys(void) {
    /*
     * -i FILE：从文件读 dumpsys 输出，而不是 popen。
     *
     * 为什么需要它：解析逻辑的正确性只能在【同一份输入】上回归才有意义。
     * 线上 dumpsys 每次都变，出问题时无法区分"解析错了"还是"数据变了"。
     * 把一次现场输出存成文件，改完代码立刻重跑同一份数据 diff 结果。
     *   adb shell su -c 'dumpsys batterystats --charged > /data/local/tmp/bs.txt'
     *   adb shell su -c 'pldims -i /data/local/tmp/bs.txt -d /tmp/t -v'
     */
    const char *cmd = g_input_file
        ? NULL
        : "/system/bin/dumpsys batterystats --charged 2>/dev/null";
    FILE *f = g_input_file ? fopen(g_input_file, "r") : popen(cmd, "r");
    if (!f) {
        fprintf(stderr, "pldims: 打开输入失败: %s\n", strerror(errno));
        return;
    }
    static char line[LINEBUF];          /* 8KB，放静态区避免栈压力 */
    int cur = -1, inOffDoz = 0, expectDetail = 0;
    int inEst = 0, estDone = 0;
    int nHead = 0;

    /*
     * ===== dumpsys 结构（实测）=====
     *
     *   Estimated power use (mAh):
     *     Capacity: 6100, Computed drain: 2768, actual drain: 2768
     *     Global
     *       screen: 560 apps: 560
     *       cpu: 2359 apps: 2356 duration: 8h 19m 48s 766ms
     *       ...
     *         (on battery, screen on)      ← 分段，从这里起不再算 Global
     *   ...
     *       UID u0a341: 832 fg: 545 (43m 42s 223ms) bg: 34.3 (15s 496ms) fgs: 184 (5h 58m) cached: 0.0195 (98ms)
     *           screen=71.6 cpu=135 (44m 10s) cpu:fg=108 ... wakelock=1.74 (3m 11s)   ← 全时段合计（mAh 在这）
     *           (on battery, screen on) screen=71.6 cpu=127 ...                        ← 亮屏分段
     *           (on battery, screen off/doze) cpu=8.12 (2m 22s) wakelock=...           ← 息屏分段
     *           (not on battery, screen on) ...
     *
     * ===== 三个必须记住的坑 =====
     * ① 段标记和数据【在同一行】：
     *      "(on battery, screen off/doze) cpu=8.12 (2m 22s 207ms) ..."
     *    遇到标记就 continue 会把 cpu=8.12 丢掉 → cpuOff 永远 0。
     *    → 设标志后【不 continue】，继续解析本行。
     *
     * ② headline 的 fg/bg/fgs 与下面分段行是【总和 vs 拆分】关系，
     *    两边都累加会翻倍。
     *    → fg/bg/fgs 只从 headline 取；cpuOff/wlOff 只从 off/doze 段取；
     *      wl 总时长从全时段合计行取。
     *
     * ③ headline 可能在多个统计周期重复出现（时长是状态量，不是计数器），
     *    重复累加会把 5h 灌成 20h。
     *    → 同一 UID 同一字段只取第一次出现的值。
     */
    while (fgets(line, sizeof line, f)) {
        /* ---- Estimated power use 的 Global 段 ---- */
        if (!estDone && strstr(line, "Estimated power use")) { inEst = 1; continue; }
        if (inEst) {
            if (strstr(line, "(on battery") || strstr(line, "(not on battery")) {
                inEst = 0; estDone = 1;
            } else {
                parse_global(line);
            }
        }

        /* ---- UID 头行 ---- */
        char *u = strstr(line, "  UID ");
        if (u && strchr(u, ':')) {
            nHead++;
            char uidbuf[64];
            const char *s = u + 6;
            const char *c = strchr(s, ':');
            size_t len = (size_t)(c - s);
            if (len > 0 && len < sizeof uidbuf) {
                memcpy(uidbuf, s, len);
                uidbuf[len] = 0;
                int uid = parse_uid(uidbuf);
                cur = (uid >= 0) ? add_cur(uid) : -1;
            }
            inOffDoz = 0;
            expectDetail = 1;          /* 下一行是全时段合计明细 */

            if (cur >= 0) {
                long f2 = paren_after(line, "fg:");
                long b2 = paren_after(line, "bg:");
                long g2 = paren_after(line, "fgs:");
                if (f2 > 0 && g_cur[cur].fgMs  == 0) g_cur[cur].fgMs  = f2;
                if (b2 > 0 && g_cur[cur].bgMs  == 0) g_cur[cur].bgMs  = b2;
                if (g2 > 0 && g_cur[cur].fgsMs == 0) g_cur[cur].fgsMs = g2;
                trace(g_cur[cur].uid, "head", line, g_cur[cur].fgMs);
            }
            continue;
        }

        if (cur < 0) continue;

        /* ---- 是否分段行 ---- */
        int isSeg = 0;
        if (strstr(line, "(on battery, screen off/doze)")) { inOffDoz = 1; isSeg = 1; }
        else if (strstr(line, "(on battery, screen on)"))  { inOffDoz = 0; isSeg = 1; }
        else if (strstr(line, "(on battery,"))             { isSeg = 1; }
        else if (strstr(line, "(not on battery"))          { isSeg = 1; inOffDoz = 0; }

        /* ---- 全时段合计明细行：取 mAh + 总唤醒锁时长 ---- */
        if (expectDetail && !isSeg &&
            (strstr(line, "cpu=") || strstr(line, "screen=") || strstr(line, "wakelock="))) {
            parse_mah(line, &g_cur[cur]);
            long wl = paren_after(line, "wakelock=");
            if (wl > 0 && g_cur[cur].wlMs == 0) g_cur[cur].wlMs = wl;
            expectDetail = 0;
            trace(g_cur[cur].uid, "mah", line, g_cur[cur].mahUah[MAH_CPU]);
            continue;   /* 这行的 cpu= 是总 CPU，不属于息屏段 */
        }

        /* ---- 息屏分段：cpuOff / wlOff ---- */
        if (isSeg && inOffDoz) {
            dimrec *r = &g_cur[cur];
            if (strstr(line, "cpu=") && r->cpuOffMs == 0) {
                long d = paren_after(line, "cpu=");
                if (d > 0) { r->cpuOffMs = d; trace(r->uid, "cpuOff", line, d); }
            }
            if (strstr(line, "wakelock=") && r->wlOffMs == 0) {
                long d = paren_after(line, "wakelock=");
                if (d > 0) { r->wlOffMs = d; trace(r->uid, "wlOff", line, d); }
            }
        }
    }
    if (g_input_file) fclose(f); else pclose(f);
    if (g_verbose) {
        fprintf(stderr, "pldims: headline=%d UID=%d\n", nHead, g_cn);
        for (int i = 0; i < MAH_N; i++) {
            fprintf(stderr, "  GLOBAL %s = %.1f mAh\n",
                    MAH_KEY[i] ? MAH_KEY[i] : "other", g_globalMah[i] / 1000.0);
        }
    }
    for (int i = 0; i < MAH_N; i++) g_globalSum += g_globalMah[i];
}

static int load_prev(const char *path) {
    FILE *f = fopen(path, "r");
    if (!f) return 0;
    static char line[LINEBUF];
    int n = 0;
    g_prevHasMah = 0;
    while (fgets(line, sizeof line, f)) {
        long long ts;
        int uid;
        long a, b, c, d, e, g;
        int r = sscanf(line, "%lld\t%d\t%ld\t%ld\t%ld\t%ld\t%ld\t%ld",
                       &ts, &uid, &a, &b, &c, &d, &e, &g);
        if (r < 8) continue;
        if (g_prevTs == 0) g_prevTs = ts;
        if (n >= MAXU) break;
        dimrec *p = &g_prev[n];
        memset(p, 0, sizeof *p);
        p->uid = uid;
        p->fgMs = a; p->bgMs = b; p->fgsMs = c;
        p->cpuOffMs = d; p->wlMs = e; p->wlOffMs = g;
        if (r == 8) {
            /* 后面还有 12 个 µAh 列？手工数制表符 */
            int tabs = 0;
            for (char *q = line; *q; q++) if (*q == '\t') tabs++;
            if (tabs >= 19) {
                /*
                 * v3：第 9 列起是 12 个 µAh。
                 * 必须用 skip_cols —— 旧的 strchr 循环会命中自身，
                 * 导致只跳过 1 个 tab，整个数组错位 8 列：
                 *   mahUah[0]=uid, mahUah[1]=fgMs ...
                 *   → Δcpu = 135000 - 10341 = 124659（垃圾数字）
                 */
                char *col = skip_cols(line, 8);
                if (col) {
                    for (int i = 0; i < MAH_N && col; i++) {
                        p->mahUah[i] = strtoll(col, &col, 10);
                        if (*col == '\t') col++; else break;
                    }
                    g_prevHasMah = 1;
                }
            }
        }
        n++;
    }
    fclose(f);
    g_pn = n;
    return n;
}

static int save_prev(const char *path, long long ts) {
    FILE *f = fopen(path, "w");
    if (!f) return 0;
    for (int i = 0; i < g_cn; i++) {
        dimrec *d = &g_cur[i];
        fprintf(f, "%lld\t%d\t%ld\t%ld\t%ld\t%ld\t%ld\t%ld",
                ts, d->uid, d->fgMs, d->bgMs, d->fgsMs, d->cpuOffMs, d->wlMs, d->wlOffMs);
        for (int k = 0; k < MAH_N; k++) fprintf(f, "\t%lld", d->mahUah[k]);
        fputc('\n', f);
    }
    fclose(f);
    return 1;
}

static void save_global(const char *dir, long long ts, long long interval) {
    char path[256];
    snprintf(path, sizeof path, "%s/dims.global", dir);
    FILE *f = fopen(path, "w");
    if (!f) return;
    fprintf(f, "%lld\t%lld", ts, interval);
    for (int k = 0; k < MAH_N; k++) fprintf(f, "\t%lld", g_globalMah[k]);
    fprintf(f, "\t%lld\n", g_globalSum);
    fclose(f);
}

int main(int argc, char **argv) {
    char out[256], prev[256], dirbuf[256];
    snprintf(out,  sizeof out,  "%s/dims.tsv",  DEF_DIR);
    snprintf(prev, sizeof prev, "%s/dims.prev", DEF_DIR);
    snprintf(dirbuf, sizeof dirbuf, "%s", DEF_DIR);

    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "-f") && i + 1 < argc) {
            snprintf(out, sizeof out, "%s", argv[++i]);
        } else if (!strcmp(argv[i], "-p") && i + 1 < argc) {
            snprintf(prev, sizeof prev, "%s", argv[++i]);
        } else if (!strcmp(argv[i], "-d") && i + 1 < argc) {
            /*
             * ===== 陷阱：argv[++i] 写两次 =====
             *   旧代码两个 snprintf 都用 argv[++i]，第二次把 i 又加一位，
             *   越界拿到 NULL → prev 变成 "(null)/dims.prev"，fopen 失败，
             *   -d 模式下基线永远存不上（实测 cp 报 No such file）。
             *   → 先取一次目录，再拼两个路径。
             */
            const char *dir = argv[++i];
            snprintf(dirbuf, sizeof dirbuf, "%s", dir);
            snprintf(out,  sizeof out,  "%s/dims.tsv",  dir);
            snprintf(prev, sizeof prev, "%s/dims.prev", dir);
        } else if (!strcmp(argv[i], "-v")) {
            g_verbose = 1;
        } else if (!strcmp(argv[i], "-i") && i + 1 < argc) {
            g_input_file = argv[++i];       /* 离线回归：从文件读 dumpsys */
        } else if (!strcmp(argv[i], "-u") && i + 1 < argc) {
            g_trace_uid = atoi(argv[++i]);
        }
    }

    g_cn = 0;
    run_dumpsys();
    if (g_cn == 0) {
        fprintf(stderr, "pldims: dumpsys 没解析出 UID\n");
        return 1;
    }

    long long ts = now_ms();
    int pn = load_prev(prev);

    /* 先读上一轮的 global，用于钳制单 UID 增量 */
    load_prev_global(dirbuf);

    /* 全机各字段的本轮增量（负/零视为无变化） */
    long long gDelta[MAH_N];
    for (int k = 0; k < MAH_N; k++) {
        long long dd = g_globalMah[k] - (g_havePrevGlobal ? g_prevGlobal[k] : 0);
        gDelta[k] = (dd > 0) ? dd : 0;
    }
    if (g_verbose) {
        fprintf(stderr, "pldims: prevGlobal=%s\n", g_havePrevGlobal ? "yes" : "NO");
        for (int k = 0; k < MAH_N; k++) {
            fprintf(stderr, "  DELTA %s: cur=%.1f prev=%.1f -> d=%.1f mAh\n",
                    MAH_KEY[k] ? MAH_KEY[k] : "other",
                    g_globalMah[k] / 1000.0,
                    (g_havePrevGlobal ? g_prevGlobal[k] : 0) / 1000.0,
                    gDelta[k] / 1000.0);
        }
    }

    if (pn == 0) {
        save_prev(prev, ts);
        FILE *f = fopen(out, "w");
        if (f) fclose(f);
        save_global(dirbuf, ts, 0);
        fprintf(stderr, "pldims: 首次运行，已存基线 %d UID（下次起出增量）\n", g_cn);
        return 0;
    }

    long long interval = (g_prevTs > 0) ? (ts - g_prevTs) : 0;
    if (interval <= 0) interval = 0;

    FILE *f = fopen(out, "w");
    if (!f) {
        fprintf(stderr, "pldims: 打不开 %s: %s\n", out, strerror(errno));
        return 1;
    }

    int nz = 0;
    for (int i = 0; i < g_cn; i++) {
        dimrec *c = &g_cur[i];
        long dfg = 0, dbg = 0, dfgs = 0, doff = 0, dwl = 0, dwlo = 0;
        long long dm[MAH_N];
        for (int k = 0; k < MAH_N; k++) dm[k] = 0;

        int pi = find_prev(c->uid);
        if (pi >= 0) {
            dimrec *p = &g_prev[pi];
            dfg  = c->fgMs     - p->fgMs;
            dbg  = c->bgMs     - p->bgMs;
            dfgs = c->fgsMs    - p->fgsMs;
            doff = c->cpuOffMs - p->cpuOffMs;
            dwl  = c->wlMs     - p->wlMs;
            dwlo = c->wlOffMs  - p->wlOffMs;
            /* 负数 = 计数器重置/进程重启，钳 0 */
            if (dfg  < 0) dfg  = 0;
            if (dbg  < 0) dbg  = 0;
            if (dfgs < 0) dfgs = 0;
            if (doff < 0) doff = 0;
            if (dwl  < 0) dwl  = 0;
            if (dwlo < 0) dwlo = 0;
            /*
             * mAh 增量：只有 prev 是 v3 格式才算。
             * 旧 8 列 prev 的 mahUah 全 0，直接相减会把
             * "开机以来累计 mAh" 当成一次增量 → 数字爆表。
             */
            if (g_prevHasMah && g_havePrevGlobal) {
                for (int k = 0; k < MAH_N; k++) {
                    long long dd = c->mahUah[k] - p->mahUah[k];
                    if (dd < 0) dd = 0;
                    /* 物理钳制：单 UID 不可能超过全机增量 */
                    if (g_havePrevGlobal && dd > gDelta[k]) dd = gDelta[k];
                    dm[k] = dd;
                }
            }
        }

        long long sum = 0;
        for (int k = 0; k < MAH_N; k++) sum += dm[k];

        if ((dfg | dbg | dfgs | doff | dwl | dwlo) == 0 && sum == 0) continue;

        fprintf(f, "%lld\t%d\t%ld\t%ld\t%ld\t%ld\t%ld\t%ld\t%lld",
                ts, c->uid, dfg, dbg, dfgs, doff, dwl, dwlo, interval);
        for (int k = 0; k < MAH_N; k++) fprintf(f, "\t%lld", dm[k]);
        fprintf(f, "\t%lld\n", sum);
        nz++;
    }
    fclose(f);

    save_prev(prev, ts);
    save_global(dirbuf, ts, interval);

    fprintf(stderr, "pldims: 增量 %d/%d UID，间隔 %.1f 分钟，mah=%s -> %s\n",
            nz, g_cn, interval / 60000.0,
            g_prevHasMah ? "增量" : "首轮(待下轮)", out);
    return 0;
}
