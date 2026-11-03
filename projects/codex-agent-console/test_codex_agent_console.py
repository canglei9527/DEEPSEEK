# -*- coding: utf-8 -*-
import json
import sys
import tempfile
import threading
import urllib.error
import urllib.request
import tomllib
import unittest
from unittest import mock
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from codex_agent_console import (
    AgentSettings,
    ANTHROPIC_API_VERSION,
    ConfigStore,
    DesktopBackendProcess,
    DesktopBackendReloader,
    CustomApiBenchmarkRunner,
    CustomApiEndpoint,
    CustomApiResult,
    CustomApiStore,
    CUSTOM_API_CACHE_PROMPT,
    CUSTOM_API_PROMPT,
    DiagnosticResult,
    DUAL_MODE_POLICY_START,
    MANAGED_EXECUTOR_AGENT_NAME,
    SessionTokenTail,
    SessionStatsReader,
    build_dual_mode_policy,
    custom_api_base_url,
    custom_api_endpoint_url,
    choose_custom_api_winners,
    classify_diagnostic_error,
    fetch_custom_api_models,
    find_codex_executable,
    has_dual_mode_policy,
    merge_dual_mode_policy,
    parse_codex_json_event,
    protect_secret,
    summarize_diagnostics,
    summarize_custom_apis,
    unprotect_secret,
    update_toml_values,
    validate_custom_api_endpoint,
)


class ConfigStoreTests(unittest.TestCase):
    def test_updates_only_selected_keys_and_preserves_other_sections(self):
        source = (
            'model_provider = "custom"\n'
            'model = "old-main" # keep comment\n'
            'model_reasoning_effort = "low"\n\n'
            '[features]\n'
            'web_search = true\n\n'
            '[agents]\n'
            'enabled = true\n'
            'custom_key = "keep"\n'
        )
        updated = update_toml_values(
            source,
            {
                ("", "model"): "gpt-5.6-sol",
                ("", "model_reasoning_effort"): "high",
                ("agents", "enabled"): False,
                ("agents", "default_subagent_model"): "gpt-5.6-terra",
                ("agents", "default_subagent_reasoning_effort"): "medium",
            },
        )
        self.assertIn('model_provider = "custom"', updated)
        self.assertIn('model = "gpt-5.6-sol" # keep comment', updated)
        self.assertIn("web_search = true", updated)
        self.assertIn('custom_key = "keep"', updated)
        self.assertIn("enabled = false", updated)
        self.assertIn('default_subagent_model = "gpt-5.6-terra"', updated)

    def test_save_is_valid_and_creates_backup(self):
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "config.toml"
            config.write_text('model = "old"\n[features]\nx = true\n', encoding="utf-8")
            store = ConfigStore(config)
            backup = store.save(
                AgentSettings(
                    "gpt-5.6-sol",
                    "high",
                    "gpt-5.6-luna",
                    "medium",
                    True,
                    3,
                )
            )
            loaded = store.load()
            self.assertEqual(loaded.main_model, "gpt-5.6-sol")
            self.assertEqual(loaded.subagent_model, "gpt-5.6-luna")
            self.assertEqual(loaded.max_threads, 3)
            self.assertTrue(backup.exists())
            self.assertIn('model = "old"', backup.read_text(encoding="utf-8"))

    def test_saves_managed_executor_with_selected_model_and_effort(self):
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "config.toml"
            store = ConfigStore(config)
            enabled = AgentSettings(
                "gpt-5.6-sol", "high", "gpt-5.6-luna", "xhigh", True, 4
            )
            store.save(enabled)
            executor = tomllib.loads(
                store.managed_executor_path.read_text(encoding="utf-8")
            )
            self.assertEqual(executor["name"], MANAGED_EXECUTOR_AGENT_NAME)
            self.assertEqual(executor["model"], "gpt-5.6-luna")
            self.assertEqual(executor["model_reasoning_effort"], "xhigh")

            store.save(
                AgentSettings(
                    "gpt-5.6-sol", "high", "gpt-5.6-luna", "xhigh", False, 4
                )
            )
            self.assertFalse(store.managed_executor_path.exists())

    def test_dual_mode_policy_is_added_once_and_removed_without_losing_user_text(self):
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "config.toml"
            config.write_text(
                'developer_instructions = "Keep my existing instruction."\n',
                encoding="utf-8",
            )
            agents = Path(temp) / "AGENTS.md"
            agents.write_text("Keep my global instruction.\n", encoding="utf-8")
            store = ConfigStore(config)
            dual = AgentSettings(
                "gpt-5.6-sol", "high", "gpt-5.6-terra", "medium", True, 4
            )
            store.save(dual)
            store.save(dual)
            enabled_data = tomllib.loads(config.read_text(encoding="utf-8"))
            enabled_instructions = agents.read_text(encoding="utf-8")
            self.assertEqual(enabled_instructions.count(DUAL_MODE_POLICY_START), 1)
            self.assertIn("Keep my global instruction.", enabled_instructions)
            self.assertIn("`model` `gpt-5.6-terra`", enabled_instructions)
            self.assertIn("`reasoning_effort` `medium`", enabled_instructions)
            self.assertTrue(has_dual_mode_policy(enabled_instructions))
            self.assertTrue(store.load().agents_enabled)
            self.assertEqual(
                enabled_data["developer_instructions"], "Keep my existing instruction."
            )

            store.save(
                AgentSettings(
                    "gpt-5.6-sol",
                    "high",
                    "gpt-5.6-terra",
                    "medium",
                    False,
                    4,
                )
            )
            disabled_data = tomllib.loads(config.read_text(encoding="utf-8"))
            self.assertEqual(
                disabled_data["developer_instructions"],
                "Keep my existing instruction.",
            )
            self.assertEqual(agents.read_text(encoding="utf-8"), "Keep my global instruction.")
            self.assertFalse(disabled_data["agents"]["enabled"])
            self.assertFalse(store.load().agents_enabled)

    def test_prefers_nonempty_global_agents_override(self):
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "config.toml"
            agents = Path(temp) / "AGENTS.md"
            override = Path(temp) / "AGENTS.override.md"
            agents.write_text("Base global guidance.", encoding="utf-8")
            override.write_text("Temporary global guidance.", encoding="utf-8")
            store = ConfigStore(config)
            settings = AgentSettings(
                "gpt-5.6-sol", "high", "gpt-5.6-luna", "xhigh", True, 4
            )

            store.save(settings)

            self.assertFalse(has_dual_mode_policy(agents.read_text(encoding="utf-8")))
            self.assertTrue(has_dual_mode_policy(override.read_text(encoding="utf-8")))
            self.assertEqual(store.active_global_instruction_path, override)

    def test_nonempty_override_masks_policy_in_global_agents_file(self):
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "config.toml"
            store = ConfigStore(config)
            settings = AgentSettings(
                "gpt-5.6-sol", "high", "gpt-5.6-luna", "xhigh", True, 4
            )
            store.save(settings)
            store.global_agents_override_path.write_text(
                "Temporary guidance without the console policy.", encoding="utf-8"
            )

            self.assertFalse(store.load().agents_enabled)

    def test_load_accepts_legacy_policy_before_first_migration_save(self):
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "config.toml"
            legacy = merge_dual_mode_policy(
                "Legacy user instruction.", True, "gpt-5.6-luna", "xhigh"
            )
            config.write_text(
                'developer_instructions = ' + json.dumps(legacy) + '\n[agents]\nenabled = true\n',
                encoding="utf-8",
            )
            self.assertTrue(ConfigStore(config).load().agents_enabled)

    def test_policy_merge_handles_empty_and_unmanaged_instructions(self):
        enabled = merge_dual_mode_policy(None, True, "gpt-5.6-terra", "medium")
        self.assertTrue(has_dual_mode_policy(enabled))
        self.assertEqual(merge_dual_mode_policy(enabled, False), "")

    def test_dual_mode_policy_has_the_subagent_plan_and_the_main_model_execute(self):
        policy = build_dual_mode_policy("gpt-5.6-terra", "low")
        self.assertIn(
            "is the high-capability planner and advisor",
            policy,
        )
        self.assertIn("exactly one `codex_agent_console_executor`", policy)
        self.assertIn(MANAGED_EXECUTOR_AGENT_NAME, policy)
        self.assertIn("the primary model does the hands-on work itself", policy)
        self.assertIn(
            '`fork_turns` `"none"`',
            policy,
        )
        self.assertIn(
            "Never use `fork_turns` `\"all\"`",
            policy,
        )
        self.assertIn("followup_task", policy)
        self.assertIn("the plan fails twice", policy)
        self.assertIn("Whenever a tool can still make progress", policy)
        self.assertIn("`model` `gpt-5.6-terra`", policy)
        self.assertIn("`reasoning_effort` `low`", policy)
        self.assertNotIn("Act as a thin dispatcher", policy)
        self.assertNotIn("execution student", policy)


