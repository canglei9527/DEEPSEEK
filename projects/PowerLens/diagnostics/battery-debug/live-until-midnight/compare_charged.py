"""Compare two saved batterystats snapshots without touching the device."""

import argparse
import csv
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
CST = timezone(timedelta(hours=8))
UNIT_MS = {"ms": 1, "s": 1000, "m": 60_000, "h": 3_600_000, "d": 86_400_000}
UNIT_BYTES = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3}
UID_LINE = re.compile(r"^  (u\d+a\d+|\d+):\s*$")
PROC_LINE = re.compile(r"^    Proc (.*):$")
DURATION = r"(?:\d+(?:ms|s|m|h|d) ?)+"
FOCUS = {
    "0": "kernel/root", "1000": "system", "1001": "phone/IMS", "2000": "shell/adbd",
    "u0a130": "Xiaomi AI", "u0a139": "Xiaomi location", "u0a209": "Xiaomi push",
    "u0a318": "Tongyi", "u0a319": "Baidu Maps", "u0a340": "Xiaohongshu", "u0a343": "WeChat", "u0a344": "QQ",
    "u0a350": "NoActive", "u0a368": "JD", "u0a425": "NoActive Monitor",
}


def ms(value):
    return sum(int(n) * UNIT_MS[unit] for n, unit in re.findall(r"(\d+)(ms|s|m|h|d)", value))


def snapshot(path):
    counters = defaultdict(int)
    names = {}
    uid = model_uid = proc = start_clock = None
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        if line.startswith("  Start clock time:"):
            start_clock = line.split(": ", 1)[1]
        match = re.match(r"^  (Time on battery screen off|Screen on|Device full idling|Total partial wakelock time): (.*)", line)
        if match:
            field, body = match.groups()
            counters["global", "", field] = ms(body.split(" (")[0])
            if field == "Time on battery screen off":
                counters["global", "", "Screen-off uptime"] = ms(body.split("realtime, ", 1)[1].split(" (")[0])
        match = re.match(r"^  Screen off discharge: (\d+) mAh", line)
        if match:
            counters["global", "", "Screen-off mAh"] = int(match[1])
        match = re.match(r"^  (Wakeup reason|Kernel Wake lock) (.*?): (" + DURATION + r")(?=\s*\(| realtime)", line)
        if match:
            kind, source, duration = match.groups()
            count = re.search(r"\((\d+) times?\)", line)
            counters[kind, "", source + " ms"] = ms(duration)
            counters[kind, "", source + " count"] = int(count[1]) if count else 0
        match = UID_LINE.match(line)
        if match:
            uid, proc = match[1], None
            model_uid = None
            continue
        match = re.match(r"^  UID (\S+):", line)
        if match:
            model_uid = match[1]
        if model_uid:
            match = re.match(r"^      \(on battery, (screen on|screen off/doze)\) (.*)", line)
            if match:
                duration = re.search(r"\bcpu=\S+ \(([^)]+)\)", match[2])
                if duration:
                    counters["model", model_uid, match[1]] = ms(duration[1])
        if uid is None:
            continue
        match = PROC_LINE.match(line)
        if match:
            proc = match[1]
            continue
        if line.startswith("      CPU: ") and proc:
            kind = "synthetic proc" if proc.startswith("*") else "proc"
            counters[kind, uid, proc] = ms(line.split(" ; ")[0])
        elif line.startswith("    Apk "):
            names.setdefault(uid, line.strip()[4:-1])
        elif line.startswith("    Total screen-off cpu time per freq:"):
            counters["uid", uid, "Screen-off CPU"] = sum(map(int, line.split(":", 1)[1].split()))
        elif line.startswith(("    Wi-Fi network:", "    Mobile network:")):
            match = re.match(r"^    (Wi-Fi|Mobile) network: ([\d.]+)([KMG]?B) received, "
                             r"([\d.]+)([KMG]?B) sent \(packets (\d+) received, (\d+) sent\)", line)
            if match:
                link, rx, rx_unit, tx, tx_unit, rx_packets, tx_packets = match.groups()
                counters[link, uid, "rx bytes"] = round(float(rx) * UNIT_BYTES[rx_unit])
                counters[link, uid, "tx bytes"] = round(float(tx) * UNIT_BYTES[tx_unit])
                counters[link, uid, "rx packets"] = int(rx_packets)
                counters[link, uid, "tx packets"] = int(tx_packets)
        elif line.startswith("    TOTAL wake:"):
            for key in ("blamed partial", "actual partial", "actual background partial"):
                duration = re.search(r"(" + DURATION + r") " + key, line)
                if duration:
                    counters["uid", uid, key] = ms(duration[1])
        else:
            match = re.match(r"^    Job (.*?): (" + DURATION + r") realtime \((\d+) times?\)", line)
            if match:
                name, duration, count = match.groups()
                counters["job count", uid, name] = int(count)
                counters["job ms", uid, name] = ms(duration)
            match = re.match(r"^      Wakeup alarm (.*): (\d+) times?$", line)
            if match:
                counters["alarm", uid, match[1]] = int(match[2])
    return start_clock, counters, names


