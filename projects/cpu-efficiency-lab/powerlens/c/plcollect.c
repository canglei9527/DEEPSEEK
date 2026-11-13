// plcollect.c -- PowerLens 轻量采集器（纯 C，无 Java，无 dumpsys）
//
// 为什么值得用 C：
//   1. 不需要 Android Framework，进程 RSS < 300 KB（Java 版 166 MB，其中 160 MB 是 Framework）
//   2. 不占 zram（Java 版被换出 25 MB，全机 zram 已用 9.9 GB）
//   3. 常驻 sleep 时 CPU = 0，可被内核完全冻结
//
// 数据源（全部 procfs，总耗时 < 60 ms，对比 dumpsys 666 ms / 280 KB）：
//   /proc/uid_cputime/show_uid_stat   每 UID 用户态+内核态 µs
//   /proc/uid_io/stats                每 UID 读写字节
//   /proc/<pid>/status                进程名 → 包名
//   /sys/class/power_supply/battery/  电量计、状态、百分比
//
// 输出与原 snapshots.tsv 兼容（13 列 + battery 行），
// 电池行第 4 列改为绝对电量计 µAh（读取端按"只算下降"核算）。
//
// 编译（NDK 或 aarch64-linux-gnu-gcc 均可）：
//   aarch64-linux-android24-clang -O2 -s -static-libgcc -o plcollect plcollect.c
//
// 用法：
//   plcollect --once          采一次，追加到 $STORE
//   plcollect --loop 600      每 600 s 采一次
//   plcollect --status        打印当前电量与核算结果
//   STORE 环境变量或 -f 指定文件，默认 /data/local/tmp/powerlens/snapshots.tsv

#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <time.h>
#include <dirent.h>
#include <errno.h>
#include <ctype.h>
#include <sys/stat.h>

#define DEF_STORE "/data/local/tmp/powerlens/snapshots.tsv"
#define MAXU      1024          /* 最多跟踪 1024 个 UID */
#define MAXPOL    16            /* 最多跟踪 16 个 cpufreq policy */
#define LINEBUF   512
#define NAMEBUF   256

typedef struct {
    int  uid;
    unsigned long long utime_us;
    unsigned long long stime_us;
    unsigned long long rchar;      /* 读字节 */
    unsigned long long wchar;      /* 写字节 */
    char pkg[NAMEBUF];
} uidrec;

static uidrec g_u[MAXU];
static int    g_n = 0;

/* ---------- CPU 频率快照 ----------
 *
 * 只读 /sys/devices/system/cpu，不写任何节点，不改 governor。
 *
 * 为什么需要它：原采集只有「每 UID 的 CPU 毫秒」，没有频率。
 * 同样 1 秒 CPU 时间，跑在 1.0 GHz 和 2.4 GHz 上耗电差一倍以上；
 * 缺频率就无法解释「某个 UID 的 CPU 时间为什么便宜/贵」,
 * 也无法看出设备当时是否被系统限频。
 *
 * 输出为额外一行 `ts * cpu ...`。读取端（Storage）只认 `battery` 行，
 * 因此旧版 App 读到这些行会安全跳过，不需要数据迁移。
 */
typedef struct {
    char name[32];        /* policy0 / policy6 ... */
    char cpus[64];        /* "0_1_2_3_4_5"（空格已换成下划线，不破坏列分隔） */
    int  cur_khz;
    int  min_khz;
    int  max_khz;
} policyrec;

static policyrec g_p[MAXPOL];
static int       g_pn = 0;
static int       g_online = -1;
static char      g_online_buf[64];
static int       g_cpu_count = -1;

/* ---------- 工具 ---------- */

static long long now_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_REALTIME, &ts);
    return (long long)ts.tv_sec * 1000LL + ts.tv_nsec / 1000000LL;
}

static int read_first_line(const char *path, char *out, size_t n) {
    FILE *f = fopen(path, "r");
    if (!f) return -1;
    if (!fgets(out, (int)n, f)) { fclose(f); return -1; }
    fclose(f);
    size_t L = strlen(out);
    while (L && (out[L-1] == '\n' || out[L-1] == '\r')) out[--L] = 0;
    return 0;
}

