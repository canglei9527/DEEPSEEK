"""Aggregate every captured window: drain rate, screen/doze state, suspend stats, key deltas.

Read-only over the files already on disk; never talks to the phone.
"""
import importlib.util
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CST = timezone(timedelta(hours=8))
CYCLE = "2026-11-08-11-04-31"

spec = importlib.util.spec_from_file_location("cc", ROOT / "compare_charged.py")
cc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cc)

DUR = cc.DURATION


def load_battery():
    import csv
    rows = []
    with (ROOT / "battery-samples.csv").open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            try:
                when = datetime.fromisoformat(row["time_shanghai"]).replace(tzinfo=CST)
                mah = int(row["charge_counter_uAh"]) / 1000
                pct = int(row["capacity_pct"])
            except (TypeError, ValueError, KeyError):
                continue
            if row.get("status") != "Discharging":
                continue
            rows.append((when, mah, pct))
    rows.sort()
    return rows


def load_states():
    import csv
    rows = []
    with (ROOT / "state-snapshots.csv").open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            try:
                when = datetime.fromisoformat(row["time_shanghai"]).replace(tzinfo=CST)
            except (TypeError, ValueError):
                continue
            rows.append((when, row.get("wakefulness", ""), row.get("deviceidle_state", ""),
                         row.get("screen_on", "")))
    rows.sort()
    return rows


def capacity_estimate(battery):
    """Least-squares slope of charge_counter vs capacity percent -> implied full capacity."""
    pts = [(pct, mah) for _, mah, pct in battery]
    if len(pts) < 10:
        return None
    n = len(pts)
    mx = sum(p for p, _ in pts) / n
    my = sum(m for _, m in pts) / n
    den = sum((p - mx) ** 2 for p, _ in pts)
    if den == 0:
        return None
    slope = sum((p - mx) * (m - my) for p, m in pts) / den
    if slope <= 0:
        return None
    return slope * 100  # mAh per 100 %


def state_label(states, when):
    best = None
    for row in states:
        if row[0] <= when:
            best = row
        else:
            break
    if not best:
        return "?"
    wake, idle, screen = best[1], best[2], best[3]
    on = "true" in screen
    m = re.search(r"mState=(\S+)", idle or "")
    return ("screen-on" if on else "screen-off") + "/" + (m[1] if m else "?")


def wakelock_lines(path):
    """2- or 4-space 'Wake lock <name>: <dur> ... (N times)' lines -> (ms, count)."""
    out = {}
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        m = re.match(r"^ {2,4}Wake lock (.+?): (" + DUR + r")", line)
        if m:
            name = re.sub(r"^\d+ ", "", m[1]).strip()
            cnt = re.search(r"\((\d+) times?\)", line)
            out[name] = (cc.ms(m[2]), int(cnt[1]) if cnt else 0)
    return out


