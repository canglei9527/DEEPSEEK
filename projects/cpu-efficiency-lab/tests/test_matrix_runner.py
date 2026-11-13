"""Tests for the firmware-vote lock self-check used before a matrix run."""

from __future__ import annotations

import unittest

from cpu_efficiency_lab.matrix_runner import firmware_vote_locked_policies
from cpu_efficiency_lab.models import CpuPolicy, PreflightReport


def _report(policy0_min, policy0_max, policy0_avail):
    return PreflightReport(
        serial="phone", model="model", device="device", soc="SM8750",
        android_release="16", kernel="kernel", root_uid="0", root_context="u:r:su:s0",
        selinux="Enforcing",
        nodes={"status": {"readable": True, "value": "Discharging"}},
        policies=(
            CpuPolicy("policy0", "0 1 2 3 4 5", policy0_avail,
                      policy0_min, policy0_max, 384000, 3532800, "performance"),
            CpuPolicy("policy6", "6 7", "1017600 1209600 1401600 1689600 1958400",
                      1017600, 1958400, 1017600, 4320000, "performance"),
        ),
    )


class FirmwareVoteLockTests(unittest.TestCase):
    def test_single_pinned_policy_detected(self):
        # policy0 stuck at min=max=2400000 with only one in-range step.
        report = _report(2400000, 2400000, "384000 556800 2400000 2745600")
        locked = firmware_vote_locked_policies(report, required_levels=3)
        self.assertEqual(len(locked), 1)
        self.assertIn("policy0", locked[0])
        self.assertIn("1 in-range", locked[0])

    def test_healthy_policy_not_flagged(self):
        report = _report(556800, 2745600, "556800 960000 1363200 1996800 2745600")
        self.assertEqual(firmware_vote_locked_policies(report, required_levels=3), [])

    def test_required_levels_threshold(self):
        # Two in-range steps: fine for full (needs 2), insufficient for reduced (needs 3).
        report = _report(1000000, 2000000, "384000 1000000 2000000 3000000")
        self.assertEqual(firmware_vote_locked_policies(report, required_levels=2), [])
        self.assertEqual(len(firmware_vote_locked_policies(report, required_levels=3)), 1)

    def test_out_of_range_steps_not_counted(self):
        # Many available steps exist but none fall inside the pinned window.
        report = _report(2400000, 2400000, "384000 556800 748800 960000 2400000")
        locked = firmware_vote_locked_policies(report, required_levels=2)
        self.assertEqual(len(locked), 1)


if __name__ == "__main__":
    unittest.main()