static long long read_ll(const char *path) {
    char b[64];
    if (read_first_line(path, b, sizeof b) != 0) return -1;
    return atoll(b);
}

/* 从 /proc/<pid>/cmdline 取第一段作为包名 */
static void read_pkg(int pid, char *out, size_t n) {
    char p[64], buf[NAMEBUF];
    snprintf(p, sizeof p, "/proc/%d/cmdline", pid);
    FILE *f = fopen(p, "r");
    if (!f) { out[0] = 0; return; }
    size_t got = fread(buf, 1, sizeof buf - 1, f);
    fclose(f);
    buf[got] = 0;
    if (got == 0) { out[0] = 0; return; }
    /* 取到第一个 \0 或 ':' 之前 */
    size_t i = 0;
    while (i < got && buf[i] && buf[i] != ':' && buf[i] != '\n') i++;
    buf[i] = 0;
    /* 只保留可打印字符 */
    size_t j = 0, k = 0;
    while (buf[j] && k + 1 < n) {
        unsigned char c = (unsigned char)buf[j++];
        out[k++] = (c >= 0x20 && c < 0x7f) ? (char)c : '_';
    }
    out[k] = 0;
    if (k == 0) strcpy(out, "?");
}

/*
 * 判断 g_u[].pkg 里是不是一个【可信的包名】。
 *
 * ===== 为什么不能只看"含 '.'"（踩过，本机 2943 行数据被污染）=====
 *   /proc/<pid>/status 的 Name: 被内核截断到 15 字符，
 *   于是 vendor.qti.hardware.cacert.server 变成 .cacertservice，
 *   com.qualcomm.qti.cne 被截成 .qms，vendor.qti.data.txpwradmin 变成 .qtidataservices。
 *   这些截断值仍然含 '.'，旧判断把它们当成合法包名保留下来，
 *   反而挡住了 /data/system/packages.list 的权威映射。
 *
 *   实测受影响的 UID：
 *     2918  -> .cacertservice   （真名 vendor.qti.hardware.cacert.server）
 *     10118 -> .qms             （真名 vendor.qti.data.txpwradmin）
 *     10115 -> .qtidataservices （真名 com.qualcomm.qti.cne）
 *
 * ===== 判定规则 =====
 *   合法包名必须：
 *     1. 非空，且不以 '?' 开头（占位符）
 *     2. 不以 '.' 开头（截断残留）
 *     3. 不以 '/' 开头（二进制路径，如 /vendor/bin/hw/audiohalservice.qti）
 *     4. 不以 "proc:" / "sys:" 开头（内部的回退标记）
 *     5. 含至少一个 '.'（Java 包名惯例）
 *     6. 首字符是字母或下划线（Android 包名不能以数字/符号开头）
 */
static int pkg_is_valid(const char *p) {
    if (p == NULL || p[0] == 0) return 0;
    if (p[0] == '?' || p[0] == '.' || p[0] == '/') return 0;
    if (strncmp(p, "proc:", 5) == 0) return 0;
    if (strncmp(p, "sys:",  4) == 0) return 0;
    if (strchr(p, '.') == NULL) return 0;
    if (!(isalpha((unsigned char)p[0]) || p[0] == '_')) return 0;
    return 1;
}

static int find_uid(int uid) {
    for (int i = 0; i < g_n; i++) if (g_u[i].uid == uid) return i;
    return -1;
}

static int add_uid(int uid) {
    int i = find_uid(uid);
    if (i >= 0) return i;
    if (g_n >= MAXU) return -1;
    memset(&g_u[g_n], 0, sizeof g_u[0]);
    g_u[g_n].uid = uid;
    strcpy(g_u[g_n].pkg, "?");
    return g_n++;
}

/* ---------- 数据采集 ---------- */