def snapshot_pairs():
    snaps = sorted((ROOT / "snapshots").glob("*-charged*.txt"))
    return snaps


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=8)
    args = ap.parse_args()

    battery = load_battery()
    states = load_states()
    cap = capacity_estimate(battery)

    if battery:
        span_h = (battery[-1][0] - battery[0][0]).total_seconds() / 3600
        print(f"battery samples: {len(battery)}  {battery[0][0]:%H:%M:%S} -> {battery[-1][0]:%H:%M:%S} "
              f"({span_h:.2f} h)  {battery[0][1]:.0f} -> {battery[-1][1]:.0f} mAh  pct {battery[0][2]} -> {battery[-1][2]}")
        if cap:
            print(f"implied full capacity from charge_counter vs capacity% slope: {cap:.0f} mAh")
        print(f"whole-series average: {(battery[0][1] - battery[-1][1]) / span_h:.1f} mA"
              + (f", {(battery[0][1] - battery[-1][1]) / span_h / cap * 100:.2f} %/h" if cap else ""))
    print()

    snaps = snapshot_pairs()
    print("== snapshot windows ==")
    print("  window                     dur   mAh  mAh/h  %/h   so_s   on_s  idle_s  sysCPU  krnCPU  susSvc  tfd  wlan")
    prev = None
    parsed = {}
    for snap in snaps:
        start, counters, names = cc.snapshot(snap)
        if start != CYCLE:
            continue
        parsed[snap] = counters
    order = sorted(parsed)
    for a, b in zip(order, order[1:]):
        ca, cb = parsed[a], parsed[b]
        t0, t1 = cc.clock(a), cc.clock(b)
        dur = (t1 - t0).total_seconds()

        def d(key, default=0):
            return cb.get(key, default) - ca.get(key, default)

        so = d(("global", "", "Time on battery screen off")) / 1000
        on = d(("global", "", "Screen on")) / 1000
        idle = d(("global", "", "Device full idling")) / 1000
        sys_cpu = d(("uid", "1000", "Screen-off CPU")) / 1000
        krn_cpu = d(("uid", "0", "Screen-off CPU")) / 1000
        sus = d(("proc", "1000", "android.system.suspend-service")) / 1000
        tfd = d(("Wakeup reason", "", "Abort: Pending Wakeup Sources: [timerfd] count"))
        wlan = d(("Wakeup reason", "", "Abort: Device 0000:01:00.0 failed to suspend: error -11 count"))
        m_bs = d(("global", "", "Screen-off mAh"))

        near = [row for row in battery if t0 <= row[0] <= t1]
        if len(near) >= 2:
            mah = near[0][1] - near[-1][1]
            rate = mah / dur * 3600
            pcth = f"{rate / cap * 100:5.2f}" if cap else "  ?  "
            mah_s = f"{mah:5.0f}"
            rate_s = f"{rate:6.0f}"
        else:
            mah_s, rate_s, pcth = "    ?", "     ?", "  ?  "
        print(f"  {t0:%H:%M:%S}->{t1:%H:%M:%S} {dur/60:5.1f}m {mah_s} {rate_s} {pcth} "
              f"{so:6.0f} {on:6.0f} {idle:7.0f} {sys_cpu:7.0f} {krn_cpu:7.0f} {sus:7.0f} {tfd:4.0f} {wlan:4.0f}")
    print()

    print("== named wake locks (top by delta in each window) ==")
    prev_locks = None
    for snap in order:
        locks = wakelock_lines(snap)
        if prev_locks is not None:
            deltas = sorted(((v[0] - prev_locks.get(k, (0, 0))[0], k) for k, v in locks.items()), reverse=True)
            hot = [(ms, k) for ms, k in deltas if ms > 3000][:args.top]
            if hot:
                print(f"  ->{snap.stem.split('-')[-1]}: " + ", ".join(f"{k}={ms/1000:.0f}s" for ms, k in hot))
            else:
                print(f"  ->{snap.stem.split('-')[-1]}: (none >3s)")
        prev_locks = locks
    print()

    print("== 15-minute rolling drain from charge counter ==")
    print("  window                 state_at_end        mAh   mA   %/h")
    i = 0
    while i < len(battery):
        t0 = battery[i][0]
        j = i
        while j < len(battery) and (battery[j][0] - t0).total_seconds() < 900:
            j += 1
        if j < len(battery) and (battery[j][0] - t0).total_seconds() >= 840:
            t1 = battery[j][0]
            mah = battery[i][1] - battery[j][1]
            dur = (t1 - t0).total_seconds()
            rate = mah / dur * 3600
            pcth = f"{rate / cap * 100:5.2f}" if cap else "  ?  "
            print(f"  {t0:%H:%M:%S}->{t1:%H:%M:%S} {state_label(states, t1):18s} {mah:5.0f} {rate:5.0f} {pcth}")
            i = j
        else:
            i += 1


if __name__ == "__main__":
    main()
