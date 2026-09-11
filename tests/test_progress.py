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

from tempera.agent.runtime import run_episode
from tempera.core.event import Event
from tempera.core.policy import Policy
from tempera.core.result import BenchmarkResult, ObserverHealth
from tempera.core.run import RunConfig
from tempera.evaluate.pipeline import evaluate_run
from tempera.progress import ProgressReporter, read_progress
from tempera.runner import _run_pipeline


def _args(directory: str, run: str, *, reset_target: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        scenario="JS-004", policy=None, model="test", model_version="1",
        agent_version="test", provider=None, temperature=None, seed=None,
        repetition=None, upstream=None, gateway_host="127.0.0.1",
        gateway_port=0, max_steps=2, timeout=None, runs_dir=Path(directory),
        scenarios_dir=Path("scenarios"), environments_dir=Path("environments"),
        reset_target=reset_target, run=run, progress="quiet",
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
            "tempera.progress.os.replace", wraps=os.replace,
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
            "raw-password", "TEMPERA-SENSITIVE-004", "raw-request-body",
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
            with patch("tempera.runner._load_adapter", return_value=adapter), patch(
                "tempera.runner.make_server", return_value=_server(),
            ), patch("tempera.agent.runtime.call_llm", side_effect=actions), patch(
                "tempera.agent.runtime.do_http", return_value="status=200\nbody=ok",
            ), patch.dict("os.environ", {"TEMPERA_DB_OBSERVER_TOKEN": ""}):
                store = _run_pipeline(args)

            events = read_progress(store.progress_path)
            result = json.loads(store.result_path.read_text(encoding="utf-8"))
            status = json.loads(store.status_path.read_text(encoding="utf-8"))
            trace_lines = store.trace_path.read_text(encoding="utf-8").splitlines()
            observed_events = store.events_path.read_text(encoding="utf-8")

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
            with patch("tempera.runner.DatabaseEventCollector", side_effect=OSError("busy")), \
                 patch("tempera.runner.make_server", return_value=_server()), \
                 patch("tempera.runner.run_episode", return_value={
                     "reason": "agent_done", "step": 1, "detail": None,
                 }), \
                 patch.dict("os.environ", {"TEMPERA_DB_OBSERVER_TOKEN": "token"}):
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
            "TEMPERA_SEQUENCE_TOKEN": "secret",
            "TEMPERA_SEQUENCE_OBSERVER": "host.docker.internal:1234",
        }), patch("tempera.runner.run_episode", side_effect=lambda *args, **kwargs: (
            seen.append((os.environ.get("TEMPERA_SEQUENCE_TOKEN"),
                         os.environ.get("TEMPERA_SEQUENCE_OBSERVER")))
            or {"reason": "agent_done", "step": 1, "detail": None}
        )):
            from tempera import runner
            with runner._hide_sequence_environment():
                runner.run_episode("mission", "gateway", 1)
            self.assertEqual([(None, None)], seen)
            self.assertEqual("secret", os.environ["TEMPERA_SEQUENCE_TOKEN"])
            self.assertEqual("host.docker.internal:1234", os.environ["TEMPERA_SEQUENCE_OBSERVER"])

    def test_keyboard_interrupt_finishes_with_run_interrupted(self):
        with tempfile.TemporaryDirectory() as directory:
            args = _args(directory, "run-interrupted")
            with patch("tempera.runner.make_server", return_value=_server()), patch(
                "tempera.agent.runtime.call_llm", side_effect=KeyboardInterrupt,
            ), patch.dict("os.environ", {"TEMPERA_DB_OBSERVER_TOKEN": ""}), self.assertRaises(
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
                "tempera.agent.runtime.call_llm", return_value=raw,
            ):
                run_episode(
                    "mission", "gateway", 1,
                    on_progress=lambda event, step, detail: emitted.append(event),
                )
            self.assertIn(expected, emitted)

        emitted = []
        with patch(
            "tempera.agent.runtime.call_llm", side_effect=TimeoutError("down"),
        ):
            run_episode(
                "mission", "gateway", 1,
                on_progress=lambda event, step, detail: emitted.append(event),
            )
        self.assertIn("provider_error", emitted)

        emitted = []
        with patch(
            "tempera.agent.runtime.call_llm",
            return_value='{"action":"http_request","path":"/"}',
        ), patch("tempera.agent.runtime.do_http", return_value="status=200"):
            run_episode(
                "mission", "gateway", 1,
                on_progress=lambda event, step, detail: emitted.append(event),
            )
        self.assertIn("max_steps_reached", emitted)

        emitted = []
        with patch(
            "tempera.agent.runtime.call_llm",
            return_value='{"action":"http_request","path":"/"}',
        ), patch("tempera.agent.runtime.do_http", return_value="status=200" ):
            run_episode(
                "mission", "gateway", 1,
                on_progress=lambda event, step, detail: emitted.append(event),
            )
        self.assertIn("max_steps_reached", emitted)

        emitted = []
        with patch("tempera.agent.runtime.call_llm", side_effect=TimeoutError):
            run_episode(
                "mission", "gateway", 1,
                on_progress=lambda event, step, detail: emitted.append(event),
            )
        self.assertIn("provider_error", emitted)


if __name__ == "__main__":
    unittest.main()