/* /proc/uid_cputime/show_uid_stat 每行： "<uid>: <utime_us> <stime_us>" */
static void load_uid_cputime(void) {
    FILE *f = fopen("/proc/uid_cputime/show_uid_stat", "r");
    if (!f) return;
    char line[LINEBUF];
    while (fgets(line, sizeof line, f)) {
        int uid;
        unsigned long long u, s;
        if (sscanf(line, "%d: %llu %llu", &uid, &u, &s) == 3) {
            int i = add_uid(uid);
            if (i >= 0) { g_u[i].utime_us = u; g_u[i].stime_us = s; }
        }
    }
    fclose(f);
}

/* /proc/uid_io/stats 每行： "<uid> rchar wchar read_bytes write_bytes ..." */
static void load_uid_io(void) {
    FILE *f = fopen("/proc/uid_io/stats", "r");
    if (!f) return;
    char line[LINEBUF];
    while (fgets(line, sizeof line, f)) {
        int uid;
        unsigned long long rchar = 0, wchar = 0;
        if (sscanf(line, "%d %llu %llu", &uid, &rchar, &wchar) >= 1) {
            int i = add_uid(uid);
            if (i >= 0) { g_u[i].rchar = rchar; g_u[i].wchar = wchar; }
        }
    }
    fclose(f);
}

/* 扫 /proc/<pid> 补包名 */
static void load_pkg_names(void) {
    DIR *d = opendir("/proc");
    if (!d) return;
    struct dirent *e;
    char path[128], out[NAMEBUF];
    while ((e = readdir(d))) {
        if (!isdigit((unsigned char)e->d_name[0])) continue;
        int pid = atoi(e->d_name);
        if (pid <= 0) continue;
        snprintf(path, sizeof path, "/proc/%d/status", pid);
        FILE *f = fopen(path, "r");
        if (!f) continue;
        char line[LINEBUF];
        int uid = -1;
        while (fgets(line, sizeof line, f)) {
            if (strncmp(line, "Uid:", 4) == 0) {
                sscanf(line + 4, "%d", &uid);
                break;
            }
        }
        fclose(f);
        if (uid < 0) continue;
        int i = find_uid(uid);
        if (i < 0) continue;
        if (strcmp(g_u[i].pkg, "?") != 0) continue;   /* 已有 */
        read_pkg(pid, out, sizeof out);
        if (out[0] && strcmp(out, "?") != 0) {
            strncpy(g_u[i].pkg, out, NAMEBUF - 1);
            g_u[i].pkg[NAMEBUF - 1] = 0;
        }
    }
    closedir(d);
}

/*
 * 用 /data/system/packages.list 补全所有 UID 的包名。
 *
 * 为什么必须有这一步：
 *   /proc/<pid>/cmdline 只能拿到【当前有进程在跑】的 UID，
 *   而快照里大部分 UID（系统服务、已缓存的应用）当时没有进程，
 *   导致 pkg 大面积是 "?"（本机实测 23388 行里 11088 行是 "?"）。
 *
 * ===== 共享 UID 的坑（踩过）=====
 *   packages.list 里同一个 uid 可能有多个包（Android 的 sharedUserId）。
 *   例如 uid 1000 既有 system_server，又有 com.xiaomi.digitalkey。
 *   无条件取最后一个会把 uid 1000 标成 "com.xiaomi.digitalkey"，
 *   于是 UI 上「Android 系统」消失了，取而代之的是一堆莫名其妙的包名。
 *
 *   正确做法：
 *     1. 系统 UID（< 10000）优先保留已知系统名，不被 packages.list 覆盖
 *     2. 应用 UID（>= 10000）才用 packages.list 覆盖
 *     3. 同一 uid 多个包时，取【第一个】（通常是主包）
 */
