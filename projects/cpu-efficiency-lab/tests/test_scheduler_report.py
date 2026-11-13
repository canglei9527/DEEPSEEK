"""Offline scheduler-report integrity, parsing and presentation tests."""

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from cpu_efficiency_lab import scheduler_report


BASE_SOURCES = [
    ("/system/etc/cgroups.json", "system/etc/cgroups.json",
     '{"Cgroups":[{"Controller":"cpuset","Path":"/dev/cpuset"}],"Cgroups2":{"Path":"/sys/fs/cgroup"}}'),
    ("/system/etc/task_profiles.json", "system/etc/task_profiles.json",
     '''{"Attributes":[{"Name":"MaxCPUs","Controller":"cpuset","File":"top-app/cpus"}],
     "Profiles":[{"Name":"MaxPerformance","Actions":[{"Name":"JoinCgroup","Params":{"Controller":"cpuset","Path":"top-app"}}]},
     {"Name":"Quiet","Actions":[{"Name":"SetAttribute","Params":{"Name":"MaxCPUs","Value":"0-5"}}]}],
     "AggregateProfiles":[{"Name":"SCHED_SP_TOP_APP","Profiles":["MaxPerformance","Quiet"]}]}'''),
    ("/vendor/etc/perf/targetresourceconfigs.xml", "vendor/etc/perf/targetresourceconfigs.xml",
     '''<ResourceConfigs><PerfResources>
     <Config MajorValue="0x1" MinorValue="0x1" Supported="no" /> <!-- disabled -->
     <Config MajorValue="0x2" MinorValue="0x0" Node="/sys/kernel/msm_performance/parameters/cpu_min_freq" />
     </PerfResources></ResourceConfigs>'''),
    ("/vendor/etc/perf/targetconfig.xml", "vendor/etc/perf/targetconfig.xml",
     '''<TargetConfig><!-- alternative --><Config1><TargetInfo Target="sun" SocIds="618" CpufreqGov="1" />
     <ClustersInfo Id="0" NumCores="6" Type="big" /></Config1>
     <Config2><TargetInfo Target="tuna" SocIds="694" /><ClustersInfo Id="0" NumCores="2" /></Config2>
     </TargetConfig>'''),
]
SOURCE_PATHS = (
    "/system/etc/cgroups.json", "/system/etc/task_profiles.json",
    "/system/etc/task_profiles/cgroups_35.json", "/system/etc/task_profiles/task_profiles_35.json",
    "/vendor/etc/cgroups.json", "/vendor/etc/task_profiles.json",
    "/vendor/etc/perf/targetresourceconfigs.xml", "/vendor/etc/perf/targetconfig.xml",
)


