import unittest

from cpu_efficiency_lab.preflight import parse_preflight, report_dict


OUTPUT = """ROOT_UID|0
ROOT_CONTEXT|u:r:su:s0
SELINUX|Enforcing
PROP|model|example-model
PROP|device|example-device
PROP|soc|SM8750
PROP|android|16
KERNEL|Linux localhost test
NODE|current_now|present|readable|165000
NODE|voltage_now|present|readable|4397000
NODE|power_now|absent|unreadable|
NODE|status|present|readable|Discharging
POLICY|policy0|0-5|384000 556800|384000|1996800|384000|3532800|walt
"""


class PreflightTests(unittest.TestCase):
    def test_parse_device_nodes_and_policy(self):
        report = parse_preflight("device-serial", OUTPUT)
        self.assertTrue(report.ready_for_capture)
        self.assertEqual(report.soc, "SM8750")
        self.assertFalse(report.nodes["power_now"]["present"])
        self.assertEqual(report.policies[0].related_cpus, "0-5")
        self.assertEqual(report.policies[0].scaling_max_khz, 1996800)
        self.assertEqual(report_dict(report)["serial"], "device-serial")

    def test_capture_requires_root_and_required_nodes(self):
        no_root = OUTPUT.replace("ROOT_UID|0", "ROOT_UID|2000")
        no_current = OUTPUT.replace(
            "NODE|current_now|present|readable|165000",
            "NODE|current_now|present|unreadable|",
        )
        self.assertFalse(parse_preflight("x", no_root).ready_for_capture)
        self.assertFalse(parse_preflight("x", no_current).ready_for_capture)


if __name__ == "__main__":
    unittest.main()
