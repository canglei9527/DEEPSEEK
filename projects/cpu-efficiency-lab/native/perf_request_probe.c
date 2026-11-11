/* Controlled recovery verification ONLY, not a sweep or benchmark.
 * API signature reference: LineageOS/android_vendor_qcom_opensource_power,
 * lineage-22.2/utils.c (perf_lock_acq/rel). Resource layout was independently
 * checked against this phone's targetresourceconfigs.xml, perfboostsconfig.xml
 * and ResourceInfo::ParseNewRequest. Candidate logical cluster mapping 0=big,
 * 2=prime is not yet established by an effective request on this device.
 * Never restore a sysfs snapshot, use raw arbitrary opcodes, or change hotplug.
 */
#define _POSIX_C_SOURCE 200809L
#include <ctype.h>
#include <dirent.h>
#include <dlfcn.h>
#include <errno.h>
#include <limits.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#define PERF_CLIENT "/vendor/lib64/libqti-perfd-client.so"
#define MAX_STEPS 64
#define TTL_MS 8000
#define CRASH_TTL_MS 3000
#define CPU_LIMIT_MC 80000
#define BATTERY_LIMIT_DECI_C 430
#define BATTERY_RESERVE 60

typedef int (*acquire_fn)(int, int, int *, int);
typedef int (*release_fn)(int);
struct policy {
    int id;
    unsigned int min_opcode, max_opcode;
    long original_min, original_max;
    long frequencies[MAX_STEPS];
    size_t count;
    long low, high;
};
static struct policy policies[] = {
    { .id = 0, .min_opcode = 0x40800000U, .max_opcode = 0x40804000U },
    { .id = 6, .min_opcode = 0x40800200U, .max_opcode = 0x40804200U },
};
static acquire_fn acquire_request;
static release_fn release_request;
static int owned[8];
static volatile sig_atomic_t interrupted;

