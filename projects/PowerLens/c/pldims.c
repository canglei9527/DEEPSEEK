// pldims.c -- PowerLens 维度补全器 v2（增量版）
//
// ===== v1 的问题 =====
//   v1 输出的是 dumpsys 的【累计值】（开机以来）。
//   用它排序 = "开机以来谁在前台待得久"，和"这 1 小时谁在耗电"毫无关系。
//   实测：Σfg = 290 小时，单个 UID 6-7 小时，比例全被摊平。
//
// ===== v2 的做法 =====
//   每次运行保存上一份累计值（dims.prev），本次算出【增量】再写 dims.tsv：
//     Δfg = fg_now - fg_prev
//   这样 dims.tsv 里的数就是"距上次采样这段时间内的前台/后台时长"，
//   与 snapshots.tsv 的区间增量口径一致，可以直接排序。
//
//   dims.prev 里同时存时间戳，用于判断采样间隔。
//
// ===== 输出格式 =====
//   dims.tsv:    <epochMs> <uid> <ΔfgMs> <ΔbgMs> <ΔfgsMs> <ΔcpuOffMs> <ΔwlMs> <ΔwlOffMs> <intervalMs>
//   dims.prev:   <epochMs> <uid> <fgMs> <bgMs> <fgsMs> <cpuOffMs> <wlMs> <wlOffMs>   (累计)
//
// ===== 首次运行 =====
//   没有 prev 时只写 prev，dims.tsv 留空（App 会提示"维度数据不足"）。
//   第二次起才有增量。

#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <time.h>
#include <ctype.h>
#include <errno.h>

#define MAXU    2048
#define LINEBUF 1024
#define DEF_DIR "/data/local/tmp/powerlens"

typedef struct {
    int  uid;
    long fgMs, bgMs, fgsMs, cpuOffMs, wlMs, wlOffMs;
} dimrec;

static dimrec g_cur[MAXU];   /* 本次累计 */
static int    g_cn = 0;
static dimrec g_prev[MAXU];  /* 上次累计 */
static int    g_pn = 0;
static long long g_prevTs = 0;

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

static long parse_dur(const char *s) {
    long ms = 0, v = 0;
    int inNum = 0;
    const char *p = s;
    while (*p) {
        if (isdigit((unsigned char)*p)) { v = v * 10 + (*p - '0'); inNum = 1; }
        else if (inNum) {
            if (p[0] == 'h')                     { ms += v * 3600000L; p++; }
            else if (p[0] == 'm' && p[1] == 's') { ms += v;            p += 2; }
            else if (p[0] == 'm')                { ms += v * 60000L;    p++; }
            else if (p[0] == 's')                { ms += v * 1000L;     p++; }
            else                                  { p++; }
            v = 0; inNum = 0; continue;
        }
        p++;
    }
    return ms;
}

static long paren_after(const char *line, const char *key) {
    const char *k = strstr(line, key);
    if (!k) return 0;
    const char *lp = strchr(k, '(');
    if (!lp) return 0;
    const char *rp = strchr(lp, ')');
    if (!rp) return 0;
    return parse_dur(lp + 1);
}

static void run_dumpsys(void) {
    FILE *f = popen("/system/bin/dumpsys batterystats --charged 2>/dev/null", "r");
    if (!f) return;
    char line[LINEBUF];
    int cur = -1, inOffDoz = 0;

    while (fgets(line, sizeof line, f)) {
        char *u = strstr(line, "  UID ");
        if (u && strchr(u, ':')) {
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
            if (cur >= 0) {
                g_cur[cur].fgMs  += paren_after(line, "fg:");
                g_cur[cur].bgMs  += paren_after(line, "bg:");
                g_cur[cur].bgMs  += paren_after(line, "cached:");
                g_cur[cur].fgsMs += paren_after(line, "fgs:");
            }
            continue;
        }
        if (cur < 0) continue;

        if (strstr(line, "(on battery, screen off/doze)"))      inOffDoz = 1;
        else if (strstr(line, "(on battery, screen on)"))       inOffDoz = 0;
        else if (strstr(line, "(on battery,"))                  inOffDoz = (strstr(line, "off") || strstr(line, "doze")) ? 1 : 0;

        int hasCpu = strstr(line, "cpu=") != NULL;
        int hasWl  = strstr(line, "wakelock=") != NULL;
        if (!hasCpu && !hasWl) continue;

        if (hasCpu) { long d = paren_after(line, "cpu=");      if (inOffDoz) g_cur[cur].cpuOffMs += d; }
        if (hasWl)  { long d = paren_after(line, "wakelock="); if (inOffDoz) g_cur[cur].wlOffMs += d; else g_cur[cur].wlMs += d; }
    }
    pclose(f);
}

