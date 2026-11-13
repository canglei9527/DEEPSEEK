"""Canonical device paths and sampling constraints."""

BATTERY_PATH = "/sys/class/power_supply/battery"
BATTERY_NODES = {
    "current_now": f"{BATTERY_PATH}/current_now",
    "voltage_now": f"{BATTERY_PATH}/voltage_now",
    "status": f"{BATTERY_PATH}/status",
    "temp": f"{BATTERY_PATH}/temp",
    "capacity": f"{BATTERY_PATH}/capacity",
    "charge_counter": f"{BATTERY_PATH}/charge_counter",
    "power_now": f"{BATTERY_PATH}/power_now",
}
BATTERY_NODE_NAMES = tuple(BATTERY_NODES)
CPUFREQ_ROOT = "/sys/devices/system/cpu/cpufreq"
MIN_SAMPLE_INTERVAL_S = 0.2
MAX_CAPTURE_DURATION_S = 7200.0
PROTOCOL_VERSION = 1

# Screening thresholds for paired whole-device idle baselines, not CPU-rail accuracy.
BASELINE_MAX_DRIFT_W = 0.15
BASELINE_MAX_DRIFT_FRACTION = 0.20
BASELINE_MAX_SAMPLE_GAP_S = 5.0

# Read-only scheduler source inventory. API-specific files use the launch API.
SCHEDULER_IDENTITY_PROPERTIES = (
    "ro.product.model",
    "ro.product.device",
    "ro.soc.model",
    "ro.build.version.release",
    "ro.build.version.sdk",
    "ro.product.first_api_level",
    "ro.build.version.incremental",
    "ro.build.fingerprint",
    "ro.vendor.build.fingerprint",
)
SCHEDULER_SOURCE_PATHS = (
    "/system/etc/cgroups.json",
    "/system/etc/task_profiles.json",
    "/system/etc/task_profiles/cgroups_{first_api_level}.json",
    "/system/etc/task_profiles/task_profiles_{first_api_level}.json",
    "/vendor/etc/cgroups.json",
    "/vendor/etc/task_profiles.json",
    "/vendor/etc/perf/targetresourceconfigs.xml",
    "/vendor/etc/perf/targetconfig.xml",
)

# Two short repetitions per fixed-frequency topology point; all values in seconds.
MATRIX_SWEEP_REPETITIONS = 2
MATRIX_SWEEP_WARMUP_S = 2
MATRIX_SWEEP_MEASURE_S = 6
MATRIX_SWEEP_BASELINE_S = 2
MATRIX_SWEEP_COOLDOWN_S = 1

# Bounded low-noise screening; independent of the full staged matrix timings.
TWO_HOUR_TOTAL_S = 7200
TWO_HOUR_FINISH_RESERVE_S = 600
TWO_HOUR_GRID_LEVELS = 5
TWO_HOUR_WARMUP_S = 3
TWO_HOUR_MEASURE_S = 12
TWO_HOUR_BASELINE_S = 4
TWO_HOUR_COOLDOWN_S = 2
TWO_HOUR_REPETITIONS = 2
TWO_HOUR_SETUP_BUDGET_S = 600
TWO_HOUR_TRANSPORT_BUDGET_S = 600
TWO_HOUR_COOLING_BUDGET_S = 480
TWO_HOUR_ARTIFACT_BUDGET_S = 420
LOW_NOISE_HEARTBEAT_TIMEOUT_S = 180
LOW_NOISE_START_CPU_MC = 45000
LOW_NOISE_START_BATTERY_MC = 32000
LOW_NOISE_COOL_POLL_S = 10
# Includes the controller transport timeout and snapshot/log verification budget.
TWO_HOUR_CONFIG_TIMEOUT_FACTOR = 1.6
TWO_HOUR_CONFIG_TIMEOUT_EXTRA_S = 105
