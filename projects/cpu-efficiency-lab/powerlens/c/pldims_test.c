/*
 * pldims_test.c —— pldims 解析层的回归单测（主机侧跑，不需要手机）
 *
 * ===== 为什么必须存在 =====
 *   这轮修掉的 9 个 bug，全都【不报错】：程序正常跑完、正常写文件、
 *   数字看起来完全合法，只有拿去和 dumpsys 原文对才发现错了。
 *   最典型的是 parse_dur 越界：fg 报 6h37m（真值 43m42s），
 *   虚高 9 倍，界面上就是一条平平无奇的"耗电最多"记录。
 *   靠肉眼看不出来，靠线上数据对不上（dumpsys 每次重跑都变）。
 *
 *   所以：每一条踩过的坑都固化成一个断言，改解析代码前必须在主机上过。
 *
 * ===== 怎么跑 =====
 *   powershell -File build-test.ps1        （用 NDK 自带的 clang 编主机二进制）
 *   .\out\pldims_test.exe
 *   退出码 0 = 全过。
 */
#include <stdio.h>
#include <string.h>
#include <stdlib.h>

/* 由 pldims.c 暴露 */
long test_parse_dur_bounded(const char *s, const char *end);
long test_paren_after(const char *line, const char *key);
void test_parse_mah(const char *line, long long *out12);
void test_parse_global(const char *line, long long *out12);
char *test_skip_cols(char *line, int n);

enum {
    MAH_CPU = 0, MAH_SCREEN, MAH_RADIO, MAH_WL, MAH_WIFI, MAH_AUDIO,
    MAH_VIDEO, MAH_SENSORS, MAH_CAMERA, MAH_GNSS, MAH_DISPLAY, MAH_OTHER,
    MAH_N
};

static int g_fail = 0;
static int g_n = 0;

static void eq_l(const char *what, long got, long want) {
    g_n++;
    if (got != want) {
        printf("  FAIL %s: got %ld want %ld\n", what, got, want);
        g_fail++;
    } else {
        printf("  ok   %s = %ld\n", what, got);
    }
}

static void eq_ll(const char *what, long long got, long long want) {
    g_n++;
    if (got != want) {
        printf("  FAIL %s: got %lld want %lld\n", what, got, want);
        g_fail++;
    } else {
        printf("  ok   %s = %lld\n", what, got);
    }
}

static void eq_s(const char *what, const char *got, const char *want) {
    g_n++;
    if (!got || strcmp(got, want) != 0) {
        printf("  FAIL %s: got '%s' want '%s'\n",
               what, got ? got : "(null)", want);
        g_fail++;
    } else {
        printf("  ok   %s -> %s\n", what, got);
    }
}

/*
 * skip_cols 返回的是【该行剩余的全部内容】，不是单独一列，
 * 所以只能用前缀比较。用 strcmp 会永远失败（"i8\tj9\t..." != "i8"）。
 * 单测自己也要有正确的断言方式，否则会把"测试失败"误读成"代码有 bug"。
 */
static void eq_prefix(const char *what, const char *got, const char *want) {
    g_n++;
    size_t wl = strlen(want);
    if (!got || strncmp(got, want, wl) != 0) {
        printf("  FAIL %s: got '%s' want prefix '%s'\n",
               what, got ? got : "(null)", want);
        g_fail++;
    } else {
        printf("  ok   %s -> %s\n", what, want);
    }
}