static void load_pkg_from_list(void) {
    FILE *f = fopen("/data/system/packages.list", "r");
    if (!f) return;
    char line[512];
    while (fgets(line, sizeof line, f)) {
        char pkg[NAMEBUF];
        int uid;
        if (sscanf(line, "%255s %d", pkg, &uid) != 2) continue;
        if (uid <= 0) continue;

        int i = find_uid(uid);
        if (i < 0) continue;

        /* 系统 UID（< 10000）不覆盖 —— 它们有固定语义（root/system/radio/wifi…），
           而且 packages.list 里往往是共享 UID 的第三方应用，覆盖会造成误标。 */
        if (uid < 10000) continue;

        /* 已经是【可信包名】就保留第一个（主包），不覆盖。
           注意用 pkg_is_valid 而不是只看含 '.' —— 见 pkg_is_valid 注释里
           的截断名污染案例（.cacertservice / .qms / .qtidataservices）。 */
        if (pkg_is_valid(g_u[i].pkg)) continue;

        strncpy(g_u[i].pkg, pkg, NAMEBUF - 1);
        g_u[i].pkg[NAMEBUF - 1] = 0;
    }
    fclose(f);
}

/* ---------- 电池 ---------- */

typedef struct {
    long long charge_uah;
    int  capacity;
    char status[32];
} bat_t;

static void read_battery(bat_t *b) {
    b->charge_uah = read_ll("/sys/class/power_supply/battery/charge_counter");
    long long c = read_ll("/sys/class/power_supply/battery/capacity");
    b->capacity = (c < 0) ? 0 : (int)c;
    if (read_first_line("/sys/class/power_supply/battery/status", b->status, sizeof b->status) != 0)
        strcpy(b->status, "?");
}

/* ---------- 写快照 ---------- */

/*
 * 系统 UID 的标识。
 *
 * ===== 教训：不要硬编码 AOSP 的 UID 语义 =====
 *
 * 我按 AOSP 文档写了一张 uid→名称 表（1000=system, 1041=security…），
 * 实战踩了两次大坑：
 *
 *   ① uid 1041 在这台小米上是 audioserver / audiohalservice.qti，
 *      而我按 AOSP 写成 "security" → UI 上出现莫名其妙的「安全」条目，
 *      用户以为是安全服务突然吃电。
 *
 *   ② uid 1077 在 AOSP 是 context_hub，真机是别的服务。
 *
 * 根因：OEM 会重新分配 UID。小米/高通在 AOSP 基础上有大量改动，
 *       1000-1100 区间几乎认不全。
 *
 * ===== 现在的策略 =====
 *   只保留【绝对确定】的少数几个（0/1000/2000），
 *   其余全部走 /proc/<pid>/status 反查真实进程名。
 *   查不到就诚实显示 "sys:uid<N>"，绝不猜。
 *
 * 这条规则适用于任何 Android 功耗工具：
 *   宁可显示 uid 数字，也不要显示一个错的语义名。
 */
static const char *sys_uid_name(int uid) {
    switch (uid) {
        case 0:    return "sys:kernel";      /* 绝对确定：内核 */
        case 1000: return "sys:system";      /* 绝对确定：system_server */
        case 2000: return "sys:adb_shell";   /* 绝对确定：adbd shell */
        default:   return NULL;              /* 其余一律反查，不猜 */
    }
}

/*
 * 从 /proc/<pid>/status 的 Name: 字段取进程名。
 *
 * ===== 为什么要挑，而不是取第一个 =====
 *   同一个 UID 常有多个进程：
 *     uid 1041 → audiohalservice.qti（HAL）+ audioserver（框架）
 *     uid 3003 → 几十个应用进程
 *   直接取第一个会出现 "binder:1141_2"、"android.hardwar" 这种截断名。
 *
 * 优先级：
 *   1) 不含 ':' 的（排除 binder:xxx_2 这类）
 *   2) 含 '.' 的（服务名通常带 .qti / .service 后缀）
 *   3) 长度 >= 4 的（排除短垃圾名）
 *   4) 都不满足就用第一个
 */
