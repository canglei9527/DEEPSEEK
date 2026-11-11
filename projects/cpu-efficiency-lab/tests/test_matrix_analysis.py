import unittest
from dataclasses import replace

from cpu_efficiency_lab.matrix_analysis import (
    aggregate_matrix,
    analyze_matrix_runs,
    choose_retest_configs,
    parse_matrix_log,
    rank_single_core_order,
)
from cpu_efficiency_lab.matrix_plan import MatrixConfig
from cpu_efficiency_lab.topology import CpuCore, CpuTopology


def sample(config, rep, phase, t, current, freq0="384000", freq6="NA", online="0,4"):
    return "M|%s|%d|%s|%.2f|%d|%.2f|%.2f|%d|%.2f|Discharging|27000|90|35000|%s|%s|5000000|%s" % (
        config, rep, phase, t, current, t + 0.01, t + 0.02, 4300000, t + 0.03, freq0, freq6, online
    )


class MatrixAnalysisTests(unittest.TestCase):
    def test_old_samples_without_capacity_remain_readable(self):
        new = sample("legacy", 1, "active", 1, 300000)
        old = new.replace("|Discharging|27000|90|35000|", "|Discharging|27000|35000|")
        readings, _ = parse_matrix_log(old)
        self.assertEqual(len(readings), 1)
        self.assertIsNone(readings[0].sample.capacity_percent)
        self.assertEqual(readings[0].sample.charge_counter_uah, 5000000)
        self.assertEqual(readings[0].cpu_temp_mc, 35000)
        self.assertEqual(readings[0].online_cpus, (0, 4))

    def test_parses_synchronized_workers_and_computes_device_battery_metrics(self):
        config = MatrixConfig("B-CPU4", "B", (4,), (("policy0", 384000),), 1, 3, 1)
        lines = [
            sample(config.config_id, 0, "baseline_pre", 0, 100000),
            sample(config.config_id, 0, "baseline_pre", 1, 100000),
            "RUN|B-CPU4|1|4|4",
            "B|META|scalar_mix_v2|cpus=4|workers=1|batch=4096|warmup_s=1|measure_s=3",
            "B|MEASURE_BEGIN|4|1.000000|4",
            sample(config.config_id, 1, "active", 1, 300000),
            sample(config.config_id, 1, "active", 2, 300000),
            sample(config.config_id, 1, "active", 3, 300000),
            sample(config.config_id, 1, "active", 4, 300000),
            "B|WORKER|4|4|1000|30000|3.000000|10000.0|1234",
            "B|MEASURE_END|4|4.000000|3.000000|30000|10000.0|1234",
            sample(config.config_id, 0, "baseline_post", 4, 100000),
            sample(config.config_id, 0, "baseline_post", 5, 100000),
        ]
        readings, runs = parse_matrix_log("\n".join(lines))
        self.assertEqual(len(readings), 8)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].cpus, (4,))
        self.assertEqual(runs[0].workers[0].observed_cpu, 4)
        self.assertEqual(readings[0].sample.capacity_percent, 90)
        self.assertEqual(readings[0].sample.charge_counter_uah, 5000000)
        metrics = analyze_matrix_runs((config,), readings, runs)
        self.assertEqual(len(metrics), 1)
        metric = metrics[0]
        self.assertTrue(metric.valid, metric.invalid_reason)
        self.assertFalse(metric.strict_single_core_isolation)
        self.assertEqual(metric.online_cpus, "0,4")
        self.assertEqual(metric.background_online_cpus, "0")
        self.assertAlmostEqual(metric.load_power_w, 1.29, places=2)
        self.assertGreater(metric.operations_per_total_joule, 0)
        self.assertAlmostEqual(metric.policy0_match_fraction, 1.0)
        self.assertTrue(metric.baseline_valid)
        self.assertTrue(metric.incremental_valid)

    def test_nonpositive_incremental_power_does_not_discard_valid_total_efficiency(self):
        config = MatrixConfig("B-CPU4", "B", (4,), (("policy0", 384000),), 1, 3, 2)
        lines = [
            sample("B-CPU4", 0, "baseline_pre", 0, 300000),
            sample("B-CPU4", 0, "baseline_pre", 1, 300000),
            "RUN|B-CPU4|1|4|4", "B|MEASURE_BEGIN|4|1.0|4",
            *[sample("B-CPU4", 1, "active", t, 100000) for t in (1, 2, 3, 4)],
            "B|WORKER|4|4|100|3000|3.0|1000|1234",
            "B|MEASURE_END|4|4.0|3.0|3000|1000|1234",
            sample("B-CPU4", 0, "baseline_post", 4, 300000),
            sample("B-CPU4", 0, "baseline_post", 5, 300000),
        ]
        readings, runs = parse_matrix_log("\n".join(lines))
        metric = analyze_matrix_runs((config,), readings, runs)[0]
        self.assertTrue(metric.valid, metric.invalid_reason)
        self.assertTrue(metric.baseline_valid)
        self.assertFalse(metric.incremental_valid)
        self.assertEqual(metric.incremental_invalid_reason, "non-positive incremental battery power")
        self.assertIsNone(metric.operations_per_incremental_joule)
        self.assertGreater(metric.operations_per_total_joule, 0)
        aggregate = aggregate_matrix([metric])[0]
        self.assertTrue(aggregate["valid"])
        self.assertEqual(aggregate["valid_incremental_repetitions"], 0)
        self.assertIsNone(aggregate["incremental_power_w_median"])
        self.assertIsNotNone(aggregate["operations_per_total_joule_median"])
        repeated_config = replace(config, repetitions=2)
        repeated_metric = replace(metric, required_repetitions=2)
        self.assertFalse(aggregate_matrix([repeated_metric], [repeated_config])[0]["valid"])
        second = replace(repeated_metric, repetition=2)
        complete = aggregate_matrix([repeated_metric, second], [repeated_config])[0]
        self.assertTrue(complete["valid"])
        self.assertEqual(complete["required_repetitions"], 2)
        self.assertFalse(aggregate_matrix([repeated_metric, repeated_metric], [repeated_config])[0]["valid"])
        skipped = replace(repeated_metric, repetition=3)
        self.assertFalse(aggregate_matrix([repeated_metric, skipped], [repeated_config])[0]["valid"])
        candidate = MatrixConfig("C-E2", "C", (1, 2), (("policy0", 384000),), 1, 3, 2, repetitions=2)
        first_candidate = replace(repeated_metric, config_id=candidate.config_id, stage="C", target_cpus="1,2")
        self.assertEqual(choose_retest_configs([candidate], [first_candidate]), [])
        self.assertEqual(choose_retest_configs([candidate], [first_candidate, replace(first_candidate, repetition=2)]), [candidate])

    def test_missing_one_side_baseline_does_not_invalidate_total_efficiency(self):
        config = MatrixConfig("B-CPU4", "B", (4,), (("policy0", 384000),), 1, 3, 2)
        lines = [
            sample("B-CPU4", 0, "baseline_pre", 0, 100000),
            sample("B-CPU4", 0, "baseline_pre", 1, 100000),
            "RUN|B-CPU4|1|4|4", "B|MEASURE_BEGIN|4|1.0|4",
            *[sample("B-CPU4", 1, "active", t, 300000) for t in (1, 2, 3, 4)],
            "B|WORKER|4|4|100|3000|3.0|1000|1234",
            "B|MEASURE_END|4|4.0|3.0|3000|1000|1234",
        ]
        readings, runs = parse_matrix_log("\n".join(lines))
        metric = analyze_matrix_runs((config,), readings, runs)[0]
        self.assertTrue(metric.valid, metric.invalid_reason)
        self.assertFalse(metric.baseline_valid)
        self.assertFalse(metric.incremental_valid)
        self.assertEqual(metric.incremental_invalid_reason, "missing paired idle baseline")
        self.assertIsNone(metric.baseline_power_w)
        self.assertGreater(metric.operations_per_total_joule, 0)

    def test_frequency_and_affinity_failures_invalidate_the_run(self):
        config = MatrixConfig("B-CPU6", "B", (6,), (("policy6", 1017600),), 1, 3, 1)
        lines = [
            sample("B-CPU6", 0, "baseline_pre", 0, 100000, freq0="NA", freq6="1017600", online="0,6"),
            sample("B-CPU6", 0, "baseline_pre", 1, 100000, freq0="NA", freq6="1017600", online="0,6"),
            "RUN|B-CPU6|1|6|6",
            "B|MEASURE_BEGIN|6|1.000000|6",
            sample("B-CPU6", 1, "active", 1, 300000, freq0="NA", freq6="1209600", online="0,6"),
            sample("B-CPU6", 1, "active", 2, 300000, freq0="NA", freq6="1209600", online="0,6,7"),
            sample("B-CPU6", 1, "active", 3, 300000, freq0="NA", freq6="1209600", online="0,6"),
            sample("B-CPU6", 1, "active", 4, 300000, freq0="NA", freq6="1209600", online="0,6"),
            "B|WORKER|6|7|1000|30000|3.0|10000|1234",
            "B|MEASURE_END|6|4.0|3.0|30000|10000|1234",
            sample("B-CPU6", 0, "baseline_post", 4, 100000, freq0="NA", freq6="1017600", online="0,6"),
            sample("B-CPU6", 0, "baseline_post", 5, 100000, freq0="NA", freq6="1017600", online="0,6"),
        ]
        readings, runs = parse_matrix_log("\n".join(lines))
        metric = analyze_matrix_runs((config,), readings, runs)[0]
        self.assertFalse(metric.valid)
        self.assertIn("worker affinity/count mismatch", metric.invalid_reason)
        self.assertIn("policy6 target frequency not held", metric.invalid_reason)
        self.assertIn("CPU online mask not held", metric.invalid_reason)

    def test_ranking_and_retest_selection_ignore_invalid_rows(self):
        topology = CpuTopology(tuple(
            CpuCore(cpu, True, cpu != 0, 792 if cpu < 6 else 1024, 0 if cpu < 6 else 1,
                    cpu if cpu < 6 else cpu - 6, "midr")
            for cpu in range(8)
        ))
        configs = [
            MatrixConfig("B-E1", "B", (1,), (("policy0", 384000),), 1, 2, 1),
            MatrixConfig("B-E2", "B", (2,), (("policy0", 384000),), 1, 2, 1),
            MatrixConfig("C-E2", "C", (1, 2), (("policy0", 384000),), 1, 2, 1),
            MatrixConfig("D-E1P1", "D", (1, 6), (("policy0", 384000), ("policy6", 1017600)), 1, 2, 1),
        ]
        # Synthetic valid rows are built from a real parsed run to exercise the production metric path.
        raw = []
        for cpu, config_id, current, iterations in ((1, "B-E1", 100000, 1000), (2, "B-E2", 75000, 2000)):
            raw.extend([
                sample(config_id, 0, "baseline_pre", 0, 50000, online="0,%d" % cpu),
                sample(config_id, 0, "baseline_pre", 1, 50000, online="0,%d" % cpu),
                "RUN|%s|1|%d|%d" % (config_id, cpu, cpu),
                "B|MEASURE_BEGIN|%d|1.0|%d" % (cpu, cpu),
                sample(config_id, 1, "active", 1, current, online="0,%d" % cpu),
                sample(config_id, 1, "active", 2, current, online="0,%d" % cpu),
                sample(config_id, 1, "active", 3, current, online="0,%d" % cpu),
                "B|WORKER|%d|%d|100|%d|2.0|1000|%d" % (cpu, cpu, iterations, iterations),
                "B|MEASURE_END|%d|3.0|2.0|%d|1000|%d" % (cpu, iterations, iterations),
                sample(config_id, 0, "baseline_post", 3, 50000, online="0,%d" % cpu),
                sample(config_id, 0, "baseline_post", 4, 50000, online="0,%d" % cpu),
            ])
        readings, runs = parse_matrix_log("\n".join(raw))
        metrics = analyze_matrix_runs(configs, readings, runs)
        e_order, p_order = rank_single_core_order(configs, metrics, topology)
        self.assertEqual(e_order[0], 2)
        self.assertEqual(set(p_order), {6, 7})
        chosen = choose_retest_configs(configs, metrics, limit=2)
        self.assertEqual(chosen, [])
        # Invalid/ineligible stage rows cannot be promoted into candidate retests.


if __name__ == "__main__":
    unittest.main()