def clock(path):
    return datetime.fromtimestamp(path.stat().st_mtime, CST).replace(microsecond=0)


def rows_near(path, start, end, margin=0):
    with path.open(encoding="utf-8-sig", newline="") as data:
        for row in csv.DictReader(data):
            try:
                when = datetime.fromisoformat(row["time_shanghai"]).replace(tzinfo=CST)
            except (TypeError, ValueError):
                continue
            if start - timedelta(seconds=margin) <= when <= end + timedelta(seconds=margin):
                yield when, row


def report_csv(start, end):
    samples = [
        (when, int(row["charge_counter_uAh"]), row["capacity_pct"])
        for when, row in rows_near(ROOT / "battery-samples.csv", start, end, margin=35)
        if (row.get("charge_counter_uAh") or "").isdigit() and row["status"] == "Discharging"
    ]
    if len(samples) >= 2:
        first = min(samples, key=lambda row: abs(row[0] - start))
        last = min(samples, key=lambda row: abs(row[0] - end))
        print(f"charge_counter {first[0]:%H:%M:%S}->{last[0]:%H:%M:%S}: "
              f"{first[1] / 1000:g}->{last[1] / 1000:g} mAh ({(first[1] - last[1]) / 1000:g} mAh used)")
    processes = defaultdict(list)
    for when, row in rows_near(ROOT / "process-cpu-ticks.csv", start, end, margin=20):
        try:
            ticks = int(row["utime_ticks"]) + int(row["stime_ticks"])
        except (TypeError, ValueError):
            continue
        processes[row["pid"], row["start_ticks"]].append((when, ticks, row["package"]))
    print("Process ticks (same PID and starttime only)")
    for (pid, _), points in sorted(processes.items()):
        if len(points) >= 2:
            first, last = min(points), max(points)
            print(f"  {last[2]} [{pid}] {first[0]:%H:%M:%S}->{last[0]:%H:%M:%S}: {last[1] - first[1]:+d} ticks")


def report(before, after, top):
    time0, time1 = clock(before), clock(after)
    start0, a, names0 = snapshot(before)
    start1, b, names1 = snapshot(after)
    if not start0 or start0 != start1:
        raise SystemExit(f"Cannot compare different or missing cycle starts: {start0!r}, {start1!r}")
    print(f"{before.name} -> {after.name}, {time0:%H:%M:%S}->{time1:%H:%M:%S}, cycle {start0}")
    names = {**names0, **names1, **FOCUS}

    def name(uid):
        return f"{uid} {names.get(uid, '')}".strip()

    diffs = {key: b.get(key, 0) - a.get(key, 0) for key in a.keys() | b.keys()}
    for field in ("Time on battery screen off", "Screen-off uptime", "Screen on", "Device full idling", "Total partial wakelock time"):
        print(f"  {field}: {diffs['global', '', field] / 1000:+g} s")
    print(f"  BatteryStats screen-off discharge: {diffs['global', '', 'Screen-off mAh']:+g} mAh")
    report_csv(time0, time1)
    fields = ("Screen-off CPU", "actual partial", "actual background partial")
    print("Focused UID deltas, seconds (CPU and lock durations are separate)")
    for uid in FOCUS:
        if uid in ("u0a350", "u0a425") and not any(k[1] == uid for k in a.keys() | b.keys()):
            print(f"  {name(uid)}: no applicable counter rows")
            continue
        values = [diffs.get(("uid", uid, field), 0) / 1000 for field in fields]
        print(f"  {name(uid)}: {values[0]:.3f} CPU / {values[1]:.3f} partial / {values[2]:.3f} bg partial")
    for kind, metric in (("uid", "Screen-off CPU"), ("uid", "actual background partial"),
                         ("proc", None), ("synthetic proc", None), ("Wakeup reason", " count"), ("Kernel Wake lock", " count"),
                         ("alarm", None), ("job count", None), ("job ms", None)):
        print(kind + (" " + metric.strip() if metric else ""))
        positive = sorted(((v, uid, key) for (t, uid, key), v in diffs.items()
                           if t == kind and v > 0 and (metric is None or key.endswith(metric))), reverse=True)
        for value, uid, key in positive[:top]:
            measure = f"{value / 1000:.3f}s" if kind in ("uid", "proc", "synthetic proc", "job ms") else str(value)
            print(f"  {name(uid)} {key}: +{measure}".strip())
    print("Separate CPU model durations (do not add to the raw frequency counters)")
    cpu_uids = sorted((uid for (kind, uid, key), value in diffs.items() if kind == "uid"
                       and key == "Screen-off CPU" and value > 0),
                      key=lambda uid: diffs["uid", uid, "Screen-off CPU"], reverse=True)
    for uid in cpu_uids[:top]:
        on = diffs.get(("model", uid, "screen on"), 0)
        off = diffs.get(("model", uid, "screen off/doze"), 0)
        print(f"  {name(uid)}: {on / 1000:+.3f}s on / {off / 1000:+.3f}s off or display doze")
    for link in ("Wi-Fi", "Mobile"):
        traffic = {uid: tuple(diffs.get((link, uid, field), 0) for field in
                   ("rx packets", "tx packets", "rx bytes", "tx bytes"))
                   for kind, uid, _ in diffs if kind == link}
        print(f"{link} per-UID deltas (RX/TX packets, approximate KiB from rounded byte totals)")
        ranked = sorted((uid for uid, values in traffic.items() if any(values)),
                        key=lambda uid: sum(traffic[uid][:2]), reverse=True)
        focus = [uid for uid in ("1000", "0", "2000", "u0a343", "u0a344", "u0a368")
                 if uid in traffic and uid not in ranked[:top]]
        for uid in ranked[:top] + focus:
            rx, tx, rx_bytes, tx_bytes = traffic[uid]
            rx_text = "rounded out" if rx and not rx_bytes else f"{rx_bytes / 1024:+.1f}"
            tx_text = "rounded out" if tx and not tx_bytes else f"{tx_bytes / 1024:+.1f}"
            print(f"  {name(uid)}: {rx:+d}/{tx:+d} pkt, {rx_text}/{tx_text} KiB")