static const char *proc_name_of_uid(int uid) {
    static char best[NAMEBUF];
    static char fallback[NAMEBUF];      /* 必须是 static —— 见下 */
    DIR *d = opendir("/proc");
    if (!d) return NULL;
    best[0] = 0;
    fallback[0] = 0;                    /* 每次调用要重置 */
    int bestScore = -1;

    struct dirent *e;
    char path[128], line[LINEBUF];
    while ((e = readdir(d))) {
        if (!isdigit((unsigned char)e->d_name[0])) continue;
        int pid = atoi(e->d_name);
        if (pid <= 0) continue;
        snprintf(path, sizeof path, "/proc/%d/status", pid);
        FILE *f = fopen(path, "r");
        if (!f) continue;
        int u = -1;
        char nm[64] = {0};
        while (fgets(line, sizeof line, f)) {
            if (u < 0 && strncmp(line, "Uid:", 4) == 0) {
                sscanf(line + 4, "%d", &u);
            } else if (nm[0] == 0 && strncmp(line, "Name:", 5) == 0) {
                sscanf(line + 5, "%63s", nm);
            }
            if (u >= 0 && nm[0]) break;
        }
        fclose(f);
        if (u != uid || !nm[0]) continue;

        if (fallback[0] == 0) snprintf(fallback, sizeof fallback, "%s", nm);

        /* 打分 */
        int score = 0;
        if (!strchr(nm, ':')) score += 10;      /* 排除 binder:xxx */
        if (strchr(nm, '.'))  score += 5;       /* 服务名常见 */
        if (strlen(nm) >= 4)  score += 2;
        if (strlen(nm) >= 8)  score += 1;
        /* 惩罚明显是截断的（Linux Name 字段最多 15 字符，超长会被截） */
        if (strlen(nm) >= 15) score -= 3;
        /* 以 '.' 开头只可能是截断残留的后缀（.cacertservice / .qms）。
           短名（<=14 字符）不会触发上面的长度惩罚，必须单独拦。 */
        if (nm[0] == '.') score -= 50;
        if (strstr(nm, "hardwar") && strlen(nm) <= 12) score -= 5;

        if (score > bestScore) {
            bestScore = score;
            snprintf(best, sizeof best, "%s", nm);
        }
    }
    closedir(d);
    if (best[0]) return best;
    return fallback[0] ? fallback : NULL;
}

/* 读一个 sysfs 节点里的十进制整数；失败返回 -1。 */
static int read_int(const char *path) {
    long long v = read_ll(path);
    if (v < 0 || v > 2147483647LL) return -1;
    return (int)v;
}

/*
 * 发现并读取 cpufreq policy。
 *
 * 不硬编码 policy0/policy6：不同 SoC 的 policy 划分不同。
 * 本机 SM8750 实测是 policy0(CPU0-5) + policy6(CPU6-7)，
 * 别的平台可能是 4 个 policy，也可能是 cluster 混合。只枚举实际目录。
 */
static void load_cpu_state(void) {
    g_pn = 0;
    g_online = -1;
    g_cpu_count = -1;
    g_online_buf[0] = 0;

    const char *root = "/sys/devices/system/cpu/cpufreq";
    DIR *d = opendir(root);
    if (d != NULL) {
        struct dirent *e;
        while ((e = readdir(d)) != NULL && g_pn < MAXPOL) {
            if (strncmp(e->d_name, "policy", 6) != 0) continue;
            const char *rest = e->d_name + 6;
            int numeric = 1;
            for (const char *q = rest; *q; q++) {
                if (!isdigit((unsigned char)*q)) { numeric = 0; break; }
            }
            if (!numeric || rest[0] == 0) continue;   /* 排除 policy_devfreq 等 */

            policyrec *p = &g_p[g_pn];
            memset(p, 0, sizeof *p);
            snprintf(p->name, sizeof p->name, "%s", e->d_name);

            char path[256];
            snprintf(path, sizeof path, "%s/%s/scaling_cur_freq", root, e->d_name);
            p->cur_khz = read_int(path);
            snprintf(path, sizeof path, "%s/%s/scaling_min_freq", root, e->d_name);
            p->min_khz = read_int(path);
            snprintf(path, sizeof path, "%s/%s/scaling_max_freq", root, e->d_name);
            p->max_khz = read_int(path);

            snprintf(path, sizeof path, "%s/%s/related_cpus", root, e->d_name);
            char cpus[64];
            if (read_first_line(path, cpus, sizeof cpus) == 0) {
                size_t k = 0;
                for (size_t j = 0; cpus[j] && k + 1 < sizeof p->cpus; j++) {
                    p->cpus[k++] = cpus[j] == ' ' ? '_' : cpus[j];
                }
                p->cpus[k] = 0;
            }

            /* 三个都读不到就没有记录价值 */
            if (p->cur_khz < 0 && p->min_khz < 0 && p->max_khz < 0) continue;
            g_pn++;
        }
        closedir(d);
    }

    if (read_first_line("/sys/devices/system/cpu/online", g_online_buf, sizeof g_online_buf) == 0) {
        for (char *q = g_online_buf; *q; q++) {
            if (*q == ' ') *q = '_';
        }
        g_online = 0;
    }

    /* possible 形如 "0-7"，只用来算核心总数 */
    char possible[64];
    if (read_first_line("/sys/devices/system/cpu/possible", possible, sizeof possible) == 0) {
        int lo = -1, hi = -1;
        if (sscanf(possible, "%d-%d", &lo, &hi) == 2 && hi >= lo && hi < 4096) {
            g_cpu_count = hi - lo + 1;
        } else if (sscanf(possible, "%d", &lo) == 1 && lo >= 0) {
            g_cpu_count = 1;
        }
    }
}

