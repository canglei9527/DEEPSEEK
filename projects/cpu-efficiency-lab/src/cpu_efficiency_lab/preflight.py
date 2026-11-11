"""Read-only Android capability and sensor preflight."""

from __future__ import annotations

import json
from typing import Dict, List

from .adb import AdbClient
from .config import BATTERY_NODE_NAMES, BATTERY_PATH, CPUFREQ_ROOT
from .models import CpuPolicy, PreflightReport


PREFLIGHT_SCRIPT = r'''\
set +e
printf 'ROOT_UID|'; id -u
printf 'ROOT_CONTEXT|'; id -Z 2>/dev/null
printf 'SELINUX|'; getenforce 2>/dev/null
printf 'PROP|model|'; getprop ro.product.model
printf 'PROP|device|'; getprop ro.product.device
printf 'PROP|soc|'; getprop ro.soc.model
printf 'PROP|android|'; getprop ro.build.version.release
printf 'KERNEL|'; uname -a
for name in @BATTERY_NODE_NAMES@; do
  path=@BATTERY_PATH@/$name
  if [ -e "$path" ]; then
    if read -r value < "$path" 2>/dev/null; then
      printf 'NODE|%s|present|readable|%s\n' "$name" "$value"
    else
      printf 'NODE|%s|present|unreadable|\n' "$name"
    fi
  else
    printf 'NODE|%s|absent|unreadable|\n' "$name"
  fi
done
for policy in @CPUFREQ_ROOT@/policy*; do
  [ -d "$policy" ] || continue
  name=${policy##*/}
  related=''; available=''; scaling_min=''; scaling_max=''; hw_min=''; hw_max=''; governor=''
  read -r related < "$policy/related_cpus" 2>/dev/null
  read -r available < "$policy/scaling_available_frequencies" 2>/dev/null
  read -r scaling_min < "$policy/scaling_min_freq" 2>/dev/null
  read -r scaling_max < "$policy/scaling_max_freq" 2>/dev/null
  read -r hw_min < "$policy/cpuinfo_min_freq" 2>/dev/null
  read -r hw_max < "$policy/cpuinfo_max_freq" 2>/dev/null
  read -r governor < "$policy/scaling_governor" 2>/dev/null
  printf 'POLICY|%s|%s|%s|%s|%s|%s|%s|%s\n' "$name" "$related" "$available" "$scaling_min" "$scaling_max" "$hw_min" "$hw_max" "$governor"
done
'''


def _optional_int(value: str):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_preflight(serial: str, output: str) -> PreflightReport:
    properties: Dict[str, str] = {}
    nodes: Dict[str, dict] = {}
    policies: List[CpuPolicy] = []
    root_uid = ""
    root_context = ""
    selinux = ""
    kernel = ""

    for raw_line in output.splitlines():
        parts = raw_line.strip().split("|")
        if not parts or not parts[0]:
            continue
        kind = parts[0]
        if kind == "ROOT_UID" and len(parts) >= 2:
            root_uid = parts[1]
        elif kind == "ROOT_CONTEXT" and len(parts) >= 2:
            root_context = parts[1]
        elif kind == "SELINUX" and len(parts) >= 2:
            selinux = parts[1]
        elif kind == "PROP" and len(parts) >= 3:
            properties[parts[1]] = "|".join(parts[2:])
        elif kind == "KERNEL" and len(parts) >= 2:
            kernel = "|".join(parts[1:])
        elif kind == "NODE" and len(parts) >= 5:
            nodes[parts[1]] = {
                "present": parts[2] == "present",
                "readable": parts[3] == "readable",
                "value": "|".join(parts[4:]) or None,
            }
        elif kind == "POLICY" and len(parts) >= 9:
            policies.append(
                CpuPolicy(
                    name=parts[1],
                    related_cpus=parts[2],
                    available_frequencies_khz=parts[3],
                    scaling_min_khz=_optional_int(parts[4]),
                    scaling_max_khz=_optional_int(parts[5]),
                    hardware_min_khz=_optional_int(parts[6]),
                    hardware_max_khz=_optional_int(parts[7]),
                    governor=parts[8],
                )
            )

    return PreflightReport(
        serial=serial,
        model=properties.get("model", ""),
        device=properties.get("device", ""),
        soc=properties.get("soc", ""),
        android_release=properties.get("android", ""),
        kernel=kernel,
        root_uid=root_uid,
        root_context=root_context,
        selinux=selinux,
        nodes=nodes,
        policies=tuple(policies),
    )


def collect_preflight(client: AdbClient) -> PreflightReport:
    script = (
        PREFLIGHT_SCRIPT.replace("@BATTERY_NODE_NAMES@", " ".join(BATTERY_NODE_NAMES))
        .replace("@BATTERY_PATH@", BATTERY_PATH)
        .replace("@CPUFREQ_ROOT@", CPUFREQ_ROOT)
    )
    output = client.run_root_script(script)
    return parse_preflight(client.serial, output)


def report_dict(report: PreflightReport) -> dict:
    return {
        "serial": report.serial,
        "model": report.model,
        "device": report.device,
        "soc": report.soc,
        "android_release": report.android_release,
        "kernel": report.kernel,
        "root_uid": report.root_uid,
        "root_context": report.root_context,
        "selinux": report.selinux,
        "ready_for_capture": report.ready_for_capture,
        "nodes": report.nodes,
        "policies": [policy.__dict__ for policy in report.policies],
    }


def render_report(report: PreflightReport) -> str:
    return json.dumps(report_dict(report), indent=2, ensure_ascii=False)