class SchedulerReportTests(unittest.TestCase):
    def make_export(self, directory, contents=None):
        directory = Path(directory)
        directory.mkdir()
        contents = dict(self.base_contents() if contents is None else contents)
        sources = []
        import hashlib
        for path in SOURCE_PATHS:
            entry = {"source": path, "status": "missing", "size_bytes": None,
                     "sha256": None, "saved_as": None, "error": None}
            if path in contents:
                content, saved_as = contents[path]
                target = directory / saved_as
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                entry.update(status="present", size_bytes=len(content),
                             sha256=hashlib.sha256(content).hexdigest(), saved_as=saved_as)
            sources.append(entry)
        (directory / "manifest.json").write_text(json.dumps({
            "schema": "cpu-lab-scheduler-sources-v1", "collected_utc": "2026-01-02T03:04:05Z",
            "identity": {"ro.product.model": "model-x", "ro.product.device": "device-x",
                         "ro.soc.model": "soc-x", "ro.product.first_api_level": "35"},
            "sources": sources,
        }), encoding="utf-8")
        return directory

    def base_contents(self):
        return {source: (text.encode(), saved_as) for source, saved_as, text in BASE_SOURCES}

    def test_real_export_has_verified_sources_and_does_not_select_vendor_block(self):
        directory = Path("data/scheduler-sources-exe-20261112-204339")
        if not directory.is_dir():
            self.skipTest("real device export is not present")
        report = scheduler_report.analyze_sources(directory)
        self.assertEqual(len(report["sources"]), 4)
        self.assertEqual(len(report["vendor_targets"]), 3)
        self.assertEqual({item["selection"] for item in report["vendor_targets"]},
                         {scheduler_report.PROFILE_SELECTION_KEY})
        self.assertIn("MaxPerformance", {item["name"] for item in report["task_groups"]})
        self.assertIn("SCHED_SP_TOP_APP", report["aggregate_profiles"])
        self.assertIn("/sys/kernel/msm_performance/parameters/cpu_min_freq",
                      {item["node"] for item in report["vendor_resources"]})
        self.assertFalse(any("recommended" in item or "最优" in item for item in report["limitations"]))

    def test_outputs_share_values_and_escape_markup(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.make_export(Path(directory) / "input")
            report = scheduler_report.build_report(source, Path(directory) / "output")
            markdown = (Path(directory) / "output/report.md").read_text(encoding="utf-8")
            page = (Path(directory) / "output/report.html").read_text(encoding="utf-8")
            self.assertEqual(json.loads((Path(directory) / "output/report.json").read_text(encoding="utf-8")), report)
            for needle in ("0x2", "cpu_min_freq", "top-app/cpus", "sun", "tuna"):
                self.assertIn(needle, markdown)
                self.assertIn(needle, page)
            self.assertIn("disabled", page)
            self.assertIn("未记录运行时选择依据", page)
            self.assertNotIn("<TargetInfo Target=", page)
            with self.assertRaises(FileExistsError):
                scheduler_report.build_report(source, Path(directory) / "output")

    def test_aggregate_members_and_unused_attributes_keep_source_and_raw_values(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.make_export(Path(directory) / "input")
            report = scheduler_report.build_report(source, Path(directory) / "output")
            aggregate = next(item for item in report["aggregate_definitions"]
                             if item["name"] == "SCHED_SP_TOP_APP")
            self.assertEqual(aggregate["value"]["Profiles"], ["MaxPerformance", "Quiet"])
            self.assertEqual(aggregate["source"], "/system/etc/task_profiles.json")
            attribute = next(item for item in report["attribute_definitions"] if item["name"] == "MaxCPUs")
            self.assertEqual(attribute["value"]["File"], "top-app/cpus")
            for name in ("report.md", "report.html"):
                text = (Path(directory) / "output" / name).read_text(encoding="utf-8")
                self.assertIn("MaxPerformance", text)
                self.assertIn("Quiet", text)
                self.assertIn("聚合定义", text)

    def test_api_and_vendor_overrides_preserve_each_definition(self):
        with tempfile.TemporaryDirectory() as directory:
            contents = self.base_contents()
            contents["/system/etc/task_profiles/cgroups_35.json"] = (
                b'{"Cgroups":[],"Cgroups2":{"Path":"/sys/fs/cgroup/api"}}',
                "system/etc/task_profiles/cgroups_35.json")
            contents["/system/etc/task_profiles/task_profiles_35.json"] = (
                b'{"Profiles":[{"Name":"Quiet","Actions":[]}]}',
                "system/etc/task_profiles/task_profiles_35.json")
            contents["/vendor/etc/task_profiles.json"] = (
                b'{"Attributes":[{"Name":"MaxCPUs","Controller":"cpuset","File":"background/cpus"}],'
                b'"Profiles":[{"Name":"Quiet","Actions":[{"Name":"SetAttribute",'
                b'"Params":{"Name":"MaxCPUs","Value":"0-3"}}]}]}',
                "vendor/etc/task_profiles.json")
            source = self.make_export(Path(directory) / "input", contents)
            report = scheduler_report.analyze_sources(source)
            self.assertEqual(len(report["sources"]), 7)
            quiet = next(item for item in report["task_groups"] if item["name"] == "Quiet")
            self.assertEqual(quiet["source"], "/vendor/etc/task_profiles.json")
            self.assertEqual(quiet["actions"][0]["parameters"]["Value"], "0-3")
            self.assertEqual(sum(item["name"] == "Quiet" for item in report["profile_definitions"]), 3)
            self.assertEqual(len(report["definition_overrides"]), 3)
            self.assertIn("静态覆盖", scheduler_report.render_markdown(report))

    def test_invalid_manifest_source_type_returns_cli_error_without_output(self):
        from cpu_efficiency_lab.cli import main
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stderr(io.StringIO()) as error:
            source = self.make_export(Path(directory) / "input")
            manifest_path = source / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["sources"][0]["source"] = []
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            code = main(["scheduler-report", "--input", str(source), "--output", str(Path(directory) / "out")])
            self.assertEqual(code, 2)
            self.assertIn("error:", error.getvalue())
            self.assertFalse((Path(directory) / "out").exists())

    def test_bad_paths_references_and_formats_fail_before_output(self):
        cases = ("unsafe_path", "manifest_encoding", "unknown_profile", "cyclic_profile",
                 "invalid_attribute", "broken_xml", "duplicate_profile")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                contents = self.base_contents()
                profile_path = "/system/etc/task_profiles.json"
                document = json.loads(contents[profile_path][0])
                if case == "unknown_profile":
                    document["AggregateProfiles"][0]["Profiles"] = ["Unknown"]
                elif case == "cyclic_profile":
                    document["AggregateProfiles"][0]["Profiles"] = ["SCHED_SP_TOP_APP"]
                elif case == "invalid_attribute":
                    document["Profiles"][1]["Actions"][0]["Params"]["Name"] = []
                elif case == "duplicate_profile":
                    document["Profiles"].append(document["Profiles"][0])
                contents[profile_path] = (json.dumps(document).encode(), contents[profile_path][1])
                if case == "broken_xml":
                    path = "/vendor/etc/perf/targetconfig.xml"
                    contents[path] = (b"<TargetConfig>", contents[path][1])
                source = self.make_export(Path(directory) / "input", contents)
                if case == "unsafe_path":
                    manifest_path = source / "manifest.json"
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    manifest["sources"][0]["saved_as"] = "../system/etc/cgroups.json"
                    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                elif case == "manifest_encoding":
                    (source / "manifest.json").write_bytes(b"\xff")
                with self.assertRaises((ValueError, OSError)):
                    scheduler_report.build_report(source, Path(directory) / "output")
                self.assertFalse((Path(directory) / "output").exists())

    def test_vendor_target_keeps_additional_mpam_values(self):
        with tempfile.TemporaryDirectory() as directory:
            contents = self.base_contents()
            path = "/vendor/etc/perf/targetconfig.xml"
            text = contents[path][0].replace(b"</Config1>", b'<MPAMInfo Enabled="1"><Group Id="9" /></MPAMInfo></Config1>')
            contents[path] = (text, contents[path][1])
            source = self.make_export(Path(directory) / "input", contents)
            report = scheduler_report.analyze_sources(source)
            block = report["vendor_targets"][0]["configuration"]
            mpam = next(item for item in block["children"] if item["tag"] == "MPAMInfo")
            self.assertEqual(mpam["children"][0]["attributes"]["Id"], "9")
            self.assertIn("MPAMInfo", scheduler_report.render_markdown(report))
            self.assertIn("MPAMInfo", scheduler_report.render_html(report))

    def test_comment_marker_in_string_is_not_stripped(self):
        document = {"Attributes": [{"Name": "Url", "File": "https://example.invalid/a//b"}],
                    "Profiles": [], "AggregateProfiles": []}
        self.assertEqual(scheduler_report._parse_json(json.dumps(document).encode(), "test"), document)
        with self.assertRaises(ValueError):
            scheduler_report._parse_json(b'{"Profiles": [}', "broken")

    def test_integrity_or_required_source_failure_creates_no_output(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.make_export(Path(directory) / "input")
            target = source / "system/etc/task_profiles.json"
            target.write_text(target.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                scheduler_report.build_report(source, Path(directory) / "output")
            self.assertFalse((Path(directory) / "output").exists())

    def test_missing_required_kind_fails_cleanly(self):
        with tempfile.TemporaryDirectory() as directory:
            contents = self.base_contents()
            del contents["/vendor/etc/perf/targetconfig.xml"]
            source = self.make_export(Path(directory) / "input", contents)
            with self.assertRaisesRegex(ValueError, "targetconfig"):
                scheduler_report.build_report(source, Path(directory) / "output")
            self.assertFalse((Path(directory) / "output").exists())

    def test_cli_returns_error_for_invalid_input(self):
        from cpu_efficiency_lab.cli import main
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stderr(io.StringIO()) as error:
            code = main(["scheduler-report", "--input", directory, "--output", str(Path(directory) / "out")])
            self.assertEqual(code, 2)
            self.assertIn("error:", error.getvalue())
            self.assertFalse((Path(directory) / "out").exists())


if __name__ == "__main__":
    unittest.main()
