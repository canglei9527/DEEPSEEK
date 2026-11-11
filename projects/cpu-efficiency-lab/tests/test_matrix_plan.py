import unittest

from cpu_efficiency_lab.matrix_plan import (
    build_stage_a,
    build_stage_b,
    build_stage_c,
    build_stage_d,
    all_frequency_levels,
    frequency_levels,
    total_plan_seconds,
)
from cpu_efficiency_lab.models import CpuPolicy, PreflightReport
from cpu_efficiency_lab.topology import CpuCore, CpuTopology


class MatrixPlanTests(unittest.TestCase):
    def setUp(self):
        self.topology = CpuTopology(
            tuple(
                CpuCore(
                    cpu_id=cpu,
                    online=True,
                    online_writable=(cpu != 0),
                    capacity=792 if cpu < 6 else 1024,
                    cluster_id=0 if cpu < 6 else 1,
                    core_id=cpu if cpu < 6 else cpu - 6,
                    midr="midr-e" if cpu < 6 else "midr-p",
                )
                for cpu in range(8)
            )
        )
        self.report = PreflightReport(
            serial="phone",
            model="model",
            device="device",
            soc="SM8750",
            android_release="16",
            kernel="kernel",
            root_uid="0",
            root_context="u:r:su:s0",
            selinux="Enforcing",
            nodes={
                "current_now": {"readable": True},
                "voltage_now": {"readable": True},
                "status": {"readable": True, "value": "Discharging"},
            },
            policies=(
                CpuPolicy("policy0", "0 1 2 3 4 5", "384000 556800 748800 960000 1152000 1363200 1555200 1785600 1996800 2227200", 384000, 1996800, 384000, 3532800, "walt"),
                CpuPolicy("policy6", "6 7", "1017600 1209600 1401600 1689600 1958400 2246400", 1017600, 1958400, 1017600, 4320000, "walt"),
            ),
        )

    def test_eight_core_single_frequency_stage_and_system_keeper(self):
        stage = build_stage_b(self.topology, self.report)
        self.assertEqual(len(stage), 64)
        self.assertTrue(all(config.baseline_s >= 2 for config in stage))
        self.assertTrue(all(config.repetitions == 2 for config in stage))
        expected_e = all_frequency_levels(self.report, "policy0")
        expected_p = all_frequency_levels(self.report, "policy6")
        for cpu in range(6):
            self.assertEqual({c.target_for("policy0") for c in stage if c.target_cpus == (cpu,)}, set(expected_e))
        for cpu in (6, 7):
            self.assertEqual({c.target_for("policy6") for c in stage if c.target_cpus == (cpu,)}, set(expected_p))
        self.assertEqual({config.target_cpus[0] for config in stage}, set(range(8)))
        for config in stage:
            self.assertLessEqual(config.target_for("policy0") or 0, 1996800)
            self.assertLessEqual(config.target_for("policy6") or 0, 1958400)
        cpu6 = next(config for config in stage if config.target_cpus == (6,))
        self.assertEqual(cpu6.desired_online_cpus, (0, 6))
        self.assertEqual(cpu6.keeper_cpus, (0,))
        cpu0 = next(config for config in stage if config.target_cpus == (0,))
        self.assertEqual(cpu0.desired_online_cpus, (0,))
        self.assertTrue(cpu0.strict_single_core_isolation)
        self.assertFalse(cpu6.strict_single_core_isolation)

    def test_stage_a_has_single_core_and_mixed_restore_smokes(self):
        stage = build_stage_a(self.topology, self.report)
        self.assertEqual(len(stage), 4)
        self.assertEqual({config.stage for config in stage}, {"A"})
        self.assertTrue(any(len(config.target_cpus) > 1 for config in stage))

    def test_multicore_phases_have_expected_grid_sizes_and_no_frequency_unlock(self):
        e_order = (1, 2, 3, 4, 5, 0)
        p_order = (6, 7)
        stage_c = build_stage_c(self.topology, self.report, e_order, p_order)
        stage_d = build_stage_d(self.topology, self.report, e_order, p_order)
        self.assertEqual(len(stage_c), 32)
        self.assertEqual(len(stage_d), 270)
        self.assertTrue(all(config.baseline_s >= 2 and config.repetitions == 2 for config in stage_c + stage_d))
        self.assertEqual(len({config.config_id for config in stage_d}), 270)
        e_freqs = set(all_frequency_levels(self.report, "policy0"))
        p_freqs = set(all_frequency_levels(self.report, "policy6"))
        self.assertEqual({(c.target_for("policy0"), c.target_for("policy6")) for c in stage_d}, {(e, p) for e in e_freqs for p in p_freqs})
        for config in stage_d:
            self.assertLessEqual(config.target_for("policy0"), 1996800)
            self.assertLessEqual(config.target_for("policy6"), 1958400)
            self.assertEqual(config.desired_online_cpus, tuple(sorted(set(config.target_cpus) | {0})))

    def test_frequency_quantiles_are_available_steps_within_current_bounds(self):
        self.assertEqual(frequency_levels(self.report, "policy0", 4), (384000, 960000, 1363200, 1996800))
        self.assertEqual(frequency_levels(self.report, "policy6", 3), (1017600, 1401600, 1958400))
        self.assertEqual(all_frequency_levels(self.report, "policy0"), (384000, 556800, 748800, 960000, 1152000, 1363200, 1555200, 1785600, 1996800))
        self.assertEqual(total_plan_seconds(self.topology, self.report), 9576)


if __name__ == "__main__":
    unittest.main()