class DesktopBackendReloaderTests(unittest.TestCase):
    def test_selects_only_desktop_owned_app_servers(self):
        desktop_path = (
            "C:\\Program Files\\WindowsApps\\"
            "OpenAI.Codex_26.814.5167.0_x64__2p2nqsd0c76g0\\app"
        )
        desktop = DesktopBackendProcess(
            10, 1, "ChatGPT.exe", f"{desktop_path}\\ChatGPT.exe", "ChatGPT.exe"
        )
        backend = DesktopBackendProcess(
            11,
            10,
            "codex.exe",
            f"{desktop_path}\\resources\\codex.exe",
            'codex.exe -c features.code_mode_host=true app-server',
        )
        standalone_cli = DesktopBackendProcess(
            12,
            1,
            "codex.exe",
            "C:\\Users\\me\\AppData\\Local\\OpenAI\\Codex\\bin\\codex.exe",
            "codex.exe app-server",
        )
        wrong_parent = DesktopBackendProcess(
            13,
            99,
            "codex.exe",
            f"{desktop_path}\\resources\\codex.exe",
            "codex.exe app-server",
        )

        selected = DesktopBackendReloader.select_desktop_backends(
            [desktop, backend, standalone_cli, wrong_parent]
        )

        self.assertEqual(selected, [backend])

    def test_rejects_desktop_codex_without_app_server_command(self):
        desktop_path = (
            "C:\\Program Files\\WindowsApps\\"
            "OpenAI.Codex_26.814.5167.0_x64__2p2nqsd0c76g0\\app"
        )
        desktop = DesktopBackendProcess(
            1, 0, "ChatGPT.exe", f"{desktop_path}\\ChatGPT.exe", "ChatGPT.exe"
        )
        utility = DesktopBackendProcess(
            2,
            1,
            "codex.exe",
            f"{desktop_path}\\resources\\codex.exe",
            "codex.exe exec status",
        )

        self.assertEqual(
            DesktopBackendReloader.select_desktop_backends([desktop, utility]), []
        )


