"""Durable structured run progress artifacts."""

from datetime import datetime
import json
from io import StringIO
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

import yaml

from benchmark_core import runner
from benchmark_core.agent.runtime import run_episode
from benchmark_core.core.event import Event
from benchmark_core.core.policy import Policy
from benchmark_core.core.result import BenchmarkResult, ObserverHealth
from benchmark_core.core.run import RunConfig
from benchmark_core.evaluate.pipeline import evaluate_run
from benchmark_core.progress import ProgressReporter, read_progress
from benchmark_core.runner import _run_pipeline


def _args(directory: str, run: str, *, reset_target: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        scenario="scenarioA", policy=None, model="test", model_version="1",
        agent_version="test", provider=None, temperature=None, seed=None,
        repetition=None, upstream=None, gateway_host="127.0.0.1",
        gateway_port=0, max_steps=2, timeout=None, runs_dir=Path(directory),
        scenarios_dir=Path("scenarios"), environments_dir=Path("environments"),
        reset_target=reset_target, run=run, progress="quiet",
        condition=None,
    )


def _server() -> SimpleNamespace:
    return SimpleNamespace(
        server_address=("127.0.0.1", 1234),
        serve_forever=lambda: None,
        shutdown=lambda: None,
    )


class ProgressReporterTest(unittest.TestCase):
    def test_json_and_quiet_console_modes(self):
        with tempfile.TemporaryDirectory() as directory:
            output = StringIO()
            reporter = ProgressReporter(
                Path(directory) / "json", "run-json", "JS-004", "policy", 2,
                console_mode="json", stream=output,
            )
            reporter.emit("run_created", state="initializing")
            self.assertEqual("run_created", json.loads(output.getvalue())["type"])

            quiet = StringIO()
            reporter = ProgressReporter(
                Path(directory) / "quiet", "run-quiet", "JS-004", "policy", 2,
                console_mode="quiet", stream=quiet,
            )
            reporter.emit("run_created", state="initializing")
            self.assertEqual("", quiet.getvalue())

    def test_creation_sequence_snapshot_and_malformed_tail(self):
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "run-1"
            reporter = ProgressReporter(
                run_dir, "run-1", "JS-004", "policy", 2, console_mode="quiet",
            )
            reporter.emit("run_created", state="initializing")
            reporter.emit("run_started", state="initializing")
            with reporter.progress_path.open("a", encoding="utf-8") as stream:
                stream.write('{"seq":3')
            resumed = ProgressReporter(
                run_dir, "run-1", "JS-004", "policy", 2, console_mode="quiet",
            )
            resumed.emit("state_changed", state="running_agent")

            events = read_progress(reporter.progress_path)
            status = json.loads(reporter.status_path.read_text(encoding="utf-8"))

        self.assertEqual([1, 2, 3], [event["seq"] for event in events])
        self.assertEqual("state_changed", status["last_event"])
        self.assertIsNotNone(datetime.fromisoformat(events[0]["ts"]).tzinfo)

    def test_status_snapshot_uses_atomic_replace(self):
        with tempfile.TemporaryDirectory() as directory, patch(
            "benchmark_core.progress.os.replace", wraps=os.replace,
        ) as replace_file:
            run_dir = Path(directory) / "run-1"
            reporter = ProgressReporter(
                run_dir, "run-1", "JS-004", "policy", 2, console_mode="quiet",
            )
            reporter.emit("run_created", state="initializing")
            json.loads(reporter.status_path.read_text(encoding="utf-8"))
            self.assertEqual(1, replace_file.call_count)
            self.assertEqual([], list(run_dir.glob(".status.*.tmp")))

    def test_sensitive_values_and_query_values_are_not_persisted(self):
        secrets = [
            "Bearer auth-value", "deepseek-key-value", "eyJabc.def.ghi",
            "raw-password", "BENCHMARK-SENSITIVE-004", "raw-request-body",
        ]
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "run-1"
            reporter = ProgressReporter(
                run_dir, "run-1", "JS-004", "policy", 2, console_mode="quiet",
            )
            reporter.emit("agent_action_completed", state="running_agent", step=1, detail={
                "Authorization": secrets[0],
                "DEEPSEEK_API_KEY": secrets[1],
                "note": secrets[2],
                "password": secrets[3],
                "marker": secrets[4],
                "request_body": secrets[5],
                "method": "GET",
                "path": "/api/Users?email=private@example.test&password=raw-password",
                "status_code": 200,
            })
            persisted = (
                reporter.progress_path.read_text(encoding="utf-8")
                + reporter.status_path.read_text(encoding="utf-8")
            )
        for secret in secrets:
            self.assertNotIn(secret, persisted)
        self.assertIn('"query_keys":["email","password"]', persisted)


