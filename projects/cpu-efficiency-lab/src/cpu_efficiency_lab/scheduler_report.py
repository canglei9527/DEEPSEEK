"""Create a descriptive, source-checked report from exported scheduler files."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
from pathlib import Path, PurePosixPath
import sys
from typing import Dict, List, Optional, Sequence, Tuple
from xml.etree import ElementTree

from .config import SCHEDULER_SOURCE_PATHS


INPUT_SCHEMA = "cpu-lab-scheduler-sources-v1"
REPORT_SCHEMA = "cpu-lab-scheduler-report-v1"
KIND_CGROUPS = "cgroups"
KIND_PROFILES = "task_profiles"
KIND_RESOURCES = "targetresourceconfigs"
KIND_TARGET = "targetconfig"
REQUIRED_KINDS = (KIND_CGROUPS, KIND_PROFILES, KIND_RESOURCES, KIND_TARGET)
SOURCE_KINDS = {
    "/system/etc/cgroups.json": KIND_CGROUPS,
    "/system/etc/task_profiles/cgroups_{first_api_level}.json": KIND_CGROUPS,
    "/vendor/etc/cgroups.json": KIND_CGROUPS,
    "/system/etc/task_profiles.json": KIND_PROFILES,
    "/system/etc/task_profiles/task_profiles_{first_api_level}.json": KIND_PROFILES,
    "/vendor/etc/task_profiles.json": KIND_PROFILES,
    "/vendor/etc/perf/targetresourceconfigs.xml": KIND_RESOURCES,
    "/vendor/etc/perf/targetconfig.xml": KIND_TARGET,
}
# The exported manifest does not contain runtime SoC ID or profile-selection evidence.
PROFILE_SELECTION_KEY = "selection_not_recorded_in_export"
LIMITATIONS = (
    "报告描述导出目录中的当前文件，未证明它们与原厂固件逐字节一致。",
    "这些配置不覆盖完整内核调度器、温控仲裁、固件 vote 或运行时任务选择。",
    "读取时间先后不构成原子快照；报告不执行任何设备操作或生成调参建议。",
    "厂商资源 Node 是配置映射，不表示节点在当前设备存在，也不表示已读取节点值。",
)


def _list_of_dicts(value, label: str) -> List[dict]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError("%s must be a list of objects" % label)
    return value


def _required_text(document: dict, key: str, label: str) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError("%s requires a non-empty string %s" % (label, key))
    return value


def _source_path(template: str, api: str) -> str:
    return template.format(first_api_level=api)


def _safe_saved_as(value, source: str) -> Path:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ValueError("invalid saved_as for %s" % source)
    relative = PurePosixPath(value)
    if (relative.is_absolute() or any(part in ("", ".", "..") for part in relative.parts)
            or ":" in value or not value.endswith(PurePosixPath(source).name)):
        raise ValueError("saved_as must be a safe source-relative path for %s" % source)
    return Path(*relative.parts)


def _load_manifest(directory: Path) -> Tuple[dict, Dict[str, List[dict]]]:
    try:
        with (directory / "manifest.json").open(encoding="utf-8") as stream:
            manifest = json.load(stream)
    except json.JSONDecodeError as exc:
        raise ValueError("invalid manifest JSON: %s" % exc) from exc
    if not isinstance(manifest, dict) or manifest.get("schema") != INPUT_SCHEMA:
        raise ValueError("manifest schema must be %s" % INPUT_SCHEMA)
    identity = manifest.get("identity")
    if not isinstance(identity, dict):
        raise ValueError("manifest identity must be an object")
    for name in ("ro.product.model", "ro.product.device", "ro.product.first_api_level"):
        if not isinstance(identity.get(name), str):
            raise ValueError("manifest identity requires %s" % name)
    api = identity["ro.product.first_api_level"]
    if not api.isdigit() or api == "0":
        raise ValueError("manifest first API level is invalid")
    sources = _list_of_dicts(manifest.get("sources"), "manifest sources")
    expected = [_source_path(path, api) for path in SCHEDULER_SOURCE_PATHS]
    observed = [entry.get("source") for entry in sources]
    if (any(not isinstance(path, str) for path in observed)
            or len(observed) != len(set(observed)) or set(observed) != set(expected)):
        raise ValueError("manifest sources must exactly match the supported export inventory")
    sources = sorted(sources, key=lambda entry: expected.index(entry["source"]))
    if set(SOURCE_KINDS) != set(SCHEDULER_SOURCE_PATHS):
        raise ValueError("unsupported scheduler source inventory")
    result = {kind: [] for kind in REQUIRED_KINDS}
    resolved_kinds = {_source_path(template, api): kind for template, kind in SOURCE_KINDS.items()}
    for entry in sources:
        source, status = entry["source"], entry.get("status")
        if status == "missing":
            continue
        if status == "error":
            raise ValueError("export contains a source read error: %s" % source)
        if status != "present":
            raise ValueError("invalid status for %s" % source)
        relative = _safe_saved_as(entry.get("saved_as"), source)
        target = (directory / relative).resolve()
        try:
            target.relative_to(directory.resolve())
        except ValueError as exc:
            raise ValueError("saved source resolves outside export directory: %s" % source) from exc
        try:
            content = target.read_bytes()
        except OSError as exc:
            raise ValueError("cannot read exported source %s: %s" % (source, exc)) from exc
        if len(content) != entry.get("size_bytes") or hashlib.sha256(content).hexdigest() != entry.get("sha256"):
            raise ValueError("size or SHA-256 mismatch for exported source %s" % source)
        resolved = resolved_kinds[source]
        result[resolved].append({"path": source, "saved_as": relative.as_posix(),
                                 "sha256": entry["sha256"], "content": content})
    missing = [kind for kind in REQUIRED_KINDS if not result[kind]]
    if missing:
        raise ValueError("no readable sources for required configuration types: %s" % ", ".join(missing))
    return manifest, result


def _without_json_comments(content: bytes) -> str:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("scheduler JSON must be UTF-8: %s" % exc) from exc
    output, index, quoted, escaped = [], 0, False, False
    while index < len(text):
        char = text[index]
        if quoted:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            index += 1
        elif char == '"':
            quoted = True
            output.append(char)
            index += 1
        elif char == "/" and index + 1 < len(text) and text[index + 1] == "/":
            newline = text.find("\n", index + 2)
            index = len(text) if newline < 0 else newline
        else:
            output.append(char)
            index += 1
    if quoted:
        raise ValueError("unterminated string in scheduler JSON")
    return "".join(output)


def _parse_json(content: bytes, path: str) -> dict:
    try:
        document = json.loads(_without_json_comments(content))
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError("invalid JSON in %s: %s" % (path, exc)) from exc
    if not isinstance(document, dict):
        raise ValueError("scheduler JSON root must be an object: %s" % path)
    return document


def _profile_maps(document: dict, path: str) -> Tuple[Dict[str, dict], Dict[str, dict]]:
    attributes = {}
    for item in _list_of_dicts(document.get("Attributes", []), path + " Attributes"):
        name = _required_text(item, "Name", path + " attribute")
        if name in attributes:
            raise ValueError("duplicate attribute %s in %s" % (name, path))
        attributes[name] = item
    profiles = {}
    for item in _list_of_dicts(document.get("Profiles", []), path + " Profiles"):
        name = _required_text(item, "Name", path + " profile")
        if name in profiles:
            raise ValueError("duplicate profile %s in %s" % (name, path))
        profiles[name] = item
    return attributes, profiles


def _flatten_profiles(name: str, profiles: Dict[str, dict], attributes: Dict[str, dict], path: str,
                      aggregate_names: set, stack: Tuple[str, ...] = ()) -> List[dict]:
    if name in stack:
        raise ValueError("cyclic aggregate profile in %s: %s" % (path, " -> ".join(stack + (name,))))
    if name not in profiles:
        raise ValueError("undefined profile reference %s in %s" % (name, path))
    if name in aggregate_names:
        for child in profiles[name]["aggregate_members"]:
            for action in _flatten_profiles(child, profiles, attributes, path, aggregate_names, stack + (name,)):
                yield action
        return
    for action in _list_of_dicts(profiles[name].get("Actions"), path + " profile " + name):
        kind = _required_text(action, "Name", path + " action")
        params = action.get("Params")
        if not isinstance(params, dict):
            raise ValueError("action %s in %s must have Params object" % (kind, path))
        rendered = {"action": kind, "profile": name, "parameters": params}
        if kind == "SetAttribute":
            attribute = params.get("Name")
            if not isinstance(attribute, str) or attribute not in attributes:
                raise ValueError("undefined attribute %s in %s" % (attribute, path))
            rendered["attribute"] = attributes[attribute]
        yield rendered


def _parse_profiles(sources: Dict[str, List[dict]]) -> dict:
    attributes, profiles = {}, {}
    attribute_definitions, profile_definitions, aggregate_definitions, overrides = [], [], [], []

    def register(mapping, history, kind, name, item):
        if name in mapping:
            overrides.append({"kind": kind, "name": name, "previous_source": mapping[name]["source"],
                              "source": item["source"]})
        mapping[name] = item
        history.append({"name": name, "source": item["source"], "value": item["value"]})

    for source in sources[KIND_PROFILES]:
        path = source["path"]
        document = _parse_json(source["content"], path)
        source_attributes, source_profiles = _profile_maps(document, path)
        for name, attribute in source_attributes.items():
            register(attributes, attribute_definitions, "attribute", name, {"source": path, "value": attribute})
        for name, profile in source_profiles.items():
            register(profiles, profile_definitions, "profile", name,
                     {"source": path, "value": profile, "Actions": profile.get("Actions", [])})
        local_names = set(source_profiles)
        for item in _list_of_dicts(document.get("AggregateProfiles", []), path + " AggregateProfiles"):
            name = _required_text(item, "Name", path + " aggregate")
            if name in local_names:
                raise ValueError("duplicate aggregate/profile %s in %s" % (name, path))
            local_names.add(name)
            members = item.get("Profiles")
            if not isinstance(members, list) or any(not isinstance(member, str) or not member for member in members):
                raise ValueError("aggregate %s in %s requires profile names" % (name, path))
            register(profiles, aggregate_definitions, "aggregate", name,
                     {"source": path, "value": item, "aggregate_members": members})
    if not profiles:
        raise ValueError("task profiles must define Profiles or AggregateProfiles")
    aggregate_names = {name for name, item in profiles.items() if "aggregate_members" in item}
    for name in sorted(aggregate_names):
        list(_flatten_profiles(name, profiles, attributes, profiles[name]["source"], aggregate_names))
    groups = []
    for name, item in sorted(profiles.items()):
        if "aggregate_members" in item:
            continue
        actions = list(_flatten_profiles(name, profiles, attributes, item["source"], aggregate_names))
        groups.append({"name": name, "source": item["source"], "actions": actions})
    return {"task_groups": groups, "profile_attributes": sorted(attributes),
            "aggregate_profiles": sorted(aggregate_names), "attribute_definitions": attribute_definitions,
            "profile_definitions": profile_definitions, "aggregate_definitions": aggregate_definitions,
            "definition_overrides": overrides}


def _parse_cgroups(sources: Dict[str, List[dict]]) -> List[dict]:
    result = []
    for source in sources[KIND_CGROUPS]:
        path = source["path"]
        document = _parse_json(source["content"], path)
        for item in _list_of_dicts(document.get("Cgroups", []), path + " Cgroups"):
            result.append({"source": path, "version": "v1", "configuration": item})
        version2 = document.get("Cgroups2")
        if version2 is not None:
            if not isinstance(version2, dict):
                raise ValueError("Cgroups2 in %s must be an object" % path)
            result.append({"source": path, "version": "v2", "configuration": version2})
        if not any(item["source"] == path for item in result):
            raise ValueError("no Cgroups or Cgroups2 definitions in %s" % path)
    return result


def _parse_xml(content: bytes, path: str):
    try:
        text = content.decode("utf-8-sig")
        return ElementTree.fromstring(text, parser=ElementTree.XMLParser(
            target=ElementTree.TreeBuilder(insert_comments=True)))
    except (UnicodeDecodeError, ElementTree.ParseError) as exc:
        raise ValueError("invalid UTF-8 XML in %s: %s" % (path, exc)) from exc


def _xml_comment_before(root, node) -> Optional[str]:
    # Comments are labels only, not a replacement for missing opcode attributes.
    for parent in root.iter():
        children = list(parent)
        for index, child in enumerate(children):
            if child is node and index:
                previous = children[index - 1]
                if not isinstance(previous.tag, str) and previous.text:
                    return previous.text.strip()
    return None


def _parse_resources(sources: Dict[str, List[dict]]) -> List[dict]:
    result = []
    for source in sources[KIND_RESOURCES]:
        path = source["path"]
        root = _parse_xml(source["content"], path)
        configs = list(root.iter("Config"))
        if not configs:
            raise ValueError("no vendor resource Config elements in %s" % path)
        for node in configs:
            major, minor = node.get("MajorValue"), node.get("MinorValue")
            if major is None or minor is None:
                raise ValueError("vendor resource in %s lacks MajorValue/MinorValue" % path)
            result.append({"major": major, "minor": minor, "target": node.get("Target"),
                           "supported": node.get("Supported", "not declared"),
                           "node": node.get("Node"), "disp_mode": node.get("DispMode"),
                           "comment": _xml_comment_before(root, node), "source": path})
    return result


def _xml_value(node) -> dict:
    return {"tag": node.tag, "attributes": dict(node.attrib), "text": (node.text or "").strip(),
            "children": [_xml_value(child) for child in node if isinstance(child.tag, str)]}


def _parse_targets(sources: Dict[str, List[dict]]) -> List[dict]:
    result = []
    for source in sources[KIND_TARGET]:
        path = source["path"]
        root = _parse_xml(source["content"], path)
        for block in root:
            if not isinstance(block.tag, str) or not block.tag.startswith("Config"):
                continue
            targets = [dict(node.attrib) for node in block.iter("TargetInfo")]
            clusters = [dict(node.attrib) for node in block.iter("ClustersInfo")]
            if not targets:
                raise ValueError("vendor target block %s in %s lacks TargetInfo" % (block.tag, path))
            result.append({"block": block.tag, "targets": targets, "clusters": clusters,
                           "configuration": _xml_value(block),
                           "selection": PROFILE_SELECTION_KEY, "source": path})
    if not result:
        raise ValueError("no vendor target Config blocks found")
    return result


def analyze_sources(directory: Path) -> dict:
    """Read only an exported directory and return the canonical report model."""
    directory = Path(directory)
    manifest, sources = _load_manifest(directory)
    profiles = _parse_profiles(sources)
    resources = _parse_resources(sources)
    return {
        "schema": REPORT_SCHEMA,
        "device": manifest["identity"],
        "collected_utc": manifest.get("collected_utc"),
        "sources": [{"source": source["path"], "saved_as": source["saved_as"], "sha256": source["sha256"]}
                    for entries in sources.values() for source in entries],
        "load_groups": _parse_cgroups(sources),
        **profiles,
        "vendor_resources": resources,
        "vendor_targets": _parse_targets(sources),
        "limitations": list(LIMITATIONS),
    }


def _value(value) -> str:
    if value is None:
        return "未声明"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def _report_sections(report: dict) -> List[dict]:
    """Build every presentation row from the same structured report model."""
    sections = [{"title": "说明", "paragraphs": list(LIMITATIONS)}]
    sources = [{"配置来源": item["source"], "本地相对路径": item["saved_as"], "SHA-256": item["sha256"]}
               for item in report["sources"]]
    sections.append({"title": "来源与完整性", "rows": sources,
                     "note": "生成报告前已逐文件核对 manifest 中的字节数和 SHA-256。可选覆盖文件未导出时不作存在性推断。"})
    groups = [{"版本": item["version"], **item["configuration"], "来源": item["source"]}
              for item in report["load_groups"]]
    sections.append({"title": "负载分组（cgroup）", "rows": groups,
                     "note": "这是控制器挂载/路径配置，不是 CPU 核心分配或实时 cgroup 状态。"})
    tasks = [{"任务 Profile": group["name"], "来源": group["source"], "动作": _value(group["actions"])}
             for group in report["task_groups"]]
    sections.append({"title": "线程任务归类（task profiles）", "rows": tasks,
                     "paragraphs": ["已导出的属性：%s。" % "、".join(report["profile_attributes"]),
                                    "已导出的聚合 Profile：%s。" % "、".join(report["aggregate_profiles"])],
                     "note": "按基础、API 专用、vendor 文件顺序做静态覆盖归并；这是离线重建，不证明运行时已加载。实际应用由系统调用方决定。"})
    for title, key in (("属性原始定义", "attribute_definitions"), ("Profile 原始定义", "profile_definitions"),
                       ("聚合定义与成员关系", "aggregate_definitions")):
        rows = [{"名称": item["name"], "来源": item["source"], "原始定义": item["value"]}
                for item in report[key]]
        sections.append({"title": title, "rows": rows,
                         "note": "保留每份来源的原值，包括被后续静态覆盖的同名定义。"})
    sections.append({"title": "静态覆盖记录", "rows": report["definition_overrides"],
                     "note": "同名定义按固定来源顺序记录前后来源；缺失的覆盖文件不推断内容，未验证设备运行时选择。"})
    targets = [{"配置块": item["block"], "TargetInfo": _value(item["targets"]),
                "ClustersInfo": _value(item["clusters"]),
                "选择状态": "导出清单未记录运行时选择依据", "来源": item["source"]}
               for item in report["vendor_targets"]]
    sections.append({"title": "厂商目标与性能场景配置", "rows": targets,
                     "note": "CpufreqGov 等字段是厂商配置枚举，不等同于已观测的 governor。未读取运行时 SoC ID，因此不选择某个配置块。"})
    sections.append({"title": "厂商目标完整原始字段",
                     "rows": [{"配置块": item["block"], "来源": item["source"], "完整配置": item["configuration"]}
                              for item in report["vendor_targets"]],
                     "note": "保留 MPAM 等附加字段及嵌套内容；未推断未声明字段的含义。"})
    resources = [{"MajorValue": item["major"], "MinorValue": item["minor"], "Target": item["target"],
                  "Supported": item["supported"], "Node": item["node"], "DispMode": item["disp_mode"],
                  "注释": item["comment"], "来源": item["source"]} for item in report["vendor_resources"]]
    sections.append({"title": "厂商资源映射", "rows": resources,
                     "note": "逐项列出导出中的 Config。Supported 为 XML 原值；没有该属性时标记未声明。Target 的筛选证据未包含在导出中。"})
    return sections


def _markdown_table(rows: Sequence[dict]) -> List[str]:
    if not rows:
        return ["无记录。"]
    keys = list(dict.fromkeys(key for row in rows for key in row))
    def cell(value):
        return html.escape(_value(value), quote=False).replace("|", "\\|").replace("\r", " ").replace("\n", "<br>")
    return ["| " + " | ".join(keys) + " |", "| " + " | ".join("---" for _ in keys) + " |"] + [
        "| " + " | ".join(cell(row.get(key)) for key in keys) + " |" for row in rows]


def render_markdown(report: dict) -> str:
    device = report["device"]
    lines = ["# 官方调度配置离线分析", "",
             "设备型号：`%s`；设备代号：`%s`；SoC 属性：`%s`。" % (
                 device.get("ro.product.model", ""), device.get("ro.product.device", ""),
                 device.get("ro.soc.model", "未记录")),
             "采集时间（UTC）：`%s`。" % _value(report["collected_utc"]), ""]
    for section in _report_sections(report):
        lines.extend(["## " + section["title"], ""])
        for paragraph in section.get("paragraphs", []):
            lines.extend([paragraph, ""])
        if "note" in section:
            lines.extend([section["note"], ""])
        if "rows" in section:
            lines.extend(_markdown_table(section["rows"]))
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_html(report: dict) -> str:
    def h(value):
        return html.escape(_value(value), quote=True)
    def table(rows):
        if not rows:
            return "<p>无记录。</p>"
        keys = list(dict.fromkeys(key for row in rows for key in row))
        out = ["<div class=table-wrap><table><thead><tr>"]
        out.extend("<th>%s</th>" % h(key) for key in keys)
        out.append("</tr></thead><tbody>")
        for row in rows:
            out.append("<tr>" + "".join("<td>%s</td>" % h(row.get(key)) for key in keys) + "</tr>")
        out.append("</tbody></table></div>")
        return "".join(out)
    device = report["device"]
    out = ["<!doctype html><html lang=zh-CN><head><meta charset=utf-8>",
           "<meta name=viewport content='width=device-width,initial-scale=1'>",
           "<title>官方调度配置离线分析</title><style>",
           ":root{color-scheme:light dark;--bg:#f5f7fb;--fg:#172033;--muted:#526079;--card:#fff;--line:#dbe1eb;--accent:#245da8}",
           "@media(prefers-color-scheme:dark){:root{--bg:#111722;--fg:#e8edf5;--muted:#b2bfd1;--card:#1b2432;--line:#354257;--accent:#8bbcff}}",
           "*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.65 system-ui,'Microsoft YaHei',sans-serif}",
           "main{max-width:1500px;margin:auto;padding:32px 22px}h1{font-size:28px;line-height:1.3;margin:0 0 8px}h2{font-size:21px;border-bottom:2px solid var(--accent);padding-bottom:8px}",
           "section{margin:26px 0}p{max-width:105ch}.meta,.note{color:var(--muted)}.table-wrap{overflow-x:auto;border:1px solid var(--line);border-radius:8px;background:var(--card)}",
           "table{border-collapse:collapse;width:100%;font-size:13px}th,td{text-align:left;vertical-align:top;padding:9px 11px;border-bottom:1px solid var(--line);overflow-wrap:anywhere}",
           "th{background:var(--card);position:sticky;top:0}td:last-child,th:last-child{min-width:240px}td{font-family:ui-monospace,Consolas,monospace}th{font-family:inherit}",
           "</style></head><body><main><h1>官方调度配置离线分析</h1>",
           "<p class=meta>设备型号：%s　设备代号：%s　SoC 属性：%s<br>采集时间（UTC）：%s</p>" % (
               h(device.get("ro.product.model", "")), h(device.get("ro.product.device", "")),
               h(device.get("ro.soc.model", "未记录")), h(report["collected_utc"]))]
    for section in _report_sections(report):
        out.append("<section><h2>%s</h2>" % h(section["title"]))
        out.extend("<p>%s</p>" % h(paragraph) for paragraph in section.get("paragraphs", []))
        if "note" in section:
            out.append("<p class=note>%s</p>" % h(section["note"]))
        if "rows" in section:
            out.append(table(section["rows"]))
        out.append("</section>")
    out.append("</main></body></html>\n")
    return "\n".join(out)


def build_report(input_directory: Path, output_directory: Path) -> dict:
    """Validate all input before creating a new report directory."""
    report = analyze_sources(input_directory)
    contents = {"report.html": render_html(report), "report.md": render_markdown(report),
                "report.json": json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"}
    output_directory = Path(output_directory)
    output_directory.mkdir(parents=True, exist_ok=False)
    for name, content in contents.items():
        with (output_directory / name).open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
    return report


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="existing scheduler-sources export directory")
    parser.add_argument("--output", required=True, type=Path, help="new output directory")
    args = parser.parse_args(argv)
    try:
        report = build_report(args.input, args.output)
    except (OSError, ValueError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    print(json.dumps({"output": str(args.output), "sources": len(report["sources"]),
                      "task_profiles": len(report["task_groups"]),
                      "vendor_resources": len(report["vendor_resources"]),
                      "vendor_target_blocks": len(report["vendor_targets"]),
                      "factory_original_verified": False}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
