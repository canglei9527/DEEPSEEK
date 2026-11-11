#define _GNU_SOURCE

#include <errno.h>
#include <inttypes.h>
#include <math.h>
#include <pthread.h>
#include <sched.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define BATCH_ITERATIONS 4096u
#define MAX_WORKERS 8
#define START_LEAD_NS 200000000LL

typedef struct {
    int cpu;
    int observed_cpu;
    double warmup_seconds;
    double measure_seconds;
    int error_number;
    uint64_t warmup_iterations;
    uint64_t measure_iterations;
    uint64_t checksum;
    int64_t end_ns;
    pthread_t thread;
} Worker;

static atomic_int start_gate = 0;
static atomic_int ready_workers = 0;
static int64_t common_start_ns;

static int64_t boot_time_ns(void) {
    struct timespec ts;
    if (clock_gettime(CLOCK_BOOTTIME, &ts) != 0) {
        perror("clock_gettime");
        exit(2);
    }
    return (int64_t)ts.tv_sec * 1000000000LL + (int64_t)ts.tv_nsec;
}

static double ns_to_seconds(int64_t value) {
    return (double)value / 1000000000.0;
}

static uint64_t mix64(uint64_t value) {
    value ^= value >> 30;
    value *= UINT64_C(0xbf58476d1ce4e5b9);
    value ^= value >> 27;
    value *= UINT64_C(0x94d049bb133111eb);
    value ^= value >> 31;
    return value;
}

static uint64_t do_batch(uint64_t state, uint64_t iteration_base) {
    for (uint64_t i = 0; i < BATCH_ITERATIONS; ++i) {
        state = mix64(state + iteration_base + i + UINT64_C(0x9e3779b97f4a7c15));
    }
    return state;
}

static void *worker_main(void *opaque) {
    Worker *worker = (Worker *)opaque;
    cpu_set_t cpus;
    CPU_ZERO(&cpus);
    CPU_SET(worker->cpu, &cpus);
    if (sched_setaffinity(0, sizeof(cpus), &cpus) != 0) {
        worker->error_number = errno;
    } else {
        worker->observed_cpu = sched_getcpu();
        if (worker->observed_cpu != worker->cpu) {
            worker->error_number = EAGAIN;
        }
    }
    atomic_fetch_add_explicit(&ready_workers, 1, memory_order_release);

    while (atomic_load_explicit(&start_gate, memory_order_acquire) == 0) {
        sched_yield();
    }
    if (worker->error_number != 0) {
        return NULL;
    }

    const int64_t warmup_ns = (int64_t)(worker->warmup_seconds * 1000000000.0);
    const int64_t measure_ns = (int64_t)(worker->measure_seconds * 1000000000.0);
    const int64_t measure_start_ns = common_start_ns + warmup_ns;
    const int64_t measure_end_ns = measure_start_ns + measure_ns;
    uint64_t state = UINT64_C(0x123456789abcdef0) ^ (uint64_t)worker->cpu;
    uint64_t warmup_iterations = 0;
    uint64_t measure_iterations = 0;

    while (boot_time_ns() < common_start_ns) {
        sched_yield();
    }
    while (boot_time_ns() < measure_start_ns) {
        state = do_batch(state, warmup_iterations);
        warmup_iterations += BATCH_ITERATIONS;
    }
    while (boot_time_ns() < measure_end_ns) {
        state = do_batch(state, measure_iterations);
        measure_iterations += BATCH_ITERATIONS;
    }

    worker->warmup_iterations = warmup_iterations;
    worker->measure_iterations = measure_iterations;
    worker->checksum = state;
    worker->end_ns = boot_time_ns();
    return NULL;
}

static int parse_cpu_list(const char *input, int *cpus, int *count) {
    char buffer[128];
    if (strlen(input) == 0 || strlen(input) >= sizeof(buffer)) {
        return -1;
    }
    memcpy(buffer, input, strlen(input) + 1);
    *count = 0;
    char *save = NULL;
    for (char *token = strtok_r(buffer, ",", &save); token != NULL; token = strtok_r(NULL, ",", &save)) {
        char *end = NULL;
        long cpu = strtol(token, &end, 10);
        if (end == token || *end != '\0' || cpu < 0 || cpu >= CPU_SETSIZE || *count >= MAX_WORKERS) {
            return -1;
        }
        for (int i = 0; i < *count; ++i) {
            if (cpus[i] == (int)cpu) {
                return -1;
            }
        }
        cpus[(*count)++] = (int)cpu;
    }
    return *count > 0 ? 0 : -1;
}

static int parse_seconds(const char *input, int allow_zero, double *value) {
    char *end = NULL;
    double parsed = strtod(input, &end);
    if (end == input || *end != '\0' || !isfinite(parsed) || parsed < 0.0 || (!allow_zero && parsed == 0.0)) {
        return -1;
    }
    *value = parsed;
    return 0;
}

