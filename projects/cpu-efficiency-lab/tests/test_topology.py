import unittest

from cpu_efficiency_lab.topology import CpuTopology, parse_cpu_topology


class CpuTopologyTests(unittest.TestCase):
    def test_parses_eight_cpu_capacity_clusters(self):
        lines = []
        for cpu in range(8):
            capacity = 792 if cpu < 6 else 1024
            cluster = 0 if cpu < 6 else 1
            midr = "0x514f0014" if cpu < 6 else "0x513f0014"
            lines.append("CORE|%d|1|1|%d|%d|%d|%s" % (cpu, capacity, cluster, cpu % 6, midr))
        topology = parse_cpu_topology("\n".join(lines))
        self.assertEqual(topology.online_cpus, tuple(range(8)))
        self.assertEqual(topology.capacity_groups(), {"E": (0, 1, 2, 3, 4, 5), "P": (6, 7)})
        topology.require_hotplug_controls((6,))

    def test_cpu0_must_remain_online_and_nonzero_workers_need_hotplug(self):
        topology = parse_cpu_topology(
            "CORE|0|1|0|792|0|0|m0\nCORE|1|1|0|792|0|1|m1\nCORE|6|1|1|1024|1|0|m6"
        )
        with self.assertRaisesRegex(ValueError, "CPU0"):
            offline_cpu0 = CpuTopology((topology.cores[0].__class__(0, False, False, 792, 0, 0, "m0"),) + topology.cores[1:])
            offline_cpu0.require_hotplug_controls((6,))
        with self.assertRaisesRegex(ValueError, "hotplug control"):
            topology.require_hotplug_controls((1,))

    def test_rejects_duplicate_ids(self):
        with self.assertRaisesRegex(Exception, "duplicate"):
            parse_cpu_topology("CORE|0|1|1|792|0|0|m0\nCORE|0|1|1|792|0|0|m0")

    def test_capacity_group_requires_two_clusters(self):
        topology = parse_cpu_topology("CORE|0|1|0|792|0|0|m0\nCORE|1|1|1|792|0|1|m1")
        with self.assertRaisesRegex(ValueError, "exactly two"):
            topology.capacity_groups()


if __name__ == "__main__":
    unittest.main()