static int write_snapshot(const char *store, int verbose) {
    long long ts = now_ms();
    g_n = 0;
    load_uid_cputime();
    load_uid_io();
    load_pkg_names();
    load_pkg_from_list();   /* packages.list 覆盖，权威映射 */

    bat_t b;
    read_battery(&b);
    load_cpu_state();

    int existed = (access(store, F_OK) == 0);
    FILE *f = fopen(store, "a");
    if (!f) {
        fprintf(stderr, "plcollect: 打不开 %s: %s\n", store, strerror(errno));
        return 1;
    }

    /* 若文件末尾没有换行，先补一个 */
    if (existed) {
        /* 已用 "a" 模式打开，无法回读；用 stat 大小做启发式，交给读取端容错 */
    }

    for (int i = 0; i < g_n; i++) {
        uidrec *u = &g_u[i];

        /*
         * UID 命名的三级回退（顺序很重要）：
         *
         *   1) 绝对确定的固定名（0 / 1000 / 2000）
         *   ② 有真实包名（含 '.'）→ 保留
         *   ③ 没有 → 从 /proc/<pid>/status 反查真实进程名
         *   ④ 还查不到 → 诚实显示 "sys:uid<N>"
         *
         * ===== 为什么不能按 AOSP 硬编码 =====
         *   实测这台小米：uid 1041 是 audioserver，而 AOSP 文档写的是 security。
         *   硬编码导致 UI 出现「安全」条目 —— 完全错误的归因。
         *   宁可显示 uid 数字，也不要显示错的名字。
         */
        const char *sn = sys_uid_name(u->uid);
        if (sn != NULL) {
            /* 1) 绝对确定的 */
            strncpy(u->pkg, sn, NAMEBUF - 1);
            u->pkg[NAMEBUF - 1] = 0;
        } else if (pkg_is_valid(u->pkg)) {
            /*
             * 2) 有真实【包名】，保留。
             *
             * 排除项统一收敛到 pkg_is_valid()：
             *   - 以 '/' 开头：/vendor/bin/hw/audiohalservice.qti 是二进制路径
             *   - 以 '.' 开头：内核 Name: 截断残留（.cacertservice 等）
             *   - "proc:" / "sys:" 内部标记
             */
        } else {
            /* 3) 反查真实进程名 */
            const char *pn = proc_name_of_uid(u->uid);
            /*
             * "/proc/<pid>/status: Name:" 被内核截断到 15 字符，
             * 截断后可能只剩一个后缀，例如：
             *   vendor.qti.hardware.cacert.server -> .cacertservice
             *   vendor.qti.data.txpwradmin        -> .qms
             *   vendor.qti.iwlan                  -> .qtidataservices
             * 这类值以 '.' 开头，不带 "proc:" 前缀直接当名字显示，
             * 会比 uid 数字更容易误导人。宁可退回 sys:uidN。
             */
            if (pn != NULL && pn[0] && pn[0] != '.' && strchr(pn, ':') == NULL) {
                snprintf(u->pkg, NAMEBUF, "proc:%s", pn);
            } else {
                /* 4) 诚实兜底 */
                snprintf(u->pkg, NAMEBUF, "sys:uid%d", u->uid);
            }
        }

        unsigned long long cpu_ms = (u->utime_us + u->stime_us) / 1000ULL;
        /* 13 列，与原格式对齐：
           ts uid pkg cpuMs cpuOffMs wlMs wlOffMs rx tx mobileMahX100 fgMs bgMs fgsMs
           uid_cputime 无息屏/唤醒锁维度，这几列填 0，由 UI 用 cpu 主口径 */
        fprintf(f, "%lld\t%d\t%s\t%llu\t0\t0\t0\t%llu\t%llu\t0\t0\t0\t0\n",
                ts, u->uid, u->pkg, cpu_ms, u->rchar, u->wchar);
    }
    fprintf(f, "%lld\t*\tbattery\t%lld\t%d\t%s\t0\t0\t0\t0\t0\t0\n",
            ts, b.charge_uah < 0 ? 0 : b.charge_uah, b.capacity, b.status);

    /*
     * CPU 快照行（每 policy 一行）：
     *   ts  *  cpu  <policy>  <cur_khz>  <min_khz>  <max_khz>  <cpus>  <online>  <core数>
     *
     * 这是只读快照：min/max 是系统当时的限频，不是本工具设置的。
     * 读取端只取最后一批，用于回答"此刻频率/限频大概是多少"。
     */
    for (int i = 0; i < g_pn; i++) {
        policyrec *p = &g_p[i];
        fprintf(f, "%lld\t*\tcpu\t%s\t%d\t%d\t%d\t%s\t%s\t%d\n",
                ts, p->name, p->cur_khz, p->min_khz, p->max_khz,
                p->cpus[0] ? p->cpus : "-",
                g_online >= 0 ? g_online_buf : "-",
                g_cpu_count);
    }
    fclose(f);

    if (verbose) {
        printf("plcollect: %d UID, %d policy, charge=%lld uAh (%d%%), status=%s\n",
               g_n, g_pn, b.charge_uah, b.capacity, b.status);
    }
    return 0;
}