static int load_prev(const char *path) {
    FILE *f = fopen(path, "r");
    if (!f) return 0;
    char line[LINEBUF];
    int n = 0;
    while (fgets(line, sizeof line, f)) {
        long long ts;
        int uid;
        long a, b, c, d, e, g;
        if (sscanf(line, "%lld\t%d\t%ld\t%ld\t%ld\t%ld\t%ld\t%ld",
                   &ts, &uid, &a, &b, &c, &d, &e, &g) == 8) {
            if (g_prevTs == 0) g_prevTs = ts;
            if (n < MAXU) {
                g_prev[n].uid = uid;
                g_prev[n].fgMs = a; g_prev[n].bgMs = b; g_prev[n].fgsMs = c;
                g_prev[n].cpuOffMs = d; g_prev[n].wlMs = e; g_prev[n].wlOffMs = g;
                n++;
            }
        }
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
        fprintf(f, "%lld\t%d\t%ld\t%ld\t%ld\t%ld\t%ld\t%ld\n",
                ts, d->uid, d->fgMs, d->bgMs, d->fgsMs, d->cpuOffMs, d->wlMs, d->wlOffMs);
    }
    fclose(f);
    return 1;
}

int main(int argc, char **argv) {
    char out[256], prev[256];
    snprintf(out,  sizeof out,  "%s/dims.tsv",  DEF_DIR);
    snprintf(prev, sizeof prev, "%s/dims.prev", DEF_DIR);

    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "-f") && i + 1 < argc) {
            snprintf(out, sizeof out, "%s", argv[++i]);
        } else if (!strcmp(argv[i], "-p") && i + 1 < argc) {
            snprintf(prev, sizeof prev, "%s", argv[++i]);
        } else if (!strcmp(argv[i], "-d") && i + 1 < argc) {
            snprintf(out,  sizeof out,  "%s/dims.tsv",  argv[++i]);
            snprintf(prev, sizeof prev, "%s/dims.prev", argv[++i]);
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

    if (pn == 0) {
        /* 首次：只存基线，dims.tsv 写成空（App 会提示维度数据不足） */
        save_prev(prev, ts);
        FILE *f = fopen(out, "w");
        if (f) fclose(f);
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
        int pi = find_prev(c->uid);
        long dfg = 0, dbg = 0, dfgs = 0, doff = 0, dwl = 0, dwlo = 0;
        if (pi >= 0) {
            dimrec *p = &g_prev[pi];
            dfg  = c->fgMs     - p->fgMs;
            dbg  = c->bgMs     - p->bgMs;
            dfgs = c->fgsMs    - p->fgsMs;
            doff = c->cpuOffMs - p->cpuOffMs;
            dwl  = c->wlMs     - p->wlMs;
            dwlo = c->wlOffMs  - p->wlOffMs;
            /* 负数表示计数器重置/进程重启，钳到 0 */
            if (dfg  < 0) dfg  = 0;
            if (dbg  < 0) dbg  = 0;
            if (dfgs < 0) dfgs = 0;
            if (doff < 0) doff = 0;
            if (dwl  < 0) dwl  = 0;
            if (dwlo < 0) dwlo = 0;
        }
        if ((dfg | dbg | dfgs | doff | dwl | dwlo) == 0) continue;
        fprintf(f, "%lld\t%d\t%ld\t%ld\t%ld\t%ld\t%ld\t%ld\t%lld\n",
                ts, c->uid, dfg, dbg, dfgs, doff, dwl, dwlo, interval);
        nz++;
    }
    fclose(f);

    save_prev(prev, ts);

    fprintf(stderr, "pldims: 增量 %d/%d UID，间隔 %.1f 分钟 -> %s\n",
            nz, g_cn, interval / 60000.0, out);
    return 0;
}