class SessionStatsTests(unittest.TestCase):
    @staticmethod
    def _write_session(
        path: Path,
        thread_source: str,
        totals: list[dict],
        source: dict | None = None,
        started_at: datetime | None = None,
    ) -> None:
        now = (started_at or datetime.now(timezone.utc)).isoformat()
        metadata = {
            "timestamp": now,
            "thread_source": thread_source,
            "model": "gpt-5.6-terra" if thread_source == "subagent" else "gpt-5.6-sol",
            "collaboration_mode": {
                "settings": {
                    "reasoning_effort": "medium" if thread_source == "subagent" else "high"
                }
            },
        }
        if source is not None:
            metadata["source"] = source
        records = [
            {
                "timestamp": now,
                "type": "session_meta",
                "payload": metadata,
            }
        ]
        for total in totals:
            records.append(
                {
                    "timestamp": now,
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {"total_token_usage": total},
                    },
                }
            )
        path.write_text(
            "".join(json.dumps(record) + "\n" for record in records),
            encoding="utf-8",
        )

    def test_aggregates_main_and_subagent_using_latest_cumulative_value(self):
        with tempfile.TemporaryDirectory() as temp:
            sessions = Path(temp)
            self._write_session(
                sessions / "main.jsonl",
                "user",
                [
                    {"input_tokens": 10, "cached_input_tokens": 2, "total_tokens": 12},
                    {
                        "input_tokens": 100,
                        "cached_input_tokens": 40,
                        "output_tokens": 20,
                        "reasoning_output_tokens": 5,
                        "total_tokens": 120,
                    },
                ],
            )
            self._write_session(
                sessions / "sub.jsonl",
                "subagent",
                [
                    {
                        "input_tokens": 50,
                        "cached_input_tokens": 25,
                        "output_tokens": 10,
                        "total_tokens": 60,
                    }
                ],
            )
            self._write_session(
                sessions / "guardian.jsonl",
                "subagent",
                [{"input_tokens": 999, "total_tokens": 999}],
                {"subagent": {"other": "guardian"}},
            )
            main, sub = SessionStatsReader(sessions).aggregate(None)
            self.assertEqual(main.sessions, 1)
            self.assertEqual(main.input_tokens, 100)
            self.assertEqual(main.cached_input_tokens, 40)
            self.assertEqual(main.cache_hit_rate, 40.0)
            self.assertEqual(sub.sessions, 1)
            self.assertEqual(sub.total_tokens, 60)
            self.assertEqual(sub.cache_hit_rate, 50.0)
            self.assertEqual(sub.models, {"gpt-5.6-terra/medium": 1})
            self.assertEqual(sub.latest_model, "gpt-5.6-terra/medium")

    def test_preserves_execution_subagent_identity_from_forked_history(self):
        with tempfile.TemporaryDirectory() as temp:
            sessions = Path(temp)
            self._write_session(
                sessions / "main.jsonl",
                "user",
                [{"input_tokens": 100, "total_tokens": 100}],
            )
            now = datetime.now(timezone.utc)
            child_started = now.isoformat()
            parent_started = (now - timedelta(seconds=5)).isoformat()
            records = [
                {
                    "timestamp": child_started,
                    "type": "session_meta",
                    "payload": {
                        "timestamp": child_started,
                        "thread_source": "subagent",
                        "source": {
                            "subagent": {
                                "thread_spawn": {
                                    "agent_role": "codex_agent_console_executor"
                                }
                            }
                        },
                    },
                },
                {
                    "timestamp": parent_started,
                    "type": "session_meta",
                    "payload": {
                        "timestamp": parent_started,
                        "thread_source": "user",
                    },
                },
                {
                    "timestamp": parent_started,
                    "type": "turn_context",
                    "payload": {"model": "gpt-5.6-terra", "effort": "xhigh"},
                },
                {
                    "timestamp": child_started,
                    "type": "event_msg",
                    "payload": {
                        "type": "thread_settings_applied",
                        "thread_settings": {
                            "model": "gpt-5.5",
                            "reasoning_effort": "xhigh",
                        },
                    },
                },
                {
                    "timestamp": child_started,
                    "type": "turn_context",
                    "payload": {"model": "gpt-5.5", "effort": "xhigh"},
                },
                {
                    "timestamp": child_started,
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {"total_token_usage": {"total_tokens": 60}},
                    },
                },
            ]
            (sessions / "execution.jsonl").write_text(
                "".join(json.dumps(record) + "\n" for record in records),
                encoding="utf-8",
            )

            main, sub = SessionStatsReader(sessions).aggregate(None)

            self.assertEqual(main.sessions, 1)
            self.assertEqual(main.latest_model, "gpt-5.6-sol/high")
            self.assertEqual(sub.sessions, 1)
            self.assertEqual(sub.total_tokens, 60)
            self.assertEqual(sub.models, {"gpt-5.5/xhigh": 1})
            self.assertEqual(sub.latest_model, "gpt-5.5/xhigh")
            self.assertEqual(
                SessionStatsReader(sessions)._parse_file(sessions / "execution.jsonl").started_at,
                datetime.fromisoformat(child_started),
            )

    def test_excludes_parent_token_history_from_forked_execution_subagent(self):
        with tempfile.TemporaryDirectory() as temp:
            sessions = Path(temp)
            now = datetime.now(timezone.utc)
            child_started = now.isoformat()
            parent_started = (now - timedelta(seconds=5)).isoformat()

            def token_record(total: dict[str, int]) -> dict:
                return {
                    "timestamp": child_started,
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {"total_token_usage": total},
                    },
                }

            records = [
                {
                    "timestamp": child_started,
                    "type": "session_meta",
                    "payload": {
                        "id": "execution-child",
                        "forked_from_id": "main-parent",
                        "timestamp": child_started,
                        "thread_source": "subagent",
                        "source": {
                            "subagent": {
                                "thread_spawn": {
                                    "agent_role": "codex_agent_console_executor"
                                }
                            }
                        },
                    },
                },
                {
                    "timestamp": parent_started,
                    "type": "session_meta",
                    "payload": {
                        "id": "main-parent",
                        "timestamp": parent_started,
                        "thread_source": "user",
                    },
                },
                {
                    "timestamp": parent_started,
                    "type": "turn_context",
                    "payload": {"model": "gpt-5.6-terra", "effort": "max"},
                },
                token_record(
                    {
                        "input_tokens": 90,
                        "cached_input_tokens": 50,
                        "cache_write_input_tokens": 2,
                        "output_tokens": 10,
                        "reasoning_output_tokens": 5,
                        "total_tokens": 100,
                    }
                ),
                token_record(
                    {
                        "input_tokens": 130,
                        "cached_input_tokens": 80,
                        "cache_write_input_tokens": 3,
                        "output_tokens": 20,
                        "reasoning_output_tokens": 9,
                        "total_tokens": 150,
                    }
                ),
                {
                    "timestamp": child_started,
                    "type": "event_msg",
                    "payload": {
                        "type": "thread_settings_applied",
                        "thread_settings": {
                            "model": "gpt-5.5",
                            "reasoning_effort": "xhigh",
                        },
                    },
                },
                {
                    "timestamp": child_started,
                    "type": "event_msg",
                    "payload": {"type": "task_started"},
                },
                {
                    "timestamp": child_started,
                    "type": "turn_context",
                    "payload": {"model": "gpt-5.5", "effort": "xhigh"},
                },
                token_record(
                    {
                        "input_tokens": 160,
                        "cached_input_tokens": 100,
                        "cache_write_input_tokens": 4,
                        "output_tokens": 20,
                        "reasoning_output_tokens": 10,
                        "total_tokens": 180,
                    }
                ),
                {
                    "timestamp": child_started,
                    "type": "event_msg",
                    "payload": {"type": "task_complete"},
                },
                {
                    "timestamp": child_started,
                    "type": "event_msg",
                    "payload": {"type": "task_started"},
                },
                {
                    "timestamp": child_started,
                    "type": "turn_context",
                    "payload": {"model": "gpt-5.5", "effort": "xhigh"},
                },
                token_record(
                    {
                        "input_tokens": 220,
                        "cached_input_tokens": 140,
                        "cache_write_input_tokens": 7,
                        "output_tokens": 40,
                        "reasoning_output_tokens": 20,
                        "total_tokens": 260,
                    }
                ),
            ]
            execution_path = sessions / "execution.jsonl"
            execution_path.write_text(
                "".join(json.dumps(record) + "\n" for record in records),
                encoding="utf-8",
            )
            self._write_session(
                sessions / "direct-subagent.jsonl",
                "subagent",
                [
                    {
                        "input_tokens": 50,
                        "cached_input_tokens": 25,
                        "output_tokens": 10,
                        "total_tokens": 60,
                    }
                ],
            )
            self._write_session(
                sessions / "guardian.jsonl",
                "subagent",
                [{"input_tokens": 999, "total_tokens": 999}],
                {"subagent": {"other": "guardian"}},
            )

            parsed = SessionStatsReader(sessions)._parse_file(execution_path)
            self.assertIsNotNone(parsed)
            self.assertEqual(
                parsed.tokens,
                {
                    "input_tokens": 90,
                    "cached_input_tokens": 60,
                    "cache_write_input_tokens": 4,
                    "output_tokens": 20,
                    "reasoning_output_tokens": 11,
                    "total_tokens": 110,
                },
            )

            _main, subagent = SessionStatsReader(sessions).aggregate(None)
            self.assertEqual(subagent.sessions, 2)
            self.assertEqual(subagent.input_tokens, 140)
            self.assertEqual(subagent.cached_input_tokens, 85)
            self.assertEqual(subagent.total_tokens, 170)

    def test_keeps_clean_forked_execution_usage_without_parent_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            sessions = Path(temp)
            now = datetime.now(timezone.utc).isoformat()
            records = [
                {
                    "timestamp": now,
                    "type": "session_meta",
                    "payload": {
                        "id": "execution-child",
                        "forked_from_id": "main-parent",
                        "timestamp": now,
                        "thread_source": "subagent",
                    },
                },
                {
                    "timestamp": now,
                    "type": "event_msg",
                    "payload": {
                        "type": "thread_settings_applied",
                        "thread_settings": {
                            "model": "gpt-5.5",
                            "reasoning_effort": "xhigh",
                        },
                    },
                },
                {
                    "timestamp": now,
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {"total_token_usage": {"total_tokens": 20}},
                    },
                },
                {
                    "timestamp": now,
                    "type": "turn_context",
                    "payload": {"model": "gpt-5.5", "effort": "xhigh"},
                },
                {
                    "timestamp": now,
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {"total_token_usage": {"total_tokens": 60}},
                    },
                },
            ]
            execution_path = sessions / "clean-execution.jsonl"
            execution_path.write_text(
                "".join(json.dumps(record) + "\n" for record in records),
                encoding="utf-8",
            )

            parsed = SessionStatsReader(sessions)._parse_file(execution_path)
            self.assertIsNotNone(parsed)
            self.assertEqual(parsed.tokens["total_tokens"], 60)

    def test_today_uses_local_calendar_day_not_previous_24_hours(self):
        with tempfile.TemporaryDirectory() as temp:
            sessions = Path(temp)
            local_timezone = timezone(timedelta(hours=8))
            now = datetime(2026, 8, 19, 9, 0, tzinfo=local_timezone)
            yesterday = datetime(2026, 8, 18, 23, 59, 59, tzinfo=local_timezone)
            today = datetime(2026, 8, 19, 0, 0, tzinfo=local_timezone)
            self._write_session(
                sessions / "yesterday-main.jsonl",
                "user",
                [{"total_tokens": 100}],
                started_at=yesterday,
            )
            self._write_session(
                sessions / "today-main.jsonl",
                "user",
                [{"total_tokens": 200}],
                started_at=today,
            )
            self._write_session(
                sessions / "yesterday-subagent.jsonl",
                "subagent",
                [{"total_tokens": 300}],
                started_at=yesterday,
            )
            self._write_session(
                sessions / "today-subagent.jsonl",
                "subagent",
                [{"total_tokens": 400}],
                started_at=today,
            )

            reader = SessionStatsReader(sessions)
            main, subagent = reader.aggregate(1, calendar_day=True, now=now)
            rolling_main, rolling_subagent = reader.aggregate(1, now=now)

            self.assertEqual(main.sessions, 1)
            self.assertEqual(main.total_tokens, 200)
            self.assertEqual(subagent.sessions, 1)
            self.assertEqual(subagent.total_tokens, 400)
            self.assertEqual(rolling_main.sessions, 2)
            self.assertEqual(rolling_subagent.sessions, 2)