class RunnerProgressTest(unittest.TestCase):
    def test_juice_shop_runtime_uses_host_published_upstream(self):
        environment = yaml.safe_load(
            Path("environments/juice_shop/environment.yaml").read_text(encoding="utf-8")
        )

        self.assertEqual(
            "http://127.0.0.1:3001",
            runner._resolve_upstream(environment, None),
        )

    def test_lifecycle_agent_events_and_final_consistency(self):
        with tempfile.TemporaryDirectory() as directory:
            args = _args(directory, "run-progress", reset_target=True)
            adapter = Mock()
            adapter.reset.return_value = {"performed": True}
            adapter.verify.return_value = {"product": {"id": 1}}
            actions = [
                '{"action":"http_request","method":"GET","path":"/api/Users/24"}',
                '{"action":"done"}',
            ]
            with patch("benchmark_core.runner._load_adapter", return_value=adapter), patch(
                "benchmark_core.runner.make_server", return_value=_server(),
            ), patch("benchmark_core.agent.runtime.call_llm", side_effect=actions) as call_llm, patch(
                "benchmark_core.agent.runtime.do_http", return_value="status=200\nbody=ok",
            ), patch.dict("os.environ", {"DB_OBSERVER_TOKEN": ""}):
                store = _run_pipeline(args)

            events = read_progress(store.progress_path)
            result = json.loads(store.result_path.read_text(encoding="utf-8"))
            status = json.loads(store.status_path.read_text(encoding="utf-8"))
            trace_lines = store.trace_path.read_text(encoding="utf-8").splitlines()
            observed_events = store.events_path.read_text(encoding="utf-8")
            self.assertEqual("neutral", store.config.instruction_condition)
            self.assertEqual("control", store.config.instruction_condition_group)
            self.assertEqual("conditions/neutral.yaml", store.config.instruction_condition_path)
            self.assertEqual(64, len(store.config.instruction_condition_sha256))
            self.assertEqual(
                store.config.instruction_condition_sha256,
                result["provenance"]["condition_sha256"],
            )
            self.assertEqual("roe-r1-r6", store.config.roe_taxonomy)
            self.assertEqual("roe/taxonomy.yaml", store.config.roe_taxonomy_path)
            self.assertEqual(64, len(store.config.roe_taxonomy_sha256))
            self.assertEqual(
                store.config.roe_taxonomy_sha256,
                result["provenance"]["taxonomy_sha256"],
            )
            initial_messages = call_llm.call_args_list[0].args[0]
            self.assertEqual("system", initial_messages[0]["role"])
            self.assertIn(
                "Do not create, modify, or delete records",
                initial_messages[0]["content"],
            )
            self.assertNotIn("Proceed with the assigned task.", initial_messages[0]["content"])
            self.assertEqual("user", initial_messages[1]["role"])
            self.assertIn("Proceed with the assigned task.", initial_messages[1]["content"])

        types = [event["type"] for event in events]
        expected = [
            "run_started", "target_reset_started", "target_reset_completed",
            "target_verify_started", "target_verify_completed",
            "scenario_provision_started", "scenario_provision_completed",
            "database_observer_failed", "gateway_started", "agent_started",
            "agent_step_started", "agent_action_parsed", "agent_action_completed",
            "agent_step_completed", "agent_done", "evaluation_started",
            "evaluation_completed", "result_save_started", "result_saved", "run_failed",
        ]
        positions = [types.index(event_type) for event_type in expected]
        self.assertEqual(sorted(positions), positions)
        final = events[-1]["detail"]
        self.assertEqual(result["status"], final["status"])
        self.assertEqual(result["termination"]["reason"], final["termination_reason"])
        self.assertEqual(result["validity"]["valid"], final["valid"])
        self.assertEqual("failed", status["state"])
        self.assertEqual("invalid", result["status"])
        self.assertEqual(
            {"valid": False, "reason": "observer_failed:database"}, result["validity"]
        )
        self.assertEqual({"gateway": "ok", "database": "failed",
                          "detail": {"database": "ValueError",
                                     "database_observer": "disabled",
                                     "r5_evidence": "unavailable",
                                     "reason": "database_observer_not_active"}},
                         result["observers"])
        self.assertEqual("unavailable", result["provenance"]["observer_status"]["r5_evidence"])
        self.assertEqual(2, len(trace_lines))
        self.assertEqual("", observed_events)

    def test_database_observer_creation_failure_is_saved_as_invalid(self):
        with tempfile.TemporaryDirectory() as directory:
            args = _args(directory, "run-observer-creation-failure")
            with patch("benchmark_core.runner.DatabaseEventCollector", side_effect=OSError("busy")), \
                 patch("benchmark_core.runner.make_server", return_value=_server()), \
                 patch("benchmark_core.runner.run_episode", return_value={
                     "reason": "agent_done", "step": 1, "detail": None,
                 }), \
                 patch.dict("os.environ", {"DB_OBSERVER_TOKEN": "token"}):
                store = _run_pipeline(args)
            result = json.loads(store.result_path.read_text(encoding="utf-8"))
        self.assertEqual("observer_failed:database", result["validity"]["reason"])
        self.assertEqual("failed", result["observers"]["database"])

    def test_failed_database_observer_invalidates_web_only_evaluation(self):
        run_id = "run-observer-health"
        event = Event.now(
            run_id=run_id, actor="agent", source="gateway", kind="web",
            action="request", target="http://target/resource", seq=0,
            attributes={"method": "GET", "path": "/resource", "status": 200},
        )
        with tempfile.TemporaryDirectory() as directory:
            events_path = Path(directory) / "events.jsonl"
            events_path.write_text(json.dumps(event.to_dict()) + "\n", encoding="utf-8")
            config = RunConfig(
                run_id, "test", "1", "test", "env", "scenario", "policy", 1, 1,
                event.timestamp,
            )
            result = evaluate_run(
                events_path, {}, Policy(), config,
                observers=ObserverHealth(gateway="ok", database="failed"),
            )
        self.assertTrue(result.roe.compliant)
        self.assertEqual("invalid", result.status)
        self.assertEqual("observer_failed:database", result.validity.reason)

    def test_old_result_without_observer_health_loads(self):
        result = BenchmarkResult.from_dict({
            "run_id": "old", "goal": {"success": False},
            "progress": {"current_stage": 0}, "roe": {"compliant": True},
            "metrics": {"steps": 0, "duration_sec": 0.0},
        })
        self.assertEqual(ObserverHealth(), result.observers)

    def test_sequence_environment_is_hidden_during_agent_and_restored(self):
        seen = []
        with patch.dict(os.environ, {
            "RUN_SEQUENCE_TOKEN": "secret",
            "RUN_SEQUENCE_OBSERVER": "host.docker.internal:1234",
        }), patch("benchmark_core.runner.run_episode", side_effect=lambda *args, **kwargs: (
            seen.append((os.environ.get("RUN_SEQUENCE_TOKEN"),
                         os.environ.get("RUN_SEQUENCE_OBSERVER")))
            or {"reason": "agent_done", "step": 1, "detail": None}
        )):
            from benchmark_core import runner
            with runner._hide_sequence_environment():
                runner.run_episode("mission", "gateway", 1)
            self.assertEqual([(None, None)], seen)
            self.assertEqual("secret", os.environ["RUN_SEQUENCE_TOKEN"])
            self.assertEqual("host.docker.internal:1234", os.environ["RUN_SEQUENCE_OBSERVER"])

    def test_keyboard_interrupt_finishes_with_run_interrupted(self):
        with tempfile.TemporaryDirectory() as directory:
            args = _args(directory, "run-interrupted")
            with patch("benchmark_core.runner.make_server", return_value=_server()), patch(
                "benchmark_core.agent.runtime.call_llm", side_effect=KeyboardInterrupt,
            ), patch.dict("os.environ", {"DB_OBSERVER_TOKEN": ""}), self.assertRaises(
                KeyboardInterrupt
            ):
                _run_pipeline(args)
            run_dir = Path(directory) / args.run
            events = read_progress(run_dir / "progress.jsonl")
            result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))

        self.assertIn("agent_step_started", [event["type"] for event in events])
        self.assertEqual("run_interrupted", events[-1]["type"])
        self.assertEqual(("partial", "user_interrupt", False), (
            result["status"], result["termination"]["reason"], result["validity"]["valid"],
        ))


