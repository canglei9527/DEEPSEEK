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

# Two short repetitions per fixed-frequency topology point; all values in seconds.
MATRIX_SWEEP_REPETITIONS = 2
MATRIX_SWEEP_WARMUP_S = 2
MATRIX_SWEEP_MEASURE_S = 6
MATRIX_SWEEP_BASELINE_S = 2
MATRIX_SWEEP_COOLDOWN_S = 1