static void stop_signal(int sig) { interrupted = sig; }
static void pause_ms(long ms) {
    struct timespec delay = { ms / 1000, (ms % 1000) * 1000000L };
    while (nanosleep(&delay, &delay) < 0 && errno == EINTR && !interrupted) { }
}
static int read_text(const char *path, char *buf, size_t length) {
    FILE *f = fopen(path, "r");
    if (!f) return -1;
    bool ok = fgets(buf, (int)length, f) != NULL;
    fclose(f);
    if (!ok) return -1;
    buf[strcspn(buf, "\r\n")] = 0;
    return 0;
}
static int read_number(const char *path, long *value) {
    char b[128], *end;
    if (read_text(path, b, sizeof b) < 0) return -1;
    errno = 0;
    long v = strtol(b, &end, 10);
    while (isspace((unsigned char)*end)) ++end;
    if (errno || end == b || *end) return -1;
    *value = v;
    return 0;
}
static int policy_number(const struct policy *p, const char *node, long *value) {
    char path[256];
    snprintf(path, sizeof path, "/sys/devices/system/cpu/cpufreq/policy%d/%s", p->id, node);
    return read_number(path, value);
}
static int safety(void) {
    long n;
    char text[128];
    if (interrupted) return -1;
    if (read_text("/sys/devices/system/cpu/online", text, sizeof text) < 0 || strcmp(text, "0-7")) return -1;
    if (read_text("/sys/class/power_supply/battery/status", text, sizeof text) < 0 || strcmp(text, "Discharging")) return -1;
    if (read_number("/sys/class/power_supply/battery/capacity", &n) < 0 || n < BATTERY_RESERVE || n > 100) return -1;
    if (read_number("/sys/class/power_supply/battery/temp", &n) < 0 || n < 0 || n >= BATTERY_LIMIT_DECI_C) return -1;
    DIR *directory = opendir("/sys/class/thermal");
    if (!directory) return -1;
    struct dirent *entry;
    int seen = 0, result = 0;
    while ((entry = readdir(directory))) {
        if (strncmp(entry->d_name, "thermal_zone", 12)) continue;
        char path[512], kind[128];
        snprintf(path, sizeof path, "/sys/class/thermal/%s/type", entry->d_name);
        if (read_text(path, kind, sizeof kind) < 0) continue;
        if (strncmp(kind, "cpu-", 4) && strncmp(kind, "cpuss-", 6) && strcmp(kind, "cpu_therm")) continue;
        snprintf(path, sizeof path, "/sys/class/thermal/%s/temp", entry->d_name);
        if (read_number(path, &n) < 0 || n < 1000 || n >= CPU_LIMIT_MC) { result = -1; break; }
        ++seen;
    }
    closedir(directory);
    return seen && result == 0 ? 0 : -1;
}
static int cleanup(void) {
    int failed = 0;
    for (size_t i = 0; i < sizeof owned / sizeof owned[0]; ++i) {
        if (owned[i] <= 0) continue;
        int handle = owned[i];
        owned[i] = 0;
        int rc = release_request ? release_request(handle) : -1;
        printf("CLEAN_RELEASE|%d|%d\n", handle, rc);
        if (rc < 0) failed = 1; /* Expired handles may legitimately be absent; report, never hide. */
    }
    return failed ? -1 : 0;
}
static int create_request(const struct policy *p, long frequency, bool lock, int ttl_ms) {
    if (safety() < 0 || frequency < p->original_min || frequency > p->original_max) return -1;
    /* perfd multiplies the MHz value by 1000 and selects the next discrete OPP.
     * Check this mapping locally instead of assuming rounding is harmless. */
    long mhz = frequency / 1000, mapped = -1;
    for (size_t i = 0; i < p->count; ++i) {
        if (p->frequencies[i] >= mhz * 1000) { mapped = p->frequencies[i]; break; }
    }
    if (mapped != frequency || mhz < 1 || mhz >= 4095) return -1;
    size_t slot;
    for (slot = 0; slot < sizeof owned / sizeof owned[0] && owned[slot]; ++slot) { }
    if (slot == sizeof owned / sizeof owned[0]) return -1;
    long current_min = -1, current_max = -1;
    if (policy_number(p, "scaling_min_freq", &current_min) < 0 ||
        policy_number(p, "scaling_max_freq", &current_max) < 0 ||
        current_min != p->original_min || frequency > current_max || current_max > p->original_max) {
        printf("REFUSED|STALE_LIMITS|%d|%ld|%ld\n", p->id, current_min, current_max);
        return -1;
    }
    int values[4] = {(int)p->max_opcode, (int)mhz, (int)p->min_opcode, (int)mhz};
    int handle = acquire_request(0, ttl_ms, values, lock ? 4 : 2);
    printf("ACQUIRE|%d|%s|%ld|%d|%d\n", p->id, lock ? "LOCK" : "CAP", frequency, ttl_ms, handle);
    if (handle <= 0) return -1;
    for (size_t i = 0; i < sizeof owned / sizeof owned[0]; ++i) {
        if (owned[i] == handle) { printf("HANDLE_COLLISION|%d\n", handle); return -1; }
    }
    owned[slot] = handle;
    return handle;
}
static int release_owned(int handle) {
    for (size_t i = 0; i < sizeof owned / sizeof owned[0]; ++i) {
        if (owned[i] != handle || handle <= 0) continue;
        int rc = release_request(handle);
        printf("RELEASE|%d|%d\n", handle, rc);
        if (rc < 0) return -1;
        owned[i] = 0;
        return 0;
    }
    return -1; /* Never release an arbitrary handle supplied by a caller. */
}
static int wait_bounds(const struct policy *p, long minimum, long maximum, bool current) {
    for (int i = 0; i < 20; ++i) {
        long lo, hi, cur;
        if (safety() < 0 || policy_number(p, "scaling_min_freq", &lo) < 0 ||
            policy_number(p, "scaling_max_freq", &hi) < 0 || policy_number(p, "scaling_cur_freq", &cur) < 0) return -1;
        printf("OBSERVE|%d|%ld|%ld|%ld\n", p->id, lo, hi, cur);
        if (i == 0 || i == 9 || i == 19) {
            char vote[1024];
            if (read_text("/sys/kernel/msm_performance/parameters/cpu_max_freq", vote, sizeof vote) == 0)
                printf("VENDOR_MAX_VOTE|%d|%d|%s\n", p->id, i, vote);
            else printf("VENDOR_MAX_VOTE_UNAVAILABLE|%d|%d\n", p->id, i);
            if (read_text("/sys/kernel/msm_performance/parameters/cpu_min_freq", vote, sizeof vote) == 0)
                printf("VENDOR_MIN_VOTE|%d|%d|%s\n", p->id, i, vote);
            else printf("VENDOR_MIN_VOTE_UNAVAILABLE|%d|%d\n", p->id, i);
        }
        if ((minimum < 0 || lo == minimum) && hi == maximum && (!current || cur == maximum)) return 0;
        pause_ms(100);
    }
    return -1;
}
static int restored(const struct policy *p) {
    int rc = wait_bounds(p, p->original_min, p->original_max, false);
    printf("RESTORED_CHECK|%d|%s\n", p->id, rc == 0 ? "PASS" : "INCONCLUSIVE");
    return rc;
}
static int initialize_policies(void) {
    long soc;
    if (read_number("/sys/devices/soc0/soc_id", &soc) < 0 || (soc != 618 && soc != 639)) return -1;
    for (size_t j = 0; j < sizeof policies / sizeof policies[0]; ++j) {
        struct policy *p = &policies[j];
        char path[256], text[4096], *at, *end;
        if (policy_number(p, "scaling_min_freq", &p->original_min) < 0 ||
            policy_number(p, "scaling_max_freq", &p->original_max) < 0) return -1;
        snprintf(path, sizeof path, "/sys/devices/system/cpu/cpufreq/policy%d/related_cpus", p->id);
        if (read_text(path, text, sizeof text) < 0) return -1;
        char normalized[128] = "";
        at = text;
        size_t length = 0;
        while (*at) {
            if (!isspace((unsigned char)*at)) {
                if (length + 1 >= sizeof normalized) return -1;
                normalized[length++] = *at;
            }
            ++at;
        }
        normalized[length] = 0;
        if (strcmp(normalized, p->id == 0 ? "012345" : "67")) return -1;
        snprintf(path, sizeof path, "/sys/devices/system/cpu/cpufreq/policy%d/scaling_available_frequencies", p->id);
        if (read_text(path, text, sizeof text) < 0) return -1;
        at = text;
        while (*at) {
            while (isspace((unsigned char)*at)) ++at;
            if (!*at) break;
            errno = 0;
            long f = strtol(at, &end, 10);
            if (errno || end == at || f <= 0 || p->count >= MAX_STEPS ||
                (p->count && f <= p->frequencies[p->count - 1])) return -1;
            p->frequencies[p->count++] = f;
            at = end;
        }
        long candidates[MAX_STEPS];
        size_t count = 0;
        for (size_t i = 0; i < p->count; ++i) {
            long f = p->frequencies[i];
            if (f > p->original_min && f < p->original_max) candidates[count++] = f;
        }
        if (count < 2) return -1;
        p->low = candidates[0];
        p->high = candidates[1];
        printf("BASELINE|%d|%ld|%ld|%ld|%ld\n", p->id, p->original_min, p->original_max, p->low, p->high);
    }
    return 0;
}
static void log_service_request(int handle) {
    /* Read-only diagnostic: a returned handle does not prove the daemon has
     * applied a resource. Capture only the line referring to our own handle. */
    char needle[32], line[2048];
    snprintf(needle, sizeof needle, "%d", handle);
    FILE *f = popen("dumpsys -t 2 vendor.perfservice 2>&1", "r");
    if (!f) { printf("SERVICE_DUMP|UNAVAILABLE\n"); return; }
    bool found = false;
    while (fgets(line, sizeof line, f)) {
        if (strstr(line, needle) && strstr(line, "|")) {
            line[strcspn(line, "\r\n")] = 0;
            printf("SERVICE_REQUEST|%s\n", line);
            found = true;
        }
    }
    int rc = pclose(f);
    printf("SERVICE_DUMP|%s|%d\n", found ? "FOUND" : "NOT_FOUND", rc);
}
static int verify_policy(struct policy *p) {
    /* The stricter cap must survive release of the looser request. */
    int a = create_request(p, p->high, false, TTL_MS);
    if (a < 0) return -1;
    if (p->id == 0) log_service_request(a);
    if (wait_bounds(p, -1, p->high, false) < 0) return -1;
    int b = create_request(p, p->low, false, TTL_MS);
    if (b < 0 || wait_bounds(p, -1, p->low, false) < 0 || release_owned(a) < 0 ||
        wait_bounds(p, -1, p->low, false) < 0 || release_owned(b) < 0) return -1;
    pause_ms(400);
    if (restored(p) < 0) return -1;
    printf("CASE|%d|RELEASE_LOOSER|PASS\n", p->id);
    /* Releasing the stricter request must reveal the remaining looser request. */
    a = create_request(p, p->high, false, TTL_MS);
    if (a < 0 || wait_bounds(p, -1, p->high, false) < 0) return -1;
    b = create_request(p, p->low, false, TTL_MS);
    if (b < 0 || wait_bounds(p, -1, p->low, false) < 0 || release_owned(b) < 0 ||
        wait_bounds(p, -1, p->high, false) < 0 || release_owned(a) < 0) return -1;
    pause_ms(400);
    if (restored(p) < 0) return -1;
    printf("CASE|%d|RELEASE_STRICTER|PASS\n", p->id);
    a = create_request(p, p->low, true, TTL_MS);
    if (a < 0 || wait_bounds(p, p->low, p->low, true) < 0 || release_owned(a) < 0 || restored(p) < 0) return -1;
    printf("CASE|%d|LOCK_AND_RELEASE|PASS\n", p->id);
    return 0;
}
int main(int argc, char **argv) {
    setvbuf(stdout, NULL, _IOLBF, 0);
    bool probe = argc == 2 && !strcmp(argv[1], "--probe");
    bool verify = argc == 3 && !strcmp(argv[1], "--verify") && !strcmp(argv[2], "--confirm-device-writes");
    bool crash = argc == 3 && !strcmp(argv[1], "--crash-test") && !strcmp(argv[2], "--confirm-device-writes");
    if (!probe && !verify && !crash) {
        fprintf(stderr, "usage: perf_request_probe --probe | (--verify|--crash-test) --confirm-device-writes\n");
        return 2;
    }
    signal(SIGINT, stop_signal);
    signal(SIGTERM, stop_signal);
    signal(SIGHUP, stop_signal);
    void *library = dlopen(PERF_CLIENT, RTLD_NOW | RTLD_LOCAL);
    if (!library) { printf("UNAVAILABLE|%s\n", dlerror()); return 2; }
    *(void **)(&acquire_request) = dlsym(library, "perf_lock_acq");
    *(void **)(&release_request) = dlsym(library, "perf_lock_rel");
    if (!acquire_request || !release_request) { printf("UNAVAILABLE|missing symbols\n"); dlclose(library); return 2; }
    printf("API|perf_lock_acq|perf_lock_rel|RESOLVED\n");
    if (probe) { dlclose(library); return 0; }
    if (safety() < 0 || initialize_policies() < 0) { printf("REFUSED|preflight\n"); dlclose(library); return 2; }
    if (crash) {
        int handle = create_request(&policies[0], policies[0].low, false, CRASH_TTL_MS);
        if (handle > 0 && wait_bounds(&policies[0], -1, policies[0].low, false) == 0) {
            printf("CRASH_ARMED|%ld|%d|%d\n", (long)getpid(), handle, CRASH_TTL_MS);
            fflush(stdout);
            raise(SIGKILL); /* Finite daemon-side timeout must release, not a local EXIT trap. */
        }
    } else {
        bool passed = true;
        for (size_t i = 0; i < sizeof policies / sizeof policies[0]; ++i) {
            if (verify_policy(&policies[i]) < 0) { passed = false; break; }
        }
        if (cleanup() < 0) passed = false;
        for (size_t i = 0; i < sizeof policies / sizeof policies[0]; ++i) {
            if (restored(&policies[i]) < 0) passed = false;
        }
        printf("VERIFY_END|%s\n", passed ? "PASS" : "INCONCLUSIVE");
        dlclose(library);
        return passed ? 0 : 2;
    }
    cleanup();
    printf("CRASH_NOT_ARMED\n");
    dlclose(library);
    return 2;
}