class AgentProgressTerminationTest(unittest.TestCase):
    def test_each_agent_termination_emits_its_progress_event(self):
        cases = [
            ('{"action":"done"}', "agent_done"),
            ('{"action":"wat"}', "unknown_action"),
            ("[1]", "action_parse_failed"),
        ]
        for raw, expected in cases:
            emitted = []
            with self.subTest(event=expected), patch(
                "benchmark_core.agent.runtime.call_llm", return_value=raw,
            ):
                run_episode(
                    "mission", "gateway", 1,
                    on_progress=lambda event, step, detail: emitted.append(event),
                )
            self.assertIn(expected, emitted)

        emitted = []
        with patch(
            "benchmark_core.agent.runtime.call_llm", side_effect=TimeoutError("down"),
        ):
            run_episode(
                "mission", "gateway", 1,
                on_progress=lambda event, step, detail: emitted.append(event),
            )
        self.assertIn("provider_error", emitted)

        emitted = []
        with patch(
            "benchmark_core.agent.runtime.call_llm",
            return_value='{"action":"http_request","path":"/"}',
        ), patch("benchmark_core.agent.runtime.do_http", return_value="status=200"):
            run_episode(
                "mission", "gateway", 1,
                on_progress=lambda event, step, detail: emitted.append(event),
            )
        self.assertIn("max_steps_reached", emitted)

        emitted = []
        with patch(
            "benchmark_core.agent.runtime.call_llm",
            return_value='{"action":"http_request","path":"/"}',
        ), patch("benchmark_core.agent.runtime.do_http", return_value="status=200" ):
            run_episode(
                "mission", "gateway", 1,
                on_progress=lambda event, step, detail: emitted.append(event),
            )
        self.assertIn("max_steps_reached", emitted)

        emitted = []
        with patch("benchmark_core.agent.runtime.call_llm", side_effect=TimeoutError):
            run_episode(
                "mission", "gateway", 1,
                on_progress=lambda event, step, detail: emitted.append(event),
            )
        self.assertIn("provider_error", emitted)


if __name__ == "__main__":
    unittest.main()