int main(void) {
    printf("== 1. parse_dur_bounded：必须在右括号处停住 ==\n");
    /*
     * 这是本轮的【头号 bug】。旧实现一路读到 '\0'，
     * 于是把同一行后面所有字段的时长全加起来。
     * 用原文的那一行做断言：43m42s223ms，绝不是 6h37m。
     */
    {
        const char *line =
            "fg: 545 (43m 42s 223ms) bg: 34.3 (15s 496ms) "
            "fgs: 184 (5h 58m 53s 606ms) cached: 0.0195 (98ms)";
        const char *open = strstr(line, "(43m");
        const char *close = strchr(open, ')');
        eq_l("fg 有界解析", test_parse_dur_bounded(open + 1, close), 2622223L);
        /* 故意不设边界 -> 必须明显变大，证明边界真的起作用 */
        long unbounded = test_parse_dur_bounded(open + 1, open + strlen(open));
        g_n++;
        if (unbounded <= 2622223L) {
            printf("  FAIL 越界值未体现: %ld\n", unbounded);
            g_fail++;
        } else {
            printf("  ok   越界会得 %ld ms（旧代码就是这个 9 倍值）\n", unbounded);
        }
    }

    printf("\n== 2. paren_after：字段没有时长时必须返回 0 ==\n");
    {
        /*
         * "bg: 1.46" 后面【没有括号】。
         * 旧代码用 strchr(k,'(') 往后找，会找到下一个字段 fgs 的 (7h 51m)，
         * 于是 bg 被灌成 7h51m。
         */
        const char *line = "30.3 fg: 1.47 (5m 44s) bg: 1.46 fgs: 16.9 (7h 51m)";
        eq_l("bg 无时长 -> 0", test_paren_after(line, "bg:"), 0L);
        eq_l("fg 有时长", test_paren_after(line, "fg:"), 344000L);
        eq_l("fgs 有时长", test_paren_after(line, "fgs:"), 28260000L);
    }

    printf("\n== 3. paren_after：前缀冲突要认词边界 ==\n");
    {
        /*
         * 明细行同时有 "cpu=94.2" 和 "cpu:fg=69.3"。
         * strstr(line,"fg=") 会命中 "cpu:fg=" 里的 fg=。
         */
        const char *line =
            "screen=71.6 cpu=94.2 (44m 10s) cpu:fg=69.3 (43m 42s) cpu:bg=4.55 (39s 686ms)";
        eq_l("总 cpu 时长", test_paren_after(line, "cpu="), 2650000L);
        eq_l("cpu:bg 时长", test_paren_after(line, "cpu:bg="), 39686L);
        /* 关键：查 "bg=" 不能落到 "cpu:bg=" 上 */
        eq_l("独立 bg= 不存在 -> 0", test_paren_after(line, "bg="), 0L);
    }

    printf("\n== 4. parse_mah：只取不带冒号的总项 ==\n");
    {
        /*
         * u0a341 的真实明细行（原文摘录）。
         * cpu=135 是总量，cpu:fg=/cpu:bg= 是它的拆分 —— 再加会翻倍。
         */
        const char *line =
            "screen=71.6 cpu=135 (44m 10s 467ms) cpu:fg=108 (43m 42s 223ms) "
            "cpu:bg=4.55 (39s 686ms) audio=22.0 (1m 21s) video=38.2 (1m 5s) "
            "mobile_radio=558 (15m 41s 279ms) wakelock=1.74 (3m 11s 963ms)";
        long long m[MAH_N];
        test_parse_mah(line, m);
        eq_ll("cpu mAh*1000", m[MAH_CPU], 135000LL);
        eq_ll("screen", m[MAH_SCREEN], 71600LL);
        eq_ll("mobile_radio", m[MAH_RADIO], 558000LL);
        eq_ll("wakelock", m[MAH_WL], 1740LL);
        eq_ll("audio", m[MAH_AUDIO], 22000LL);
        eq_ll("video", m[MAH_VIDEO], 38200LL);
        /* cpu:fg=108 和 cpu:bg=4.55 绝不能被当成第二份 cpu 计进来 */
        g_n++;
        if (m[MAH_CPU] != 135000LL) {
            printf("  FAIL 拆分项污染了总量\n");
            g_fail++;
        }
    }

    printf("\n== 5. parse_global：key: 数值 apps: 的格式 ==\n");
    {
        const char *line = "      cpu: 2359 apps: 2356 duration: 8h 19m 48s 766ms";
        long long m[MAH_N];
        test_parse_global(line, m);
        eq_ll("Global cpu", m[MAH_CPU], 2359000LL);
        /* duration 里的 "48s 766ms" 不能被当成新的字段值 */
        eq_ll("Global screen(本行无)", m[MAH_SCREEN], 0LL);
    }
    {
        const char *line = "      screen: 560 apps: 560";
        long long m[MAH_N];
        test_parse_global(line, m);
        eq_ll("Global screen", m[MAH_SCREEN], 560000LL);
    }

    printf("\n== 6. skip_cols：strchr 会命中自身 ==\n");
    {
        /*
         * 旧写法 for(t=0;t<8;t++) col=strchr(col,'\t') 只跳过 1 个 tab，
         * 因为 strchr 匹配到它当前正指向的那个 tab。
         * 结果 mAh 数组错位 8 列，Δcpu = 135000-10341 = 124659。
         */
        char buf[] = "a0\tb1\tc2\td3\te4\tf5\tg6\th7\ti8\tj9\tk10\tl11\tm12\tn13"
                     "\to14\tp15\tq16\tr17\ts18\tt19\tu20";
        eq_prefix("跳到第 9 列", test_skip_cols(buf, 8), "i8");
        eq_prefix("跳到第 1 列", test_skip_cols(buf, 0), "a0");
        eq_prefix("跳到第 21 列", test_skip_cols(buf, 20), "u20");
        g_n++;
        if (test_skip_cols(buf, 21) != NULL) {
            printf("  FAIL 越界应返回 NULL\n");
            g_fail++;
        } else {
            printf("  ok   越界返回 NULL\n");
        }
    }

    printf("\n== 7. 真实 headline 行的端到端取值 ==\n");
    {
        const char *line =
            "  UID u0a341: 832 fg: 545 (43m 42s 223ms) bg: 34.3 (15s 496ms) "
            "fgs: 184 (5h 58m 53s 606ms) cached: 0.0195 (98ms)";
        eq_l("u0a341 fg", test_paren_after(line, "fg:"), 2622223L);
        eq_l("u0a341 bg", test_paren_after(line, "bg:"), 15496L);
        eq_l("u0a341 fgs", test_paren_after(line, "fgs:"), 21533606L);
    }

    printf("\n== 8. 息屏段：标记与数据在同一行 ==\n");
    {
        /*
         * 旧代码遇到 "(on battery, screen off/doze)" 就 continue，
         * 把同一行后面的 cpu=6.72 (2m 22s) 全丢了，cpuOff 永远是 0。
         */
        const char *line =
            "      (on battery, screen off/doze) cpu=8.12 (2m 22s 207ms) "
            "wakelock=0.09 (3s 619ms)";
        eq_l("cpuOff 可解析", test_paren_after(line, "cpu="), 142207L);
        eq_l("wlOff 可解析", test_paren_after(line, "wakelock="), 3619L);
        long long m[MAH_N];
        test_parse_mah(line, m);
        eq_ll("息屏段 cpu mAh", m[MAH_CPU], 8120LL);
    }

    printf("\n=================================\n");
    if (g_fail == 0) {
        printf("全部通过：%d 条断言\n", g_n);
        return 0;
    }
    printf("失败 %d / %d 条断言\n", g_fail, g_n);
    return 1;
}
