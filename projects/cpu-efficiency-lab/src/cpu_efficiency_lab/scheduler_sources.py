"""Collect a bounded set of Android scheduler configuration files read-only."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
import re
import shlex
import subprocess
from typing import Dict, List, Optional, Tuple

from .adb import AdbClient, AdbError
from .config import SCHEDULER_IDENTITY_PROPERTIES, SCHEDULER_SOURCE_PATHS


def source_paths(first_api_level: str) -> List[str]:
    """AOSP loads the launch API layer before the vendor overrides."""
    if not re.fullmatch(r"[1-9][0-9]{0,2}", first_api_level):
        raise ValueError("invalid or unavailable ro.product.first_api_level")
    return [path.format(first_api_level=first_api_level) for path in SCHEDULER_SOURCE_PATHS]


def read_identity(client: AdbClient) -> Dict[str, str]:
    return {name: client.run(["shell", "getprop", name]).strip()
            for name in SCHEDULER_IDENTITY_PROPERTIES}


def read_source(client: AdbClient, path: str) -> Tuple[str, bytes, Optional[str]]:
    """Use exec-out without text decoding so that the saved bytes are exact."""
    quoted = shlex.quote(path)
    metadata_script = (
        "if [ ! -e {0} ] && [ ! -L {0} ]; then printf 'MISSING\\n'; "
        "elif [ ! -f {0} ] || [ ! -r {0} ]; then printf 'UNREADABLE\\n'; "
        "else printf 'PRESENT\\n'; sha256sum {0}; wc -c < {0}; fi"
    ).format(quoted)
    try:
        metadata = client.run_root_script(metadata_script).splitlines()
    except (AdbError, OSError) as exc:
        return "error", b"", str(exc)
    if metadata == ["MISSING"]:
        return "missing", b"", None
    if metadata == ["UNREADABLE"]:
        return "error", b"", "source is not a readable regular file"
    if len(metadata) != 3 or metadata[0] != "PRESENT":
        return "error", b"", "invalid source metadata"
    match = re.match(r"^([0-9a-fA-F]{64})\s", metadata[1])
    size = metadata[2].strip()
    if not match or not size.isdigit():
        return "error", b"", "invalid source hash or size"
    expected_hash, expected_size = match.group(1).lower(), int(size)
    script = "cat {}".format(quoted)
    command = [client.adb_path, "-s", client.serial, "exec-out",
               "su -c " + shlex.quote(script)]
    try:
        result = subprocess.run(command, capture_output=True, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "error", b"", str(exc)
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        return "error", b"", detail or "adb exit {}".format(result.returncode)
    if len(result.stdout) != expected_size or hashlib.sha256(result.stdout).hexdigest() != expected_hash:
        return "error", b"", "device metadata and returned bytes differ"
    return "present", result.stdout, None


def collect(serial: str, expected_model: str, expected_device: str, output: Path) -> dict:
    client = AdbClient(serial)
    identity = read_identity(client)
    if (identity["ro.product.model"] != expected_model or
            identity["ro.product.device"] != expected_device):
        raise ValueError("device identity does not match expected model and device")
    paths = source_paths(identity["ro.product.first_api_level"])
    output = Path(output)
    output.mkdir(parents=False, exist_ok=False)
    manifest = {
        "schema": "cpu-lab-scheduler-sources-v1",
        "collected_utc": datetime.now(timezone.utc).isoformat(),
        "serial": serial,
        "identity": identity,
        "sources": [],
        "limitations": [
            "Files are observed on the connected device and are not proven factory originals.",
            "Sequential reads are not an atomic snapshot.",
            "These files do not capture all kernel or vendor scheduling behavior.",
        ],
    }
    for source in paths:
        status, content, error = read_source(client, source)
        entry = {"source": source, "status": status, "size_bytes": None,
                 "sha256": None, "saved_as": None, "error": error}
        if status == "present":
            relative = Path(*PurePosixPath(source).parts[1:])
            target = output / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as stream:
                stream.write(content)
            entry.update(size_bytes=len(content), sha256=hashlib.sha256(content).hexdigest(),
                         saved_as=relative.as_posix())
        manifest["sources"].append(entry)
    with (output / "manifest.json").open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return manifest


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--expected-model", required=True)
    parser.add_argument("--expected-device", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        report = collect(args.serial, args.expected_model, args.expected_device, args.output)
    except (AdbError, OSError, ValueError) as exc:
        parser.exit(2, "error: {}\n".format(exc))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if any(item["status"] == "error" for item in report["sources"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