class SessionIncrementalScanTests(unittest.TestCase):
    """The live rollout log grows every second; a refresh must only read the tail."""

    @staticmethod
    def _session_lines(totals: list[dict]) -> str:
        now = datetime.now(timezone.utc).isoformat()
        records = [
            {
                "timestamp": now,
                "type": "session_meta",
                "payload": {
                    "id": "live",
                    "timestamp": now,
                    "thread_source": "user",
                    "model": "gpt-5.6-sol",
                    "effort": "high",
                },
            }
        ]
        for total in totals:
            records.append(
                {
                    "timestamp": now,
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {"total_token_usage": total},
                    },
                }
            )
        return "".join(json.dumps(record) + "\n" for record in records)

    def test_append_only_growth_is_read_incrementally(self):
        with tempfile.TemporaryDirectory() as temp:
            sessions = Path(temp)
            log = sessions / "live.jsonl"
            log.write_text(
                self._session_lines([{"input_tokens": 10, "total_tokens": 10}]),
                encoding="utf-8",
            )
            reader = SessionStatsReader(sessions)
            main, _sub = reader.aggregate(None)
            self.assertEqual(main.total_tokens, 10)
            scanned = reader._scans[log].offset
            self.assertEqual(scanned, log.stat().st_size)
            first_pass_bytes = reader.scanned_bytes
            self.assertGreaterEqual(first_pass_bytes, log.stat().st_size)

            with log.open("a", encoding="utf-8") as handle:
                handle.write(
                    "".join(
                        json.dumps(
                            {
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                                "type": "event_msg",
                                "payload": {
                                    "type": "token_count",
                                    "info": {
                                        "total_token_usage": {
                                            "input_tokens": 90,
                                            "output_tokens": 10,
                                            "total_tokens": 100,
                                        }
                                    },
                                },
                            }
                        )
                        + "\n"
                        for _ in range(1)
                    )
                )
            main, _sub = reader.aggregate(None)
            self.assertEqual(main.total_tokens, 100)
            self.assertEqual(main.sessions, 1)
            # The resumed scan consumed only the appended tail.
            self.assertEqual(reader._scans[log].offset, log.stat().st_size)
            tail_bytes = reader.scanned_bytes - first_pass_bytes
            self.assertLess(tail_bytes, log.stat().st_size // 2)

    def test_partial_trailing_record_is_deferred_until_complete(self):
        with tempfile.TemporaryDirectory() as temp:
            sessions = Path(temp)
            log = sessions / "live.jsonl"
            complete = self._session_lines(
                [{"input_tokens": 10, "total_tokens": 10}]
            )
            partial = json.dumps(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {"total_token_usage": {"total_tokens": 50}},
                    },
                }
            )
            log.write_text(complete + partial[: len(partial) // 2], encoding="utf-8")
            reader = SessionStatsReader(sessions)
            main, _sub = reader.aggregate(None)
            self.assertEqual(main.total_tokens, 10)

            with log.open("a", encoding="utf-8") as handle:
                handle.write(partial[len(partial) // 2 :] + "\n")
            main, _sub = reader.aggregate(None)
            self.assertEqual(main.total_tokens, 50)

    def test_rewritten_log_discards_the_resumed_state(self):
        with tempfile.TemporaryDirectory() as temp:
            sessions = Path(temp)
            log = sessions / "live.jsonl"
            log.write_text(
                self._session_lines(
                    [
                        {"input_tokens": 500, "cached_input_tokens": 400, "total_tokens": 500},
                        {"input_tokens": 900, "cached_input_tokens": 800, "total_tokens": 900},
                    ]
                ),
                encoding="utf-8",
            )
            reader = SessionStatsReader(sessions)
            main, _sub = reader.aggregate(None)
            self.assertEqual(main.total_tokens, 900)

            log.write_text(
                self._session_lines([{"input_tokens": 7, "total_tokens": 7}]),
                encoding="utf-8",
            )
            main, _sub = reader.aggregate(None)
            self.assertEqual(main.total_tokens, 7)
            self.assertEqual(main.input_tokens, 7)

    def test_persistent_cache_survives_a_new_reader(self):
        with tempfile.TemporaryDirectory() as temp:
            sessions = Path(temp) / "sessions"
            sessions.mkdir()
            log = sessions / "live.jsonl"
            log.write_text(
                self._session_lines(
                    [{"input_tokens": 300, "cached_input_tokens": 150, "total_tokens": 300}]
                ),
                encoding="utf-8",
            )
            cache = Path(temp) / "stats-cache.json"
            first = SessionStatsReader(sessions, cache_path=cache)
            expected_main, expected_sub = first.aggregate(None)
            self.assertTrue(cache.exists())

            second = SessionStatsReader(sessions, cache_path=cache)
            main, sub = second.aggregate(None)
            self.assertEqual(main.total_tokens, expected_main.total_tokens)
            self.assertEqual(main.cached_input_tokens, expected_main.cached_input_tokens)
            self.assertEqual(sub.total_tokens, expected_sub.total_tokens)

    def test_corrupt_cache_is_ignored(self):
        with tempfile.TemporaryDirectory() as temp:
            sessions = Path(temp) / "sessions"
            sessions.mkdir()
            log = sessions / "live.jsonl"
            log.write_text(
                self._session_lines([{"input_tokens": 42, "total_tokens": 42}]),
                encoding="utf-8",
            )
            cache = Path(temp) / "stats-cache.json"
            cache.write_text("{not json", encoding="utf-8")
            main, _sub = SessionStatsReader(sessions, cache_path=cache).aggregate(None)
            self.assertEqual(main.total_tokens, 42)


class DiagnosticLogicTests(unittest.TestCase):
    def test_parses_codex_json_lifecycle_and_usage(self):
        self.assertEqual(
            parse_codex_json_event(
                {"type": "thread.started", "thread_id": "thread-123"}
            ),
            {"thread_id": "thread-123"},
        )
        self.assertTrue(
            parse_codex_json_event(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": "API_OK"},
                }
            )["first_response"]
        )
        completed = parse_codex_json_event(
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 100,
                    "cached_input_tokens": 60,
                    "output_tokens": 8,
                },
            }
        )
        self.assertTrue(completed["completed"])
        self.assertEqual(completed["usage"]["cached_input_tokens"], 60)

    def test_classifies_common_api_and_transport_errors(self):
        cases = {
            "HTTP 401 Unauthorized": "401 认证失败",
            "model alpha returned 404 not found": "404 模型不可用",
            "429 Too Many Requests": "429 请求受限",
            "request timed out": "请求超时",
            "TLS connection failed": "网络错误",
            "unexpected CLI exit": "CLI 失败",
        }
        for message, expected in cases.items():
            with self.subTest(message=message):
                self.assertEqual(classify_diagnostic_error(message), expected)

    def test_summarizes_success_latency_and_output_speed(self):
        results = [
            DiagnosticResult(
                "主模型", "a", "high", 1, True, "成功", 1.0, 3.0,
                output_tokens=20, tokens_per_second=10.0,
            ),
            DiagnosticResult(
                "子代理", "b", "medium", 1, False, "请求超时", None, 5.0,
            ),
        ]
        summary = summarize_diagnostics(results)
        self.assertEqual(summary.total, 2)
        self.assertEqual(summary.succeeded, 1)
        self.assertEqual(summary.success_rate, 50.0)
        self.assertEqual(summary.average_first_response, 1.0)
        self.assertEqual(summary.average_total_time, 4.0)
        self.assertEqual(summary.average_tokens_per_second, 10.0)

    def test_finds_bundled_codex_executable_before_path(self):
        with tempfile.TemporaryDirectory() as temp:
            local = Path(temp)
            executable = local / "OpenAI" / "Codex" / "bin" / "version" / "codex.exe"
            executable.parent.mkdir(parents=True)
            executable.touch()
            found = find_codex_executable(local, lambda _name: None)
            self.assertEqual(found, executable)

    def test_diagnostic_passes_model_to_codex_once(self):
        from codex_agent_console import CodexDiagnosticsRunner

        runner = CodexDiagnosticsRunner(
            Path("codex.exe"), Path(".codex"), Path("workspace")
        )
        with mock.patch("codex_agent_console.subprocess.Popen") as popen:
            popen.side_effect = OSError("stop after command capture")
            runner._run_one("主模型", "gpt-test", "high", 1, 10, lambda _value: None)

        command = popen.call_args.args[0]
        self.assertEqual(command.count("-m"), 1)
        self.assertEqual(command[command.index("-m") + 1], "gpt-test")

    def test_tails_cumulative_token_events_incrementally(self):
        with tempfile.TemporaryDirectory() as temp:
            session = Path(temp) / "session.jsonl"

            def token_line(input_tokens: int, cached: int, output: int) -> str:
                return json.dumps(
                    {
                        "type": "event_msg",
                        "payload": {
                            "type": "token_count",
                            "info": {
                                "total_token_usage": {
                                    "input_tokens": input_tokens,
                                    "cached_input_tokens": cached,
                                    "output_tokens": output,
                                }
                            },
                        },
                    }
                ) + "\n"

            session.write_text(token_line(100, 50, 2), encoding="utf-8")
            tail = SessionTokenTail(session)
            self.assertEqual(tail.read_latest()["output_tokens"], 2)
            with session.open("a", encoding="utf-8") as handle:
                handle.write(token_line(100, 50, 12))
            latest = tail.read_latest()
            self.assertEqual(latest["input_tokens"], 100)
            self.assertEqual(latest["cached_input_tokens"], 50)
            self.assertEqual(latest["output_tokens"], 12)


class CustomApiTests(unittest.TestCase):
    def test_base_url_builds_supported_api_paths(self):
        base = "https://api.example.com/v1"
        self.assertEqual(
            custom_api_base_url("https://api.example.com/v1/chat/completions"),
            base,
        )
        self.assertEqual(custom_api_endpoint_url(base), f"{base}/chat/completions")
        self.assertEqual(
            custom_api_endpoint_url(base, "responses"), f"{base}/responses"
        )
        self.assertEqual(
            custom_api_endpoint_url("https://api.example.com", "completions"),
            "https://api.example.com/v1/completions",
        )
        self.assertEqual(
            custom_api_endpoint_url(
                "https://api.anthropic.com/v1", "anthropic_messages"
            ),
            "https://api.anthropic.com/v1/messages",
        )

    def test_fetch_models_uses_models_endpoint_and_auth(self):
        class Handler(BaseHTTPRequestHandler):
            authorization = ""
            path = ""

            def do_GET(self):
                Handler.authorization = self.headers.get("Authorization", "")
                Handler.path = self.path
                body = json.dumps(
                    {"data": [{"id": "alpha"}, {"id": "beta"}]}
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            models = fetch_custom_api_models(
                f"http://127.0.0.1:{server.server_port}/v1",
                "bearer",
                "test-model-key",
            )
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(Handler.path, "/v1/models")
        self.assertEqual(Handler.authorization, "Bearer test-model-key")
        self.assertEqual(models, ["alpha", "beta"])

    def test_fetch_claude_models_uses_anthropic_headers(self):
        class Handler(BaseHTTPRequestHandler):
            api_key = ""
            version = ""
            path = ""

            def do_GET(self):
                Handler.api_key = self.headers.get("x-api-key", "")
                Handler.version = self.headers.get("anthropic-version", "")
                Handler.path = self.path
                body = json.dumps(
                    {"data": [{"id": "claude-sonnet"}, {"id": "claude-haiku"}]}
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            models = fetch_custom_api_models(
                f"http://127.0.0.1:{server.server_port}/v1",
                "x-api-key",
                "test-anthropic-key",
                api_type="anthropic_messages",
            )
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(Handler.path, "/v1/models")
        self.assertEqual(Handler.api_key, "test-anthropic-key")
        self.assertEqual(Handler.version, ANTHROPIC_API_VERSION)
        self.assertEqual(models, ["claude-sonnet", "claude-haiku"])

    @unittest.skipUnless(sys.platform == "win32", "Windows DPAPI only")
    def test_dpapi_round_trip_and_store_does_not_write_plaintext_key(self):
        secret = "temporary-test-key-not-a-real-credential"
        encrypted = protect_secret(secret)
        self.assertNotIn(secret, encrypted)
        self.assertEqual(unprotect_secret(encrypted), secret)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "apis.json"
            endpoint = CustomApiEndpoint(
                "endpoint-a",
                "Local API",
                "http://127.0.0.1:8000/v1/chat/completions",
                "test-model",
                encrypted_api_key=encrypted,
            )
            store = CustomApiStore(path)
            store.save([endpoint])
            raw = path.read_text(encoding="utf-8")
            self.assertNotIn(secret, raw)
            loaded = store.load()
            self.assertEqual(len(loaded), 1)
            self.assertEqual(unprotect_secret(loaded[0].encrypted_api_key), secret)

    def test_endpoint_validation_requires_https_except_localhost(self):
        validate_custom_api_endpoint(
            CustomApiEndpoint(
                "local",
                "Local",
                "http://localhost:8080/v1/chat/completions",
                "model",
            )
        )
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            validate_custom_api_endpoint(
                CustomApiEndpoint(
                    "remote",
                    "Remote",
                    "http://api.example.com/v1/chat/completions",
                    "model",
                )
            )

    def test_summary_prefers_reliable_api_over_faster_failed_api(self):
        results = [
            CustomApiResult(
                "a", "Reliable", "m", 1, True, "成功", 200, 0.2, 1.0,
                output_tokens=10, tokens_per_second=10.0,
            ),
            CustomApiResult(
                "a", "Reliable", "m", 2, True, "成功", 200, 0.2, 1.1,
                output_tokens=10, tokens_per_second=9.0,
            ),
            CustomApiResult(
                "b", "Flaky", "m", 1, True, "成功", 200, 0.1, 0.4,
                output_tokens=10, tokens_per_second=20.0,
            ),
            CustomApiResult(
                "b", "Flaky", "m", 2, False, "请求超时", None, None, 3.0,
            ),
        ]
        summaries = summarize_custom_apis(results)
        fastest, stable = choose_custom_api_winners(summaries)
        self.assertEqual(fastest.name, "Reliable")
        self.assertEqual(stable.name, "Reliable")
        reliable = next(item for item in summaries if item.name == "Reliable")
        self.assertEqual(reliable.success_rate, 100.0)
        self.assertIsNotNone(reliable.jitter_percent)

    def test_streaming_benchmark_uses_bearer_key_and_parses_usage(self):
        class Handler(BaseHTTPRequestHandler):
            authorization = ""

            def do_POST(self):
                Handler.authorization = self.headers.get("Authorization", "")
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                chunks = (
                    b'data: {"choices":[{"delta":{"content":"API"}}]}\n\n',
                    b'data: {"choices":[{"delta":{"content":"_OK"}}],'
                    b'"usage":{"prompt_tokens":2000,'
                    b'"prompt_tokens_details":{"cached_tokens":1200},'
                    b'"completion_tokens":2}}\n\n',
                    b"data: [DONE]\n\n",
                )
                for chunk in chunks:
                    self.wfile.write(chunk)
                    self.wfile.flush()

            def log_message(self, _format, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            endpoint = CustomApiEndpoint(
                "local",
                "Local",
                f"http://127.0.0.1:{server.server_port}/v1/chat/completions",
                "test-model",
            )
            results: list[CustomApiResult] = []
            runner = CustomApiBenchmarkRunner()
            runner.run(
                [(endpoint, "test-bearer-key")],
                1,
                10.0,
                lambda _event: None,
                results.append,
            )
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(Handler.authorization, "Bearer test-bearer-key")
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].success)
        self.assertEqual(results[0].output_tokens, 2)
        self.assertEqual(results[0].input_tokens, 2000)
        self.assertEqual(results[0].cached_input_tokens, 1200)
        self.assertEqual(results[0].cache_hit_rate, 60.0)
        self.assertFalse(results[0].tokens_estimated)
        self.assertIsNotNone(results[0].first_response_seconds)

    def test_cache_benchmark_warms_then_measures_a_reused_prompt(self):
        class Handler(BaseHTTPRequestHandler):
            payloads: list[dict[str, object]] = []

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                Handler.payloads.append(
                    json.loads(self.rfile.read(length).decode("utf-8"))
                )
                cached_tokens = 0 if len(Handler.payloads) == 1 else 1800
                body = json.dumps(
                    {
                        "choices": [{"message": {"content": "API_OK"}}],
                        "usage": {
                            "prompt_tokens": 2000,
                            "prompt_tokens_details": {
                                "cached_tokens": cached_tokens
                            },
                            "completion_tokens": 2,
                        },
                    }
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            endpoint = CustomApiEndpoint(
                "cache",
                "Cache",
                f"http://127.0.0.1:{server.server_port}/v1/chat/completions",
                "test-model",
            )
            results: list[CustomApiResult] = []
            CustomApiBenchmarkRunner().run(
                [(endpoint, "")],
                1,
                10.0,
                lambda _event: None,
                results.append,
                cache_test=True,
            )
        finally:
            server.shutdown()
            server.server_close()

        self.assertEqual(len(Handler.payloads), 2)
        self.assertEqual(Handler.payloads[0], Handler.payloads[1])
        self.assertEqual(
            Handler.payloads[0]["messages"][0]["content"],
            CUSTOM_API_CACHE_PROMPT,
        )
        self.assertFalse(Handler.payloads[0]["stream"])
        self.assertEqual([result.cache_phase for result in results], ["warmup", "measure"])
        self.assertEqual(results[0].cache_hit_rate, 0.0)
        self.assertEqual(results[1].cache_hit_rate, 90.0)
        summary = summarize_custom_apis(results)[0]
        self.assertEqual(summary.total, 1)
        self.assertEqual(summary.cache_hit_rate, 90.0)

    def test_claude_messages_streaming_benchmark_parses_sse_and_usage(self):
        class Handler(BaseHTTPRequestHandler):
            headers = {}
            payload = {}

            def do_POST(self):
                Handler.headers = dict(self.headers.items())
                length = int(self.headers.get("Content-Length", "0"))
                Handler.payload = json.loads(self.rfile.read(length).decode("utf-8"))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                chunks = (
                    b": keep-alive\n\n",
                    b"event: message_start\n\n"
                    b'data: {"type":"message_start","message":{"usage":'
                    b'{"input_tokens":120,"cache_creation_input_tokens":0,'
                    b'"cache_read_input_tokens":0}}}\n\n',
                    b"event: content_block_start\n\n"
                    b'data: {"type":"content_block_start","content_block":'
                    b'{"type":"text","text":""}}\n\n',
                    b"event: content_block_delta\n\n"
                    b'data: {"type":"content_block_delta","delta":'
                    b'{"type":"text_delta","text":"API"}}\n\n',
                    b"event: content_block_delta\n\n"
                    b'data: {"type":"content_block_delta","delta":'
                    b'{"type":"text_delta","text":"_OK"}}\n\n',
                    b"event: message_delta\n\n"
                    b'data: {"type":"message_delta","usage":{"output_tokens":3}}\n\n',
                    b"event: message_stop\n\n"
                    b'data: {"type":"message_stop"}\n\n',
                )
                for chunk in chunks:
                    self.wfile.write(chunk)
                    self.wfile.flush()

            def log_message(self, _format, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            endpoint = CustomApiEndpoint(
                "claude",
                "Claude",
                f"http://127.0.0.1:{server.server_port}/v1/messages",
                "claude-sonnet",
                auth_mode="x-api-key",
                api_type="anthropic_messages",
            )
            results: list[CustomApiResult] = []
            CustomApiBenchmarkRunner().run(
                [(endpoint, "test-claude-key")],
                1,
                10.0,
                lambda _event: None,
                results.append,
            )
        finally:
            server.shutdown()
            server.server_close()

        headers = {key.casefold(): value for key, value in Handler.headers.items()}
        self.assertEqual(headers["x-api-key"], "test-claude-key")
        self.assertEqual(headers["anthropic-version"], ANTHROPIC_API_VERSION)
        self.assertNotIn("authorization", headers)
        self.assertEqual(Handler.payload["max_tokens"], 16)
        self.assertTrue(Handler.payload["stream"])
        self.assertEqual(Handler.payload["messages"][0]["role"], "user")
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].success)
        self.assertEqual(results[0].api_type, "anthropic_messages")
        self.assertEqual(results[0].output_tokens, 3)
        self.assertEqual(results[0].input_tokens, 120)
        self.assertEqual(results[0].cached_input_tokens, 0)

    def test_claude_cache_benchmark_uses_cache_control_and_reads_cache_usage(self):
        class Handler(BaseHTTPRequestHandler):
            payloads: list[dict[str, object]] = []
            headers: list[dict[str, str]] = []

            def do_POST(self):
                Handler.headers.append(dict(self.headers.items()))
                length = int(self.headers.get("Content-Length", "0"))
                Handler.payloads.append(
                    json.loads(self.rfile.read(length).decode("utf-8"))
                )
                is_warmup = len(Handler.payloads) == 1
                body = json.dumps(
                    {
                        "id": "msg_test",
                        "type": "message",
                        "content": [{"type": "text", "text": "API_OK"}],
                        "usage": {
                            "input_tokens": 100,
                            "cache_creation_input_tokens": 1900 if is_warmup else 0,
                            "cache_read_input_tokens": 0 if is_warmup else 1900,
                            "output_tokens": 2,
                        },
                    }
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            endpoint = CustomApiEndpoint(
                "claude-cache",
                "Claude Cache",
                f"http://127.0.0.1:{server.server_port}/v1/messages",
                "claude-sonnet",
                auth_mode="x-api-key",
                api_type="anthropic_messages",
            )
            results: list[CustomApiResult] = []
            CustomApiBenchmarkRunner().run(
                [(endpoint, "test-claude-key")],
                1,
                10.0,
                lambda _event: None,
                results.append,
                cache_test=True,
            )
        finally:
            server.shutdown()
            server.server_close()

        self.assertEqual(len(Handler.payloads), 2)
        self.assertEqual(Handler.payloads[0], Handler.payloads[1])
        self.assertFalse(Handler.payloads[0]["stream"])
        content = Handler.payloads[0]["messages"][0]["content"]
        self.assertEqual(content[0]["text"], CUSTOM_API_CACHE_PROMPT)
        self.assertEqual(content[0]["cache_control"], {"type": "ephemeral"})
        cache_headers = {
            key.casefold(): value for key, value in Handler.headers[0].items()
        }
        self.assertEqual(
            cache_headers["anthropic-beta"], "prompt-caching-2024-07-31"
        )
        self.assertEqual(results[0].input_tokens, 2000)
        self.assertEqual(results[0].cache_hit_rate, 0.0)
        self.assertEqual(results[1].input_tokens, 2000)
        self.assertEqual(results[1].cached_input_tokens, 1900)
        self.assertEqual(results[1].cache_hit_rate, 95.0)
        summary = summarize_custom_apis(results)[0]
        self.assertEqual(summary.cache_hit_rate, 95.0)

    def test_responses_streaming_benchmark_parses_response_events(self):
        class Handler(BaseHTTPRequestHandler):
            payload = {}

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                Handler.payload = json.loads(self.rfile.read(length).decode("utf-8"))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                chunks = (
                    b"event: response.created\n\n"
                    b'data: {"type":"response.created"}\n\n',
                    b"event: response.output_text.delta\n\n"
                    b'data: {"type":"response.output_text.delta","delta":"API"}\n\n',
                    b"event: response.output_text.delta\n\n"
                    b'data: {"type":"response.output_text.delta","delta":"_OK"}\n\n',
                    b"event: response.completed\n\n"
                    b'data: {"type":"response.completed","response":{"usage":'
                    b'{"input_tokens":1600,"input_tokens_details":'
                    b'{"cached_tokens":1280},"output_tokens":2}}}\n\n',
                )
                for chunk in chunks:
                    self.wfile.write(chunk)
                    self.wfile.flush()

            def log_message(self, _format, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            endpoint = CustomApiEndpoint(
                "responses",
                "Responses",
                f"http://127.0.0.1:{server.server_port}/v1/responses",
                "response-model",
                api_type="responses",
            )
            results: list[CustomApiResult] = []
            CustomApiBenchmarkRunner().run(
                [(endpoint, "")],
                1,
                10.0,
                lambda _event: None,
                results.append,
            )
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(Handler.payload["input"], CUSTOM_API_PROMPT)
        self.assertNotIn("messages", Handler.payload)
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].success)
        self.assertEqual(results[0].api_type, "responses")
        self.assertEqual(results[0].output_tokens, 2)
        self.assertEqual(results[0].input_tokens, 1600)
        self.assertEqual(results[0].cached_input_tokens, 1280)
        self.assertEqual(results[0].cache_hit_rate, 80.0)
        self.assertFalse(results[0].tokens_estimated)


import codex_advisor
from codex_advisor import (
    ADVISOR_POLICY_START,
    AdvisorConfig,
    AdvisorError,
    AdvisorRunner,
    AdvisorSessions,
    AdvisorStore,
    build_advisor_policy,
    build_advisor_prompt,
    has_advisor_policy,
    merge_advisor_policy,
    merge_mcp_config,
)

from codex_appserver import dynamic_tools
from codex_gateway import (
    CodexGateway,
    Decision,
    GatewayConfig,
    RunResult,
    RunUsage,
    ToolCall,
    build_prompt,
    decision_schema,
    dsh_mcp_insert,
    dsh_provider_snippet,
    parse_decision,
    render_tools,
    strip_dsh_entries,
    strip_dsh_mcp_insert,
)


class GatewayTests(unittest.TestCase):
    @staticmethod
    def _config(**overrides) -> GatewayConfig:
        values = {
            "host": "127.0.0.1",
            "port": 0,
            "model": "gpt-6-luna",
            "effort": "max",
            "workdir": str(Path(tempfile.gettempdir())),
            "token": "",
        }
        values.update(overrides)
        return GatewayConfig(**values)

    @staticmethod
    def _fake_factory(decision: Decision):
        class _FakeRunner:
            def __init__(self):
                self.prompts: list[str] = []

            def run(self, prompt, model, effort, workdir):
                self.prompts.append(prompt)
                return RunResult(
                    decision=decision,
                    usage=RunUsage(input_tokens=120, cached_input_tokens=100, output_tokens=8),
                    seconds=0.01,
                    thread_id="t1",
                )

        runner = _FakeRunner()
        return lambda _path, _timeout: runner, runner

    def test_decision_schema_is_strict(self):
        schema = decision_schema()
        self.assertEqual(schema["type"], "object")
        self.assertEqual(
            set(schema["required"]), {"kind", "content", "tool_calls"}
        )
        self.assertFalse(schema["additionalProperties"])

    def test_prompt_carries_system_tools_and_transcript(self):
        messages = [
            {"role": "system", "content": "You are terse."},
            {"role": "user", "content": "Read the config."},
            {"role": "tool", "name": "read_file", "content": "model = 'x'"},
        ]
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read a file",
                    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                },
            }
        ]
        prompt = build_prompt(messages, tools)
        self.assertIn("You are terse.", prompt)
        self.assertIn("read_file", prompt)
        self.assertIn("Read the config.", prompt)
        self.assertIn("model = 'x'", prompt)
        self.assertIn('"path"', prompt)

    def test_tool_catalogue_handles_missing_tools(self):
        self.assertIn("没有可用工具", render_tools(None))
        self.assertIn("没有可用工具", render_tools([]))

    def test_parse_decision_reads_final_and_tool_calls(self):
        final = parse_decision('{"kind":"final","content":"done","tool_calls":null}')
        self.assertEqual(final.kind, "final")
        self.assertEqual(final.content, "done")
        self.assertFalse(final.is_tool_call)

        calls = parse_decision(
            '{"kind":"tool_calls","content":null,"tool_calls":'
            '[{"name":"read_file","arguments":"{\\"path\\":\\"a.txt\\"}"}]}'
        )
        self.assertTrue(calls.is_tool_call)
        self.assertEqual(calls.tool_calls[0].name, "read_file")
        self.assertEqual(json.loads(calls.tool_calls[0].arguments), {"path": "a.txt"})

    def test_parse_decision_tolerates_prose_and_fences(self):
        fenced = '```json\n{"kind":"final","content":"ok","tool_calls":null}\n```'
        self.assertEqual(parse_decision(fenced).content, "ok")
        prose = 'Here you go: {"kind":"final","content":"hi","tool_calls":null} thanks'
        self.assertEqual(parse_decision(prose).content, "hi")
        plain = "just a plain answer"
        self.assertEqual(parse_decision(plain).content, "just a plain answer")

    def test_parse_decision_coerces_object_arguments(self):
        decision = parse_decision(
            '{"kind":"tool_calls","content":null,"tool_calls":'
            '[{"name":"run","arguments":{"cmd":"dir"}}]}'
        )
        self.assertTrue(decision.is_tool_call)
        self.assertEqual(json.loads(decision.tool_calls[0].arguments), {"cmd": "dir"})

    def test_config_round_trip_and_loopback_only(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "gateway.json"
            config = GatewayConfig(
                port=8899, model="gpt-6-luna", effort="max", auto_start=True
            )
            config.save(path)
            loaded = GatewayConfig.load(path)
            self.assertEqual(loaded.port, 8899)
            self.assertEqual(loaded.model_id, "gpt-6-luna@max")
            self.assertTrue(loaded.auto_start)
            public = GatewayConfig.from_dict({"host": "0.0.0.0", "port": 80})
            self.assertEqual(public.host, "127.0.0.1")
            self.assertEqual(public.port, 1024)

    def test_gateway_serves_openai_completion(self):
        factory, runner = self._fake_factory(Decision(kind="final", content="hello"))
        gateway = CodexGateway(self._config(), Path("codex.exe"), runner_factory=factory)
        gateway.start()
        try:
            base = gateway.api_base
            request = urllib.request.Request(
                f"{base}/chat/completions",
                data=json.dumps(
                    {
                        "model": "gpt-6-luna@max",
                        "messages": [{"role": "user", "content": "hi"}],
                    }
                ).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=10) as response:
                body = json.loads(response.read().decode("utf-8"))
            self.assertEqual(body["object"], "chat.completion")
            self.assertEqual(body["choices"][0]["message"]["content"], "hello")
            self.assertEqual(body["choices"][0]["finish_reason"], "stop")
            self.assertEqual(body["usage"]["prompt_tokens"], 120)
            self.assertEqual(len(runner.prompts), 1)

            models_request = urllib.request.Request(f"{base}/models")
            with urllib.request.urlopen(models_request, timeout=10) as response:
                models = json.loads(response.read().decode("utf-8"))
            self.assertEqual(models["data"][0]["id"], "gpt-6-luna@max")
        finally:
            gateway.stop()

    def test_gateway_returns_tool_calls_and_streams(self):
        decision = Decision(
            kind="tool_calls",
            content=None,
            tool_calls=(ToolCall("read_file", '{"path":"a.txt"}'),),
        )
        factory, _runner = self._fake_factory(decision)
        gateway = CodexGateway(self._config(), Path("codex.exe"), runner_factory=factory)
        gateway.start()
        try:
            request = urllib.request.Request(
                f"{gateway.api_base}/chat/completions",
                data=json.dumps(
                    {
                        "model": "gpt-6-luna@max",
                        "stream": True,
                        "messages": [{"role": "user", "content": "read a.txt"}],
                        "tools": [
                            {
                                "type": "function",
                                "function": {
                                    "name": "read_file",
                                    "parameters": {"type": "object"},
                                },
                            }
                        ],
                    }
                ).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=10) as response:
                raw = response.read().decode("utf-8")
        finally:
            gateway.stop()
        self.assertIn('"tool_calls"', raw)
        self.assertIn("read_file", raw)
        self.assertIn('"finish_reason": "tool_calls"', raw)
        self.assertIn("data: [DONE]", raw)

    def test_gateway_requires_token(self):
        factory, _ = self._fake_factory(Decision(kind="final", content="ok"))
        gateway = CodexGateway(
            self._config(token="secret"), Path("codex.exe"), runner_factory=factory
        )
        gateway.start()
        try:
            request = urllib.request.Request(
                f"{gateway.api_base}/chat/completions",
                data=json.dumps({"messages": []}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(request, timeout=10)
            self.assertEqual(caught.exception.code, 401)
        finally:
            gateway.stop()

    def test_dynamic_tools_convert_openai_functions(self):
        specs = dynamic_tools(
            [
                {
                    "type": "function",
                    "function": {
                        "name": "write",
                        "description": "Write a file.",
                        "parameters": {
                            "type": "object",
                            "properties": {"path": {"type": "string"}},
                            "required": ["path"],
                        },
                    },
                },
                {"type": "function", "function": {"name": "", "description": "skip"}},
                "not-a-dict",
            ]
        )
        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0]["type"], "function")
        self.assertEqual(specs[0]["name"], "write")
        self.assertEqual(specs[0]["inputSchema"]["required"], ["path"])

    def test_dynamic_tools_default_schema(self):
        specs = dynamic_tools([{"type": "function", "function": {"name": "noargs"}}])
        self.assertEqual(specs[0]["inputSchema"], {"type": "object", "properties": {}})
        self.assertEqual(dynamic_tools(None), [])

    def test_mcp_insert_is_an_insert_patch(self):
        block = dsh_mcp_insert("C:/tools/console.exe", ["--mcp-advisor"])
        self.assertIn("- insert:", block)
        self.assertIn("mcp-deepseek-advisor", block)
        self.assertIn("@deepseek-ai/dsh-mcp-client", block)
        self.assertIn('"--mcp-advisor"', block)
        self.assertIn("transport: stdio", block)

        patch = "- id: keep\n  config:\n    flag: 1\n" + block
        self.assertIn("mcp-deepseek-advisor", strip_dsh_entries(patch, ("keep",)) + block)
        cleaned = strip_dsh_mcp_insert(block)
        self.assertNotIn("mcp-deepseek-advisor", cleaned)

    def test_strip_replaces_previous_entries_only(self):
        original = (
            "- id: desktop-shell\n"
            "  name: dsh-plugin-desktop\n"
            "- id: llm-pi-ai\n"
            "  config:\n"
            "    providers:\n"
            "      codex-local:\n"
            "        baseURL: http://127.0.0.1:8765/v1\n"
            "- id: tool-subagent\n"
            "  config:\n"
            "    provider: spawn\n"
            "- id: keep-me\n"
            "  config:\n"
            "    flag: true\n"
        )
        cleaned = strip_dsh_entries(
            original, ("llm-codex-local", "llm-pi-ai", "tool-subagent", "tool-subagent-fork")
        )
        self.assertIn("- id: desktop-shell", cleaned)
        self.assertIn("- id: keep-me", cleaned)
        self.assertNotIn("codex-local", cleaned)
        self.assertNotIn("- id: tool-subagent", cleaned)

    def test_provider_snippet_matches_gateway(self):
        snippet = dsh_provider_snippet(self._config(port=8765))
        self.assertIn("openai-completions", snippet)
        self.assertIn("http://127.0.0.1:8765/v1", snippet)
        self.assertIn("gpt-6-luna@max", snippet)
        self.assertNotIn("tool-subagent", snippet)
        # pi-ai refuses a route with no credential reference, even when the
        # gateway itself requires no token.
        self.assertIn("apiKeyEnv: CODEX_GATEWAY_TOKEN", snippet)

        with_subagent = dsh_provider_snippet(
            self._config(port=8765), subagent_model="deepseek-v4.1-flash"
        )
        self.assertIn("- id: tool-subagent", with_subagent)
        self.assertIn("- id: tool-subagent-fork", with_subagent)
        self.assertIn("agentOptions:", with_subagent)
        self.assertIn("model: deepseek-v4.1-flash", with_subagent)
        self.assertIn("maxTokens: 32768", with_subagent)


class AdvisorTests(unittest.TestCase):
    def test_prompt_carries_project_question_and_context(self):
        prompt = build_advisor_prompt(
            "这个布局怎么改？", "src/app.py:120-140", Path(r"E:\demo")
        )
        self.assertIn(r"E:\demo", prompt)
        self.assertIn("这个布局怎么改？", prompt)
        self.assertIn("src/app.py:120-140", prompt)

    def test_config_patch_points_at_the_configured_endpoint(self):
        config = AdvisorConfig(
            base_url="https://example.test/v1", model="deepseek-test"
        )
        patch = config.patch_text()
        self.assertIn("baseURL: https://example.test/v1", patch)
        self.assertIn("id: deepseek-test", patch)
        self.assertIn("reasoningEffort: high", patch)
        self.assertIn(
            "reasoningEffort: max", AdvisorConfig(effort="max").patch_text()
        )
        self.assertIn("dsh-agent-default-model", patch)

    def test_policy_is_added_once_and_removable(self):
        policy = build_advisor_policy()
        self.assertIn(ADVISOR_POLICY_START, policy)
        self.assertIn("ask_deepseek", policy)
        self.assertIn("UI", policy)

        merged = merge_advisor_policy("用户自己的规则", True)
        self.assertTrue(merged.startswith("用户自己的规则"))
        self.assertTrue(has_advisor_policy(merged))
        self.assertEqual(merged.count(ADVISOR_POLICY_START), 1)

        again = merge_advisor_policy(merged, True)
        self.assertEqual(again.count(ADVISOR_POLICY_START), 1)

        removed = merge_advisor_policy(merged, False)
        self.assertFalse(has_advisor_policy(removed))
        self.assertIn("用户自己的规则", removed)

    def test_mcp_config_insert_keeps_other_servers(self):
        existing = (
            "[mcp_servers.other]\n"
            'command = "node"\n'
            "\n"
            "[profiles.default]\n"
            'model = "gpt-6-luna"\n'
        )
        script = r"E:\tools\codex_mcp_advisor.py"
        merged = merge_mcp_config(existing, "py", ["-3.11", script])
        self.assertIn("[mcp_servers.other]", merged)
        self.assertIn("[mcp_servers.deepseek_advisor]", merged)
        self.assertIn("codex_mcp_advisor.py", merged)
        self.assertIn("[profiles.default]", merged)
        self.assertEqual(merged.count("[mcp_servers.deepseek_advisor]"), 1)

        replaced = merge_mcp_config(merged, "python", [script])
        self.assertEqual(replaced.count("[mcp_servers.deepseek_advisor]"), 1)
        self.assertIn('"python"', replaced)

    def test_sessions_are_persisted_per_project(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "sessions.json"
            store = AdvisorSessions(path)
            project = Path(temp) / "proj"
            project.mkdir()
            self.assertEqual(store.get(project), "")
            store.set(project, "session-abc")
            self.assertEqual(store.get(project), "session-abc")

            reloaded = AdvisorSessions(path)
            self.assertEqual(reloaded.get(project), "session-abc")
            reloaded.clear(project)
            self.assertEqual(AdvisorSessions(path).get(project), "")

    def test_runner_builds_a_headless_session_command(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "DeepSeek Harness.exe").write_text("stub", encoding="utf-8")
            (root / "resources").mkdir()
            (root / "resources" / "app.asar").write_text("asar", encoding="utf-8")
            config = AdvisorConfig(
                base_url="https://example.test/v1",
                model="deepseek-test",
                install_root=str(root),
            )
            sessions = AdvisorSessions(Path(temp) / "s.json")
            runner = AdvisorRunner(config, sessions, Path(temp) / "patch.yml")
            command = runner.build_command("问题", "")
            self.assertIn("--profile", command)
            self.assertIn("headless", command)
            self.assertIn("--patch", command)
            self.assertNotIn("--session-id", command)

            command = runner.build_command("问题", "session-xyz")
            self.assertIn("--session-id", command)
            self.assertIn("session-xyz", command)

    def test_runner_parses_events_and_stores_the_session(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "DeepSeek Harness.exe").write_text("stub", encoding="utf-8")
            (root / "resources").mkdir()
            (root / "resources" / "app.asar").write_text("asar", encoding="utf-8")
            project = root / "project"
            project.mkdir()
            config = AdvisorConfig(
                base_url="https://example.test/v1",
                model="deepseek-test",
                encrypted_api_key="ignored",
                install_root=str(root),
            )
            sessions = AdvisorSessions(root / "sessions.json")
            runner = AdvisorRunner(config, sessions, root / "patch.yml")

            events_text = "\n".join(
                [
                    '{"type":"session","sessionId":"session-new"}',
                    '{"type":"status","phase":"turn_start"}',
                    '{"type":"final","text":"改这三处"}',
                ]
            )

            class _Completed:
                returncode = 0
                stderr = ""
                stdout = events_text

            recorded = {}

            def fake_run(command, **kwargs):
                recorded["command"] = command
                recorded["env"] = kwargs.get("env")
                recorded["cwd"] = kwargs.get("cwd")
                return _Completed()

            original_run = codex_advisor.subprocess.run
            original_decrypt = codex_advisor._decrypt
            codex_advisor.subprocess.run = fake_run
            codex_advisor._decrypt = lambda _blob: "test-key"
            try:
                answer = AdvisorRunner.consult(
                    runner, "看看布局", "src/ui.py", project
                )
            finally:
                codex_advisor.subprocess.run = original_run
                codex_advisor._decrypt = original_decrypt

            self.assertTrue(answer.ok)
            self.assertEqual(answer.text, "改这三处")
            self.assertEqual(answer.session_id, "session-new")
            self.assertEqual(sessions.get(project), "session-new")
            self.assertIn("DEEPSEEK_API_KEY", recorded["env"])
            self.assertEqual(recorded["cwd"], str(project))

    def test_runner_reports_missing_install(self):
        with tempfile.TemporaryDirectory() as temp:
            config = AdvisorConfig(install_root=str(Path(temp) / "nope"))
            runner = AdvisorRunner(
                config, AdvisorSessions(Path(temp) / "s.json"), Path(temp) / "p.yml"
            )
            original = codex_advisor.find_dsh_install
            codex_advisor.find_dsh_install = lambda _explicit="": None
            try:
                with self.assertRaises(AdvisorError):
                    runner.build_command("问题", "")
            finally:
                codex_advisor.find_dsh_install = original

    def test_store_round_trip(self):
        with tempfile.TemporaryDirectory() as temp:
            store = AdvisorStore.for_home(Path(temp))
            config = AdvisorConfig(
                base_url="https://example.test/v1", model="deepseek-test"
            )
            store.save_config(config)
            loaded = store.load_config()
            self.assertEqual(loaded.base_url, "https://example.test/v1")
            self.assertEqual(loaded.model, "deepseek-test")
            self.assertEqual(store.sessions_path.name, "codex-agent-console-advisor-sessions.json")


if __name__ == "__main__":
    unittest.main()
