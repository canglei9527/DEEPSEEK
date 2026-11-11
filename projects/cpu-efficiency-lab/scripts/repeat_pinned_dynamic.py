"""Run two guarded all-online dynamic-frequency rounds, then plot both."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from cpu_efficiency_lab.adb import AdbClient, AdbError
from cpu_efficiency_lab.matrix_runner import _validate_device_ready
from cpu_efficiency_lab.screen_control import DEFAULT_MATRIX_TIMEOUT_MS, keep_screen_awake, read_screen_state, restore_screen_timeout


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--serial", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    prefix = args.output
    screen_path = prefix.with_name(prefix.name + ".screen-state.json")
    round_prefixes = [prefix.with_name(prefix.name + ".round%d" % n) for n in (1, 2)]
    artifacts = [screen_path]
    artifacts.extend(p.with_name(p.name + suffix) for p in round_prefixes
                     for suffix in (".raw.log", ".summary.csv", ".summary.json"))
    artifacts.extend(prefix.with_name(prefix.name + suffix) for suffix in
                     (".comparison.csv", ".report.json", ".percore.svg", ".groups.svg", ".frequency.svg", ".frequency-efficiency.svg", ".html"))
    existing = [str(p) for p in artifacts if p.exists()]
    if existing:
        raise FileExistsError("refusing to overwrite: " + ", ".join(existing))
    client = AdbClient(args.serial)
    _validate_device_ready(client, 60)
    before = read_screen_state(client)
    record = {"serial": args.serial, "before": asdict(before), "prepared": False,
              "restored": False, "rounds_completed": 0,
              "started_utc": datetime.now(timezone.utc).isoformat()}
    prefix.parent.mkdir(parents=True, exist_ok=True)
    with screen_path.open("x", encoding="utf-8") as f:
        json.dump(record, f, indent=2)
    def save():
        screen_path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    prepared = False
    try:
        keep_screen_awake(client, expected_current_timeout_ms=before.timeout_ms)
        prepared = True
        record["prepared"] = True; save()
        for index, round_prefix in enumerate(round_prefixes, 1):
            print("ROUND|%d/2|START" % index, flush=True)
            command = [sys.executable, "-u", str(ROOT / "scripts" / "run_pinned_dynamic.py"),
                       "--serial", args.serial, "--output", str(round_prefix)]
            if index == 2:
                command.append("--reverse-order")
            status = subprocess.call(command, cwd=str(ROOT), stdout=sys.stdout, stderr=sys.stderr)
            if status:
                raise AdbError("round %d aborted with exit code %d; partial logs kept" % (index, status))
            record["rounds_completed"] = index; save()
            print("ROUND|%d/2|COMPLETE" % index, flush=True)
    except BaseException as exc:
        record["run_error"] = repr(exc)
        raise
    finally:
        if prepared:
            try:
                after = restore_screen_timeout(client, before.timeout_ms, DEFAULT_MATRIX_TIMEOUT_MS)
                record["restored"] = True
                record["after"] = asdict(after)
                print("SCREEN_RESTORED|%d" % before.timeout_ms, flush=True)
            except BaseException as exc:
                record["restore_error"] = repr(exc)
                print("SCREEN_RESTORE_ERROR|%s" % exc, file=sys.stderr, flush=True)
        record["completed_utc"] = datetime.now(timezone.utc).isoformat()
        save()
    if not record["restored"]:
        raise AdbError("measurements ended, but screen timeout restoration failed")
    print("PLOTTING|START", flush=True)
    plotter = ROOT / "scripts" / "plot_pinned_repeats.py"
    result = subprocess.call([sys.executable, str(plotter), "--output", str(prefix)],
                             cwd=str(ROOT), stdout=sys.stdout, stderr=sys.stderr)
    if result:
        raise RuntimeError("plot generation failed (exit %d); round data remains saved" % result)
    print("REPEATS_FINISHED|2/2", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AdbError, OSError, RuntimeError, ValueError) as exc:
        print("ERROR|%s" % exc, file=sys.stderr, flush=True)
        raise SystemExit(2)