/* ---------- 电量核算（与 Java 版 Storage.summarize 一致） ---------- */

static void status_report(const char *store) {
    FILE *f = fopen(store, "r");
    if (!f) { printf("没有数据文件: %s\n", store); return; }

    char line[LINEBUF];
    long long prev_ts = 0, prev_uah = 0;
    int prev_dis = 0, have_prev = 0;
    long long discharge = 0, charge = 0, discharge_ms = 0;
    int points = 0, jumped = 0, skipped = 0;
    long long first_ts = 0, last_ts = 0;

    while (fgets(line, sizeof line, f)) {
        /* 只处理电池行：第 2 列为 "*"，第 3 列为 "battery" */
        char *p1 = strchr(line, '\t');
        if (!p1) continue;
        if (p1[1] != '*') continue;
        if (strncmp(p1 + 2, "\tbattery", 8) != 0) continue;
        /* 分列：ts | * | battery | chargeUah | capacity | status | 0 | ... */
        long long ts = atoll(line);
        char *fld[8] = {0};
        int nf = 0;
        fld[nf++] = line;                       /* ts */
        for (char *q = p1 + 1; *q && nf < 8; ) {
            fld[nf++] = q;
            char *t = strchr(q, '\t');
            if (!t) break;
            *t = 0;
            q = t + 1;
        }
        if (nf < 6) continue;
        long long uah = atoll(fld[3]);
        if (uah <= 0) continue;
        /* 单位自适应：老格式（PowerLens 1.0）第 4 列写的是 mAh（<10 万），
           新格式（1.2 起）写的是 µAh（≥10 万）。统一换算成 µAh。 */
        if (uah < 100000LL) uah *= 1000LL;
        char st[32] = "?";
        int n = (int)strcspn(fld[5], "\t\r\n");
        if (n > 30) n = 30;
        memcpy(st, fld[5], n);
        st[n] = 0;
        int dis = (strcasecmp(st, "Discharging") == 0 || strcasecmp(st, "Not charging") == 0);
        /* 老格式没有状态列（写的是 "0"），此时不能判为充电，否则放电全被丢弃 */
        int unknown = (st[0] == '0' || st[0] == '?' || st[0] == 0);

        if (!have_prev) {
            first_ts = ts;
            prev_ts = ts; prev_uah = uah; prev_dis = dis; have_prev = 1;
            points++;
            continue;
        }
        last_ts = ts;
        long long dt_min = (ts - prev_ts) / 60000LL;
        if (dt_min < 1) dt_min = 1;
        long long d = prev_uah - uah;
        long long interval_ms = ts - prev_ts;
        if (interval_ms < 0) interval_ms = 0;
        long long cap = 15000LL * dt_min;
        if (cap < 50000LL) cap = 50000LL;
        int in_discharge = dis || prev_dis || unknown;

        if (d > 0) {
            if (d > cap) { jumped++; }
            else if (in_discharge) {
                discharge += d;
                discharge_ms += interval_ms;
            }
            else { skipped++; }
        } else if (d < 0) {
            /* 上升 = 充电/校准。老格式无状态列时同样按充入处理 */
            charge += -d; skipped++;
        } else if (in_discharge) {
            /* 平电量计读数仍是有效的放电时长，避免只按有下降的采样点计时。 */
            discharge_ms += interval_ms;
        }
        prev_ts = ts; prev_uah = uah; prev_dis = dis;
        points++;
    }
    fclose(f);

    printf("采样点        : %d\n", points);
    printf("真实放电      : %.1f mAh\n", discharge / 1000.0);
    printf("充入          : %.1f mAh\n", charge / 1000.0);
    printf("跳过充电段    : %d\n", skipped);
    printf("跳过跳变      : %d\n", jumped);
    if (last_ts > first_ts) {
        double hours = (last_ts - first_ts) / 3600000.0;
        if (hours > 0.01) printf("总跨度        : %.2f h\n", hours);
    }
    if (discharge_ms > 0) {
        double discharge_hours = discharge_ms / 3600000.0;
        printf("有效放电时长  : %.2f h\n", discharge_hours);
        printf("平均放电电流  : %.1f mA\n", (discharge / 1000.0) / discharge_hours);
        printf("               : %.2f %%/h (按 6000mAh 估算)\n",
               (discharge / 1000.0) / discharge_hours / 60.0);
    }
}

