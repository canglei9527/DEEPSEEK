"""Read-only CPU topology discovery and validation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .adb import AdbClient, AdbError


@dataclass(frozen=True)
class CpuCore:
    cpu_id: int
    online: bool
    online_writable: bool
    capacity: Optional[int]
    cluster_id: Optional[int]
    core_id: Optional[int]
    midr: str


@dataclass(frozen=True)
class CpuTopology:
    cores: Tuple[CpuCore, ...]

    @property
    def by_id(self) -> Dict[int, CpuCore]:
        return {core.cpu_id: core for core in self.cores}

    @property
    def online_cpus(self) -> Tuple[int, ...]:
        return tuple(core.cpu_id for core in self.cores if core.online)

    def capacity_groups(self) -> Dict[str, Tuple[int, ...]]:
        capacities = sorted({core.capacity for core in self.cores if core.capacity is not None})
        if len(capacities) != 2:
            raise ValueError("expected exactly two distinguishable CPU capacity groups")
        low, high = capacities
        groups = {
            "E": tuple(core.cpu_id for core in self.cores if core.capacity == low),
            "P": tuple(core.cpu_id for core in self.cores if core.capacity == high),
        }
        if not groups["E"] or not groups["P"]:
            raise ValueError("could not map both CPU capacity groups")
        return groups

    def require_hotplug_controls(self, target_cpus: Tuple[int, ...]) -> None:
        by_id = self.by_id
        missing = sorted(set(target_cpus) - set(by_id))
        if missing:
            raise ValueError("target CPU ids not present: %s" % missing)
        if 0 not in by_id or not by_id[0].online:
            raise ValueError("CPU0 must remain online as the system keeper")
        unavailable = [
            core.cpu_id
            for core in self.cores
            if core.cpu_id != 0 and not core.online_writable
        ]
        if unavailable:
            raise ValueError("CPU hotplug control unavailable for CPUs %s" % unavailable)
        if any(not by_id[cpu].online_writable for cpu in target_cpus if cpu != 0):
            raise ValueError("a requested non-CPU0 worker cannot be brought online")


def parse_cpu_topology(output: str) -> CpuTopology:
    cores: List[CpuCore] = []
    for raw_line in output.splitlines():
        fields = raw_line.strip().split("|")
        if len(fields) != 8 or fields[0] != "CORE":
            continue
        try:
            cpu_id = int(fields[1])
            if fields[2] not in ("0", "1") or fields[3] not in ("0", "1"):
                raise ValueError("invalid online state")
            online = fields[2] == "1"
            online_writable = fields[3] == "1"
            capacity = _optional_int(fields[4])
            cluster_id = _optional_int(fields[5])
            core_id = _optional_int(fields[6])
        except ValueError:
            continue
        cores.append(
            CpuCore(
                cpu_id=cpu_id,
                online=online,
                online_writable=online_writable,
                capacity=capacity,
                cluster_id=cluster_id,
                core_id=core_id,
                midr=fields[7],
            )
        )
    topology = CpuTopology(tuple(sorted(cores, key=lambda core: core.cpu_id)))
    if not topology.cores:
        raise AdbError("CPU topology discovery returned no valid cores")
    ids = [core.cpu_id for core in topology.cores]
    if len(ids) != len(set(ids)):
        raise AdbError("CPU topology contains duplicate CPU ids")
    return topology


def discover_cpu_topology(client: AdbClient) -> CpuTopology:
    script = r'''for cpu_dir in /sys/devices/system/cpu/cpu[0-9]*; do
  [ -d "$cpu_dir" ] || continue
  cpu=${cpu_dir##*/cpu}
  online=1
  online_writable=0
  if [ -e "$cpu_dir/online" ]; then
    online_writable=0
    [ -w "$cpu_dir/online" ] && online_writable=1
    read -r online < "$cpu_dir/online" 2>/dev/null || online=unknown
  fi
  capacity=unknown; cluster=unknown; core=unknown; midr=unknown
  read -r capacity < "$cpu_dir/cpu_capacity" 2>/dev/null
  read -r cluster < "$cpu_dir/topology/cluster_id" 2>/dev/null
  read -r core < "$cpu_dir/topology/core_id" 2>/dev/null
  read -r midr < "$cpu_dir/regs/identification/midr_el1" 2>/dev/null
  printf 'CORE|%s|%s|%s|%s|%s|%s|%s\n' "$cpu" "$online" "$online_writable" "$capacity" "$cluster" "$core" "$midr"
done'''
    return parse_cpu_topology(client.run_root_script(script))


def _optional_int(value: str) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