def report_suspend(path, pairs):
    captures = []
    counts = None
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        header = re.match(r"^===== (?:FINAL )?(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) =====$", line)
        if header:
            counts = {}
            captures.append((datetime.fromisoformat(header[1]), counts))
            continue
        if counts is None:
            continue
        source = re.match(r"^WakeupInfo\{name: (.*), count: (\d+)\}$", line)
        if source:
            counts[source[1]] = int(source[2])
        stat = re.match(r"^([\w_ ]+): (\d+)(?: ms)?$", line)
        if stat:
            counts[stat[1]] = int(stat[2])
    captures = [(when, data) for when, data in captures if "total sleep time between suspends" in data]
    if len(captures) < 2:
        print("Waiting for two complete suspend-control captures.")
        return
    fields = {
        "suspend attempts": "attempts", "success": "success", "fail": "failed",
        "Abort: Pending Wakeup Sources: [timerfd]": "timerfd abort",
        "Abort: Pending Wakeup Sources: [timerfd] [timerfd]": "double timerfd abort",
        "Abort: Device 0000:01:00.0 failed to suspend: error -11": "WLAN PCIe -11",
        "Abort: Device 0000:01:00.0 failed to suspend noirq: error -11": "WLAN PCIe noirq -11",
        "Abort: Pending Wakeup Sources: qcom_rx_wakelock": "Qualcomm RX pending",
        "Abort: Last active Wakeup Source: qcom_rx_wakelock": "Qualcomm RX last active",
        "-1 modem": "modem wake reason", "total_hw_sleep": "kernel total_hw_sleep",
        "total sleep time between suspends": "sleep between attempts (ms)",
    }
    for (time0, a), (time1, b) in list(zip(captures, captures[1:]))[-pairs:]:
        elapsed = int((time1 - time0).total_seconds() * 1000)
        print(f"{time0:%H:%M:%S}->{time1:%H:%M:%S} ({elapsed / 1000:g}s)")
        for key, label in fields.items():
            print(f"  {label}: {b.get(key, 0) - a.get(key, 0):+d}")
        sleep_delta = b["total suspend time"] - a["total suspend time"]
        suffix = "invalid delta" if not 0 <= sleep_delta <= elapsed + 2000 else "plausible delta; absolute counter has an implausible offset"
        print(f"  total suspend time: {sleep_delta:+d} ms ({suffix})")
    print("Wireless ADB polling is part of the measured workload.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path, help="Optional start and end snapshots. Defaults to the latest two.")
    parser.add_argument("--top", type=int, default=6)
    parser.add_argument("--suspend", action="store_true", help="Report the last three completed suspend-control intervals.")
    args = parser.parse_args()
    if args.suspend:
        report_suspend(ROOT / "suspend-control-samples.log", 3)
        return
    if args.paths and len(args.paths) != 2:
        parser.error("pass exactly two paths, or omit both")
    paths = args.paths or sorted((ROOT / "snapshots").glob("*-charged*.txt"))[-2:]
    if len(paths) != 2:
        parser.error("no comparable pair of snapshots found")
    report(*paths, args.top)


if __name__ == "__main__":
    main()
