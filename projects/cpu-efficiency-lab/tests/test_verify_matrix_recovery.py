import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from cpu_efficiency_lab.adb import AdbError
from cpu_efficiency_lab.models import CpuPolicy


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("verify_matrix_recovery", ROOT / "scripts" / "verify_matrix_recovery.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class VerifyMatrixRecoveryTests(unittest.TestCase):
    def test_no_device_access_without_explicit_confirmation(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(module, "AdbClient") as client:
            with self.assertRaisesRegex(ValueError, "confirm-device-writes"):
                module.main(["--serial", "phone", "--output", str(Path(directory) / "evidence")])
            client.assert_not_called()
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_interior_frequency_is_strictly_inside_existing_limits(self):
        policy = CpuPolicy("policy0", "0 1", "384000 556800 960000 1363200 1996800",
                           384000, 1996800, 384000, 3532800, "walt")
        report = SimpleNamespace(policies=(policy,))
        self.assertEqual(module._interior_frequency(report, "policy0"), 960000)
        policy2 = CpuPolicy("policy6", "6 7", "1017600 1209600", 1017600, 1209600,
                            1017600, 4320000, "walt")
        with self.assertRaisesRegex(AdbError, "no interior frequency"):
            module._interior_frequency(SimpleNamespace(policies=(policy2,)), "policy6")

    def test_only_journal_restored_without_taint_counts_as_restored(self):
        good = "RECOVERY_FILE|policy-policy0.state\npolicy0 400 2000 1200 RESTORED\n"
        self.assertTrue(module._journal_restored(good, "policy0"))
        self.assertFalse(module._journal_restored(good + "RECOVERY_FILE|policy-policy0.state.tainted\nOBSERVED_POLICY_DRIFT\n", "policy0"))
        self.assertFalse(module._journal_restored(good, "policy6"))
        self.assertFalse(module._journal_restored(good.replace("RESTORED", "LOCKED"), "policy0"))

    def test_external_restriction_requires_both_marker_and_taint(self):
        restricted = (
            "POLICY_RESTORE|policy0|1152000|1996800|CONSERVATIVE\n"
            "POLICY_RESTORE_RESTRICTED|policy0|1152000|1996800\n"
            "RECOVERY_FILE|policy-policy0.state.tainted\nOBSERVED_POLICY_DRIFT\n"
        )
        self.assertEqual(module._external_restriction_policies(restricted, ["policy0"]), {"policy0"})
        # A restriction without the taint marker is not attributable to the system.
        self.assertEqual(
            module._external_restriction_policies(
                "POLICY_RESTORE_RESTRICTED|policy0|1152000|1996800\n", ["policy0"]), set())
        # A taint belonging to another policy must not excuse this one.
        self.assertEqual(module._external_restriction_policies(restricted, ["policy6"]), set())
        # Plain successful restoration is not an external restriction.
        ok = ("POLICY_RESTORE|policy0|1152000|1996800|JOURNALED\n"
              "WATCHDOG_RECOVERY_OK|0\n")
        self.assertEqual(module._external_restriction_policies(ok, ["policy0"]), set())

    def test_cases_are_fault_injections_not_a_measurement_sweep(self):
        modes = {case[1] for case in module.CASES}
        self.assertNotIn("0", modes)
        self.assertTrue({"after_policy_max", "after_policy_min", "during_restore_after_min", "after_policy6_max", "during_benchmark"}.issubset(modes))
        self.assertEqual(len({case[0] for case in module.CASES}), len(module.CASES))


if __name__ == "__main__":
    unittest.main()