int main(int argc, char **argv) {
    if (argc != 4) {
        fprintf(stderr, "usage: %s CPU[,CPU...] WARMUP_SECONDS MEASURE_SECONDS\n", argv[0]);
        return 2;
    }

    int cpus[MAX_WORKERS];
    int count = 0;
    double warmup_seconds = 0.0;
    double measure_seconds = 0.0;
    if (parse_cpu_list(argv[1], cpus, &count) != 0) {
        fprintf(stderr, "invalid or duplicate CPU list: %s\n", argv[1]);
        return 2;
    }
    if (parse_seconds(argv[2], 1, &warmup_seconds) != 0 ||
        parse_seconds(argv[3], 0, &measure_seconds) != 0) {
        fprintf(stderr, "invalid warmup/measure duration\n");
        return 2;
    }

    Worker workers[MAX_WORKERS];
    memset(workers, 0, sizeof(workers));
    for (int i = 0; i < count; ++i) {
        workers[i].cpu = cpus[i];
        workers[i].observed_cpu = -1;
        workers[i].warmup_seconds = warmup_seconds;
        workers[i].measure_seconds = measure_seconds;
        int result = pthread_create(&workers[i].thread, NULL, worker_main, &workers[i]);
        if (result != 0) {
            fprintf(stderr, "pthread_create(cpu=%d): %s\n", cpus[i], strerror(result));
            atomic_store_explicit(&start_gate, 1, memory_order_release);
            for (int j = 0; j < i; ++j) {
                pthread_join(workers[j].thread, NULL);
            }
            return 3;
        }
    }

    while (atomic_load_explicit(&ready_workers, memory_order_acquire) < count) {
        sched_yield();
    }
    for (int i = 0; i < count; ++i) {
        if (workers[i].error_number != 0) {
            fprintf(stderr, "CPU affinity failed: target=%d observed=%d error=%s\n",
                    workers[i].cpu, workers[i].observed_cpu, strerror(workers[i].error_number));
            atomic_store_explicit(&start_gate, 1, memory_order_release);
            for (int j = 0; j < count; ++j) {
                pthread_join(workers[j].thread, NULL);
            }
            return 4;
        }
    }

    char cpu_list[64] = {0};
    size_t used = 0;
    for (int i = 0; i < count; ++i) {
        int written = snprintf(cpu_list + used, sizeof(cpu_list) - used,
                               "%s%d", i == 0 ? "" : ",", workers[i].cpu);
        if (written < 0 || (size_t)written >= sizeof(cpu_list) - used) {
            atomic_store_explicit(&start_gate, 1, memory_order_release);
            for (int j = 0; j < count; ++j) pthread_join(workers[j].thread, NULL);
            return 2;
        }
        used += (size_t)written;
    }

    printf("B|META|scalar_mix_v2|cpus=%s|workers=%d|batch=%u|warmup_s=%.3f|measure_s=%.3f\n",
           cpu_list, count, BATCH_ITERATIONS, warmup_seconds, measure_seconds);
    fflush(stdout);
    common_start_ns = boot_time_ns() + START_LEAD_NS;
    atomic_store_explicit(&start_gate, 1, memory_order_release);
    const int64_t measure_start_ns = common_start_ns + (int64_t)(warmup_seconds * 1000000000.0);
    printf("B|MEASURE_BEGIN|%d|%.6f|%s\n", workers[0].cpu, ns_to_seconds(measure_start_ns), cpu_list);
    fflush(stdout);

    uint64_t total_iterations = 0;
    uint64_t total_checksum = 0;
    int64_t latest_end_ns = measure_start_ns;
    int result_code = 0;
    for (int i = 0; i < count; ++i) {
        int result = pthread_join(workers[i].thread, NULL);
        if (result != 0) {
            fprintf(stderr, "pthread_join(cpu=%d): %s\n", workers[i].cpu, strerror(result));
            result_code = 5;
            continue;
        }
        const double elapsed = ns_to_seconds(workers[i].end_ns - measure_start_ns);
        const double ops_per_second = elapsed > 0.0 ? (double)workers[i].measure_iterations / elapsed : 0.0;
        printf("B|WORKER|%d|%d|%" PRIu64 "|%" PRIu64 "|%.6f|%.3f|%" PRIu64 "\n",
               workers[i].cpu, workers[i].observed_cpu, workers[i].warmup_iterations,
               workers[i].measure_iterations, elapsed, ops_per_second, workers[i].checksum);
        total_iterations += workers[i].measure_iterations;
        total_checksum ^= workers[i].checksum;
        if (workers[i].end_ns > latest_end_ns) {
            latest_end_ns = workers[i].end_ns;
        }
    }
    const double wall_elapsed = ns_to_seconds(latest_end_ns - measure_start_ns);
    const double total_ops_per_second = wall_elapsed > 0.0 ? (double)total_iterations / wall_elapsed : 0.0;
    printf("B|MEASURE_END|%d|%.6f|%.6f|%" PRIu64 "|%.3f|%" PRIu64 "\n",
           workers[0].cpu, ns_to_seconds(latest_end_ns), wall_elapsed,
           total_iterations, total_ops_per_second, total_checksum);
    fflush(stdout);
    return result_code;
}