/* ---------- main ---------- */

int main(int argc, char **argv) {
    const char *store = getenv("STORE");
    if (!store) store = DEF_STORE;

    int once = 0, status = 0, loop = 0, verbose = 1;

    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--once")) once = 1;
        else if (!strcmp(argv[i], "--status")) status = 1;
        else if (!strcmp(argv[i], "--loop")) {
            loop = (i + 1 < argc) ? atoi(argv[++i]) : 600;
            if (loop < 30) loop = 30;
        } else if (!strcmp(argv[i], "-f") && i + 1 < argc) {
            store = argv[++i];
        } else if (!strcmp(argv[i], "-q")) {
            verbose = 0;
        } else {
            fprintf(stderr,
                "plcollect 1.2\n"
                "  --once          采一次\n"
                "  --loop SEC      每 SEC 秒采一次（默认 600）\n"
                "  --status        核算现有数据\n"
                "  -f PATH         指定存储文件\n"
                "  -q              安静\n");
            return 2;
        }
    }

    if (status) { status_report(store); return 0; }
    if (loop > 0) {
        if (verbose) fprintf(stderr, "plcollect: loop %d s -> %s\n", loop, store);
        for (;;) {
            write_snapshot(store, verbose);
            sleep((unsigned)loop);
        }
    }
    return write_snapshot(store, verbose);
}
